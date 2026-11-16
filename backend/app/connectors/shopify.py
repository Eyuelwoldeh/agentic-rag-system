"""Shopify: orders, order lines and products/variants via the GraphQL Admin API, synced as tables (read-only)."""
import time
from collections.abc import Iterator
from datetime import datetime

from .base import MAX_ROWS, Doc, check, request, table_doc

LABEL = "Shopify"
BLURB = "Orders, sales by channel and location, and your product catalog, ready to analyze."
CATEGORY = "business"
OAUTH = None
PASTE = {"label": "Admin API access token", "placeholder": "shpat_…",
         "help": "Shopify admin → Settings → Apps → Develop apps → your app → API credentials"}
GUIDE = [
    "Shopify admin → Settings → Apps and sales channels → Develop apps → Create an app.",
    "Configure Admin API scopes: read_orders and read_products only (add read_all_orders to include orders older than 60 days).",
    "Install the app, reveal the Admin API access token once, and paste it below with your shop domain.",
]
CONFIG = [{"key": "shop", "label": "Shop domain", "help": "yourstore.myshopify.com", "required": True}]
VERSION = "2026-10"

ORDERS = """query($cursor: String) { orders(first: 30, after: $cursor, sortKey: CREATED_AT) {
  pageInfo { hasNextPage endCursor }
  nodes { id name createdAt displayFinancialStatus displayFulfillmentStatus currencyCode sourceName
    channelInformation { channelDefinition { channelName } } retailLocation { name }
    subtotalPriceSet { shopMoney { amount } } totalDiscountsSet { shopMoney { amount } } totalTaxSet { shopMoney { amount } }
    totalShippingPriceSet { shopMoney { amount } } totalPriceSet { shopMoney { amount } } totalRefundedSet { shopMoney { amount } }
    lineItems(first: 25) { nodes { name sku quantity vendor
      originalTotalSet { shopMoney { amount } } discountedTotalSet { shopMoney { amount } } } } } } }"""
PRODUCTS = """query($cursor: String) { products(first: 50, after: $cursor) {
  pageInfo { hasNextPage endCursor }
  nodes { id title productType vendor status createdAt
    variants(first: 50) { nodes { id title sku price compareAtPrice inventoryQuantity } } } } }"""


def _shop(config: dict) -> str:
    return check(config.get("shop", ""), r"[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com", "Shop domain (yourstore.myshopify.com)")


def _gql(config: dict, creds: dict, query: str, variables: dict | None = None) -> dict:
    for attempt in range(6):
        body = request("POST", f"https://{_shop(config)}/admin/api/{VERSION}/graphql.json", json={"query": query, "variables": variables or {}},
                       headers={"X-Shopify-Access-Token": creds["access_token"]}).json()
        errors = body.get("errors") or []
        if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
            time.sleep(2 ** attempt)  # query cost budget refills over a few seconds
            continue
        if errors:
            raise RuntimeError(f"Shopify: {errors[0].get('message')}")
        return body["data"]
    raise RuntimeError("Shopify kept throttling the request")


def _pages(config: dict, creds: dict, query: str, root: str) -> Iterator[dict]:
    cursor = None
    while True:
        conn = _gql(config, creds, query, {"cursor": cursor})[root]
        yield from conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


def verify(config: dict, creds: dict) -> str:
    return _gql(config, creds, "{ shop { name } }")["shop"]["name"]


def _money(obj: dict | None) -> str | None:
    return ((obj or {}).get("shopMoney") or {}).get("amount")


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    lines: list[dict] = []

    def orders() -> Iterator[dict]:
        for o in _pages(config, creds, ORDERS, "orders"):
            oid = o["id"].rsplit("/", 1)[-1]
            for li in o["lineItems"]["nodes"][:MAX_ROWS - len(lines)]:  # ponytail: first 25 lines per order; page lineItems if bigger baskets matter
                lines.append({"order_id": oid, "order": o["name"], "created_at": o["createdAt"], "product": li["name"],
                              "sku": li.get("sku"), "vendor": li.get("vendor"), "quantity": li["quantity"],
                              "gross": _money(li.get("originalTotalSet")), "net": _money(li.get("discountedTotalSet")),
                              "currency": o["currencyCode"]})
            yield {"order_id": oid, "order": o["name"], "created_at": o["createdAt"],
                   "channel": ((o.get("channelInformation") or {}).get("channelDefinition") or {}).get("channelName") or o.get("sourceName"),
                   "location": (o.get("retailLocation") or {}).get("name"), "financial_status": o.get("displayFinancialStatus"),
                   "fulfillment_status": o.get("displayFulfillmentStatus"), "subtotal": _money(o.get("subtotalPriceSet")),
                   "discounts": _money(o.get("totalDiscountsSet")), "shipping": _money(o.get("totalShippingPriceSet")),
                   "tax": _money(o.get("totalTaxSet")), "total": _money(o.get("totalPriceSet")),
                   "refunded": _money(o.get("totalRefundedSet")), "currency": o["currencyCode"]}

    yield table_doc("shopify:orders", "Shopify orders", ["order_id", "order", "created_at", "channel", "location", "financial_status",
                                                         "fulfillment_status", "subtotal", "discounts", "shipping", "tax", "total",
                                                         "refunded", "currency"], orders())
    yield table_doc("shopify:order_lines", "Shopify order lines", ["order_id", "order", "created_at", "product", "sku", "vendor",
                                                                   "quantity", "gross", "net", "currency"], lines)
    yield table_doc("shopify:products", "Shopify products", ["product_id", "variant_id", "product", "variant", "sku", "product_type",
                                                             "vendor", "status", "price", "compare_at_price", "inventory", "created_at"],
                    ({"product_id": p["id"].rsplit("/", 1)[-1], "variant_id": v["id"].rsplit("/", 1)[-1], "product": p["title"],
                      "variant": v.get("title"), "sku": v.get("sku"), "product_type": p.get("productType"), "vendor": p.get("vendor"),
                      "status": p.get("status"), "price": v.get("price"), "compare_at_price": v.get("compareAtPrice"),
                      "inventory": v.get("inventoryQuantity"), "created_at": p.get("createdAt")}
                     for p in _pages(config, creds, PRODUCTS, "products") for v in p["variants"]["nodes"]))
