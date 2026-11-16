"""Xero: invoices and the monthly Profit and Loss report, synced as tables (read-only)."""
import re
from collections.abc import Iterator
from datetime import date, datetime

from .base import Doc, request, table_doc

LABEL = "Xero"
BLURB = "Invoices, bills and monthly profit and loss from Xero, ready to analyze."
CATEGORY = "business"
OAUTH = {
    "authorize_url": "https://login.xero.com/identity/connect/authorize",
    "token_url": "https://identity.xero.com/connect/token",
    # granular read-only scopes (required for apps created after March 2026)
    "scopes": ["offline_access", "accounting.invoices.read", "accounting.reports.profitandloss.read"],
    "token_auth": "basic",
    "console": "https://developer.xero.com/app/manage",
    "note": "Create a Web app and add the redirect URI.",
}
PASTE = None
GUIDE = [
    "developer.xero.com → My Apps → New app → Web app. Add the redirect URI shown in Settings → OAuth apps.",
    "Generate a client secret and save the Client ID and Secret in Settings → OAuth apps.",
    "Click Connect and choose your organisation. Only read-only scopes are requested (invoices and profit and loss).",
]
CONFIG = [{"key": "tenant_id", "label": "Organisation ID", "help": "Optional. Only needed if you connect more than one Xero organisation; blank = the first one."}]
API = "https://api.xero.com/api.xro/2.0/"


def _tenant(config: dict, creds: dict) -> dict:
    conns = request("GET", "https://api.xero.com/connections", headers={"Authorization": f"Bearer {creds['access_token']}"}).json()
    want = (config or {}).get("tenant_id", "").strip().lower()
    for c in conns:
        if c.get("tenantType", "ORGANISATION") == "ORGANISATION" and (not want or c["tenantId"].lower() == want):
            return c
    raise ValueError("No matching Xero organisation is connected" if want else "No Xero organisation is connected")


def verify(config: dict, creds: dict) -> str:
    return _tenant(config, creds).get("tenantName") or "Xero"


def _get(headers: dict, path: str, **params) -> dict:
    return request("GET", API + path, params=params, headers=headers).json()


def _period(label: str) -> str:
    try:  # header cells look like "30 Nov 2026" (period end)
        return datetime.strptime(label, "%d %b %Y").date().replace(day=1).isoformat()
    except ValueError:
        return label


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    tenant = _tenant(config, creds)["tenantId"]
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", tenant):
        raise ValueError("Unexpected Xero tenant id")
    headers = {"Authorization": f"Bearer {creds['access_token']}", "xero-tenant-id": tenant, "Accept": "application/json"}

    def invoices() -> Iterator[dict]:
        page = 1
        while True:
            batch = _get(headers, "Invoices", page=page, pageSize=1000, summaryOnly="true").get("Invoices", [])
            for i in batch:
                yield {"invoice_id": i["InvoiceID"], "number": i.get("InvoiceNumber"),
                       "type": {"ACCREC": "sales", "ACCPAY": "bill"}.get(i.get("Type"), i.get("Type")),
                       "contact": (i.get("Contact") or {}).get("Name"), "date": (i.get("DateString") or "")[:10] or None,
                       "due_date": (i.get("DueDateString") or "")[:10] or None, "status": i.get("Status"),
                       "subtotal": i.get("SubTotal"), "tax": i.get("TotalTax"), "total": i.get("Total"),
                       "amount_due": i.get("AmountDue"), "amount_paid": i.get("AmountPaid"), "currency": i.get("CurrencyCode")}
            if len(batch) < 1000:
                return
            page += 1

    yield table_doc("xero:invoices", "Xero invoices", ["invoice_id", "number", "type", "contact", "date", "due_date", "status",
                                                       "subtotal", "tax", "total", "amount_due", "amount_paid", "currency"], invoices())

    def pnl() -> Iterator[dict]:
        today = date.today()
        report = _get(headers, "Reports/ProfitAndLoss", fromDate=today.replace(day=1).isoformat(), toDate=today.isoformat(),
                      periods=11, timeframe="MONTH")["Reports"][0]  # 12 months: Xero's maximum per request
        months: list[str] = []
        for sect in report.get("Rows", []):
            if sect.get("RowType") == "Header":
                months = [_period(c.get("Value", "")) for c in sect.get("Cells", [])[1:]]
                continue
            for row in sect.get("Rows", []):
                cells = row.get("Cells", [])
                if row.get("RowType") not in ("Row", "SummaryRow") or not cells:
                    continue
                for month, cell in zip(months, cells[1:]):
                    if cell.get("Value") not in (None, ""):
                        yield {"section": sect.get("Title") or "", "account": cells[0].get("Value"),
                               "line_type": "total" if row["RowType"] == "SummaryRow" else "account",
                               "month": month, "amount": cell["Value"]}

    yield table_doc("xero:pnl", "Xero profit and loss by month", ["section", "account", "line_type", "month", "amount"], pnl())
