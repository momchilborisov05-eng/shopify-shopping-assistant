"""
The web app: the chat API the widget talks to, the team's dashboard, and (in
demo mode) a small storefront to try the widget on.

Run it:
    DEMO_MODE=1 uvicorn web.app:create_app --factory --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from assistant.agent import DEFAULT_MODEL, ShopAssistant
from assistant.catalog import DemoStore, ShopifyConfig, ShopifyStore, Store, StoreError
from assistant.demo import DEMO_MODEL, DemoClient
from assistant.storage import Storage

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
log = logging.getLogger("shop-assistant")


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


@dataclass
class Settings:
    demo: bool = False
    model: str = DEFAULT_MODEL
    effort: str = "low"
    db_path: str = str(ROOT / "data" / "assistant.db")
    shopify_domain: str = ""
    shopify_token: str = ""
    shopify_api_version: str = "2026-10"
    faq_path: str = str(ROOT / "config" / "faq.md")
    store_about: str = ""
    admin_token: str = ""
    allowed_origins: list[str] = field(default_factory=list)
    daily_budget_usd: float = 5.0
    conversation_budget_usd: float = 0.50
    max_turns: int = 40
    rate_per_minute: int = 12

    @classmethod
    def from_env(cls) -> Settings:
        demo = os.environ.get("DEMO_MODE") == "1" or not os.environ.get("ANTHROPIC_API_KEY")
        return cls(
            demo=demo,
            model=os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL),
            effort=os.environ.get("CLAUDE_EFFORT", "low"),
            db_path=os.environ.get("ASSISTANT_DB", str(ROOT / "data" / "assistant.db")),
            shopify_domain=os.environ.get("SHOPIFY_STORE_DOMAIN", ""),
            shopify_token=os.environ.get("SHOPIFY_ADMIN_TOKEN", ""),
            shopify_api_version=os.environ.get("SHOPIFY_API_VERSION", "2026-10"),
            faq_path=os.environ.get("FAQ_PATH", str(ROOT / "config" / "faq.md")),
            store_about=os.environ.get("STORE_ABOUT", ""),
            admin_token=os.environ.get("ADMIN_TOKEN", ""),
            allowed_origins=_env_list("ALLOWED_ORIGINS"),
            daily_budget_usd=float(os.environ.get("DAILY_BUDGET_USD", "5")),
            conversation_budget_usd=float(os.environ.get("CONVERSATION_BUDGET_USD", "0.50")),
            max_turns=int(os.environ.get("MAX_TURNS", "40")),
            rate_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "12")),
        )


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = Field(default=None, max_length=64)
    page: str | None = Field(default=None, max_length=300)


class RatingRequest(BaseModel):
    rating: int = Field(ge=1, le=5)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def build_store(settings: Settings) -> Store:
    if settings.shopify_domain and settings.shopify_token:
        return ShopifyStore(ShopifyConfig(domain=settings.shopify_domain, token=settings.shopify_token,
                                          api_version=settings.shopify_api_version, faq_path=settings.faq_path))
    return DemoStore()


def create_app(settings: Settings | None = None, client=None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or build_store(settings)
    storage = Storage(settings.db_path)
    if client is None:
        if settings.demo:
            if not isinstance(store, DemoStore):
                raise RuntimeError("Demo mode works with the demo store only. Set ANTHROPIC_API_KEY for a real store.")
            client = DemoClient(store)
        else:
            client = anthropic.Anthropic()
    model = DEMO_MODEL if isinstance(client, DemoClient) else settings.model
    assistant = ShopAssistant(client, store, storage, model=model, effort=settings.effort, about=settings.store_about)
    limiter = RateLimiter(settings.rate_per_minute)

    app = FastAPI(title="Shopify AI Shopping Assistant", docs_url=None, redoc_url=None)
    app.state.storage = storage
    app.state.settings = settings
    if settings.allowed_origins:
        # The widget runs on the shop's own domain and calls this API from there.
        app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"], max_age=600)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if not request.url.path.startswith(("/widget.js", "/api/chat", "/api/conversations")):
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: https://cdn.shopify.com; style-src 'self'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        if request.url.path != "/healthz":
            log.info(json.dumps({"id": request_id, "method": request.method, "path": request.url.path,
                                 "status": response.status_code, "ms": int((time.monotonic() - started) * 1000)}))
        return response

    def admin(request: Request):
        if not settings.admin_token:
            return
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(supplied, settings.admin_token):
            raise HTTPException(401, "Admin token required")

    # ---------- shopper-facing ----------

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/info")
    def info():
        return {"store": store.name, "currency": store.currency, "demo": isinstance(client, DemoClient),
                "model": model, "demo_store": isinstance(store, DemoStore),
                "admin_protected": bool(settings.admin_token)}

    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request):
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
        if not limiter.allow(ip):
            raise HTTPException(429, "Too many messages. Please wait a minute.")
        text = body.message.strip()
        if not text:
            raise HTTPException(400, "Empty message")
        conv = storage.get_conversation(body.conversation_id) if body.conversation_id else None
        if conv is None:
            cid = storage.create_conversation(body.page)
            conv = storage.get_conversation(cid)
        cid = conv["id"]
        if conv["turns"] >= settings.max_turns:
            raise HTTPException(429, "This conversation is very long. Please start a new one.")
        if conv["cost_usd"] >= settings.conversation_budget_usd:
            raise HTTPException(429, "This conversation reached its limit. Please start a new one.")
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        if storage.spent_since(today) >= settings.daily_budget_usd:
            raise HTTPException(503, "The assistant is resting for today. Please email the store.")

        def stream():
            yield _sse({"type": "meta", "conversation_id": cid})
            try:
                for event in assistant.respond(cid, text, body.page):
                    if event["type"] == "done":
                        result = event["result"]
                        yield _sse({"type": "done", "tools": [t["name"] for t in result.tools],
                                    "cost_usd": round(result.cost, 5)})
                    else:
                        yield _sse(event)
            except anthropic.RateLimitError:
                yield _sse({"type": "error", "message": "The assistant is busy. Please try again in a moment."})
            except (anthropic.APIError, StoreError) as e:
                log.exception("chat failed: %s", e)
                yield _sse({"type": "error", "message": "Something went wrong. Please try again."})

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/conversations/{cid}")
    def conversation(cid: str):
        if not storage.get_conversation(cid):
            raise HTTPException(404, "Not found")
        return {"conversation_id": cid, "messages": storage.messages(cid)}

    @app.post("/api/conversations/{cid}/rating")
    def rate(cid: str, body: RatingRequest):
        if not storage.get_conversation(cid):
            raise HTTPException(404, "Not found")
        storage.rate(cid, body.rating)
        return {"ok": True}

    # ---------- demo storefront ----------

    @app.get("/api/catalog")
    def catalog():
        if not isinstance(store, DemoStore):
            raise HTTPException(404, "The storefront is only for the demo store")
        return {"store": store.name, "categories": store.categories(), "products": store.all_products()}

    @app.get("/api/products/{handle}")
    def product(handle: str):
        if not isinstance(store, DemoStore):
            raise HTTPException(404, "The storefront is only for the demo store")
        item = store.get_product(handle)
        if not item:
            raise HTTPException(404, "No such product")
        return item

    @app.get("/")
    def home():
        if not isinstance(store, DemoStore):
            return RedirectResponse("/admin")
        return FileResponse(STATIC / "store.html")

    @app.get("/products/{handle}")
    def product_page(handle: str):
        if not isinstance(store, DemoStore):
            raise HTTPException(404)
        return FileResponse(STATIC / "store.html")

    @app.get("/widget.js")
    def widget():
        return FileResponse(STATIC / "widget.js", media_type="application/javascript",
                            headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"})

    # ---------- the team's dashboard ----------

    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC / "admin.html")

    @app.get("/api/admin/stats", dependencies=[Depends(admin)])
    def stats(days: int = 30):
        return storage.stats(max(1, min(days, 365)))

    @app.get("/api/admin/conversations", dependencies=[Depends(admin)])
    def conversations():
        return {"conversations": storage.list_conversations()}

    @app.get("/api/admin/conversations/{cid}", dependencies=[Depends(admin)])
    def admin_conversation(cid: str):
        conv = storage.get_conversation(cid)
        if not conv:
            raise HTTPException(404, "Not found")
        conv.pop("history")
        return {**conv, "messages": storage.messages(cid),
                "tickets": [t for t in storage.tickets() if t["conversation_id"] == cid]}

    @app.get("/api/admin/tickets", dependencies=[Depends(admin)])
    def tickets(status: str | None = None):
        return {"tickets": storage.tickets(status if status in ("open", "closed") else None)}

    @app.post("/api/admin/tickets/{ticket_id}/close", dependencies=[Depends(admin)])
    def close_ticket(ticket_id: int):
        if not storage.close_ticket(ticket_id):
            raise HTTPException(404, "No such ticket")
        return {"ok": True}

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
