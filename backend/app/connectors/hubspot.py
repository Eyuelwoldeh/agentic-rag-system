"""HubSpot CRM: deals, companies and contacts, synced as tables (read-only)."""
from collections.abc import Iterator
from datetime import datetime

from .base import Doc, request, table_doc

LABEL = "HubSpot"
BLURB = "Your sales pipeline, companies and contacts from HubSpot, ready to analyze."
CATEGORY = "business"
SCOPES = ["crm.objects.deals.read", "crm.objects.companies.read", "crm.objects.contacts.read", "crm.objects.owners.read"]
OAUTH = {
    "authorize_url": "https://app.hubspot.com/oauth/authorize",
    "token_url": "https://api.hubapi.com/oauth/v1/token",
    "scopes": SCOPES,
    "console": "https://developers.hubspot.com/",
    "note": "Create a public app with only the CRM read scopes listed here.",
}
PASTE = {"label": "Private app access token", "placeholder": "pat-…",
         "help": "HubSpot → Settings → Integrations → Private apps → your app → Access token"}
GUIDE = [
    "HubSpot → Settings → Integrations → Private apps → Create a private app.",
    "Scopes: crm.objects.deals.read, crm.objects.companies.read, crm.objects.contacts.read, crm.objects.owners.read. Nothing else.",
    "Create the app, copy its access token and paste it below.",
]
CONFIG = []
API = "https://api.hubapi.com/"

DEALS = ["dealname", "pipeline", "dealstage", "amount", "closedate", "createdate", "hubspot_owner_id", "hs_is_closed_won", "hs_is_closed"]
COMPANIES = ["name", "domain", "industry", "city", "country", "numberofemployees", "annualrevenue", "lifecyclestage", "createdate", "hubspot_owner_id"]
CONTACTS = ["firstname", "lastname", "email", "company", "lifecyclestage", "createdate", "hubspot_owner_id"]  # no phone/address: not needed


def _get(creds: dict, path: str, **params) -> dict:
    return request("GET", API + path, params=params, headers={"Authorization": f"Bearer {creds['access_token']}"}).json()


def _all(creds: dict, path: str, **params) -> Iterator[dict]:
    after = None
    while True:
        data = _get(creds, path, limit=100, **params, **({"after": after} if after else {}))
        yield from data.get("results", [])
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return


def whoami(creds: dict) -> str:
    return f"HubSpot portal {_get(creds, 'account-info/v3/details')['portalId']}"


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    owners = {o["id"]: " ".join(filter(None, [o.get("firstName"), o.get("lastName")])) or o.get("email")
              for o in _all(creds, "crm/v3/owners")}
    pipelines, stages = {}, {}
    for p in _get(creds, "crm/v3/pipelines/deals").get("results", []):
        pipelines[p["id"]] = p["label"]
        stages.update({s["id"]: s["label"] for s in p.get("stages", [])})

    def rows(obj: str, props: list[str]) -> Iterator[dict]:
        for r in _all(creds, f"crm/v3/objects/{obj}", properties=",".join(props), archived="false"):
            p = r.get("properties") or {}
            yield {"id": r["id"], **{k: p.get(k) for k in props}, "owner": owners.get(p.get("hubspot_owner_id") or "")}

    def deals() -> Iterator[dict]:
        for d in rows("deals", DEALS):
            yield {**d, "pipeline": pipelines.get(d["pipeline"], d["pipeline"]), "stage": stages.get(d["dealstage"], d["dealstage"]),
                   "won": d["hs_is_closed_won"], "closed": d["hs_is_closed"]}

    yield table_doc("hubspot:deals", "HubSpot deals", ["id", "dealname", "pipeline", "stage", "amount", "closedate", "createdate",
                                                      "owner", "won", "closed"], deals())
    yield table_doc("hubspot:companies", "HubSpot companies", ["id", *[c for c in COMPANIES if c != "hubspot_owner_id"], "owner"],
                    rows("companies", COMPANIES))
    yield table_doc("hubspot:contacts", "HubSpot contacts", ["id", *[c for c in CONTACTS if c != "hubspot_owner_id"], "owner"],
                    rows("contacts", CONTACTS))
