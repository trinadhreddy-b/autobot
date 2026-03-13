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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import (
    FastAPI, HTTPException, UploadFile, File, Form,
    Request, Depends, Header, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, field_validator
import uvicorn
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path("../.env"))  # load .env from project root

# Internal modules
from tenant_manager import TenantManager
from rag_pipeline import RAGPipeline
from ingestion import DocumentIngestion
from vector_store import VectorStoreManager

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler("../logs/app.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")

# ── Config ───────────────────────────────────────────────────────────────────
CONFIG_PATH = Path("../config.json")
with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

# ── Rate-limit store (in-memory, per-IP) ─────────────────────────────────────
_rate_store: dict[str, list[float]] = {}
RATE_LIMIT   = 30   # requests
RATE_WINDOW  = 60   # seconds

def check_rate_limit(ip: str) -> None:
    now = time.time()
    hits = [t for t in _rate_store.get(ip, []) if now - t < RATE_WINDOW]
    if len(hits) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    hits.append(now)
    _rate_store[ip] = hits

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
FRONTEND_DIR = Path("../frontend")
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


class UpdateChatbotRequest(BaseModel):
    name: Optional[str] = None
    welcome_message: Optional[str] = None
    color: Optional[str] = None


# ── Auth helpers ──────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_client_from_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ", 1)[1]
    client = tenant_mgr.get_client_by_token(token)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return client


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
    if not client or client["password_hash"] != hash_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = str(uuid.uuid4())
    tenant_mgr.update_client_token(client["client_id"], token)
    return {
        "client_id": client["client_id"],
        "token": token,
        "name": client["name"],
        "email": client["email"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CHATBOT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/chatbots", tags=["Chatbots"])
async def create_chatbot(body: CreateChatbotRequest, client=Depends(get_client_from_token)):
    """Create a new chatbot for the authenticated client."""
    chatbot_id = str(uuid.uuid4())[:8]
    tenant_mgr.create_chatbot(
        chatbot_id=chatbot_id,
        client_id=client["client_id"],
        name=body.name,
        welcome_message=body.welcome_message,
        color=body.color,
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
    return bot


@app.put("/api/chatbots/{chatbot_id}", tags=["Chatbots"])
async def update_chatbot(chatbot_id: str, body: UpdateChatbotRequest, client=Depends(get_client_from_token)):
    """Update chatbot configuration."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot or bot["client_id"] != client["client_id"]:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    tenant_mgr.update_chatbot(chatbot_id, body.model_dump(exclude_none=True))
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
# PUBLIC CHATBOT INFO (used by widget)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/chatbot-config/{chatbot_id}", tags=["Widget"])
async def get_chatbot_config(chatbot_id: str):
    """Public endpoint: returns widget config (color, welcome msg, name)."""
    bot = tenant_mgr.get_chatbot(chatbot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    return {
        "chatbot_id":      chatbot_id,
        "name":            bot["name"],
        "welcome_message": bot["welcome_message"],
        "color":           bot.get("color", "#2563eb"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SERVE DASHBOARD & WIDGET
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, tags=["Pages"])
async def serve_dashboard():
    dashboard = Path("../frontend/dashboard/index.html")
    if dashboard.exists():
        return HTMLResponse(dashboard.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard not found</h1>")


@app.get("/widget-demo", response_class=HTMLResponse, tags=["Pages"])
async def serve_widget_demo():
    demo = Path("../frontend/widget.html")
    if demo.exists():
        return HTMLResponse(demo.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Widget demo not found</h1>")


@app.get("/chatbot.js", tags=["Widget"])
async def serve_chatbot_js():
    js = Path("../frontend/chatbot.js")
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
