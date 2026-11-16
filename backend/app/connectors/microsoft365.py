"""Microsoft 365: OneDrive and SharePoint files (Word, PowerPoint, PDF, text) and Excel workbooks as tables (read-only)."""
from collections.abc import Iterator
from datetime import datetime

from .base import Doc, check, request

LABEL = "Microsoft 365"
BLURB = "Search Word, PowerPoint and PDF files in OneDrive and SharePoint; Excel workbooks become tables. Sharing is respected."
OAUTH = {
    "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    "scopes": ["offline_access", "User.Read", "Files.Read.All", "Sites.Read.All"],
    "console": "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    "note": "Register a web app, add the redirect URI, and add delegated Microsoft Graph permissions Files.Read.All and Sites.Read.All.",
}
PASTE = None
GUIDE = [
    "Microsoft Entra admin center → App registrations → New registration (Web platform) with the redirect URI from Settings → OAuth apps.",
    "API permissions → Microsoft Graph → Delegated: Files.Read.All, Sites.Read.All, offline_access, User.Read (all read-only). Grant admin consent.",
    "Certificates & secrets → New client secret. Save the Application (client) ID and secret in Settings → OAuth apps, then Connect.",
]
CONFIG = [{"key": "site", "label": "SharePoint site", "help": "Optional, e.g. contoso.sharepoint.com/sites/Finance. Blank = your OneDrive."}]
GRAPH = "https://graph.microsoft.com/v1.0/"
XLSX = (".xlsx", ".csv")
CONVERT = (".docx", ".doc", ".pptx", ".ppt", ".rtf", ".odt", ".odp")  # Graph converts these to PDF for us
TEXT = (".txt", ".md")
MAX_BYTES = 50 * 1024 * 1024


def _get(creds: dict, url: str, **params):
    return request("GET", url if url.startswith(GRAPH) else GRAPH + url, params=params or None,
                   headers={"Authorization": f"Bearer {creds['access_token']}"})


def whoami(creds: dict) -> str:
    me = _get(creds, "me").json()
    return me.get("mail") or me.get("userPrincipalName") or "Microsoft 365"


def _drives(config: dict, creds: dict) -> list[str]:
    if not (site := config.get("site", "").strip()):
        return [_get(creds, "me/drive").json()["id"]]
    host, _, path = check(site, r"[a-z0-9-]+\.sharepoint\.com(/(sites|teams)/[a-z0-9._-]+)?",
                          "SharePoint site (contoso.sharepoint.com/sites/Name)").partition("/")
    site_id = _get(creds, f"sites/{host}:/{path}" if path else f"sites/{host}").json()["id"]
    return [d["id"] for d in _get(creds, f"sites/{site_id}/drives").json().get("value", [])]


def _items(creds: dict, drive: str) -> Iterator[dict]:
    url = f"drives/{drive}/root/delta"
    while url:
        data = _get(creds, url).json()
        yield from data.get("value", [])
        url = data.get("@odata.nextLink")  # absolute graph.microsoft.com URL
        if url and not url.startswith(GRAPH):
            return


def _allowed(creds: dict, drive: str, item_id: str) -> list[str] | None:
    """Who can open the file: anyone/organization links or site groups → everyone in this connection; else the people listed."""
    emails = set()
    for p in _get(creds, f"drives/{drive}/items/{item_id}/permissions").json().get("value", []):
        if (p.get("link") or {}).get("scope") in ("anonymous", "organization"):
            return None
        for ident in [p.get("grantedToV2") or {}, *(p.get("grantedToIdentitiesV2") or [])]:
            for kind in ("user", "group", "siteUser", "siteGroup"):
                who = ident.get(kind) or {}
                if email := who.get("email") or (who.get("loginName") or "").rsplit("|", 1)[-1]:
                    if "@" in email:
                        emails.add(email.lower())
                        continue
                if kind in ("group", "siteGroup") and who:
                    return None  # ponytail: groups without an email (site groups) can't be expanded; restrict via the connection's access list
    return sorted(emails) or None


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    from ..ingest import extract_pages

    for drive in _drives(config, creds):
        for item in _items(creds, drive):
            if "file" not in item or "deleted" in item or item.get("size", 0) > MAX_BYTES:
                continue
            name, modified = item.get("name", ""), datetime.fromisoformat(item["lastModifiedDateTime"].replace("Z", "+00:00"))
            if since and modified < since:
                continue
            low = name.lower()
            if not low.endswith(XLSX + CONVERT + TEXT + (".pdf",)):
                continue
            content = f"drives/{drive}/items/{item['id']}/content"
            common = dict(external_id=item["id"], title=name, url=item.get("webUrl"), updated_at=modified,
                          allowed=_allowed(creds, drive, item["id"]))
            if low.endswith(XLSX):
                yield Doc(**common, text=f"Spreadsheet: {name} (queryable as a table)", table_file=(name, _get(creds, content).content))
                continue
            if low.endswith(CONVERT):
                pages = extract_pages(f"{name}.pdf", _get(creds, content, format="pdf").content)
            else:
                pages = extract_pages(name if low.endswith(".pdf") else "file.txt", _get(creds, content).content)
            if any(t.strip() for _, t in pages):
                yield Doc(**common, pages=pages)
