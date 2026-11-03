"""Offline tests: the store, the tools, the assistant loop and the Shopify client. No API key needed."""

import json

import httpx
import pytest
from anthropic.types.beta import BetaMessage

from assistant.agent import ShopAssistant, estimate_cost, mentioned_products
from assistant.catalog import DemoStore, ShopifyConfig, ShopifyStore, StoreError, load_faq
from assistant.storage import Storage
from assistant.tools import MAX_ORDER_LOOKUPS, ToolContext, run_tool


@pytest.fixture
def store():
    return DemoStore()


@pytest.fixture
def db():
    storage = Storage()
    yield storage
    storage.close()


def ctx_for(store, db):
    cid = db.create_conversation()
    return ToolContext(store=store, conversation_id=cid, create_ticket=db.create_ticket)


# ---------- demo store ----------

def test_search_understands_bulgarian_and_filters(store):
    titles = [p["title"] for p in store.search_products("водоустойчиво яке")]
    assert titles[0] == "Alpine Shell Jacket"
    cheap = store.search_products("jacket", max_price=200)
    assert all(p["price"] <= 200 for p in cheap) and "Summit Down Jacket" not in [p["title"] for p in cheap]
    assert store.search_products("boots", category="Backpacks") == []


def test_search_drops_weak_matches(store):
    titles = [p["title"] for p in store.search_products("waterproof boots under 200")]
    assert titles == ["Ridge Mid Hiking Boots"]


def test_product_details_and_stock_labels(store):
    product = store.get_product("alpine-shell-jacket")
    by_title = {v["title"]: v for v in product["variants"]}
    assert by_title["M / Black"]["stock"] == "only 2 left"
    assert by_title["XL / Black"]["available"] is False and by_title["XL / Black"]["stock"] == "sold out"
    assert by_title["L / Black"]["stock"] == "in stock"
    assert store.get_product("no-such-thing") is None


def test_order_needs_matching_email(store):
    assert store.find_order("#1001", "Maria.Ivanova@example.com")["number"] == "#1001"
    assert store.find_order("1001", "someone@example.com") is None
    assert store.find_order("9999", "maria.ivanova@example.com") is None


# ---------- tools ----------

def test_tool_input_is_validated(store, db):
    ctx = ctx_for(store, db)
    assert run_tool(ctx, "search_products", {})[1] is True
    assert run_tool(ctx, "search_products", {"query": "jacket", "max_price": "cheap"})[1] is True
    assert run_tool(ctx, "get_store_policy", {"topic": "secrets"})[1] is True
    assert run_tool(ctx, "lookup_order", {"order_number": "1001", "email": "not-an-email"})[1] is True
    assert run_tool(ctx, "delete_everything", {})[1] is True
    assert run_tool(ctx, "search_products", "jacket")[1] is True


def test_order_lookups_are_limited(store, db):
    ctx = ctx_for(store, db)
    for _ in range(MAX_ORDER_LOOKUPS):
        result, _ = run_tool(ctx, "lookup_order", {"order_number": "1", "email": "x@example.com"})
        assert result["not_found"]
    result, _ = run_tool(ctx, "lookup_order", {"order_number": "1001", "email": "maria.ivanova@example.com"})
    assert result.get("blocked") and "number" not in result


def test_handoff_opens_one_ticket(store, db):
    ctx = ctx_for(store, db)
    first, _ = run_tool(ctx, "handoff_to_human", {"reason": "Damaged boots", "email": "a@example.com",
                                                  "priority": "urgent"})
    again, _ = run_tool(ctx, "handoff_to_human", {"reason": "Again"})
    assert again["already_open"] and again["ticket"] == first["ticket"]
    tickets = db.tickets()
    assert len(tickets) == 1 and tickets[0]["priority"] == "urgent" and tickets[0]["email"] == "a@example.com"


def test_search_hides_internal_fields_from_the_model(store, db):
    result, _ = run_tool(ctx_for(store, db), "search_products", {"query": "backpack"})
    assert result["count"] >= 2
    assert all("image" not in p and "default_variant_id" not in p for p in result["products"])


# ---------- the assistant loop with a scripted Claude ----------

def message(content, stop_reason="end_turn", input_tokens=1000, output_tokens=100, cache_read=0):
    return BetaMessage.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": 0},
    })


class FakeStream:
    def __init__(self, msg):
        self.msg = msg
        self.text_stream = iter([b.text for b in msg.content if b.type == "text"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class FakeClaude:
    """Returns the scripted messages in order and records every request."""

    def __init__(self, *messages):
        self.script = list(messages)
        self.requests = []
        self.beta = self
        self.messages = self

    def stream(self, **params):
        self.requests.append(json.loads(json.dumps(params, default=str)))
        return FakeStream(self.script.pop(0))


def run(bot, cid, text, page=None):
    return list(bot.respond(cid, text, page))


def test_tool_loop_streams_answer_and_cards(store, db):
    claude = FakeClaude(
        message([{"type": "thinking", "thinking": "", "signature": "sig"},
                 {"type": "tool_use", "id": "toolu_1", "name": "search_products",
                  "input": {"query": "waterproof jacket"}}], "tool_use"),
        message([{"type": "text", "text": "The **Alpine Shell Jacket** (€189) is fully waterproof."}]),
    )
    bot = ShopAssistant(claude, store, db)
    cid = db.create_conversation()
    events = run(bot, cid, "I need a rain jacket", page="/products/alpine-shell-jacket")
    kinds = [e["type"] for e in events]
    assert kinds == ["status", "text", "products", "done"]
    assert events[2]["products"][0]["handle"] == "alpine-shell-jacket"

    first, second = claude.requests
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "alpine-shell-jacket" in first["messages"][0]["content"][1]["text"]
    # The second request carries the thinking block and the tool result unchanged.
    assert second["messages"][1]["content"][0]["type"] == "thinking"
    result = second["messages"][2]["content"][0]
    assert result["tool_use_id"] == "toolu_1" and "Alpine Shell Jacket" in result["content"]

    conv = db.get_conversation(cid)
    assert conv["turns"] == 1 and len(conv["history"]) == 4
    assert conv["cost_usd"] == pytest.approx(estimate_cost("claude-opus-5-5", {
        "input_tokens": 2000, "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0}))
    assert db.messages(cid)[1]["products"][0]["title"] == "Alpine Shell Jacket"


def test_history_is_append_only_across_turns(store, db):
    claude = FakeClaude(message([{"type": "text", "text": "Hi! What are you looking for?"}]),
                        message([{"type": "text", "text": "Sure."}]))
    bot = ShopAssistant(claude, store, db)
    cid = db.create_conversation()
    run(bot, cid, "hello")
    run(bot, cid, "boots please")
    assert claude.requests[1]["messages"][:2] == claude.requests[0]["messages"] + [
        {"role": "assistant", "content": [{"type": "text", "text": "Hi! What are you looking for?"}]}]


def test_refusal_is_not_kept_in_history(store, db):
    claude = FakeClaude(message([{"type": "text", "text": "partial"}], "refusal"))
    bot = ShopAssistant(claude, store, db)
    cid = db.create_conversation()
    events = run(bot, cid, "something bad")
    assert any(e["type"] == "replace" for e in events)
    assert db.get_conversation(cid)["history"] == []


def test_cut_off_tool_call_does_not_run(store, db):
    claude = FakeClaude(message([{"type": "tool_use", "id": "t1", "name": "handoff_to_human",
                                  "input": {"reason": "x"}}], "max_tokens"))
    bot = ShopAssistant(claude, store, db)
    run(bot, db.create_conversation(), "help")
    assert db.tickets() == []


def test_handoff_event(store, db):
    claude = FakeClaude(
        message([{"type": "tool_use", "id": "t1", "name": "handoff_to_human",
                  "input": {"reason": "Wrong size delivered", "email": "a@example.com"}}], "tool_use"),
        message([{"type": "text", "text": "I've passed this to the team."}]),
    )
    events = run(ShopAssistant(claude, store, db), db.create_conversation(), "wrong size!")
    handoff = next(e for e in events if e["type"] == "handoff")
    assert handoff["ticket"] == 1
    assert db.stats()["handed_off"] == 1


def test_mentioned_products_order():
    seen = {"fjell-daypack-28": {"handle": "fjell-daypack-28", "title": "Fjell Daypack 28L"},
            "thermo-bottle": {"handle": "thermo-bottle", "title": "Thermo Bottle 0.75L"},
            "carbon-trekking-poles": {"handle": "carbon-trekking-poles", "title": "Carbon Trekking Poles (pair)"},
            "wool-beanie": {"handle": "wool-beanie", "title": "Ribbed Wool Beanie"}}
    found = mentioned_products("Take the **Thermo Bottle 0.75L**, Carbon Trekking Poles and the Fjell Daypack 28L.",
                               seen)
    assert [p["handle"] for p in found] == ["thermo-bottle", "carbon-trekking-poles", "fjell-daypack-28"]


# ---------- the real Shopify client, against a fake Admin API ----------

SHOP = {"shop": {"name": "Real Shop", "currencyCode": "EUR", "shopPolicies": [
    {"type": "REFUND_POLICY", "title": "Refund policy", "body": "<p>Returns within <b>14 days</b>.</p>"}]}}
NODE = {"handle": "tent-2p", "title": "Tent 2P", "productType": "Tents", "tags": [], "onlineStoreUrl": None,
        "description": "A tent.", "featuredMedia": {"preview": {"image": {"url": "https://cdn.shopify.com/t.jpg"}}},
        "priceRangeV2": {"minVariantPrice": {"amount": "299.0", "currencyCode": "EUR"}},
        "compareAtPriceRange": {"minVariantCompareAtPrice": {"amount": "0.0"}},
        "totalInventory": 4, "tracksInventory": True}


def shopify(handler, faq=None):
    def respond(request):
        assert request.headers["X-Shopify-Access-Token"] == "shpat_test"
        assert request.url.path == "/admin/api/2026-10/graphql.json"
        body = json.loads(request.content)
        return handler(body["query"], body["variables"])
    config = ShopifyConfig(domain="real-shop.myshopify.com", token="shpat_test", faq_path=faq)
    return ShopifyStore(config, http=httpx.Client(transport=httpx.MockTransport(respond)))


def test_shopify_store(tmp_path):
    faq = tmp_path / "faq.md"
    faq.write_text("## Size guide\nTents fit 2 people.\n", encoding="utf-8")

    def handler(query, variables):
        if "shopPolicies" in query:
            return httpx.Response(200, json={"data": SHOP})
        if "productByIdentifier" in query:
            return httpx.Response(200, json={"data": {"product": {**NODE, "descriptionHtml": "<p>A tent.</p>",
                "options": [{"name": "Color"}], "variants": {"nodes": [
                    {"id": "gid://shopify/ProductVariant/77", "title": "Green", "sku": "T2", "price": "299.0",
                     "availableForSale": True, "inventoryQuantity": 2,
                     "selectedOptions": [{"name": "Color", "value": "Green"}]}]}}}})
        if "products(" in query:
            assert "status:active" in variables["query"] and "price:<=300" in variables["query"]
            return httpx.Response(200, json={"data": {"products": {"nodes": [
                {**NODE, "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/77", "availableForSale": True}]}}]}}})
        if "orders(" in query:
            return httpx.Response(200, json={"data": {"orders": {"nodes": [{
                "name": "#2001", "email": "buyer@example.com", "createdAt": "2026-11-01T10:00:00Z",
                "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "FULFILLED",
                "customer": {"firstName": "Ana"},
                "totalPriceSet": {"shopMoney": {"amount": "299.0", "currencyCode": "EUR"}},
                "lineItems": {"nodes": [{"title": "Tent 2P", "variantTitle": "Green", "quantity": 1}]},
                "fulfillments": [{"status": "SUCCESS", "estimatedDeliveryAt": "2026-11-05T00:00:00Z",
                                  "deliveredAt": None,
                                  "trackingInfo": [{"company": "DHL", "number": "123", "url": "https://dhl/123"}]}],
            }]}}})
        raise AssertionError(query)

    shop = shopify(handler, faq=str(faq))
    assert shop.name == "Real Shop" and shop.currency == "EUR"
    found = shop.search_products("tent for two", max_price=300)
    assert found[0]["handle"] == "tent-2p" and found[0]["default_variant_id"] == "77"
    assert found[0]["compare_at_price"] is None
    product = shop.get_product("tent-2p")
    assert product["variants"][0]["stock"] == "only 2 left" and product["description"] == "A tent."
    assert shop.get_policy("returns")["body"] == "Returns within 14 days."
    assert shop.get_policy("size_guide")["body"] == "Tents fit 2 people."
    assert shop.get_policy("warranty") is None
    order = shop.find_order("2001", "Buyer@Example.com")
    assert order["tracking"]["company"] == "DHL" and order["estimated_delivery"] == "2026-11-05"
    assert shop.find_order("2001", "other@example.com") is None


def test_shopify_errors():
    with pytest.raises(StoreError, match="401"):
        shopify(lambda q, v: httpx.Response(401, json={}))
    with pytest.raises(StoreError, match="Throttled"):
        shopify(lambda q, v: httpx.Response(200, json={"errors": [{"message": "Throttled"}]}))
    with pytest.raises(ValueError):
        ShopifyStore(ShopifyConfig(domain="evil.com/x", token="t"))


def test_faq_file(tmp_path):
    path = tmp_path / "faq.md"
    path.write_text("# FAQ\n\n## Warranty\nTwo years.\n\n## Empty\n", encoding="utf-8")
    assert load_faq(path) == {"warranty": {"title": "Warranty", "body": "Two years."}}
    assert load_faq(tmp_path / "missing.md") == {}


def test_conversation_language_is_the_first_real_message():
    from assistant.agent import detect_language
    assert detect_language("Търся яке") == "bg"
    assert detect_language("1001, maria@example.com") is None
    assert detect_language("waterproof jacket") == "en"


def test_shopify_client_credentials():
    calls = {"exchanges": 0, "tokens": []}

    def respond(request):
        if request.url.path == "/admin/oauth/access_token":
            calls["exchanges"] += 1
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form == {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "csecret"}
            return httpx.Response(200, json={"access_token": f"tok{calls['exchanges']}", "scope": "read_products",
                                             "expires_in": 86399})
        token = request.headers["X-Shopify-Access-Token"]
        calls["tokens"].append(token)
        if token == "tok1" and len(calls["tokens"]) == 3:  # Shopify revokes the first token
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"data": SHOP})

    config = ShopifyConfig(domain="real-shop.myshopify.com", client_id="cid", client_secret="csecret")
    shop = ShopifyStore(config, http=httpx.Client(transport=httpx.MockTransport(respond)))
    shop._gql("query { shop { name } }")
    assert calls["exchanges"] == 1 and calls["tokens"] == ["tok1", "tok1"]  # the token is reused
    shop._gql("query { shop { name } }")  # 401, then one new token and a retry
    assert calls["exchanges"] == 2 and calls["tokens"][-1] == "tok2"


def test_shopify_client_credentials_errors():
    def wrong_org(request):
        return httpx.Response(400, json={"error": "shop_not_permitted"})

    config = ShopifyConfig(domain="real-shop.myshopify.com", client_id="cid", client_secret="bad")
    with pytest.raises(StoreError, match="same Shopify organization"):
        ShopifyStore(config, http=httpx.Client(transport=httpx.MockTransport(wrong_org)))
    with pytest.raises(StoreError, match="SHOPIFY_CLIENT_ID"):
        ShopifyStore(config, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(ValueError, match="SHOPIFY_CLIENT_ID"):
        ShopifyStore(ShopifyConfig(domain="real-shop.myshopify.com"))
