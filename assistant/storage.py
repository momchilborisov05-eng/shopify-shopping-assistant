"""
Storage for conversations, the team's tickets and the numbers on the dashboard.

Two backends behind one class:

    sqlite:///data/assistant.db                 a local file (also a plain path, or :memory:)
    mssql://<server>.database.windows.net/<db>  Azure SQL, signed in with Microsoft Entra ID
                                                (Azure CLI locally, the managed identity in Azure)

In Azure SQL the tables live in their own schema, shopassist.

The API history of each conversation is stored exactly as sent and received
(append-only), so a conversation can continue on the next request with its
thinking blocks and tool calls intact.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

SQLITE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS conversations (
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
        history TEXT NOT NULL DEFAULT '[]')""",
    """CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        created_at TEXT NOT NULL,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        products TEXT NOT NULL DEFAULT '[]',
        tools TEXT NOT NULL DEFAULT '[]')""",
    "CREATE INDEX IF NOT EXISTS ix_messages_conversation ON messages(conversation_id, id)",
    """CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        created_at TEXT NOT NULL,
        reason TEXT NOT NULL,
        email TEXT,
        priority TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'open')""",
    """CREATE TABLE IF NOT EXISTS tool_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        name TEXT NOT NULL,
        query TEXT,
        topic TEXT,
        is_error INTEGER NOT NULL DEFAULT 0)""",
]

# Timestamps are ISO strings in both backends, so they compare the same way everywhere.
MSSQL_SCHEMA = [
    "IF SCHEMA_ID('shopassist') IS NULL EXEC('CREATE SCHEMA shopassist')",
    """IF OBJECT_ID('shopassist.conversations') IS NULL CREATE TABLE shopassist.conversations (
        id VARCHAR(32) PRIMARY KEY,
        created_at VARCHAR(32) NOT NULL,
        updated_at VARCHAR(32) NOT NULL,
        page NVARCHAR(300),
        language VARCHAR(8),
        turns INT NOT NULL DEFAULT 0,
        input_tokens BIGINT NOT NULL DEFAULT 0,
        output_tokens BIGINT NOT NULL DEFAULT 0,
        cache_read_tokens BIGINT NOT NULL DEFAULT 0,
        cost_usd FLOAT NOT NULL DEFAULT 0,
        rating INT,
        history NVARCHAR(MAX) NOT NULL DEFAULT '[]')""",
    """IF OBJECT_ID('shopassist.messages') IS NULL CREATE TABLE shopassist.messages (
        id INT IDENTITY PRIMARY KEY,
        conversation_id VARCHAR(32) NOT NULL REFERENCES shopassist.conversations(id),
        created_at VARCHAR(32) NOT NULL,
        role VARCHAR(16) NOT NULL,
        text NVARCHAR(MAX) NOT NULL,
        products NVARCHAR(MAX) NOT NULL DEFAULT '[]',
        tools NVARCHAR(MAX) NOT NULL DEFAULT '[]')""",
    """IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_messages_conversation')
        CREATE INDEX ix_messages_conversation ON shopassist.messages(conversation_id, id)""",
    """IF OBJECT_ID('shopassist.tickets') IS NULL CREATE TABLE shopassist.tickets (
        id INT IDENTITY PRIMARY KEY,
        conversation_id VARCHAR(32) NOT NULL REFERENCES shopassist.conversations(id),
        created_at VARCHAR(32) NOT NULL,
        reason NVARCHAR(600) NOT NULL,
        email NVARCHAR(254),
        priority VARCHAR(16) NOT NULL DEFAULT 'normal',
        status VARCHAR(16) NOT NULL DEFAULT 'open')""",
    """IF OBJECT_ID('shopassist.tool_calls') IS NULL CREATE TABLE shopassist.tool_calls (
        id INT IDENTITY PRIMARY KEY,
        conversation_id VARCHAR(32) NOT NULL,
        created_at VARCHAR(32) NOT NULL,
        name VARCHAR(64) NOT NULL,
        query NVARCHAR(200),
        topic VARCHAR(32),
        is_error INT NOT NULL DEFAULT 0)""",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, url: str | Path = ":memory:"):
        url = str(url)
        if "://" not in url:  # a plain path, or :memory:
            url = "sqlite:///" + url
        parsed = urlparse(url)
        self.lock = threading.RLock()
        if parsed.scheme == "sqlite":
            self.dialect = "sqlite"
            path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
            path = path or ":memory:"
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._connect = lambda: _connect_sqlite(path)
        elif parsed.scheme == "mssql":
            self.dialect = "mssql"
            database = parsed.path.lstrip("/")
            if not parsed.hostname or not database:
                raise ValueError("Use mssql://<server>.database.windows.net/<database>")
            conn_str = (f"Server=tcp:{parsed.hostname},{parsed.port or 1433};Database={database};"
                        "Encrypt=yes;TrustServerCertificate=no;Authentication=ActiveDirectoryDefault")
            self._connect = lambda: _connect_mssql(conn_str)
        else:
            raise ValueError(f"Unsupported database URL: {url}")
        self.db = self._connect()
        cur = self.db.cursor()
        for statement in SQLITE_SCHEMA if self.dialect == "sqlite" else MSSQL_SCHEMA:
            cur.execute(statement)
        self.db.commit()

    def close(self):
        self.db.close()

    # ----- low-level helpers -----

    def _t(self, name: str) -> str:
        return f"shopassist.{name}" if self.dialect == "mssql" else name

    def _sql(self, sql: str) -> str:
        """Write queries once: {conversations} etc. become table names, LIMIT n becomes TOP for SQL Server."""
        sql = sql.format(**{t: self._t(t) for t in ("conversations", "messages", "tickets", "tool_calls")})
        if self.dialect == "mssql" and " LIMIT " in sql:
            head, n = sql.rsplit(" LIMIT ", 1)
            sql = head.replace("SELECT ", f"SELECT TOP ({int(n)}) ", 1)
        return sql

    def _run(self, sql: str, params: tuple = (), fetch: str | None = None, commit: bool = False):
        """One statement under the lock. A connection that went away (an idle Azure SQL database
        closes them) is reopened once and the statement retried."""
        sql = self._sql(sql)
        for attempt in (1, 2):
            with self.lock:
                try:
                    cur = self.db.cursor()
                    cur.execute(sql, params)
                    result = None
                    if fetch:
                        names = [c[0] for c in cur.description]
                        rows = cur.fetchall()
                        result = [dict(zip(names, row, strict=True)) for row in rows]
                        if fetch == "one":
                            result = result[0] if result else None
                    else:
                        result = cur.rowcount
                    if commit:
                        self.db.commit()
                    return result
                except sqlite3.Error:
                    raise
                except Exception:
                    if self.dialect != "mssql" or attempt == 2 or self._alive():
                        raise
                    self.db = self._connect()

    def _alive(self) -> bool:
        try:
            cur = self.db.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            return True
        except Exception:
            return False

    def _insert(self, table: str, values: dict) -> int:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        if self.dialect == "mssql":
            sql = f"INSERT INTO {{{table}}} ({cols}) OUTPUT INSERTED.id VALUES ({marks})"
        else:
            sql = f"INSERT INTO {{{table}}} ({cols}) VALUES ({marks}) RETURNING id"
        return int(self._run(sql, tuple(values.values()), fetch="one", commit=True)["id"])

    def ping(self) -> bool:
        try:
            return self._run("SELECT 1 AS ok", fetch="one")["ok"] == 1
        except Exception:
            return False

    # ----- conversations -----

    def create_conversation(self, page: str | None = None) -> str:
        cid = uuid.uuid4().hex
        self._run("INSERT INTO {conversations} (id, created_at, updated_at, page) VALUES (?, ?, ?, ?)",
                  (cid, now(), now(), (page or "")[:300]), commit=True)
        return cid

    def get_conversation(self, cid: str) -> dict | None:
        conv = self._run("SELECT * FROM {conversations} WHERE id = ?", (cid,), fetch="one")
        if conv:
            conv["history"] = json.loads(conv["history"])
        return conv

    def save_turn(self, cid: str, history: list, user_text: str, answer: str, products: list, tools: list,
                  usage: dict, cost: float, language: str | None):
        with self.lock:
            self._run(
                """UPDATE {conversations} SET history = ?, updated_at = ?, turns = turns + 1,
                   input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,
                   cache_read_tokens = cache_read_tokens + ?, cost_usd = cost_usd + ?,
                   language = COALESCE(language, ?) WHERE id = ?""",
                (json.dumps(history, ensure_ascii=False), now(), usage.get("input_tokens", 0),
                 usage.get("output_tokens", 0), usage.get("cache_read_tokens", 0), cost, language, cid))
            self._run("INSERT INTO {messages} (conversation_id, created_at, role, text) VALUES (?, ?, 'user', ?)",
                      (cid, now(), user_text))
            self._run(
                "INSERT INTO {messages} (conversation_id, created_at, role, text, products, tools) "
                "VALUES (?, ?, 'assistant', ?, ?, ?)",
                (cid, now(), answer, json.dumps(products, ensure_ascii=False), json.dumps(tools, ensure_ascii=False)),
                commit=True)

    def messages(self, cid: str) -> list[dict]:
        rows = self._run("SELECT role, text, products, tools, created_at FROM {messages} "
                         "WHERE conversation_id = ? ORDER BY id", (cid,), fetch="all")
        return [{**r, "products": json.loads(r["products"]), "tools": json.loads(r["tools"])} for r in rows]

    def rate(self, cid: str, rating: int):
        self._run("UPDATE {conversations} SET rating = ? WHERE id = ?", (rating, cid), commit=True)

    def log_tool(self, cid: str, name: str, args: dict, is_error: bool):
        """Only what the dashboard shows (the search words, the policy topic). Never emails."""
        args = args if isinstance(args, dict) else {}
        query = args.get("query") if name == "search_products" and isinstance(args.get("query"), str) else None
        topic = args.get("topic") if name == "get_store_policy" and isinstance(args.get("topic"), str) else None
        self._run("INSERT INTO {tool_calls} (conversation_id, created_at, name, query, topic, is_error) "
                  "VALUES (?, ?, ?, ?, ?, ?)",
                  (cid, now(), name[:64], query.lower()[:200] if query else None, topic[:32] if topic else None,
                   int(is_error)), commit=True)

    # ----- tickets -----

    def create_ticket(self, cid: str, reason: str, email: str | None, priority: str) -> int:
        return self._insert("tickets", {"conversation_id": cid, "created_at": now(), "reason": reason[:600],
                                        "email": email, "priority": priority})

    def tickets(self, status: str | None = None) -> list[dict]:
        where = " WHERE status = ?" if status else ""
        return self._run("SELECT * FROM {tickets}" + where + " ORDER BY id DESC LIMIT 200",
                         (status,) if status else (), fetch="all")

    def close_ticket(self, ticket_id: int) -> bool:
        return self._run("UPDATE {tickets} SET status = 'closed' WHERE id = ?", (ticket_id,), commit=True) == 1

    # ----- dashboard -----

    def list_conversations(self, limit: int = 100) -> list[dict]:
        first = ("(SELECT TOP 1 text FROM {messages} m WHERE m.conversation_id = c.id AND m.role = 'user' "
                 "ORDER BY m.id)") if self.dialect == "mssql" else (
                 "(SELECT text FROM {messages} m WHERE m.conversation_id = c.id AND m.role = 'user' "
                 "ORDER BY m.id LIMIT 1)")
        return self._run(
            f"""SELECT c.id, c.created_at, c.updated_at, c.turns, c.cost_usd, c.rating, c.language, c.page,
                      {first} AS first_question,
                      (SELECT COUNT(*) FROM {{tickets}} t WHERE t.conversation_id = c.id) AS tickets
               FROM {{conversations}} c WHERE c.turns > 0 ORDER BY c.updated_at DESC LIMIT {int(limit)}""",
            fetch="all")

    def spent_since(self, since: datetime) -> float:
        row = self._run("SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM {conversations} WHERE updated_at >= ?",
                        (since.isoformat(timespec="seconds"),), fetch="one")
        return float(row["spent"])

    def stats(self, days: int = 30) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        conv = self._run(
            """SELECT COUNT(*) AS conversations, COALESCE(SUM(turns), 0) AS messages,
                      COALESCE(SUM(cost_usd), 0) AS cost, AVG(CAST(rating AS FLOAT)) AS rating,
                      SUM(CASE WHEN rating IS NOT NULL THEN 1 ELSE 0 END) AS rated
               FROM {conversations} WHERE turns > 0 AND created_at >= ?""", (since,), fetch="one")
        handed = self._run("SELECT COUNT(DISTINCT conversation_id) AS n FROM {tickets} WHERE created_at >= ?",
                           (since,), fetch="one")["n"]
        tools = self._run("SELECT name, COUNT(*) AS n FROM {tool_calls} WHERE created_at >= ? "
                          "GROUP BY name ORDER BY n DESC", (since,), fetch="all")
        searches = self._run("SELECT query AS q, COUNT(*) AS n FROM {tool_calls} WHERE name = 'search_products' "
                             "AND query IS NOT NULL AND created_at >= ? GROUP BY query ORDER BY n DESC LIMIT 8",
                             (since,), fetch="all")
        topics = self._run("SELECT topic, COUNT(*) AS n FROM {tool_calls} WHERE name = 'get_store_policy' "
                           "AND topic IS NOT NULL AND created_at >= ? GROUP BY topic ORDER BY n DESC",
                           (since,), fetch="all")
        open_tickets = self._run("SELECT COUNT(*) AS n FROM {tickets} WHERE status = 'open'", fetch="one")["n"]
        total = conv["conversations"] or 0
        cost = float(conv["cost"] or 0)
        return {
            "days": days,
            "conversations": total,
            "messages": int(conv["messages"] or 0),
            "handed_off": handed,
            "resolved_by_ai_pct": round(100 * (total - handed) / total, 1) if total else None,
            "cost_usd": round(cost, 4),
            "cost_per_conversation_usd": round(cost / total, 4) if total else None,
            "avg_rating": round(conv["rating"], 2) if conv["rating"] is not None else None,
            "rated": conv["rated"] or 0,
            "open_tickets": open_tickets,
            "tools": tools,
            "top_searches": searches,
            "policy_topics": topics,
        }


def _connect_sqlite(path: str):
    db = sqlite3.connect(path, check_same_thread=False, timeout=15)
    if path != ":memory:":
        db.execute("PRAGMA journal_mode=WAL")
    return db


def _connect_mssql(connection_string: str, attempts: int = 8):
    """Connect to Azure SQL, waiting while a paused serverless database resumes (up to about a minute)."""
    import mssql_python  # only needed for Azure SQL

    for attempt in range(1, attempts + 1):
        try:
            return mssql_python.connect(connection_string, timeout=30)
        except mssql_python.OperationalError as e:
            if "not currently available" not in str(e) or attempt == attempts:
                raise
            if attempt == 1:
                print("Database is resuming from auto-pause, waiting...", file=sys.stderr)
            time.sleep(min(5 * attempt, 20))
