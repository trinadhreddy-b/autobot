"""
Tenant Manager — SQLite-backed Multi-Tenant Data Layer
=======================================================
Tables:
  clients     – registered client accounts
  chatbots    – each client's chatbot configurations
  documents   – uploaded/ingested documents
  chat_logs   – per-session conversation history
  leads       – lead capture form submissions

All operations are synchronous (SQLite is fast enough for the
expected load; swap to async SQLAlchemy + Postgres for high scale).
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tenant_manager")

import os as _os
_DATA_DIR = Path(_os.getenv("DATA_DIR", ".."))
DB_PATH = _DATA_DIR / "database" / "platform.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS clients (
    client_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    company       TEXT DEFAULT '',
    token         TEXT,
    oauth_provider TEXT DEFAULT '',
    oauth_id       TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chatbots (
    chatbot_id        TEXT PRIMARY KEY,
    client_id         TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    welcome_message   TEXT DEFAULT 'Hello! How can I help you today?',
    color             TEXT DEFAULT '#2563eb',
    allowed_domains   TEXT DEFAULT '',
    lead_form_enabled INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    chatbot_id   TEXT NOT NULL REFERENCES chatbots(chatbot_id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    stored_path  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'processing',
    chunk_count  INTEGER DEFAULT 0,
    error        TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chatbot_id   TEXT NOT NULL REFERENCES chatbots(chatbot_id) ON DELETE CASCADE,
    session_id   TEXT NOT NULL,
    user_message TEXT NOT NULL,
    bot_response TEXT NOT NULL,
    provider     TEXT DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chatbot_id   TEXT NOT NULL REFERENCES chatbots(chatbot_id) ON DELETE CASCADE,
    session_id   TEXT NOT NULL,
    name         TEXT DEFAULT '',
    mobile       TEXT NOT NULL,
    email        TEXT NOT NULL,
    requirement  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chatbots_client ON chatbots(client_id);
CREATE INDEX IF NOT EXISTS idx_documents_chatbot ON documents(chatbot_id);
CREATE INDEX IF NOT EXISTS idx_logs_chatbot ON chat_logs(chatbot_id);
CREATE INDEX IF NOT EXISTS idx_logs_session ON chat_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_clients_email ON clients(email);
CREATE INDEX IF NOT EXISTS idx_clients_token ON clients(token);
CREATE INDEX IF NOT EXISTS idx_leads_chatbot ON leads(chatbot_id);
CREATE INDEX IF NOT EXISTS idx_leads_session ON leads(session_id);
"""


# ─────────────────────────────────────────────────────────────────────────────
# Manager class
# ─────────────────────────────────────────────────────────────────────────────

class TenantManager:

    def __init__(self):
        self._db_path = str(DB_PATH)
        self._init_db()

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # Migration: add allowed_domains if it doesn't exist yet
            try:
                conn.execute("ALTER TABLE chatbots ADD COLUMN allowed_domains TEXT DEFAULT ''")
            except Exception:
                pass  # column already exists
            # Migration: add OAuth columns to clients
            for col, defn in [("oauth_provider", "TEXT DEFAULT ''"), ("oauth_id", "TEXT DEFAULT ''")]:
                try:
                    conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {defn}")
                except Exception:
                    pass  # column already exists
            # Migration: add lead_form_enabled to chatbots
            try:
                conn.execute("ALTER TABLE chatbots ADD COLUMN lead_form_enabled INTEGER DEFAULT 0")
            except Exception:
                pass  # column already exists
            # Migration: add must_change_password to clients
            try:
                conn.execute("ALTER TABLE clients ADD COLUMN must_change_password INTEGER DEFAULT 0")
            except Exception:
                pass  # column already exists
            # Migration: add icon columns to chatbots
            for col, defn in [("icon_type", "TEXT DEFAULT 'default'"), ("icon_value", "TEXT DEFAULT ''")]:
                try:
                    conn.execute(f"ALTER TABLE chatbots ADD COLUMN {col} {defn}")
                except Exception:
                    pass  # column already exists
        logger.info("Database initialised at %s", self._db_path)

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().isoformat()

    @staticmethod
    def _row_to_dict(row) -> Optional[dict]:
        return dict(row) if row else None

    # ═══════════════════════════════════════════════════════════════════════════
    # CLIENTS
    # ═══════════════════════════════════════════════════════════════════════════

    def create_client(self, client_id, name, email, password_hash, company, token,
                      oauth_provider="", oauth_id="", must_change_password=0):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO clients
                   (client_id, name, email, password_hash, company, token, oauth_provider, oauth_id, must_change_password, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (client_id, name, email, password_hash, company, token,
                 oauth_provider, oauth_id, must_change_password, self._now()),
            )

    def get_client_by_email(self, email: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clients WHERE email = ?", (email,)
            ).fetchone()
        return self._row_to_dict(row)

    def get_client_by_token(self, token: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clients WHERE token = ?", (token,)
            ).fetchone()
        return self._row_to_dict(row)

    def update_client_token(self, client_id: str, token: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE clients SET token = ? WHERE client_id = ?",
                (token, client_id),
            )

    def update_client_password(self, client_id: str, password_hash: str, clear_must_change: bool = False) -> None:
        with self._connect() as conn:
            if clear_must_change:
                conn.execute(
                    "UPDATE clients SET password_hash = ?, must_change_password = 0 WHERE client_id = ?",
                    (password_hash, client_id),
                )
            else:
                conn.execute(
                    "UPDATE clients SET password_hash = ? WHERE client_id = ?",
                    (password_hash, client_id),
                )

    def list_clients(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT client_id, name, email, company, must_change_password, created_at FROM clients ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_client_by_id(self, client_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        return self._row_to_dict(row)

    def delete_client(self, client_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM clients WHERE client_id = ?", (client_id,))

    def get_client_by_oauth(self, provider: str, oauth_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clients WHERE oauth_provider = ? AND oauth_id = ?",
                (provider, oauth_id),
            ).fetchone()
        return self._row_to_dict(row)

    def update_client_oauth(self, client_id: str, oauth_provider: str, oauth_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE clients SET oauth_provider = ?, oauth_id = ? WHERE client_id = ?",
                (oauth_provider, oauth_id, client_id),
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # CHATBOTS
    # ═══════════════════════════════════════════════════════════════════════════

    def create_chatbot(self, chatbot_id, client_id, name, welcome_message, color, allowed_domains=""):
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO chatbots (chatbot_id, client_id, name, welcome_message, color, allowed_domains, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (chatbot_id, client_id, name, welcome_message, color, allowed_domains, now, now),
            )

    def get_chatbot(self, chatbot_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chatbots WHERE chatbot_id = ?", (chatbot_id,)
            ).fetchone()
        return self._row_to_dict(row)

    def get_chatbots_for_client(self, client_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chatbots WHERE client_id = ? ORDER BY created_at DESC",
                (client_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_chatbot(self, chatbot_id: str, fields: dict) -> None:
        allowed = {"name", "welcome_message", "color", "allowed_domains", "lead_form_enabled", "icon_type", "icon_value"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values     = list(updates.values()) + [chatbot_id]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE chatbots SET {set_clause} WHERE chatbot_id = ?",
                values,
            )

    def delete_chatbot(self, chatbot_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chatbots WHERE chatbot_id = ?", (chatbot_id,))

    # ═══════════════════════════════════════════════════════════════════════════
    # DOCUMENTS
    # ═══════════════════════════════════════════════════════════════════════════

    def create_document(self, doc_id, chatbot_id, filename, stored_path, status):
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO documents (doc_id, chatbot_id, filename, stored_path, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, chatbot_id, filename, stored_path, status, now, now),
            )

    def update_document_status(self, doc_id: str, status: str, chunk_count: int = 0, error: str = ""):
        with self._connect() as conn:
            conn.execute(
                """UPDATE documents SET status = ?, chunk_count = ?, error = ?, updated_at = ?
                   WHERE doc_id = ?""",
                (status, chunk_count, error, self._now(), doc_id),
            )

    def get_documents(self, chatbot_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE chatbot_id = ? ORDER BY created_at DESC",
                (chatbot_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, doc_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))

    def get_document_count(self, chatbot_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM documents WHERE chatbot_id = ? AND status = 'ready'",
                (chatbot_id,),
            ).fetchone()
        return row["c"] if row else 0

    # ═══════════════════════════════════════════════════════════════════════════
    # CHAT LOGS
    # ═══════════════════════════════════════════════════════════════════════════

    def log_message(self, chatbot_id, session_id, user_message, bot_response, provider):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO chat_logs (chatbot_id, session_id, user_message, bot_response, provider, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (chatbot_id, session_id, user_message, bot_response, provider, self._now()),
            )

    def get_chat_logs(self, chatbot_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM chat_logs WHERE chatbot_id = ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (chatbot_id, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_message_count(self, chatbot_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM chat_logs WHERE chatbot_id = ?",
                (chatbot_id,),
            ).fetchone()
        return row["c"] if row else 0

    # ═══════════════════════════════════════════════════════════════════════════
    # LEADS
    # ═══════════════════════════════════════════════════════════════════════════

    def create_lead(self, chatbot_id: str, session_id: str, name: str,
                    mobile: str, email: str, requirement: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO leads (chatbot_id, session_id, name, mobile, email, requirement, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (chatbot_id, session_id, name, mobile, email, requirement, self._now()),
            )
            return cursor.lastrowid

    def get_leads(self, chatbot_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM leads WHERE chatbot_id = ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (chatbot_id, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_lead_count(self, chatbot_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM leads WHERE chatbot_id = ?",
                (chatbot_id,),
            ).fetchone()
        return row["c"] if row else 0

    # ═══════════════════════════════════════════════════════════════════════════
    # ANALYTICS
    # ═══════════════════════════════════════════════════════════════════════════

    def get_analytics(self, chatbot_id: str) -> dict:
        with self._connect() as conn:
            total_msgs = conn.execute(
                "SELECT COUNT(*) as c FROM chat_logs WHERE chatbot_id = ?",
                (chatbot_id,),
            ).fetchone()["c"]

            sessions = conn.execute(
                "SELECT COUNT(DISTINCT session_id) as c FROM chat_logs WHERE chatbot_id = ?",
                (chatbot_id,),
            ).fetchone()["c"]

            providers = conn.execute(
                """SELECT provider, COUNT(*) as count FROM chat_logs
                   WHERE chatbot_id = ? GROUP BY provider""",
                (chatbot_id,),
            ).fetchall()

            daily = conn.execute(
                """SELECT DATE(created_at) as day, COUNT(*) as count
                   FROM chat_logs WHERE chatbot_id = ?
                   GROUP BY day ORDER BY day DESC LIMIT 30""",
                (chatbot_id,),
            ).fetchall()

            docs = conn.execute(
                "SELECT COUNT(*) as c FROM documents WHERE chatbot_id = ? AND status = 'ready'",
                (chatbot_id,),
            ).fetchone()["c"]

            total_leads = conn.execute(
                "SELECT COUNT(*) as c FROM leads WHERE chatbot_id = ?",
                (chatbot_id,),
            ).fetchone()["c"]

        return {
            "total_messages":  total_msgs,
            "unique_sessions": sessions,
            "documents":       docs,
            "total_leads":     total_leads,
            "providers":       {r["provider"]: r["count"] for r in providers},
            "daily_messages":  [{"day": r["day"], "count": r["count"]} for r in daily],
        }
