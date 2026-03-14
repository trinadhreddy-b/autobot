# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run the server
```bash
# From repo root — activates venv and starts server
start.bat                          # Windows
bash start.sh                      # Unix

# Manual (venv must be active)
cd backend
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Install / update dependencies
```bash
venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
venv/bin/python -m pip install -r requirements.txt            # Unix
```

### Health check
```bash
curl http://localhost:8000/api/health
```

### Kill server & clear SQLite lock
```bash
taskkill //F //IM python.exe          # Windows
rm -f database/platform.db-wal database/platform.db-shm
```

### Git push
```bash
git add <files> && git commit -m "message" && git push origin main
# Remote is HTTPS: https://github.com/trinadhreddy-b/autobot.git
```

## Architecture

The backend must always be run from the `backend/` directory — all relative paths (`../database`, `../chroma_db`, `../logs`, `../config.json`) resolve from there.

### Request flow
```
HTTP request → main.py (FastAPI)
    → Auth: tenant_manager.py (SQLite Bearer token lookup)
    → Chat: rag_pipeline.py
        → vector_store.py (ChromaDB cosine query, BGE embeddings)
        → llm_router.py (Gemini → Groq → DeepSeek → OpenRouter)
    → Document upload: ingestion.py (background task)
        → vector_store.py (upsert chunks)
        → tenant_manager.py (status update)
```

### Multi-tenancy isolation
Each chatbot gets its own ChromaDB collection named `chatbot_<chatbot_id>`. All vector queries and deletes are scoped by collection — tenants cannot see each other's data. SQLite enforces the same isolation via foreign keys (`chatbots.client_id → clients.client_id`).

### LLM fallback chain
`llm_router.py` tries providers in order: **Gemini 2.0 Flash → Groq Llama 3.3 70B → DeepSeek V3 → OpenRouter Qwen 2.5**. A provider is skipped if its `*_API_KEY` env var is unset. `RateLimitError` (HTTP 429) triggers the next provider after a 1.5s backoff; other HTTP errors also fall through.

### Embeddings
`vector_store.py` loads `BAAI/bge-small-en-v1.5` (or `sentence-transformers/all-MiniLM-L6-v2`) locally via `sentence-transformers`. BGE models use a `"passage: "` prefix on stored chunks and `"query: "` on queries. The model is cached after first load in the process. First startup downloads ~130 MB from Hugging Face.

### Document ingestion (async)
Upload endpoints return immediately and run `ingestion.ingest_file` / `ingestion.ingest_url` as FastAPI `BackgroundTask`. Document status in SQLite progresses: `processing → ready | failed`. Chunks are 512 chars with 64-char overlap, split at sentence boundaries where possible.

### Session memory
`rag_pipeline.py` keeps an in-process `dict[session_id → deque]` of the last 3 Q/A turns, prepended to the context string for follow-up questions. This resets on server restart.

## Key configuration

`config.json` (repo root) controls platform name, API endpoint URL, default widget color, and `allowed_origins` for CORS. Read once at startup in `main.py`.

`.env` (repo root, git-ignored) holds all API keys and `EMBED_MODEL`. Loaded via `python-dotenv` at the top of `main.py`.

## Database

SQLite at `database/platform.db`. Schema is auto-created by `tenant_manager.py` on startup via `executescript`. Tables: `clients`, `chatbots`, `documents`, `chat_logs`. WAL mode is enabled — if the server crashes, delete `platform.db-wal` and `platform.db-shm` before restarting.

`chatbots` table has an `allowed_domains` column (TEXT, comma-separated hostnames). Added via `ALTER TABLE` migration on startup — safe on existing databases.

## Frontend

The dashboard (`frontend/dashboard/`) is pure HTML/CSS/JS with no build step. It auto-detects the API base URL from `window.location.origin`. The embeddable widget (`frontend/chatbot.js`) reads `data-chatbot-id` and `data-api-endpoint` from its own `<script>` tag, injects `chatbot.css` dynamically, and calls `/api/chatbot-config/{id}` on load for branding.

## Security

- **Passwords**: bcrypt (via `bcrypt` package). Old SHA-256 hashes auto-migrate to bcrypt on next login.
- **Domain restriction**: Per-chatbot `allowed_domains` field. Enforced on `/api/chat` and `/api/chatbot-config` via `check_domain_allowed()`. Hostnames normalized (port/scheme stripped). Empty = allow all.
- **Rate limiting**: 30 req/60s per IP (global). 200 req/hour per chatbot (protects LLM quota).
- **Chatbot IDs**: 16 hex chars to prevent enumeration.
- **Note**: Domain restriction only blocks browsers (Origin header). Direct API calls can spoof it.
