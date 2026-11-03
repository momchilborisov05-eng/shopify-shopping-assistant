"""
Checks the connection to a real Shopify store, without Claude and without printing any secret.

    python -m assistant.check_shopify

Reads SHOPIFY_STORE_DOMAIN and SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET (or SHOPIFY_ADMIN_TOKEN)
from .env, then shows what the assistant will see: the shop, its policies, a few products and
whether orders can be read.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .catalog import POLICY_TOPICS, ShopifyConfig, ShopifyStore, StoreError

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    load_dotenv(ROOT / ".env")
    domain = os.environ.get("SHOPIFY_STORE_DOMAIN", "")
    if not domain:
        print("SHOPIFY_STORE_DOMAIN is not set in .env")
        return 1
    config = ShopifyConfig(
        domain=domain,
        client_id=os.environ.get("SHOPIFY_CLIENT_ID", ""),
        client_secret=os.environ.get("SHOPIFY_CLIENT_SECRET", ""),
        token=os.environ.get("SHOPIFY_ADMIN_TOKEN", ""),
        api_version=os.environ.get("SHOPIFY_API_VERSION", "2026-10"),
        faq_path=os.environ.get("FAQ_PATH", str(ROOT / "config" / "faq.md")),
    )
    try:
        store = ShopifyStore(config)
        print(f"Connected to {store.name} ({domain}), currency {store.currency}")
        policies = [t for t in POLICY_TOPICS if store.get_policy(t)]
        print("Policies the assistant can answer from:", ", ".join(policies) or "none (add them in Shopify or faq.md)")
        products = store.search_products("", limit=5)
        print(f"Products (first {len(products)}):")
        for p in products:
            stock = "in stock" if p["in_stock"] else "sold out"
            print(f"  - {p['title']}: {p['price']:.2f} {p['currency']}, {stock}")
        store.find_order("1001", "nobody@example.com")
        print("Orders: readable")
    except (ValueError, StoreError) as e:
        print(f"Not connected: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
