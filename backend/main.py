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
import logging
import hashlib
import bcrypt
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlencode, quote
import secrets
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

# ── Google OAuth config ───────────────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI   = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/oauth/google/callback")

# CSRF state store: {state_token: expiry_timestamp}
_oauth_states: dict[str, float] = {}
OAUTH_STATE_TTL = 600  # 10 minutes

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

class ClientRegister(BaseModel):
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


class ClientLogin(BaseModel):
    email: str
    password: str


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
        return False  # OAuth-only account — no password set
    # Support migration from old SHA-256 hashes (64 hex chars, no $ prefix)
    if not stored_hash.startswith("$2"):
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    return bcrypt.checkpw(password.encode(), stored_hash.encode())


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

@app.post("/api/auth/register", tags=["Auth"])
async def register(body: ClientRegister, request: Request):
    """Register a new client account."""
    check_rate_limit(request.client.host)
    if tenant_mgr.get_client_by_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    client_id = str(uuid.uuid4())
    token     = str(uuid.uuid4())
    tenant_mgr.create_client(
        client_id=client_id,
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        company=body.company,
        token=token,
    )
    logger.info("New client registered: %s (%s)", body.email, client_id)
    return {"client_id": client_id, "token": token, "message": "Registration successful"}


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
        "client_id": client["client_id"],
        "token": token,
        "name": client["name"],
        "email": client["email"],
    }


# ── Google OAuth ──────────────────────────────────────────────────────────────

@app.get("/api/auth/oauth/google/authorize", tags=["Auth"])
async def google_oauth_authorize(request: Request):
    """Redirect the browser to Google's OAuth consent screen."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured on this server")
    check_rate_limit(request.client.host)
    state = secrets.token_hex(16)
    _oauth_states[state] = time.time() + OAUTH_STATE_TTL
    params = urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@app.get("/api/auth/oauth/google/callback", tags=["Auth"])
async def google_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle Google's OAuth callback: exchange code, create/login user, redirect to dashboard."""
    if error:
        return RedirectResponse("/?oauth_error=" + quote(error))

    # Validate CSRF state
    expiry = _oauth_states.pop(state, None)
    if not expiry or time.time() > expiry:
        return RedirectResponse("/?oauth_error=invalid_state")

    # Exchange authorization code for tokens
    import httpx as _httpx
    async with _httpx.AsyncClient() as hc:
        token_resp = await hc.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri":  OAUTH_REDIRECT_URI,
                "grant_type":    "authorization_code",
            },
        )
    if token_resp.status_code != 200:
        logger.error("Google token exchange failed: %s", token_resp.text)
        return RedirectResponse("/?oauth_error=token_exchange_failed")

    access_token = token_resp.json().get("access_token")

    # Fetch user info from Google
    async with _httpx.AsyncClient() as hc:
        info_resp = await hc.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if info_resp.status_code != 200:
        return RedirectResponse("/?oauth_error=userinfo_failed")

    guser     = info_resp.json()
    google_sub = guser.get("sub")          # stable unique Google user ID
    email      = guser.get("email", "")
    name       = guser.get("name") or email.split("@")[0]

    # Find or create account
    existing = tenant_mgr.get_client_by_oauth("google", google_sub)
    if not existing:
        # Check if same email already registered with password — link the accounts
        existing = tenant_mgr.get_client_by_email(email)
        if existing:
            tenant_mgr.update_client_oauth(existing["client_id"], "google", google_sub)
        else:
            # Brand-new user — create account (no password)
            client_id = str(uuid.uuid4())
            tenant_mgr.create_client(
                client_id=client_id,
                name=name,
                email=email,
                password_hash="",
                company="",
                token="",
                oauth_provider="google",
                oauth_id=google_sub,
            )
            existing = {"client_id": client_id, "name": name, "email": email}

    # Issue a session token and redirect to dashboard
    session_token = str(uuid.uuid4())
    tenant_mgr.update_client_token(existing["client_id"], session_token)
    logger.info("Google OAuth login: %s (%s)", email, existing["client_id"])

    params = urlencode({
        "oauth_token": session_token,
        "client_id":   existing["client_id"],
        "name":        existing.get("name") or name,
        "email":       email,
    })
    return RedirectResponse(f"/?{params}")


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
