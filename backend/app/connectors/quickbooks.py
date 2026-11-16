"""QuickBooks Online: monthly Profit and Loss, invoices and expenses, synced as tables (read-only)."""
import re
from collections.abc import Iterator
from datetime import date, datetime

from .base import Doc, request, table_doc

LABEL = "QuickBooks Online"
BLURB = "Monthly profit and loss, invoices and expenses from your books, ready to analyze."
CATEGORY = "business"
OAUTH = {
    "authorize_url": "https://appcenter.intuit.com/connect/oauth2",
    "token_url": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
    "scopes": ["com.intuit.quickbooks.accounting"],
    "token_auth": "basic",
    "callback_params": {"realmId": "realm_id"},  # the company id arrives on the redirect, not in the token response
    "console": "https://developer.intuit.com/app/developer/dashboard",
    "note": "Create an app with the Accounting scope and add the redirect URI under Keys & credentials.",
}
PASTE = None
GUIDE = [
    "developer.intuit.com → Dashboard → Create an app → QuickBooks Online and Payments, scope: Accounting only.",
    "Under Keys & credentials (Production), add the redirect URI shown in Settings → OAuth apps, then save the Client ID and Secret there.",
    "Click Connect and sign in as a QuickBooks user who can see reports. The app only reads data; it never writes to your books.",
]
CONFIG = [{"key": "environment", "label": "Environment", "help": "Optional. Type sandbox to use a QuickBooks developer sandbox company."}]
MINOR_VERSION = "75"
MONTHS = 24
PAGE = 1000


def _base(config: dict, creds: dict) -> str:
    realm = str(creds.get("realm_id") or "")
    if not re.fullmatch(r"\d{1,20}", realm):
        raise ValueError("QuickBooks company id (realmId) is missing. Reconnect this source.")
    host = "sandbox-quickbooks.api.intuit.com" if (config or {}).get("environment", "").lower() == "sandbox" else "quickbooks.api.intuit.com"
    return f"https://{host}/v3/company/{realm}/"


def _get(config: dict, creds: dict, path: str, **params) -> dict:
    return request("GET", _base(config, creds) + path, params={"minorversion": MINOR_VERSION, **params},
                   headers={"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json"}).json()


def verify(config: dict, creds: dict) -> str:
    return _get(config, creds, f"companyinfo/{creds.get('realm_id')}")["CompanyInfo"]["CompanyName"]


def _query(config: dict, creds: dict, entity: str) -> Iterator[dict]:
    start = 1
    while True:
        rows = _get(config, creds, "query", query=f"select * from {entity} startposition {start} maxresults {PAGE}")
        batch = rows.get("QueryResponse", {}).get(entity, [])
        yield from batch
        if len(batch) < PAGE:
            return
        start += PAGE


def _ref(obj: dict | None) -> str | None:
    return (obj or {}).get("name") or (obj or {}).get("value")


def _pnl_rows(rows: list[dict], months: list[str | None], section: str = "") -> Iterator[dict]:
    """Flatten the nested report into long format: one row per (account, month)."""
    for r in rows:
        title = (r.get("Header", {}).get("ColData") or [{}])[0].get("value") or section
        if "Rows" in r:
            yield from _pnl_rows(r["Rows"].get("Row", []), months, title)
        for cols, kind in ((r.get("ColData"), "account"), (r.get("Summary", {}).get("ColData"), "total")):
            if not cols:
                continue
            for month, cell in zip(months, cols[1:]):
                if month and cell.get("value") not in (None, ""):
                    yield {"section": section if kind == "account" else title, "account": cols[0].get("value"),
                           "line_type": kind, "month": month, "amount": cell["value"]}


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    # Tables are replaced on every sync, so `since` is ignored: each dataset is re-exported in full.
    today = date.today()
    y, m = divmod(today.year * 12 + today.month - 1 - (MONTHS - 1), 12)
    report = _get(config, creds, "reports/ProfitAndLoss", start_date=date(y, m + 1, 1).isoformat(),
                  end_date=today.isoformat(), summarize_column_by="Month", accounting_method="Accrual")
    months = [next((md["Value"] for md in c.get("MetaData", []) if md.get("Name") == "StartDate"), None)
              for c in report.get("Columns", {}).get("Column", [])[1:]]  # first column is the account name; "Total" has no StartDate
    yield table_doc("quickbooks:pnl", "QuickBooks profit and loss by month", ["section", "account", "line_type", "month", "amount"],
                    _pnl_rows(report.get("Rows", {}).get("Row", []), months))

    yield table_doc("quickbooks:invoices", "QuickBooks invoices",
                    ["invoice_id", "number", "date", "due_date", "customer", "total", "balance", "currency"],
                    ({"invoice_id": i["Id"], "number": i.get("DocNumber"), "date": i.get("TxnDate"), "due_date": i.get("DueDate"),
                      "customer": _ref(i.get("CustomerRef")), "total": i.get("TotalAmt"), "balance": i.get("Balance"),
                      "currency": _ref(i.get("CurrencyRef"))} for i in _query(config, creds, "Invoice")))

    def expense(p: dict) -> dict:
        line = next((ln.get("AccountBasedExpenseLineDetail") for ln in p.get("Line", []) if ln.get("AccountBasedExpenseLineDetail")), {})
        return {"purchase_id": p["Id"], "date": p.get("TxnDate"), "payee": _ref(p.get("EntityRef")),
                "category": _ref(line.get("AccountRef")), "paid_from": _ref(p.get("AccountRef")),
                "payment_type": p.get("PaymentType"), "total": p.get("TotalAmt"), "currency": _ref(p.get("CurrencyRef"))}

    yield table_doc("quickbooks:expenses", "QuickBooks expenses",
                    ["purchase_id", "date", "payee", "category", "paid_from", "payment_type", "total", "currency"],
                    (expense(p) for p in _query(config, creds, "Purchase")))
