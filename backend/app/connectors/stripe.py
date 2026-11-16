"""Stripe: charges, invoices and subscriptions, synced as tables (read-only restricted key)."""
from collections.abc import Iterator
from datetime import datetime, timezone

from .base import Doc, request, table_doc

LABEL = "Stripe"
BLURB = "Payments, invoices and subscriptions from Stripe, ready to analyze revenue and churn."
CATEGORY = "business"
OAUTH = None
PASTE = {"label": "Restricted API key", "placeholder": "rk_live_…",
         "help": "Stripe dashboard → Developers → API keys → Create restricted key (Read access only)"}
GUIDE = [
    "Stripe dashboard → Developers → API keys → Create restricted key.",
    "Give it Read access to Charges, Invoices, Subscriptions and Customers, and None to everything else. Don't use your secret key.",
    "Paste the rk_live_… key below.",
]
CONFIG = []
API = "https://api.stripe.com/v1/"
ZERO_DECIMAL = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}


def _list(creds: dict, path: str, **params) -> Iterator[dict]:
    after = None
    while True:
        data = request("GET", API + path, params={"limit": 100, **params, **({"starting_after": after} if after else {})},
                       headers={"Authorization": f"Bearer {creds['access_token']}"}).json()
        yield from data["data"]
        if not data.get("has_more") or not data["data"]:
            return
        after = data["data"][-1]["id"]


def whoami(creds: dict) -> str:
    # a restricted key can't always read /v1/account; listing one charge proves the key and its read access
    next(_list(creds, "charges", limit=1), None)
    return "Stripe account"


def _amt(cents: int | None, currency: str | None) -> float | None:
    if cents is None:
        return None
    return cents if (currency or "").lower() in ZERO_DECIMAL else cents / 100


def _ts(epoch: int | None) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    yield table_doc("stripe:charges", "Stripe charges", ["charge_id", "created_at", "status", "paid", "amount", "amount_refunded",
                                                        "currency", "customer", "description", "payment_method_type"],
                    ({"charge_id": c["id"], "created_at": _ts(c.get("created")), "status": c.get("status"), "paid": c.get("paid"),
                      "amount": _amt(c.get("amount"), c.get("currency")), "amount_refunded": _amt(c.get("amount_refunded"), c.get("currency")),
                      "currency": (c.get("currency") or "").upper(), "customer": c.get("customer"), "description": c.get("description"),
                      "payment_method_type": (c.get("payment_method_details") or {}).get("type")} for c in _list(creds, "charges")))
    yield table_doc("stripe:invoices", "Stripe invoices", ["invoice_id", "number", "created_at", "due_date", "status", "customer",
                                                          "customer_name", "subtotal", "total", "amount_paid", "amount_due", "currency"],
                    ({"invoice_id": i["id"], "number": i.get("number"), "created_at": _ts(i.get("created")), "due_date": _ts(i.get("due_date")),
                      "status": i.get("status"), "customer": i.get("customer"), "customer_name": i.get("customer_name"),
                      "subtotal": _amt(i.get("subtotal"), i.get("currency")), "total": _amt(i.get("total"), i.get("currency")),
                      "amount_paid": _amt(i.get("amount_paid"), i.get("currency")), "amount_due": _amt(i.get("amount_due"), i.get("currency")),
                      "currency": (i.get("currency") or "").upper()} for i in _list(creds, "invoices")))

    def subscriptions() -> Iterator[dict]:
        for s in _list(creds, "subscriptions", status="all"):
            for item in (s.get("items") or {}).get("data", []):
                price = item.get("price") or {}
                rec = price.get("recurring") or {}
                yield {"subscription_id": s["id"], "customer": s.get("customer"), "status": s.get("status"),
                       "created_at": _ts(s.get("created")), "canceled_at": _ts(s.get("canceled_at")),
                       "cancel_at_period_end": s.get("cancel_at_period_end"), "product": price.get("product"),
                       "unit_amount": _amt(price.get("unit_amount"), price.get("currency")), "quantity": item.get("quantity"),
                       "interval": rec.get("interval"), "interval_count": rec.get("interval_count"),
                       "currency": (price.get("currency") or "").upper()}

    yield table_doc("stripe:subscriptions", "Stripe subscriptions", ["subscription_id", "customer", "status", "created_at", "canceled_at",
                                                                    "cancel_at_period_end", "product", "unit_amount", "quantity",
                                                                    "interval", "interval_count", "currency"], subscriptions())
