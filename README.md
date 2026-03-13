# Multi-Tenant AI Chatbot Platform

A production-ready, **$0-operating-cost** chatbot platform that lets multiple clients upload documents and embed a fully-branded AI support assistant on any website — powered entirely by **free-tier LLM APIs**.

```
┌────────────────────────────────────────────────────────────┐
│  Client uploads docs  →  RAG pipeline  →  Embedded widget │
│                                                            │
│  Gemini 2.0 Flash → Groq Llama 3.3 → DeepSeek → Qwen 2.5 │
└────────────────────────────────────────────────────────────┘
```

---

## Features

| Category | Details |
|---|---|
| **Multi-tenancy** | Fully isolated knowledge base & chat logs per client |
| **Document support** | PDF, DOCX, TXT, Markdown, Website URLs |
| **RAG pipeline** | ChromaDB + BGE embeddings + context-grounded answers |
| **LLM fallback chain** | Gemini → Groq → DeepSeek → OpenRouter (auto-retry) |
| **Embed widget** | One `<script>` tag — floating bubble, typing indicator, mobile-ready |
| **Dashboard** | Upload docs, view embed code, analytics, chat logs |
| **Security** | Rate limiting, prompt-injection filtering, CORS, input sanitisation |
| **Cost** | $0 — all AI calls use free-tier APIs; embeddings run locally |

---

## Architecture

```
/autobot
├── backend/
│   ├── main.py            ← FastAPI app, all REST endpoints
│   ├── llm_router.py      ← Multi-provider LLM with fallback
│   ├── rag_pipeline.py    ← Retrieval-Augmented Generation
│   ├── vector_store.py    ← ChromaDB (per-chatbot collections)
│   ├── ingestion.py       ← Document loading, chunking, embedding
│   └── tenant_manager.py  ← SQLite — clients, chatbots, docs, logs
│
├── frontend/
│   ├── chatbot.js         ← Embeddable widget (drop-in script)
│   ├── chatbot.css        ← Widget styles
│   ├── widget.html        ← Live demo page
│   └── dashboard/
│       ├── index.html     ← Client dashboard
│       ├── dashboard.css
│       └── dashboard.js
│
├── database/              ← SQLite DB (auto-created)
├── chroma_db/             ← Vector store (auto-created)
├── data/uploads/          ← Uploaded files (per chatbot)
├── logs/                  ← App log + JSON chat history
├── example_docs/          ← Sample training documents
├── config.json            ← Platform configuration
├── .env.example           ← API key template
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## Quick Start

### 1 — Clone & configure

```bash
git clone <repo-url>
cd autobot
cp .env.example .env
```

Edit `.env` and add **at least one** API key (you only need one provider):

```env
GEMINI_API_KEY=your_gemini_key      # https://aistudio.google.com
GROQ_API_KEY=your_groq_key          # https://console.groq.com
DEEPSEEK_API_KEY=your_deepseek_key  # https://platform.deepseek.com
OPENROUTER_API_KEY=your_or_key      # https://openrouter.ai
```

### 2 — Install dependencies

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **First run** will download the BGE embedding model (~130 MB). This is a
> one-time download stored in `~/.cache/huggingface/`.

### 3 — Start the server

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** — the dashboard loads immediately.

---

## Docker (recommended for production)

```bash
cp .env.example .env   # fill in your API keys
docker compose up -d
```

The server starts at **http://localhost:8000**.

---

## Usage Walkthrough

### Step 1 — Register a client account

Visit `http://localhost:8000` and click **Create Account**.

### Step 2 — Create a chatbot

Click **+ New Chatbot**, give it a name, choose a colour.

### Step 3 — Upload knowledge documents

Open the chatbot → **Documents** tab → drag & drop your files.
Supported: PDF, DOCX, TXT, Markdown, or paste a URL.

> Documents are processed asynchronously.  Refresh after ~30 seconds
> to see the status change to **ready**.

### Step 4 — Test in the widget demo

Go to **Embed Code** tab → click **Open Widget Demo**.
Ask a question about your uploaded content.

### Step 5 — Embed on your website

Copy the snippet from the **Embed Code** tab and paste it before
`</body>` on any webpage:

```html
<script src="https://yourdomain.com/chatbot.js"
        data-chatbot-id="YOUR_CHATBOT_ID"
        data-api-endpoint="https://yourdomain.com">
</script>
```

That's it. The floating widget appears automatically.

---

## REST API Reference

All client endpoints require `Authorization: Bearer <token>`.
The `/api/chat` endpoint is public (called by the widget).

### Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Register new client |
| `POST` | `/api/auth/login` | Login, get token |

### Chatbots

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chatbots` | Create chatbot |
| `GET` | `/api/chatbots` | List all chatbots |
| `GET` | `/api/chatbots/{id}` | Get chatbot detail |
| `PUT` | `/api/chatbots/{id}` | Update settings |
| `DELETE` | `/api/chatbots/{id}` | Delete chatbot + data |

### Documents

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/upload-document` | Upload file (multipart) |
| `POST` | `/api/ingest-url` | Crawl & ingest URL |
| `GET` | `/api/chatbots/{id}/documents` | List documents |
| `DELETE` | `/api/chatbots/{id}/documents/{doc_id}` | Delete document |

### Chat & Widget

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Send message (public) |
| `GET` | `/api/chatbot-config/{id}` | Get widget config (public) |
| `GET` | `/api/embed-code/{id}` | Get embed snippet |

### Analytics

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/chatbots/{id}/logs` | Paginated chat logs |
| `GET` | `/api/chatbots/{id}/analytics` | Usage statistics |

**Interactive docs:** http://localhost:8000/api/docs

---

## Chat API Example

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"chatbot_id": "abc12345", "message": "What is your refund policy?"}'
```

Response:
```json
{
  "answer":     "Refunds are available within 14 days of charge...",
  "provider":   "gemini",
  "session_id": "session_abc123"
}
```

---

## LLM Provider Setup (Free Tiers)

| Provider | Model | Free tier | Get key |
|---|---|---|---|
| **Google Gemini** | Gemini 2.0 Flash | 15 RPM, 1M TPM | [aistudio.google.com](https://aistudio.google.com) |
| **Groq** | Llama 3.3 70B | 30 RPM, 14.4k RPD | [console.groq.com](https://console.groq.com) |
| **DeepSeek** | DeepSeek V3 | Generous free credits | [platform.deepseek.com](https://platform.deepseek.com) |
| **OpenRouter** | Qwen 2.5 72B | Free models available | [openrouter.ai](https://openrouter.ai) |

The router tries each provider in order, automatically skipping ones
that are rate-limited or unavailable.

---

## Configuration

Edit `config.json` to customise the platform:

```json
{
  "platform_name":   "My ChatBot Platform",
  "api_endpoint":    "https://yourdomain.com",
  "chatbot_color":   "#2563eb",
  "welcome_message": "Hello! How can I help you?",
  "allowed_origins": ["https://yourdomain.com"],
  "embed_model":     "BAAI/bge-small-en-v1.5"
}
```

---

## Security Notes

- **Rate limiting**: 30 requests/minute per IP (configurable)
- **Prompt injection**: Common attack phrases are filtered
- **Input sanitisation**: Messages capped at 2000 chars
- **Tenant isolation**: Each chatbot has its own ChromaDB collection
- **File validation**: Only whitelisted extensions accepted
- **Token auth**: All management APIs require Bearer tokens

For production, add HTTPS (nginx/Caddy reverse proxy) and set
`allowed_origins` to your specific domain.

---

## Example Client Setup

```python
import requests

BASE = "http://localhost:8000"

# 1. Register
r = requests.post(f"{BASE}/api/auth/register", json={
    "name": "Jane Smith", "email": "jane@acme.com",
    "password": "secret123", "company": "Acme Inc"
})
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

# 2. Create chatbot
r = requests.post(f"{BASE}/api/chatbots",
    json={"name": "Acme Support", "color": "#16a34a"}, headers=headers)
bot_id = r.json()["chatbot_id"]

# 3. Upload document
with open("faq.pdf", "rb") as f:
    requests.post(f"{BASE}/api/upload-document",
        data={"chatbot_id": bot_id},
        files={"file": f}, headers=headers)

# 4. Chat (no auth needed — this is the public widget endpoint)
r = requests.post(f"{BASE}/api/chat", json={
    "chatbot_id": bot_id, "message": "What is your return policy?"
})
print(r.json()["answer"])
```

---

## Troubleshooting

**Embedding model download is slow**
The BGE model (~130 MB) downloads on first run from Hugging Face.
Set `HF_HUB_OFFLINE=1` after the first download to prevent re-checks.

**"All providers failed" error**
Ensure at least one `*_API_KEY` is set in `.env`.
Check `/api/health` — available providers are listed there.

**Document status stuck at "processing"**
Background tasks run in FastAPI's thread pool. With `--reload` and heavy load,
tasks may be slow. Check `logs/app.log` for errors.

**ChromaDB error on Windows**
Install `hnswlib` separately: `pip install hnswlib`.

---

## License

MIT — free to use, modify, and deploy commercially.
