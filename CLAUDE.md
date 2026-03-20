# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run the server
```bash
# From repo root
venv/Scripts/python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000   # Windows
venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000            # Unix

# Or use the helper scripts
start.bat    # Windows
bash start.sh  # Unix
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

Run the server from the **repo root** (not from `backend/`). Relative paths in tenant_manager.py and vector_store.py resolve using the `DATA_DIR` env var (default `..` relative to backend, or `/data` on Railway).

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
`llm_router.py` tries providers in order: **Gemini 2.5 Flash → Groq Llama 3.3 70B → DeepSeek V3 → OpenRouter Qwen 2.5**. A provider is skipped if its `*_API_KEY` env var is unset. `RateLimitError` (HTTP 429) triggers the next provider after a 1.5s backoff; other HTTP errors also fall through.

> **Note**: Use `gemini-2.5-flash` — both `gemini-2.0-flash` and `gemini-1.5-flash` return HTTP 404 on new AI Studio free-tier keys. `gemini-2.5-flash` is confirmed working and free (15 RPM / 1000 RPD limit).

### Embeddings
`vector_store.py` loads `BAAI/bge-small-en-v1.5` (or `sentence-transformers/all-MiniLM-L6-v2`) locally via `sentence-transformers`. BGE models use a `"passage: "` prefix on stored chunks and `"query: "` on queries. The model is cached after first load in the process. First startup downloads ~130 MB from Hugging Face.

### Document ingestion (async)
Upload endpoints return immediately and run `ingestion.ingest_file` / `ingestion.ingest_url` as FastAPI `BackgroundTask`. Document status in SQLite progresses: `processing → ready | failed`. Chunks are 512 chars with 64-char overlap, split at sentence boundaries where possible.

### Session memory
`rag_pipeline.py` keeps an in-process `dict[session_id → deque]` of the last 3 Q/A turns, prepended to the context string for follow-up questions. This resets on server restart.

## Key configuration

`config.json` (repo root) controls platform name, API endpoint URL, default widget color, and `allowed_origins` for CORS. Read once at startup in `main.py`.

`.env` (repo root, git-ignored) holds all API keys. Loaded via `python-dotenv` at the top of `main.py`.

### Required `.env` vars
```
# LLM providers (at least one required)
GEMINI_API_KEY=...
GROQ_API_KEY=...

# Embeddings
EMBED_MODEL=BAAI/bge-small-en-v1.5

# Admin panel & OTP email (Resend API — not SMTP, Railway blocks SMTP)
ADMIN_EMAIL=your@email.com
RESEND_API_KEY=re_...
EMAIL_FROM=onboarding@resend.dev   # or your verified domain sender

# Railway persistent volume (set to /data on Railway, leave unset locally)
DATA_DIR=/data
```

## Auth model

- **No self-signup** — admin creates all client accounts
- **Admin panel**: `/admin` — OTP-based login (6-digit code sent to ADMIN_EMAIL via Resend)
- **Admin session**: 8-hour token stored in `sessionStorage`
- **Client login**: email/password → Bearer token stored in `localStorage`
- **must_change_password**: forced password change on first login; can also change anytime from sidebar

## Database

SQLite path controlled by `DATA_DIR` env var: `{DATA_DIR}/database/platform.db`. Schema auto-created by `tenant_manager.py` on startup. WAL mode enabled — if server crashes, delete `platform.db-wal` and `platform.db-shm` before restarting.

Tables: `clients`, `chatbots`, `documents`, `chat_logs`, `leads`

`clients` table columns (all added via safe ALTER TABLE migrations on startup):
- `oauth_provider` TEXT DEFAULT '' — kept for legacy, not used
- `oauth_id` TEXT DEFAULT '' — kept for legacy, not used
- `must_change_password` INTEGER DEFAULT 0 — forces password change on first login

`chatbots` table columns added via ALTER TABLE:
- `allowed_domains` TEXT DEFAULT '' — comma-separated hostnames
- `lead_form_enabled` INTEGER DEFAULT 0 — per-chatbot lead form toggle

`leads` table schema: `id` (PK), `chatbot_id` (FK → chatbots CASCADE), `session_id`, `name`, `mobile`, `email`, `requirement`, `created_at`.

## Frontend

- `frontend/dashboard/` — client dashboard, pure HTML/CSS/JS, no build step
- `frontend/admin/` — admin panel (OTP login + client management)
- `frontend/chatbot.js` — embeddable widget (single script tag)

Dashboard auto-detects API base URL from `window.location.origin`. Static asset URLs have `?v=<timestamp>` injected at runtime by FastAPI to bust browser cache after deployments.

## Security

- **Passwords**: bcrypt. Old SHA-256 hashes auto-migrate to bcrypt on next login.
- **Admin OTP**: 6-digit, 10-minute TTL, single-use, stored in memory dict.
- **Lead capture form**: Per-chatbot opt-in (`lead_form_enabled`). Mandatory — no skip. One form per browser session (sessionStorage).
- **Domain restriction**: Per-chatbot `allowed_domains`. Enforced on `/api/chat`, `/api/chatbot-config`, `/api/leads`. Empty = allow all.
- **Rate limiting**: 30 req/60s per IP (global). 200 req/hour per chatbot.
- **Chatbot IDs**: 16 hex chars to prevent enumeration.

## Railway deployment

- Persistent volume mounted at `/data` — set `DATA_DIR=/data` in Railway Variables
- SQLite: `/data/database/platform.db`
- ChromaDB: `/data/chroma_db`
- **Do not use SMTP** — Railway blocks outbound SMTP. Use Resend API instead.
- Required Railway env vars: `GEMINI_API_KEY`, `GROQ_API_KEY`, `EMBED_MODEL`, `ADMIN_EMAIL`, `RESEND_API_KEY`, `EMAIL_FROM`, `DATA_DIR`
