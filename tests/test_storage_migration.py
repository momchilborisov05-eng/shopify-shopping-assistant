"""A database file made by the first version must keep working after an upgrade."""

import json
import sqlite3

from assistant.storage import Storage, now

V1_TOOL_CALLS = """CREATE TABLE tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    input TEXT NOT NULL,
    is_error INTEGER NOT NULL DEFAULT 0
)"""


def make_v1_db(path):
    db = sqlite3.connect(path)
    db.execute(V1_TOOL_CALLS)
    rows = [
        ("c1", "search_products", json.dumps({"query": "Waterproof Jacket", "max_price": 200}), 0),
        ("c1", "get_store_policy", json.dumps({"topic": "returns"}), 0),
        ("c2", "track_order", json.dumps({"order": "1001", "email": "maria.ivanova@example.com"}), 1),
        ("c2", "search_products", "not json", 0),
    ]
    db.executemany("INSERT INTO tool_calls (conversation_id, created_at, name, input, is_error) "
                   "VALUES (?, ?, ?, ?, ?)", [(c, now(), n, i, e) for c, n, i, e in rows])
    db.commit()
    db.close()


def test_v1_tool_calls_table_is_migrated(tmp_path):
    path = tmp_path / "assistant.db"
    make_v1_db(path)

    storage = Storage(str(path))
    columns = [r["name"] for r in storage._run("PRAGMA table_info(tool_calls)", fetch="all")]
    assert "input" not in columns
    assert {"query", "topic"} <= set(columns)

    rows = storage._run("SELECT conversation_id, name, query, topic, is_error FROM tool_calls ORDER BY id",
                        fetch="all")
    assert rows == [
        {"conversation_id": "c1", "name": "search_products", "query": "waterproof jacket", "topic": None,
         "is_error": 0},
        {"conversation_id": "c1", "name": "get_store_policy", "query": None, "topic": "returns", "is_error": 0},
        {"conversation_id": "c2", "name": "track_order", "query": None, "topic": None, "is_error": 1},
        {"conversation_id": "c2", "name": "search_products", "query": None, "topic": None, "is_error": 0},
    ]
    # The old full arguments, emails included, are gone.
    dump = "\n".join(sqlite3.connect(path).iterdump())
    assert "maria.ivanova@example.com" not in dump

    # New writes work and the dashboard counts old and new rows together.
    storage.log_tool("c3", "search_products", {"query": "waterproof jacket"}, False)
    stats = storage.stats()
    assert {"q": "waterproof jacket", "n": 2} in stats["top_searches"]
    storage.close()

    # Opening the file again is a no-op.
    again = Storage(str(path))
    assert again._run("SELECT COUNT(*) AS n FROM tool_calls", fetch="one")["n"] == 5
    again.close()


def test_current_schema_is_left_alone(tmp_path):
    path = tmp_path / "assistant.db"
    storage = Storage(str(path))
    storage.log_tool("c1", "search_products", {"query": "boots"}, False)
    storage.close()
    again = Storage(str(path))
    assert again._run("SELECT query FROM tool_calls", fetch="all") == [{"query": "boots"}]
    again.close()
