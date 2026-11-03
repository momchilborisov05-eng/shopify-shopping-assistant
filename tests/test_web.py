"""The web API in demo mode: chat streaming, limits, the dashboard and its protection."""

import json

import pytest
from fastapi.testclient import TestClient

from web.app import Settings, create_app


def make(tmp_path, **overrides):
    settings = Settings(demo=True, db_path=str(tmp_path / "test.db"), **overrides)
    return TestClient(create_app(settings))


def chat(client, message, conversation_id=None, page=None):
    response = client.post("/api/chat", json={"message": message, "conversation_id": conversation_id, "page": page})
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def client(tmp_path):
    return make(tmp_path)


def text_of(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


def test_chat_streams_events_and_continues(client):
    events = chat(client, "Търся раница")
    assert events[0]["type"] == "meta" and events[-1]["type"] == "done"
    cid = events[0]["conversation_id"]
    cards = next(e for e in events if e["type"] == "products")["products"]
    assert {c["handle"] for c in cards} >= {"fjell-daypack-28"}
    assert all(c["image"].startswith("/static/products/") for c in cards)

    events = chat(client, "Къде е поръчката ми?", cid)
    assert events[0]["conversation_id"] == cid and "номера на поръчката" in text_of(events)
    events = chat(client, "#1002 georgi.petrov@example.com", cid)
    assert "доставена" in text_of(events)

    history = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"] * 3
    assert client.post(f"/api/conversations/{cid}/rating", json={"rating": 5}).json() == {"ok": True}
    assert client.post(f"/api/conversations/{cid}/rating", json={"rating": 9}).status_code == 422


def test_product_page_context(client):
    events = chat(client, "Is size M available?", page="/products/alpine-shell-jacket")
    assert "Size M is available" in text_of(events)


def test_message_validation_and_rate_limit(tmp_path):
    client = make(tmp_path, rate_per_minute=2)
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 1001}).status_code == 422
    chat(client, "hi")
    chat(client, "hi again")
    response = client.post("/api/chat", json={"message": "and again"})
    assert response.status_code == 429 and "wait" in response.json()["error"]


def test_budgets(tmp_path):
    client = make(tmp_path, daily_budget_usd=0.0)
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 503
    client = make(tmp_path, max_turns=1)
    cid = chat(client, "hi")[0]["conversation_id"]
    assert client.post("/api/chat", json={"message": "again", "conversation_id": cid}).status_code == 429


def test_admin_dashboard_and_tickets(client):
    cid = chat(client, "I want to talk to a person, wrong item. me@example.com")[0]["conversation_id"]
    stats = client.get("/api/admin/stats").json()
    assert stats["conversations"] == 1 and stats["handed_off"] == 1 and stats["open_tickets"] == 1
    ticket = client.get("/api/admin/tickets").json()["tickets"][0]
    assert ticket["conversation_id"] == cid and ticket["email"] == "me@example.com"
    detail = client.get(f"/api/admin/conversations/{cid}").json()
    assert "history" not in detail and detail["tickets"][0]["id"] == ticket["id"]
    assert client.post(f"/api/admin/tickets/{ticket['id']}/close").json() == {"ok": True}
    assert client.get("/api/admin/tickets?status=open").json()["tickets"] == []
    assert client.get("/api/admin/conversations").json()["conversations"][0]["tickets"] == 1


def test_admin_token(tmp_path):
    client = make(tmp_path, admin_token="s3cret")
    assert client.get("/api/admin/stats").status_code == 401
    assert client.get("/api/admin/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/admin/stats", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/info").json()["admin_protected"] is True


def test_pages_and_headers(client):
    home = client.get("/")
    assert home.status_code == 200 and "script-src 'self'" in home.headers["content-security-policy"]
    assert client.get("/products/thermo-bottle").status_code == 200
    widget = client.get("/widget.js")
    assert widget.status_code == 200 and "content-security-policy" not in widget.headers
    assert len(client.get("/api/catalog").json()["products"]) == 12
    assert client.get("/api/products/nope").status_code == 404
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/readyz").json() == {"ok": True, "database": True, "store": True, "demo": True}


def test_cors_for_the_shop_domain(tmp_path):
    client = make(tmp_path, allowed_origins=["https://nordvik.example"])
    preflight = client.options("/api/chat", headers={"Origin": "https://nordvik.example",
                                                     "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "content-type"})
    assert preflight.headers["access-control-allow-origin"] == "https://nordvik.example"
    other = client.options("/api/chat", headers={"Origin": "https://evil.example",
                                                 "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in other.headers
