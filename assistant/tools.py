"""
The tools Claude can call, and the code that runs them against the store.

Every tool only reads, except handoff_to_human, which opens a ticket for the
team. Inputs are model output, so each one is validated here before it runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .catalog import POLICY_TOPICS, Store, StoreError

MAX_ORDER_LOOKUPS = 5  # per conversation, so the chat can't be used to guess order numbers
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[a-z]{2,24}$", re.I)

TOOLS: list[dict] = [
    {
        "name": "search_products",
        "description": (
            "Search the store's catalog. Use it whenever the shopper asks for a product, a recommendation, "
            "a gift idea or a price range. Write the query as a few English keywords (for example "
            "'waterproof jacket' or 'trail running shoes'), whatever language the shopper uses. "
            "Returns up to 6 products with price, stock and rating."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A few English keywords."},
                "category": {"type": "string", "description": "Optional exact category name from the system prompt."},
                "max_price": {"type": "number", "description": "Optional maximum price in the store currency."},
                "in_stock_only": {"type": "boolean", "description": "Only products that can be bought now."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_product",
        "description": (
            "Full details of one product: description, features, materials, care, how it fits, and every "
            "variant (size/colour) with its stock. Use it before answering about sizes, fit, materials or "
            "whether a specific size or colour is available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"handle": {"type": "string", "description": "The product handle from search results."}},
            "required": ["handle"],
        },
    },
    {
        "name": "get_store_policy",
        "description": (
            "The store's own policy text for one topic. Use it for any question about delivery times and "
            "costs, returns and exchanges, payment methods, warranty and repairs, sizing charts, contact "
            "details, discounts or gift cards. Answer only from what it returns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "enum": list(POLICY_TOPICS)}},
            "required": ["topic"],
        },
    },
    {
        "name": "lookup_order",
        "description": (
            "Status, items and tracking of one order. Needs BOTH the order number and the email address "
            "used for the order; ask the shopper for whatever is missing and never guess either one. "
            "Returns not_found when they don't match."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_number": {"type": "string", "description": "For example 1001 or #1001."},
                "email": {"type": "string", "description": "The email address on the order."},
            },
            "required": ["order_number", "email"],
        },
    },
    {
        "name": "handoff_to_human",
        "description": (
            "Pass the conversation to the store's team. Use it when the shopper asks for a person, wants to "
            "complain, reports a damaged or wrong item, asks for a refund or cancellation, or asks "
            "something the tools can't answer. Ask for their email first if they haven't given it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "One sentence for the team: what the shopper needs."},
                "email": {"type": "string", "description": "The shopper's email, if they gave it."},
                "priority": {"type": "string", "enum": ["normal", "urgent"]},
            },
            "required": ["reason"],
        },
    },
]

# What the widget shows while a tool runs.
STATUS_LABELS = {
    "search_products": "Searching the catalog",
    "get_product": "Checking sizes and stock",
    "get_store_policy": "Reading the store policy",
    "lookup_order": "Looking up the order",
    "handoff_to_human": "Passing to the team",
}


class ToolInputError(ValueError):
    pass


@dataclass
class ToolContext:
    """Per-conversation state the tools need."""

    store: Store
    conversation_id: str
    create_ticket: Callable[[str, str, str | None, str], int]
    order_lookups: int = 0
    products_seen: dict[str, dict] = field(default_factory=dict)
    handoff: dict | None = None


def _str(args: dict, key: str, required: bool = True, max_len: int = 300) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolInputError(f"'{key}' is required")
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"'{key}' must be a string")
    return value.strip()[:max_len]


def run_tool(ctx: ToolContext, name: str, args: Any) -> tuple[dict, bool]:
    """Runs one tool. Returns (result, is_error); errors are results the model can read and recover from."""
    if not isinstance(args, dict):
        return {"error": "invalid input"}, True
    try:
        handler = HANDLERS.get(name)
        if not handler:
            return {"error": f"unknown tool {name}"}, True
        return handler(ctx, args), False
    except ToolInputError as e:
        return {"error": str(e)}, True
    except StoreError as e:
        return {"error": "The store is not reachable right now. Apologise and offer to pass this to the team.",
                "detail": str(e)}, True


def _search(ctx: ToolContext, args: dict) -> dict:
    query = _str(args, "query", max_len=120)
    category = _str(args, "category", required=False, max_len=60)
    max_price = args.get("max_price")
    if max_price is not None and (not isinstance(max_price, (int, float)) or isinstance(max_price, bool)
                                  or max_price <= 0):
        raise ToolInputError("'max_price' must be a positive number")
    in_stock = args.get("in_stock_only") is True
    products = ctx.store.search_products(query, category=category, max_price=max_price, in_stock_only=in_stock)
    for p in products:
        ctx.products_seen[p["handle"]] = p
    return {"products": [{k: v for k, v in p.items() if k not in ("image", "default_variant_id")}
                         for p in products],
            "count": len(products)}


def _product(ctx: ToolContext, args: dict) -> dict:
    handle = _str(args, "handle", max_len=120)
    product = ctx.store.get_product(handle)
    if not product:
        return {"not_found": True, "handle": handle}
    ctx.products_seen[product["handle"]] = {k: product[k] for k in (
        "handle", "title", "category", "price", "compare_at_price", "currency", "in_stock", "rating",
        "reviews", "image", "url", "default_variant_id")}
    return {k: v for k, v in product.items() if k not in ("image", "default_variant_id")}


def _policy(ctx: ToolContext, args: dict) -> dict:
    topic = _str(args, "topic", max_len=30)
    if topic not in POLICY_TOPICS:
        raise ToolInputError(f"'topic' must be one of {', '.join(POLICY_TOPICS)}")
    policy = ctx.store.get_policy(topic)
    return policy or {"topic": topic, "missing": True,
                      "note": "The store has no written policy on this. Don't guess: offer to pass it to the team."}


def _order(ctx: ToolContext, args: dict) -> dict:
    number = _str(args, "order_number", max_len=30)
    email = _str(args, "email", max_len=254)
    if not EMAIL.match(email):
        raise ToolInputError("that doesn't look like an email address; ask the shopper to check it")
    if ctx.order_lookups >= MAX_ORDER_LOOKUPS:
        return {"blocked": True, "note": "Too many order lookups in this chat. Offer to pass it to the team."}
    ctx.order_lookups += 1
    order = ctx.store.find_order(number, email)
    if not order:
        return {"not_found": True,
                "note": "No order matches this number AND email. Ask the shopper to check both. "
                        "Don't say which one is wrong."}
    return order


def _handoff(ctx: ToolContext, args: dict) -> dict:
    reason = _str(args, "reason", max_len=500)
    email = _str(args, "email", required=False, max_len=254)
    if email and not EMAIL.match(email):
        email = None
    priority = args.get("priority") if args.get("priority") in ("normal", "urgent") else "normal"
    if ctx.handoff:
        return {"ticket": ctx.handoff["ticket"], "already_open": True}
    ticket = ctx.create_ticket(ctx.conversation_id, reason, email, priority)
    ctx.handoff = {"ticket": ticket, "reason": reason, "email": email, "priority": priority}
    return {"ticket": ticket, "team_replies": "Mon-Fri 9:00-18:00, usually within 2 hours",
            "reply_by": "email" if email else "this chat"}


HANDLERS: dict[str, Callable[[ToolContext, dict], dict]] = {
    "search_products": _search,
    "get_product": _product,
    "get_store_policy": _policy,
    "lookup_order": _order,
    "handoff_to_human": _handoff,
}
