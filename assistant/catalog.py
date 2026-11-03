"""
Where the assistant's facts come from: products, store policies and orders.

Two implementations of the same interface:

- DemoStore reads data/demo_store.json, a fictional outdoor shop, so the whole
  app runs without a Shopify store.
- ShopifyStore reads a real store through the Shopify Admin GraphQL API with a
  custom-app access token (SHOPIFY_STORE_DOMAIN + SHOPIFY_ADMIN_TOKEN).

Both return the same plain dicts, so the tools, the model and the widget never
know which one they are talking to.
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

SHOP_REFRESH_SECONDS = 15 * 60
POLICY_TOPICS = ("shipping", "returns", "payment", "warranty", "size_guide", "contact", "discounts")
LOW_STOCK = 3

# Shoppers write in their own words and language. These map common words (BG and EN)
# to the words the catalog uses, so search works in demo mode without a model in front.
SYNONYMS = {
    "яке": "jacket", "якета": "jacket", "шуба": "jacket", "пухено": "down", "пухенка": "down",
    "дъжд": "rain", "дъждобран": "rain", "водоустойчиво": "waterproof", "водоустойчиви": "waterproof",
    "водоустойчив": "waterproof", "непромокаемо": "waterproof", "топло": "warm", "топла": "warm",
    "зима": "winter", "зимно": "winter", "студ": "cold", "поларено": "fleece", "полар": "fleece",
    "блуза": "midlayer", "термо": "thermal", "бельо": "base layer", "вълна": "wool", "мерино": "merino",
    "обувки": "shoes", "обувка": "shoes", "боти": "boots", "туристически": "hiking", "бягане": "running",
    "маратонки": "trainers", "раница": "backpack", "раници": "backpack", "чанта": "bag",
    "лаптоп": "laptop", "бутилка": "bottle", "термос": "thermos", "шапка": "beanie", "шапки": "beanie",
    "челник": "headlamp", "фенер": "headlamp", "щеки": "poles", "щека": "poles", "подарък": "gift",
    "палатка": "camping", "къмпинг": "camping", "планина": "mountain", "преход": "hiking",
    "trainers": "running", "sneakers": "running", "torch": "headlamp", "flashlight": "headlamp",
    "rucksack": "backpack", "puffer": "down", "raincoat": "rain", "coat": "jacket", "hat": "beanie",
    "flask": "bottle",
}


STOPWORDS = {
    "i", "a", "an", "the", "for", "and", "or", "with", "me", "my", "need", "want", "looking", "search", "some",
    "something", "do", "you", "have", "any", "under", "below", "up", "to", "max", "eur", "euro", "euros",
    "търся", "искам", "трябва", "ми", "за", "и", "с", "на", "в", "до", "под", "имате", "ли", "някакво",
    "някаква", "някакви", "нещо", "евро", "лв", "моля",
}


class StoreError(RuntimeError):
    """The store could not be reached or returned an error."""


class Store(Protocol):
    name: str
    currency: str

    def search_products(self, query: str, category: str | None = None, max_price: float | None = None,
                        in_stock_only: bool = False, limit: int = 6) -> list[dict]: ...

    def get_product(self, handle: str) -> dict | None: ...

    def get_policy(self, topic: str) -> dict | None: ...

    def find_order(self, order_number: str, email: str) -> dict | None: ...

    def categories(self) -> list[str]: ...


def normalize_order_number(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value or "").upper()


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.findall(r"[\w']+", text)


def _stock_label(quantity: int | None, available: bool) -> str:
    if not available or (quantity is not None and quantity <= 0):
        return "sold out"
    if quantity is not None and quantity <= LOW_STOCK:
        return f"only {quantity} left"
    return "in stock"


# ---------- demo store ----------


class DemoStore:
    """The fictional Nordvik Outdoor shop from data/demo_store.json."""

    def __init__(self, path: str | Path | None = None):
        path = Path(path or Path(__file__).resolve().parent.parent / "data" / "demo_store.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.name = data["shop"]["name"]
        self.currency = data["shop"]["currency"]
        self.shop = data["shop"]
        self._products = {p["handle"]: p for p in data["products"]}
        self._policies = data["policies"]
        self._policies_bg = data.get("policies_bg", {})  # only for demo mode's canned Bulgarian answers
        self._orders = {normalize_order_number(o["number"]): o for o in data["orders"]}

    def policy_bg(self, topic: str) -> str:
        return self._policies_bg.get(topic) or self._policies[topic]["body"]

    def categories(self) -> list[str]:
        return sorted({p["category"] for p in self._products.values()})

    def all_products(self) -> list[dict]:
        return [self._summary(p) for p in self._products.values()]

    def search_products(self, query, category=None, max_price=None, in_stock_only=False, limit=6):
        terms = set()
        for word in _words(query):
            if word.isdigit() or word in STOPWORDS:
                continue
            terms.add(word)
            if word in SYNONYMS:
                terms.update(_words(SYNONYMS[word]))
            if word.endswith("s") and len(word) > 3:
                terms.add(word[:-1])
        scored = []
        for product in self._products.values():
            if category and product["category"].lower() != category.lower():
                continue
            if max_price is not None and product["price"] > max_price:
                continue
            summary = self._summary(product)
            if in_stock_only and not summary["in_stock"]:
                continue
            title = set(_words(product["title"]))
            tags = set(_words(" ".join(product["tags"] + [product["category"]])))
            body = set(_words(product["description"]))
            score = sum(3 * (t in title) + 2 * (t in tags) + (t in body) for t in terms)
            if terms and not score:
                continue
            scored.append((score, product["rating"], summary))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        if scored and terms:  # drop weak matches: one shared word like "waterproof" isn't enough
            best = scored[0][0]
            scored = [s for s in scored if s[0] >= best * 0.6]
        return [s[2] for s in scored[:limit]]

    def get_product(self, handle):
        product = self._products.get((handle or "").strip().lower())
        if not product:
            return None
        variants = []
        for v in product["variants"]:
            title = " / ".join(v["options"].values()) or "Default"
            variants.append({
                "id": v["id"], "title": title, "options": v["options"], "sku": v["sku"],
                "price": product["price"], "available": v["inventory"] > 0,
                "stock": _stock_label(v["inventory"], v["inventory"] > 0),
            })
        return {
            **self._summary(product),
            "description": product["description"],
            "features": product["features"],
            "materials": product["materials"],
            "care": product["care"],
            "fit": product["fit"],
            "options": product["options"],
            "variants": variants,
        }

    def get_policy(self, topic):
        policy = self._policies.get(topic)
        return {"topic": topic, **policy} if policy else None

    def find_order(self, order_number, email):
        order = self._orders.get(normalize_order_number(order_number))
        # Same answer for "no such order" and "wrong email", so nobody can probe order numbers.
        if not order or order["email"].lower() != (email or "").strip().lower():
            return None
        return {
            "number": f"#{order['number']}",
            "customer_first_name": order["customer_first_name"],
            "created_at": order["created_at"],
            "financial_status": order["financial_status"],
            "fulfillment_status": order["fulfillment_status"],
            "total": order["total"],
            "currency": self.currency,
            "items": order["items"],
            "shipping_method": order["shipping_method"],
            "tracking": order.get("tracking"),
            **{k: order[k] for k in ("estimated_delivery", "delivered_at", "refunded_at") if k in order},
        }

    def _summary(self, p: dict) -> dict:
        available = any(v["inventory"] > 0 for v in p["variants"])
        return {
            "handle": p["handle"],
            "title": p["title"],
            "category": p["category"],
            "price": p["price"],
            "compare_at_price": p["compare_at_price"],
            "currency": self.currency,
            "in_stock": available,
            "rating": p["rating"],
            "reviews": p["reviews"],
            "image": f"/static/products/{p['handle']}.jpg",
            "url": f"/products/{p['handle']}",
            "default_variant_id": next((v["id"] for v in p["variants"] if v["inventory"] > 0),
                                       p["variants"][0]["id"]),
        }


# ---------- real Shopify store ----------

PRODUCT_FIELDS = """
  handle title productType tags onlineStoreUrl description
  featuredMedia { preview { image { url } } }
  priceRangeV2 { minVariantPrice { amount currencyCode } }
  compareAtPriceRange { minVariantCompareAtPrice { amount } }
  totalInventory tracksInventory
"""

SEARCH_QUERY = """
query Search($query: String!, $first: Int!) {
  products(first: $first, query: $query, sortKey: RELEVANCE) {
    nodes { %s variants(first: 1) { nodes { id availableForSale } } }
  }
}
""" % PRODUCT_FIELDS

PRODUCT_QUERY = """
query Product($handle: String!) {
  product: productByIdentifier(identifier: {handle: $handle}) {
    %s descriptionHtml
    options { name }
    variants(first: 100) {
      nodes { id title sku price availableForSale inventoryQuantity selectedOptions { name value } }
    }
  }
}
""" % PRODUCT_FIELDS

POLICY_QUERY = "query { shop { name currencyCode shopPolicies { type title body } } }"

# The product types of the catalog, so the assistant knows what the store sells.
CATEGORY_QUERY = 'query Categories { products(first: 250, query: "status:active") { nodes { productType } } }'

ORDER_QUERY = """
query Order($query: String!) {
  orders(first: 1, query: $query) {
    nodes {
      name email createdAt displayFinancialStatus displayFulfillmentStatus
      customer { firstName }
      totalPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 50) { nodes { title variantTitle quantity } }
      fulfillments(first: 10) { status estimatedDeliveryAt deliveredAt trackingInfo { company number url } }
    }
  }
}
"""

# Shopify's policy types for our topics. The rest (size guide, warranty...) come from the FAQ file.
SHOPIFY_POLICY_TYPES = {
    "shipping": "SHIPPING_POLICY",
    "returns": "REFUND_POLICY",
    "contact": "CONTACT_INFORMATION",
    "payment": "TERMS_OF_SERVICE",
}


def load_faq(path: str | Path | None) -> dict[str, dict]:
    """Extra knowledge in Markdown: one '## topic' section per entry (topics as in POLICY_TOPICS)."""
    if not path or not Path(path).exists():
        return {}
    sections: dict[str, dict] = {}
    current = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(1)
            current = re.sub(r"\W+", "_", title.lower()).strip("_")
            sections[current] = {"title": title, "body": ""}
        elif current:
            sections[current]["body"] += line + "\n"
    return {k: {"title": v["title"], "body": v["body"].strip()} for k, v in sections.items() if v["body"].strip()}


def _strip_html(html: str) -> str:
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@dataclass
class ShopifyConfig:
    domain: str              # your-store.myshopify.com
    # A Dev Dashboard app's credentials. The store exchanges them for an access token that lasts 24 hours.
    client_id: str = ""
    client_secret: str = ""
    # Or the permanent token of an older admin-created custom app (shpat_...), made before 2026.
    token: str = ""
    api_version: str = "2026-10"
    faq_path: str | None = None


class ShopifyStore:
    """A real store, read through the Admin GraphQL API (read-only scopes are enough)."""

    def __init__(self, config: ShopifyConfig, http: httpx.Client | None = None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", config.domain):
            raise ValueError("SHOPIFY_STORE_DOMAIN must look like your-store.myshopify.com")
        if not config.token and not (config.client_id and config.client_secret):
            raise ValueError("Set SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET (or SHOPIFY_ADMIN_TOKEN)")
        self.config = config
        self.http = http or httpx.Client(timeout=10.0)
        self._token = config.token
        self._token_expires = float("inf") if config.token else 0.0
        self._token_lock = threading.Lock()
        self.url = f"https://{config.domain}/admin/api/{config.api_version}/graphql.json"
        self.faq = load_faq(config.faq_path)
        self._load_shop()

    def _load_shop(self):
        """The shop's name, policies and product types. Re-read every 15 minutes, so edits in Shopify
        reach the assistant without a restart."""
        shop = self._gql(POLICY_QUERY)["shop"]
        self.name = shop["name"]
        self.currency = shop["currencyCode"]
        self._policies = {p["type"]: p for p in shop["shopPolicies"]}
        nodes = (self._gql(CATEGORY_QUERY).get("products") or {}).get("nodes") or []
        self._categories = sorted({n["productType"] for n in nodes if n.get("productType")})
        self._loaded_at = time.monotonic()

    def _refresh_shop(self):
        if time.monotonic() - self._loaded_at > SHOP_REFRESH_SECONDS:
            try:
                self._load_shop()
            except StoreError:
                self._loaded_at = time.monotonic()  # keep what we have; try again later

    def _access_token(self, refresh: bool = False) -> str:
        """The client credentials grant: a token for 24 hours, renewed 5 minutes before it expires."""
        if self.config.token:
            return self.config.token
        with self._token_lock:
            if refresh or time.monotonic() > self._token_expires - 300:
                try:
                    response = self.http.post(
                        f"https://{self.config.domain}/admin/oauth/access_token",
                        data={"grant_type": "client_credentials", "client_id": self.config.client_id,
                              "client_secret": self.config.client_secret},
                    )
                except httpx.HTTPError as e:
                    raise StoreError(f"Shopify is not reachable: {e}") from e
                if response.status_code >= 400:
                    detail = response.text[:200]
                    if "shop_not_permitted" in detail:
                        raise StoreError("The app is not in the same Shopify organization as this store.")
                    raise StoreError(f"Shopify rejected the app credentials (HTTP {response.status_code}). "
                                     "Check SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET, and that the app is "
                                     "installed on the store.")
                body = response.json()
                self._token = body["access_token"]
                self._token_expires = time.monotonic() + int(body.get("expires_in", 86399))
            return self._token

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        response = self._post_gql(query, variables, self._access_token())
        if response.status_code == 401 and not self.config.token:  # revoked or expired early: one retry
            response = self._post_gql(query, variables, self._access_token(refresh=True))
        if response.status_code == 401:
            raise StoreError("Shopify rejected the access token (401). Check the app's credentials and that "
                             "it is still installed.")
        if response.status_code >= 400:
            raise StoreError(f"Shopify returned HTTP {response.status_code}")
        body = response.json()
        if body.get("errors"):
            raise StoreError("Shopify GraphQL error: " + "; ".join(e.get("message", "?") for e in body["errors"]))
        return body["data"]

    def _post_gql(self, query: str, variables: dict | None, token: str) -> httpx.Response:
        try:
            return self.http.post(
                self.url,
                json={"query": query, "variables": variables or {}},
                headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            )
        except httpx.HTTPError as e:
            raise StoreError(f"Shopify is not reachable: {e}") from e

    def categories(self) -> list[str]:
        return self._categories

    def search_products(self, query, category=None, max_price=None, in_stock_only=False, limit=6):
        search = " ".join(_words(query)[:12])
        parts = [search] if search else []
        if category:
            parts.append(f'product_type:"{category}"')
        if max_price is not None:
            parts.append(f"price:<={max_price:g}")
        parts.append("status:active")
        nodes = self._gql(SEARCH_QUERY, {"query": " ".join(parts), "first": min(limit * 2, 20)})["products"]["nodes"]
        results = [self._summary(n) for n in nodes]
        if in_stock_only:
            results = [r for r in results if r["in_stock"]]
        if max_price is not None:
            results = [r for r in results if r["price"] <= max_price]
        return results[:limit]

    def get_product(self, handle):
        node = self._gql(PRODUCT_QUERY, {"handle": (handle or "").strip().lower()})["product"]
        if not node:
            return None
        variants = []
        for v in node["variants"]["nodes"]:
            qty = v.get("inventoryQuantity") if node.get("tracksInventory") else None
            variants.append({
                "id": v["id"].rsplit("/", 1)[-1],
                "title": v["title"],
                "options": {o["name"]: o["value"] for o in v["selectedOptions"]},
                "sku": v.get("sku") or "",
                "price": float(v["price"]),
                "available": v["availableForSale"],
                "stock": _stock_label(qty, v["availableForSale"]),
            })
        return {
            **self._summary(node, variants),
            "description": _strip_html(node.get("descriptionHtml")) or node.get("description", ""),
            "features": [],
            "materials": "",
            "care": "",
            "fit": "",
            "options": [o["name"] for o in node.get("options", []) if o["name"] != "Title"],
            "variants": variants,
        }

    def get_policy(self, topic):
        self._refresh_shop()
        shopify_type = SHOPIFY_POLICY_TYPES.get(topic)
        policy = self._policies.get(shopify_type) if shopify_type else None
        if policy and policy.get("body"):
            return {"topic": topic, "title": policy["title"], "body": _strip_html(policy["body"])[:6000]}
        if topic in self.faq:
            return {"topic": topic, **self.faq[topic]}
        return None

    def find_order(self, order_number, email):
        number = normalize_order_number(order_number)
        if not number:
            return None
        nodes = self._gql(ORDER_QUERY, {"query": f"name:#{number}"})["orders"]["nodes"]
        order = nodes[0] if nodes else None
        if not order or (order.get("email") or "").lower() != (email or "").strip().lower():
            return None
        tracking, estimated, delivered = None, None, None
        for f in order.get("fulfillments") or []:
            info = (f.get("trackingInfo") or [None])[0]
            if info:
                tracking = {"company": info.get("company"), "number": info.get("number"), "url": info.get("url")}
            estimated = f.get("estimatedDeliveryAt") or estimated
            delivered = f.get("deliveredAt") or delivered
        money = order["totalPriceSet"]["shopMoney"]
        result = {
            "number": order["name"],
            "customer_first_name": (order.get("customer") or {}).get("firstName") or "",
            "created_at": order["createdAt"][:10],
            "financial_status": order["displayFinancialStatus"].lower(),
            "fulfillment_status": order["displayFulfillmentStatus"].lower(),
            "total": float(money["amount"]),
            "currency": money["currencyCode"],
            "items": [{"title": i["title"], "variant": i.get("variantTitle") or "", "quantity": i["quantity"]}
                      for i in order["lineItems"]["nodes"]],
            "tracking": tracking,
        }
        if estimated:
            result["estimated_delivery"] = estimated[:10]
        if delivered:
            result["delivered_at"] = delivered[:10]
        return result

    def _summary(self, node: dict, variants: list[dict] | None = None) -> dict:
        price = node["priceRangeV2"]["minVariantPrice"]
        compare = (node.get("compareAtPriceRange") or {}).get("minVariantCompareAtPrice") or {}
        compare_at = float(compare["amount"]) if compare.get("amount") and float(compare["amount"]) > 0 else None
        image = (((node.get("featuredMedia") or {}).get("preview") or {}).get("image") or {}).get("url")
        if variants is not None:
            in_stock = any(v["available"] for v in variants)
            default_id = next((v["id"] for v in variants if v["available"]), variants[0]["id"] if variants else "")
        else:
            first = (node.get("variants") or {}).get("nodes") or [{}]
            in_stock = bool(first[0].get("availableForSale")) or (node.get("totalInventory") or 0) > 0
            default_id = (first[0].get("id") or "").rsplit("/", 1)[-1]
        return {
            "handle": node["handle"],
            "title": node["title"],
            "category": node.get("productType") or "",
            "price": float(price["amount"]),
            "compare_at_price": compare_at,
            "currency": price["currencyCode"],
            "in_stock": in_stock,
            "rating": None,
            "reviews": None,
            "image": image,
            "url": node.get("onlineStoreUrl") or f"https://{self.config.domain}/products/{node['handle']}",
            "default_variant_id": default_id,
        }
