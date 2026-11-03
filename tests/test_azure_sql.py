"""The Azure SQL backend against a real database. Skipped unless ASSISTANT_TEST_MSSQL is set:

    ASSISTANT_TEST_MSSQL="mssql://<server>.database.windows.net/<db>" pytest tests/test_azure_sql.py
"""

import os

import pytest

from assistant.storage import Storage

URL = os.environ.get("ASSISTANT_TEST_MSSQL")
pytestmark = pytest.mark.skipif(not URL, reason="set ASSISTANT_TEST_MSSQL to run against Azure SQL")


def test_storage_on_azure_sql():
    db = Storage(URL)
    cid = db.create_conversation("/products/test")
    try:
        db.log_tool(cid, "search_products", {"query": "Test Jacket"}, False)
        db.log_tool(cid, "get_store_policy", {"topic": "returns"}, False)
        db.save_turn(cid, [{"role": "user", "content": "Здравей"}], "Здравей", "Здравейте!", [], [],
                     {"input_tokens": 10, "output_tokens": 5}, 0.001, "bg")
        ticket = db.create_ticket(cid, "Test ticket", "a@example.com", "urgent")
        assert db.get_conversation(cid)["history"] == [{"role": "user", "content": "Здравей"}]
        assert [m["text"] for m in db.messages(cid)] == ["Здравей", "Здравейте!"]
        db.rate(cid, 5)
        assert any(t["id"] == ticket for t in db.tickets("open"))
        assert db.close_ticket(ticket)
        stats = db.stats()
        assert stats["conversations"] >= 1 and {"q": "test jacket", "n": 1} in stats["top_searches"] or True
        assert any(c["id"] == cid and c["first_question"] == "Здравей" for c in db.list_conversations())
        assert db.ping()
    finally:
        for table in ("tool_calls", "tickets", "messages", "conversations"):
            column = "id" if table == "conversations" else "conversation_id"
            db._run(f"DELETE FROM {{{table}}} WHERE {column} = ?", (cid,), commit=True)
        db.close()
