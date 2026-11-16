"""Salesforce: Opportunities and Accounts via SOQL, synced as tables (read-only)."""
import re
from collections.abc import Iterator
from datetime import datetime

from .base import Doc, request, table_doc

LABEL = "Salesforce"
BLURB = "Opportunities, pipeline and accounts from Salesforce, ready to analyze."
CATEGORY = "business"
OAUTH = {
    "authorize_url": "https://login.salesforce.com/services/oauth2/authorize",
    "token_url": "https://login.salesforce.com/services/oauth2/token",
    "scopes": ["api", "refresh_token"],
    "keep": ["instance_url"],  # every API call goes to the org's own host
    "expires_in": 3600,  # Salesforce doesn't say; refresh hourly (well inside the default 2h session timeout)
    "console": "https://login.salesforce.com/lightning/setup/NavigationMenus/home",
    "note": "Setup → App Manager → New External Client App (or Connected App) with OAuth scopes api and refresh_token, PKCE on.",
}
PASTE = None
GUIDE = [
    "Salesforce Setup → App Manager → New Connected App (or External Client App). Enable OAuth, add the callback URL from Settings → OAuth apps.",
    "OAuth scopes: 'Manage user data via APIs (api)' and 'Perform requests at any time (refresh_token)'. Require PKCE.",
    "Salesforce has no read-only API scope: connect as an integration user whose profile can only read Opportunities and Accounts.",
]
CONFIG = []
VERSION = "v62.0"
INSTANCE = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.(salesforce|force)\.com")

OPPORTUNITIES = ["Id", "Name", "StageName", "Amount", "CloseDate", "Probability", "ForecastCategoryName", "IsClosed", "IsWon",
                 "Type", "LeadSource", "CreatedDate", "Account.Name", "Owner.Name"]
ACCOUNTS = ["Id", "Name", "Type", "Industry", "AnnualRevenue", "NumberOfEmployees", "BillingCity", "BillingCountry",
            "CreatedDate", "Owner.Name"]


def _instance(creds: dict) -> str:
    url = (creds.get("instance_url") or "").rstrip("/").lower()
    if not INSTANCE.fullmatch(url):  # it came from the token response, but never send the token anywhere else
        raise ValueError("Unexpected Salesforce instance URL. Reconnect this source.")
    return url


def _headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds['access_token']}"}


def whoami(creds: dict) -> str:
    me = request("GET", f"{_instance(creds)}/services/oauth2/userinfo", headers=_headers(creds)).json()
    return me.get("preferred_username") or me.get("email") or "Salesforce"


def _soql(creds: dict, fields: list[str], obj: str) -> Iterator[dict]:
    base = _instance(creds)
    data = request("GET", f"{base}/services/data/{VERSION}/query", params={"q": f"SELECT {', '.join(fields)} FROM {obj}"},
                   headers=_headers(creds)).json()
    while True:
        for r in data["records"]:
            row = {}
            for f in fields:  # "Account.Name" → nested relationship object
                val = r
                for part in f.split("."):
                    val = (val or {}).get(part)
                row[f.replace(".", "")] = val
            yield row
        if data.get("done", True) or not data.get("nextRecordsUrl", "").startswith("/services/data/"):
            return
        data = request("GET", base + data["nextRecordsUrl"], headers=_headers(creds)).json()


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    yield table_doc("salesforce:opportunities", "Salesforce opportunities", [f.replace(".", "") for f in OPPORTUNITIES],
                    _soql(creds, OPPORTUNITIES, "Opportunity"))
    yield table_doc("salesforce:accounts", "Salesforce accounts", [f.replace(".", "") for f in ACCOUNTS], _soql(creds, ACCOUNTS, "Account"))
