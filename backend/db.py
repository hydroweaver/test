"""SQLite state: conversations, usage log, crawled knowledge base, settings, keys.

Sync sqlite3 is fine here - FastAPI runs `def` routes in a threadpool and this app
runs as a single instance, so there's no concurrent-writer problem to solve.
"""

import os
import sqlite3
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken

DB_PATH = os.environ.get("DB_PATH", "./data/app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    channel TEXT NOT NULL CHECK(channel IN ('whatsapp','api')),
    phone_number TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cost_usd REAL,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 1,
    latency_ms INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS kb_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT,
    crawled_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5(
    content, content='kb_chunks', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS kb_chunks_ai AFTER INSERT ON kb_chunks BEGIN
    INSERT INTO kb_chunks_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS kb_chunks_ad AFTER DELETE ON kb_chunks BEGIN
    INSERT INTO kb_chunks_fts(kb_chunks_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active_provider TEXT NOT NULL,
    active_model TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_keys (
    provider TEXT PRIMARY KEY,
    encrypted_key BLOB NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(default_provider: str, default_model: str) -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        try:
            conn.execute("SELECT 1 FROM kb_chunks_fts LIMIT 0")
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                "This SQLite build lacks FTS5 support, required for website search. "
                "Rebuild Python with a modern SQLite, or install one that has FTS5."
            ) from e

        row = conn.execute("SELECT 1 FROM settings WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO settings (id, active_provider, active_model) VALUES (1, ?, ?)",
                (default_provider, default_model),
            )


def _fernet() -> Fernet:
    key = os.environ.get("SETTINGS_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "SETTINGS_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "and add it to backend/.env."
        )
    return Fernet(key.encode())


def _mask(raw_key: str) -> str:
    if len(raw_key) <= 8:
        return "****"
    return f"{raw_key[:4]}...{raw_key[-4:]}"


def set_provider_key(provider: str, raw_key: str) -> str:
    """Encrypts and stores raw_key for provider. Returns the masked form for display."""
    encrypted = _fernet().encrypt(raw_key.encode())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO provider_keys (provider, encrypted_key, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(provider) DO UPDATE SET encrypted_key = excluded.encrypted_key, "
            "updated_at = excluded.updated_at",
            (provider, encrypted),
        )
    return _mask(raw_key)


def resolve_api_key(provider: str) -> str | None:
    """DB-stored key (pasted via the admin UI) takes precedence; falls back to env var."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT encrypted_key FROM provider_keys WHERE provider = ?", (provider,)
        ).fetchone()
    if row is not None:
        try:
            return _fernet().decrypt(row["encrypted_key"]).decode()
        except InvalidToken:
            # SETTINGS_ENCRYPTION_KEY was rotated since this key was saved - treat as absent.
            pass
    return os.environ.get(f"{provider.upper()}_API_KEY")


def key_status(provider: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT encrypted_key FROM provider_keys WHERE provider = ?", (provider,)
        ).fetchone()
    if row is not None:
        try:
            raw = _fernet().decrypt(row["encrypted_key"]).decode()
            return {"configured": True, "source": "database", "masked": _mask(raw)}
        except InvalidToken:
            pass

    env_key = os.environ.get(f"{provider.upper()}_API_KEY")
    if env_key:
        return {"configured": True, "source": "env", "masked": _mask(env_key)}
    return {"configured": False, "source": None, "masked": None}


def get_settings() -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT active_provider, active_model FROM settings WHERE id = 1").fetchone()
    return {"active_provider": row["active_provider"], "active_model": row["active_model"]}


def update_settings(provider: str, model: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE settings SET active_provider = ?, active_model = ?, updated_at = datetime('now') WHERE id = 1",
            (provider, model),
        )


def get_or_create_conversation(phone_number: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE phone_number = ?", (phone_number,)
        ).fetchone()
        if row is not None:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO conversations (phone_number) VALUES (?)", (phone_number,)
        )
        return cur.lastrowid


def get_recent_messages(conversation_id: int, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def add_message(conversation_id: int, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )


def log_usage(
    channel: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
    tool_call_count: int = 0,
    turn_count: int = 1,
    phone_number: str | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO usage_log "
            "(channel, phone_number, provider, model, input_tokens, output_tokens, total_tokens, "
            " cost_usd, tool_call_count, turn_count, latency_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                channel,
                phone_number,
                provider,
                model,
                input_tokens,
                output_tokens,
                input_tokens + output_tokens,
                cost_usd,
                tool_call_count,
                turn_count,
                latency_ms,
                error,
            ),
        )


def get_usage(limit: int = 50, offset: int = 0) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, channel, phone_number, provider, model, input_tokens, "
            "output_tokens, total_tokens, cost_usd, tool_call_count, turn_count, latency_ms, error "
            "FROM usage_log ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        totals = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens, COALESCE(SUM(cost_usd), 0) AS cost_usd "
            "FROM usage_log"
        ).fetchone()
    return {
        "rows": [dict(r) for r in rows],
        "totals": dict(totals),
    }


def upsert_kb_page(url: str, title: str | None) -> int:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO kb_pages (url, title) VALUES (?, ?) "
            "ON CONFLICT(url) DO UPDATE SET title = excluded.title, crawled_at = datetime('now')",
            (url, title),
        )
        row = conn.execute("SELECT id FROM kb_pages WHERE url = ?", (url,)).fetchone()
        page_id = row["id"]
        conn.execute("DELETE FROM kb_chunks WHERE page_id = ?", (page_id,))
    return page_id


def add_kb_chunks(page_id: int, url: str, chunks: list[str]) -> None:
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO kb_chunks (page_id, url, chunk_index, content) VALUES (?, ?, ?, ?)",
            [(page_id, url, i, chunk) for i, chunk in enumerate(chunks)],
        )


def search_kb(query: str, top_n: int = 5) -> list[dict]:
    terms = [t.replace('"', '""') for t in query.split() if t.strip()]
    if not terms:
        return []
    match_expr = " OR ".join(f'"{t}"' for t in terms)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT kb_chunks.url AS url, kb_chunks.content AS content "
            "FROM kb_chunks_fts JOIN kb_chunks ON kb_chunks.id = kb_chunks_fts.rowid "
            "WHERE kb_chunks_fts MATCH ? "
            "ORDER BY bm25(kb_chunks_fts) LIMIT ?",
            (match_expr, top_n),
        ).fetchall()
    return [{"url": r["url"], "content": r["content"]} for r in rows]


def kb_chunk_count() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM kb_chunks").fetchone()
    return row["n"]
