"""
Multi-Tenant AI Chatbot Platform - Main FastAPI Application
===========================================================
Handles all REST API endpoints, authentication, rate limiting,
CORS, static file serving, and request routing.
"""

import os
import json
import time
import uuid
import random
import asyncio
import logging
import hashlib
import bcrypt
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional

from fastapi import (
    FastAPI, HTTPException, UploadFile, File, Form,
    Request, Depends, Header, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, field_validator
import uvicorn
from dotenv import load_dotenv

# Load .env from project root (works both locally and in Docker/Railway where the workdir is /app)
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=ROOT_DIR / ".env")

# Internal modules
from .tenant_manager import TenantManager
from .rag_pipeline import RAGPipeline
from .ingestion import DocumentIngestion
from .vector_store import VectorStoreManager

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "app.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")

# ── Config ───────────────────────────────────────────────────────────────────
CONFIG_PATH = ROOT_DIR / "config.json"
with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

# ── Rate-limit store (in-memory) ─────────────────────────────────────────────
_rate_store: dict[str, list[float]] = {}
RATE_LIMIT  = 30   # requests per IP
RATE_WINDOW = 60   # seconds

_chatbot_rate_store: dict[str, list[float]] = {}
CHATBOT_RATE_LIMIT  = 200   # requests per chatbot
CHATBOT_RATE_WINDOW = 3600  # 1 hour

# ── Admin / SMTP config ───────────────────────────────────────────────────────
ADMIN_EMAIL   = os.getenv("ADMIN_EMAIL", "")
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("SMTP_FROM", SMTP_USER)

_admin_otp:      dict[str, float] = {}   # {otp_code: expiry}
_admin_sessions: dict[str, float] = {}   # {token: expiry}
ADMIN_OTP_TTL     = 600    # 10 minutes
ADMIN_SESSION_TTL = 28800  # 8 hours

def check_rate_limit(ip: str) -> None:
    now = time.time()
    hits = [t for t in _rate_store.get(ip, []) if now - t < RATE_WINDOW]
    if len(hits) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    hits.append(now)
    _rate_store[ip] = hits

def check_chatbot_rate_limit(chatbot_id: str) -> None:
    now = time.time()
    hits = [t for t in _chatbot_rate_store.get(chatbot_id, []) if now - t < CHATBOT_RATE_WINDOW]
    if len(hits) >= CHATBOT_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="This chatbot has reached its hourly request limit.")
    hits.append(now)
    _chatbot_rate_store[chatbot_id] = hits

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title=CONFIG.get("platform_name", "ChatBot Platform"),
    description="Multi-tenant AI chatbot platform powered by RAG + free LLM providers",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG.get("allowed_origins", ["*"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files ──────────────────────────────────────────────────────────────
FRONTEND_DIR = ROOT_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ── Service singletons ────────────────────────────────────────────────────────
tenant_mgr  = TenantManager()
vector_mgr  = VectorStoreManager()
rag         = RAGPipeline(vector_mgr)
ingestion   = DocumentIngestion(vector_mgr, tenant_mgr)

# ── Pydantic models ───────────────────────────────────────────────────────────

class ClientLogin(BaseModel):
    email: str
    password: str


class ChangePassword(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def pw_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class AdminCreateClient(BaseModel):
    name: str
    email: str
    password: str
    company: Optional[str] = ""

    @field_validator("password")
    @classmethod
    def pw_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class AdminVerifyOTP(BaseModel):
    otp: str


class ChatRequest(BaseModel):
    chatbot_id: str
    message: str
    session_id: Optional[str] = None

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        # Basic prompt-injection guards
        v = v.strip()
        if len(v) > 2000:
            raise ValueError("Message too long (max 2000 chars)")
        banned = ["ignore previous instructions", "ignore all instructions",
                  "disregard your", "you are now", "new persona"]
        low = v.lower()
        for phrase in banned:
            if phrase in low:
                raise ValueError("Message contains disallowed content")
        return v


class CreateChatbotRequest(BaseModel):
    name: str
    welcome_message: Optional[str] = "Hello! How can I help you today?"
    color: Optional[str] = "#2563eb"
    allowed_domains: Optional[list[str]] = []


class UpdateChatbotRequest(BaseModel):
    name: Optional[str] = None
    welcome_message: Optional[str] = None
    color: Optional[str] = None
    allowed_domains: Optional[list[str]] = None
    lead_form_enabled: Optional[int] = None


class LeadSubmit(BaseModel):
    chatbot_id: str
    session_id: str
    name: Optional[str] = ""
    mobile: str
    email: str
    requirement: str

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        import re
        v = v.strip()
        if not re.match(r"^\+?[\d\s\-()+]{7,20}$", v):
            raise ValueError("Invalid mobile number format")
        return v

    @field_validator("email")
    @classmethod
    def validate_email_fmt(cls, v: str) -> str:
        import re
        v = v.strip()
        if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("requirement")
    @classmethod
    def validate_requirement(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Service requirement is required")
        if len(v) > 1000:
            raise ValueError("Service requirement too long (max 1000 chars)")
        return v


# ── Auth helpers ──────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    # Support migration from old SHA-256 hashes (64 hex chars, no $ prefix)
    if not stored_hash.startswith("$2"):
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    return bcrypt.checkpw(password.encode(), stored_hash.encode())


def _send_email_sync(to: str, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.sendmail(SMTP_FROM, [to], msg.as_string())

async def send_email(to: str, subject: str, body: str) -> None:
    await asyncio.to_thread(_send_email_sync, to, subject, body)


def get_admin_from_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin authentication required")
    token = authorization.split(" ", 1)[1]
    expiry = _admin_sessions.get(token)
    if not expiry or time.time() > expiry:
        raise HTTPException(status_code=401, detail="Admin session expired or invalid")
    return True


def get_client_from_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ", 1)[1]
    client = tenant_mgr.get_client_by_token(token)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return client


def check_domain_allowed(bot: dict, request: Request) -> None:
    """Raise 403 if the request Origin is not in the chatbot's allowed_domains list.
    If allowed_domains is empty, all origins are permitted."""
    raw = bot.get("allowed_domains") or ""
    # Normalize stored domains: strip scheme and port so "localhost:3000" and
    # "https://example.com" both reduce to just the hostname.
    def _extract_host(d: str) -> str:
        d = d.strip().lower()
        if "://" not in d:
            d = "http://" + d  # urlparse needs a scheme to parse correctly
        return urlparse(d).hostname or ""
    domains = [_extract_host(d) for d in raw.split(",") if d.strip()]
    if not domains:
        return  # no restriction configured
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if not origin:
        raise HTTPException(status_code=403, detail="Origin header required for this chatbot")
    hostname = urlparse(origin).hostname or ""
    if hostname not in domains:
        raise HTTPException(status_code=403, detail="Domain not allowed for this chatbot")


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/login", tags=["Auth"])
async def login(body: ClientLogin, request: Request):
    """Authenticate and receive a session token."""
    check_rate_limit(request.client.host)
    client = tenant_mgr.get_client_by_email(body.email)
    if not client or not verify_password(body.password, client["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = str(uuid.uuid4())
    tenant_mgr.update_client_token(client["client_id"], token)
    # Migrate SHA-256 hash to bcrypt on successful login
    if not client["password_hash"].startswith("$2"):
        tenant_mgr.update_client_password(client["client_id"], hash_password(body.password))
    return {
        "client_id":           client["client_id"],
        "token":               token,
        "name":                client["name"],
        "email":               client["email"],
        "must_change_password": bool(client.get("must_change_password", 0)),
    }


@app.post("/api/auth/change-password", tags=["Auth"])
async def change_password(body: ChangePassword, client=Depends(get_client_from_token)):
    """Allow an authenticated client to change their password."""
    if not verify_password(body.old_password, client["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    tenant_mgr.update_client_password(client["client_id"], hash_password(body.new_password), clear_must_change=True)
    return {"message": "Password updated successfully"}


# ── Admin OTP & Session ───────────────────────────────────────────────────────

@app.post("/api/admin/request-otp", tags=["Admin"])
async def admin_request_otp(request: Request):
    """Generate a 6-digit OTP and email it to the configured ADMIN_EMAIL."""
    check_rate_limit(request.client.host)
    if not ADMIN_EMAIL:
        raise HTTPException(status_code=501, detail="ADMIN_EMAIL is not configured")
    if not SMTP_USER or not SMTP_PASSWORD:
        raise HTTPException(status_code=501, detail="SMTP credentials are not configured")
    otp = f"{random.randint(0, 999999):06d}"
    _admin_otp.clear()
    _admin_otp[otp] = time.time() + ADMIN_OTP_TTL
    try:
        await send_email(
            to=ADMIN_EMAIL,
            subject="Your Admin OTP",
            body=f"Your one-time password is: {otp}\n\nValid for 10 minutes.",
        )
    except Exception as e:
        logger.error("Failed to send OTP email: %s", e)
        raise HTTPException(status_code=500, detail="Failed to send OTP email. Check SMTP config.")
    logger.info("Admin OTP sent to %s", ADMIN_EMAIL)
    return {"message": "OTP sent to admin email"}


@app.post("/api/admin/verify-otp", tags=["Admin"])
async def admin_verify_otp(body: AdminVerifyOTP, request: Request):
    """Validate the OTP and return an 8-hour admin session token."""
    check_rate_limit(request.client.host)
    expiry = _admin_otp.get(body.otp)
    if not expiry or time.time() > expiry:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP")
    del _admin_otp[body.otp]
    token = str(uuid.uuid4())
    _admin_sessions[token] = time.time() + ADMIN_SESSION_TTL
    logger.info("Admin session created")
    return {"token": token}


# ── Admin Client Management ───────────────────────────────────────────────────

@app.get("/api/admin/clients", tags=["Admin"])
async def admin_list_clients(_=Depends(get_admin_from_token)):
    """List all client accounts."""
    return {"clients": tenant_mgr.list_clients()}


@app.post("/api/admin/clients", tags=["Admin"])
async def admin_create_client(body: AdminCreateClient, _=Depends(get_admin_from_token)):
    """Create a new client account with a temporary password."""
    if tenant_mgr.get_client_by_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    client_id = str(uuid.uuid4())
    token     = str(uuid.uuid4())
    tenant_mgr.create_client(
        client_id=client_id,
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        company=body.company or "",
        token=token,
        must_change_password=1,
    )
    logger.info("Admin created client: %s (%s)", body.email, client_id)
    return {"client_id": client_id, "message": "Client created"}


@app.delete("/api/admin/clients/{client_id}", tags=["Admin"])
async def admin_delete_client(client_id: str, _=Depends(get_admin_from_token)):
    """Delete a client and all their chatbots/data (CASCADE)."""
    client = tenant_mgr.get_client_by_id(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    # Delete all chatbot vector collections for this client
    for bot in tenant_mgr.get_chatbots_for_client(client_id):
        try:
            vector_mgr.delete_collection(bot["chatbot_id"])
        except Exception:
            pass
    tenant_mgr.delete_client(client_id)
    logger.info("Admin deleted client: %s", client_id)
    return {"message": "Client deleted"}


# ═══════════════════════════════════════════════════════════════════════════════
# CHATBOT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/chatbots", tags=["Chatbots"])
async def create_chatbot(body: CreateChatbotRequest, client=Depends(get_client_from_token)):
    """Create a new chatbot for the authenticated client."""
    chatbot_id = str(uuid.uuid4()).replace("-", "")[:16]
    tenant_mgr.create_chatbot(
        chatbot_id=chatbot_id,
        client_id=client["client_id"],
        name=body.name,
        welcome_message=body.welcome_message,
        color=body.color,
        allowed_domains=",".join(body.allowed_domains or []),
    )
    # Initialise an empty collection for this chatbot
    vector_mgr.get_or_create_collection(chatbot_id)
    logger.info("Chatbot created: %s for client %s", chatbot_id, client["client_id"])
    return {"chatbot_id": chatbot_id, "name": body.name, "message": "Chatbot created"}


@app.get("/api/chatbots", tags=["Chatbots"])
async def list_chatbots(client=Depends(get_client_from_token)):
    """List all chatbots belonging to the authenticated client."""
    bots = tenant_mgr.get_chatbots_for_client(client["client_id"])
    for bot in bots:
        bot["doc_count"] = tenant_mgr.get_document_count(bot["chatbot_id"])
        bot["message_count"] = tenant_mgr.get_message_count(bot["chatbot_id"])
    return {"chatbots": bots}


@app.get("/api/chatbots/{chatbot_id}", tags=["Chatbots"])
async def get_chatbot(chatbot_id: str, client=Depends(get_client_from_token)):
    """Get details of a specific chatbot."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    bot["doc_count"]     = tenant_mgr.get_document_count(chatbot_id)
    bot["message_count"] = tenant_mgr.get_message_count(chatbot_id)
    bot["allowed_domains"] = [d for d in (bot.get("allowed_domains") or "").split(",") if d.strip()]
    return bot


@app.put("/api/chatbots/{chatbot_id}", tags=["Chatbots"])
async def update_chatbot(chatbot_id: str, body: UpdateChatbotRequest, client=Depends(get_client_from_token)):
    """Update chatbot configuration."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    fields = body.model_dump(exclude_none=True)
    if "allowed_domains" in fields:
        fields["allowed_domains"] = ",".join(fields["allowed_domains"])
    tenant_mgr.update_chatbot(chatbot_id, fields)
    return {"message": "Chatbot updated"}


@app.delete("/api/chatbots/{chatbot_id}", tags=["Chatbots"])
async def delete_chatbot(chatbot_id: str, client=Depends(get_client_from_token)):
    """Delete a chatbot and all its data."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    vector_mgr.delete_collection(chatbot_id)
    tenant_mgr.delete_chatbot(chatbot_id)
    logger.info("Chatbot deleted: %s", chatbot_id)
    return {"message": "Chatbot deleted"}


# ═══════════════════════════════════════════════════════════════════════════════
# DOCUMENT INGESTION
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/upload-document", tags=["Documents"])
async def upload_document(
    background_tasks: BackgroundTasks,
    chatbot_id: str = Form(...),
    file: UploadFile = File(...),
    client=Depends(get_client_from_token),
):
    """Upload and ingest a document into the chatbot's knowledge base."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")

    allowed_types = {
        "application/pdf", "text/plain", "text/markdown",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }
    allowed_exts = {".pdf", ".txt", ".md", ".docx", ".doc"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    # Save uploaded file
    upload_dir = Path(f"../data/uploads/{chatbot_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name  = f"{uuid.uuid4()}{ext}"
    file_path  = upload_dir / safe_name
    content    = await file.read()
    file_path.write_bytes(content)

    doc_id = str(uuid.uuid4())
    tenant_mgr.create_document(
        doc_id=doc_id,
        chatbot_id=chatbot_id,
        filename=file.filename,
        stored_path=str(file_path),
        status="processing",
    )

    # Process in background so HTTP returns immediately
    background_tasks.add_task(
        ingestion.ingest_file,
        file_path=str(file_path),
        chatbot_id=chatbot_id,
        doc_id=doc_id,
        original_name=file.filename,
    )
    return {"doc_id": doc_id, "filename": file.filename, "status": "processing"}


@app.post("/api/ingest-url", tags=["Documents"])
async def ingest_url(
    background_tasks: BackgroundTasks,
    chatbot_id: str = Form(...),
    url: str = Form(...),
    client=Depends(get_client_from_token),
):
    """Crawl and ingest a web page into the chatbot's knowledge base."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")

    doc_id = str(uuid.uuid4())
    tenant_mgr.create_document(
        doc_id=doc_id,
        chatbot_id=chatbot_id,
        filename=url,
        stored_path=url,
        status="processing",
    )
    background_tasks.add_task(
        ingestion.ingest_url,
        url=url,
        chatbot_id=chatbot_id,
        doc_id=doc_id,
    )
    return {"doc_id": doc_id, "url": url, "status": "processing"}


@app.get("/api/chatbots/{chatbot_id}/documents", tags=["Documents"])
async def list_documents(chatbot_id: str, client=Depends(get_client_from_token)):
    """List all documents for a chatbot."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    docs = tenant_mgr.get_documents(chatbot_id)
    return {"documents": docs}


@app.delete("/api/chatbots/{chatbot_id}/documents/{doc_id}", tags=["Documents"])
async def delete_document(chatbot_id: str, doc_id: str, client=Depends(get_client_from_token)):
    """Delete a document and remove its vectors from the knowledge base."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    vector_mgr.delete_by_doc_id(chatbot_id, doc_id)
    tenant_mgr.delete_document(doc_id)
    return {"message": "Document deleted"}


# ═══════════════════════════════════════════════════════════════════════════════
# CHAT ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/chat", tags=["Chat"])
async def chat(body: ChatRequest, request: Request):
    """
    Public chat endpoint — called by the embedded widget.
    No authentication required (chatbot_id is the access key).
    """
    check_rate_limit(request.client.host)

    bot = tenant_mgr.get_chatbot(body.chatbot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Chatbot not found")

    check_domain_allowed(bot, request)
    check_chatbot_rate_limit(body.chatbot_id)

    session_id = body.session_id or str(uuid.uuid4())

    try:
        result = await rag.query(
            chatbot_id=body.chatbot_id,
            question=body.message,
            session_id=session_id,
        )
    except Exception as e:
        logger.error("RAG error for chatbot %s: %s", body.chatbot_id, e)
        raise HTTPException(status_code=500, detail="Internal error processing your request")

    # Persist to chat log
    tenant_mgr.log_message(
        chatbot_id=body.chatbot_id,
        session_id=session_id,
        user_message=body.message,
        bot_response=result["answer"],
        provider=result.get("provider", "unknown"),
    )

    # Also append to flat JSON log
    _append_json_log(body.chatbot_id, session_id, body.message, result["answer"])

    return {
        "answer":     result["answer"],
        "provider":   result.get("provider", "unknown"),
        "session_id": session_id,
    }


def _append_json_log(chatbot_id: str, session_id: str, message: str, response: str):
    log_file = Path("../logs/chat_history.json")
    entry = {
        "chatbot_id": chatbot_id,
        "session_id": session_id,
        "timestamp":  datetime.utcnow().isoformat(),
        "message":    message,
        "response":   response,
    }
    try:
        if log_file.exists():
            with open(log_file) as f:
                data = json.load(f)
        else:
            data = []
        data.append(entry)
        with open(log_file, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning("Could not write chat log: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# EMBED CODE
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/embed-code/{chatbot_id}", tags=["Embed"])
async def get_embed_code(chatbot_id: str):
    """Return the HTML embed snippet for the given chatbot."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    api_endpoint = CONFIG.get("api_endpoint", "http://localhost:8000")
    snippet = (
        f'<script src="{api_endpoint}/static/chatbot.js"\n'
        f'        data-chatbot-id="{chatbot_id}"\n'
        f'        data-api-endpoint="{api_endpoint}">\n'
        f'</script>'
    )
    return {"chatbot_id": chatbot_id, "embed_code": snippet, "chatbot_name": bot["name"]}


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYTICS / LOGS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/chatbots/{chatbot_id}/logs", tags=["Analytics"])
async def get_chat_logs(
    chatbot_id: str,
    limit: int = 50,
    offset: int = 0,
    client=Depends(get_client_from_token),
):
    """Retrieve paginated chat logs for a chatbot."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    logs  = tenant_mgr.get_chat_logs(chatbot_id, limit=limit, offset=offset)
    total = tenant_mgr.get_message_count(chatbot_id)
    return {"logs": logs, "total": total, "limit": limit, "offset": offset}


@app.get("/api/chatbots/{chatbot_id}/analytics", tags=["Analytics"])
async def get_analytics(chatbot_id: str, client=Depends(get_client_from_token)):
    """Return usage analytics for a chatbot."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    return tenant_mgr.get_analytics(chatbot_id)


# ═══════════════════════════════════════════════════════════════════════════════
# LEADS
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/leads", tags=["Leads"])
async def submit_lead(body: LeadSubmit, request: Request):
    """Public endpoint called by the widget pre-chat form. No auth required."""
    check_rate_limit(request.client.host)
    bot = tenant_mgr.get_chatbot(body.chatbot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    check_domain_allowed(bot, request)
    lead_id = tenant_mgr.create_lead(
        chatbot_id=body.chatbot_id,
        session_id=body.session_id,
        name=body.name or "",
        mobile=body.mobile,
        email=body.email,
        requirement=body.requirement,
    )
    logger.info("Lead captured: chatbot=%s session=%s email=%s",
                body.chatbot_id, body.session_id, body.email)
    return {"lead_id": lead_id, "message": "Lead submitted successfully"}


@app.get("/api/chatbots/{chatbot_id}/leads", tags=["Leads"])
async def get_leads(
    chatbot_id: str,
    limit: int = 50,
    offset: int = 0,
    client=Depends(get_client_from_token),
):
    """Retrieve paginated leads for a chatbot (dashboard only)."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    leads = tenant_mgr.get_leads(chatbot_id, limit=limit, offset=offset)
    total = tenant_mgr.get_lead_count(chatbot_id)
    return {"leads": leads, "total": total, "limit": limit, "offset": offset}


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC CHATBOT INFO (used by widget)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/chatbot-config/{chatbot_id}", tags=["Widget"])
async def get_chatbot_config(chatbot_id: str, request: Request):
    """Public endpoint: returns widget config (color, welcome msg, name)."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    check_domain_allowed(bot, request)
    return {
        "chatbot_id":        chatbot_id,
        "name":              bot["name"],
        "welcome_message":   bot["welcome_message"],
        "color":             bot.get("color", "#2563eb"),
        "lead_form_enabled": bool(bot.get("lead_form_enabled", 0)),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SERVE DASHBOARD & WIDGET
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, tags=["Pages"])
async def serve_dashboard():
    dashboard = ROOT_DIR / "frontend" / "dashboard" / "index.html"
    if dashboard.exists():
        return HTMLResponse(dashboard.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard not found</h1>")


@app.get("/admin", response_class=HTMLResponse, tags=["Pages"])
async def serve_admin():
    admin_page = ROOT_DIR / "frontend" / "admin" / "index.html"
    if admin_page.exists():
        return HTMLResponse(admin_page.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Admin page not found</h1>")


@app.get("/widget-demo", response_class=HTMLResponse, tags=["Pages"])
async def serve_widget_demo():
    demo = ROOT_DIR / "frontend" / "widget.html"
    if demo.exists():
        return HTMLResponse(demo.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Widget demo not found</h1>")


@app.get("/chatbot.js", tags=["Widget"])
async def serve_chatbot_js():
    js = ROOT_DIR / "frontend" / "chatbot.js"
    if js.exists():
        return FileResponse(str(js), media_type="application/javascript")
    raise HTTPException(status_code=404, detail="chatbot.js not found")


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/health", tags=["System"])
async def health():
    return {"status": "ok", "platform": CONFIG.get("platform_name"), "timestamp": datetime.utcnow().isoformat()}


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
