"""
SQLite storage for conversations, the team's tickets and the numbers on the dashboard.

The API history of each conversation is stored exactly as sent and received
(append-only), so a conversation can continue on the next request with its
thinking blocks and tool calls intact.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    page TEXT,
    language TEXT,
    turns INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    rating INTEGER,
    history TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    products TEXT NOT NULL DEFAULT '[]',
    tools TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_messages_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    email TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    input TEXT NOT NULL,
    is_error INTEGER NOT NULL DEFAULT 0
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL") if str(path) != ":memory:" else None
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def close(self):
        self.db.close()

    # ----- conversations -----

    def create_conversation(self, page: str | None = None) -> str:
        cid = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO conversations (id, created_at, updated_at, page) VALUES (?, ?, ?, ?)",
                            (cid, now(), now(), (page or "")[:300]))
        return cid

    def get_conversation(self, cid: str) -> dict | None:
        row = self.db.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
        if not row:
            return None
        conv = dict(row)
        conv["history"] = json.loads(conv["history"])
        return conv

    def save_turn(self, cid: str, history: list, user_text: str, answer: str, products: list, tools: list,
                  usage: dict, cost: float, language: str | None):
        with self.lock, self.db:
            self.db.execute(
                """UPDATE conversations SET history = ?, updated_at = ?, turns = turns + 1,
                   input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,
                   cache_read_tokens = cache_read_tokens + ?, cost_usd = cost_usd + ?,
                   language = COALESCE(language, ?) WHERE id = ?""",
                (json.dumps(history, ensure_ascii=False), now(), usage.get("input_tokens", 0),
                 usage.get("output_tokens", 0), usage.get("cache_read_tokens", 0), cost, language, cid))
            self.db.execute("INSERT INTO messages (conversation_id, created_at, role, text) VALUES (?, ?, 'user', ?)",
                            (cid, now(), user_text))
            self.db.execute(
                "INSERT INTO messages (conversation_id, created_at, role, text, products, tools) "
                "VALUES (?, ?, 'assistant', ?, ?, ?)",
                (cid, now(), answer, json.dumps(products, ensure_ascii=False), json.dumps(tools, ensure_ascii=False)))

    def messages(self, cid: str) -> list[dict]:
        rows = self.db.execute("SELECT role, text, products, tools, created_at FROM messages "
                               "WHERE conversation_id = ? ORDER BY id", (cid,)).fetchall()
        return [{**dict(r), "products": json.loads(r["products"]), "tools": json.loads(r["tools"])} for r in rows]

    def rate(self, cid: str, rating: int):
        with self.lock, self.db:
            self.db.execute("UPDATE conversations SET rating = ? WHERE id = ?", (rating, cid))

    def log_tool(self, cid: str, name: str, args: dict, is_error: bool):
        with self.lock, self.db:
            self.db.execute("INSERT INTO tool_calls (conversation_id, created_at, name, input, is_error) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (cid, now(), name, json.dumps(_redact(args), ensure_ascii=False), int(is_error)))

    # ----- tickets -----

    def create_ticket(self, cid: str, reason: str, email: str | None, priority: str) -> int:
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO tickets (conversation_id, created_at, reason, email, priority) "
                                  "VALUES (?, ?, ?, ?, ?)", (cid, now(), reason, email, priority))
            return cur.lastrowid

    def tickets(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM tickets" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT 200"
        return [dict(r) for r in self.db.execute(sql, (status,) if status else ()).fetchall()]

    def close_ticket(self, ticket_id: int) -> bool:
        with self.lock, self.db:
            return self.db.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (ticket_id,)).rowcount == 1

    # ----- dashboard -----

    def list_conversations(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute(
            """SELECT c.id, c.created_at, c.updated_at, c.turns, c.cost_usd, c.rating, c.language, c.page,
                      (SELECT text FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user'
                       ORDER BY m.id LIMIT 1) AS first_question,
                      (SELECT COUNT(*) FROM tickets t WHERE t.conversation_id = c.id) AS tickets
               FROM conversations c WHERE c.turns > 0 ORDER BY c.updated_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def spent_since(self, since: datetime) -> float:
        row = self.db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM conversations WHERE updated_at >= ?",
                              (since.isoformat(timespec="seconds"),)).fetchone()
        return float(row[0])

    def stats(self, days: int = 30) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        conv = self.db.execute(
            """SELECT COUNT(*) AS conversations, COALESCE(SUM(turns), 0) AS messages,
                      COALESCE(SUM(cost_usd), 0) AS cost, AVG(rating) AS rating,
                      SUM(CASE WHEN rating IS NOT NULL THEN 1 ELSE 0 END) AS rated
               FROM conversations WHERE turns > 0 AND created_at >= ?""", (since,)).fetchone()
        handed = self.db.execute("SELECT COUNT(DISTINCT conversation_id) FROM tickets WHERE created_at >= ?",
                                 (since,)).fetchone()[0]
        tools = self.db.execute("SELECT name, COUNT(*) AS n FROM tool_calls WHERE created_at >= ? "
                                "GROUP BY name ORDER BY n DESC", (since,)).fetchall()
        searches = self.db.execute(
            """SELECT LOWER(json_extract(input, '$.query')) AS q, COUNT(*) AS n FROM tool_calls
               WHERE name = 'search_products' AND created_at >= ? GROUP BY q ORDER BY n DESC LIMIT 8""",
            (since,)).fetchall()
        topics = self.db.execute(
            """SELECT json_extract(input, '$.topic') AS topic, COUNT(*) AS n FROM tool_calls
               WHERE name = 'get_store_policy' AND created_at >= ? GROUP BY topic ORDER BY n DESC""",
            (since,)).fetchall()
        total = conv["conversations"] or 0
        return {
            "days": days,
            "conversations": total,
            "messages": conv["messages"],
            "handed_off": handed,
            "resolved_by_ai_pct": round(100 * (total - handed) / total, 1) if total else None,
            "cost_usd": round(conv["cost"], 4),
            "cost_per_conversation_usd": round(conv["cost"] / total, 4) if total else None,
            "avg_rating": round(conv["rating"], 2) if conv["rating"] is not None else None,
            "rated": conv["rated"] or 0,
            "open_tickets": self.db.execute("SELECT COUNT(*) FROM tickets WHERE status = 'open'").fetchone()[0],
            "tools": [dict(r) for r in tools],
            "top_searches": [dict(r) for r in searches if r["q"]],
            "policy_topics": [dict(r) for r in topics if r["topic"]],
        }


def _redact(args: dict) -> dict:
    """Keep emails out of the analytics table."""
    return {k: ("***" if k == "email" and v else v) for k, v in (args if isinstance(args, dict) else {}).items()}
