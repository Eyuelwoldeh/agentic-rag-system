"""Square: payments by location and the locations list, synced as tables (read-only)."""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from .base import Doc, request, table_doc

LABEL = "Square"
BLURB = "Point-of-sale and online payments by store location, ready to analyze."
CATEGORY = "business"
OAUTH = None
PASTE = {"label": "Access token", "placeholder": "EAAA…",
         "help": "developer.squareup.com → your application → Credentials → Production access token"}
GUIDE = [
    "developer.squareup.com → Applications → create an application (no code needed).",
    "Open Credentials → Production and copy the access token. It can read the account's payments and locations; the app only reads.",
    "Paste it below. For a least-privilege token, use an OAuth token limited to PAYMENTS_READ and MERCHANT_PROFILE_READ instead.",
]
CONFIG = []
API = "https://connect.squareup.com/v2/"
VERSION = "2026-08-19"
LOOKBACK_DAYS = 730


def _get(creds: dict, path: str, **params) -> dict:
    return request("GET", API + path, params=params,
                   headers={"Authorization": f"Bearer {creds['access_token']}", "Square-Version": VERSION}).json()


def whoami(creds: dict) -> str:
    return _get(creds, "merchants/me")["merchant"].get("business_name") or "Square"


def _amount(money: dict | None) -> float | None:
    if not money or money.get("amount") is None:
        return None
    return money["amount"] / (1 if money.get("currency") == "JPY" else 100)  # smallest unit → major (yen has no cents)


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    locations = _get(creds, "locations").get("locations", [])
    yield table_doc("square:locations", "Square locations", ["location_id", "name", "status", "city", "country", "currency", "timezone"],
                    ({"location_id": loc["id"], "name": loc.get("name"), "status": loc.get("status"),
                      "city": (loc.get("address") or {}).get("locality"), "country": loc.get("country"),
                      "currency": loc.get("currency"), "timezone": loc.get("timezone")} for loc in locations))
    names = {loc["id"]: loc.get("name") for loc in locations}
    begin = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def payments() -> Iterator[dict]:
        for loc in names:  # ListPayments reads one location per call
            cursor = None
            while True:
                data = _get(creds, "payments", location_id=loc, begin_time=begin, limit=100, **({"cursor": cursor} if cursor else {}))
                for p in data.get("payments", []):
                    yield {"payment_id": p["id"], "created_at": p.get("created_at"), "location_id": loc, "location": names[loc],
                           "status": p.get("status"), "source": p.get("source_type"),
                           "card_brand": ((p.get("card_details") or {}).get("card") or {}).get("card_brand"),
                           "amount": _amount(p.get("amount_money")), "tip": _amount(p.get("tip_money")),
                           "total": _amount(p.get("total_money")), "refunded": _amount(p.get("refunded_money")),
                           "currency": (p.get("total_money") or p.get("amount_money") or {}).get("currency"),
                           "order_id": p.get("order_id")}
                cursor = data.get("cursor")
                if not cursor:
                    break

    yield table_doc("square:payments", "Square payments", ["payment_id", "created_at", "location_id", "location", "status", "source",
                                                           "card_brand", "amount", "tip", "total", "refunded", "currency", "order_id"],
                    payments())
