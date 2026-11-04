"""Google Drive: Docs, Sheets, Slides, PDFs and text files you can access (read-only)."""
import json
import re
from collections.abc import Iterator
from datetime import datetime

from .base import Doc, request

LABEL = "Google Drive"
BLURB = "Search Docs, Sheets, Slides and PDFs. Drive's sharing settings are respected."
OAUTH = {
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
    "scope_sep": " ",
    "extra": {"access_type": "offline", "prompt": "consent"},  # needed to receive a refresh token
    "console": "https://console.cloud.google.com/apis/credentials",
    "note": "Enable the Google Drive API in the same Cloud project.",
}
PASTE = {"label": "Service account key (JSON)", "placeholder": '{"type": "service_account", …}', "multiline": True,
         "help": "Cloud console → IAM → Service accounts → Keys → JSON. Then share folders with the service account's email."}
GUIDE = [
    "Google Cloud console → APIs & Services → enable the Google Drive API.",
    "IAM → Service accounts → Create. No roles needed. Then Keys → Add key → JSON, and paste the file below.",
    "In Google Drive, share the folders you want searchable with the service account's email (Viewer is enough).",
]
CONFIG = [{"key": "folder_id", "label": "Folder ID", "help": "Optional. Only index this folder (direct children). Blank = everything you can see."}]
API = "https://www.googleapis.com/drive/v3/files"

EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",  # first sheet only (Drive export limitation)
}


def parse_paste(text: str) -> dict:
    """Pasted credential → creds dict. Drive takes a service-account key instead of a bearer token."""
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("Paste the whole service account JSON key file")
    if info.get("type") != "service_account" or "private_key" not in info:
        raise ValueError("That JSON isn't a service account key")
    return {"service_account": info}


def _token(creds: dict) -> str:
    if "service_account" not in creds:
        return creds["access_token"]  # OAuth: refreshed by the sync engine before fetch()
    import google.auth.transport.requests
    from google.oauth2 import service_account

    sa = service_account.Credentials.from_service_account_info(
        creds["service_account"], scopes=OAUTH["scopes"])
    sa.refresh(google.auth.transport.requests.Request())
    return sa.token


def whoami(creds: dict) -> str:
    if "service_account" in creds:
        _token(creds)  # proves the key works
        return creds["service_account"]["client_email"]
    about = request("GET", "https://www.googleapis.com/drive/v3/about", params={"fields": "user(emailAddress)"},
                    headers={"Authorization": f"Bearer {creds['access_token']}"}).json()
    return about["user"]["emailAddress"]


def _list(headers: dict, q: str) -> Iterator[dict]:
    token = None
    while True:
        params = {"q": q, "pageSize": 200, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
                  "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink, permissions(type, emailAddress))"}
        if token:
            params["pageToken"] = token
        data = request("GET", API, params=params, headers=headers).json()
        yield from data["files"]
        token = data.get("nextPageToken")
        if not token:
            return


def _allowed(f: dict, headers: dict) -> list[str] | None:
    """Who can open the file in Drive. Link-shared or domain-wide → everyone; otherwise the listed people/groups."""
    perms = f.get("permissions")
    if perms is None:  # shared-drive files don't include permissions in list results
        perms = request("GET", f"{API}/{f['id']}/permissions", headers=headers, params={
            "supportsAllDrives": "true", "fields": "permissions(type, emailAddress)"}).json().get("permissions", [])
    if any(p["type"] in ("anyone", "domain") for p in perms):
        return None
    return [p["emailAddress"].lower() for p in perms if p.get("emailAddress")]
    # ponytail: Google groups appear as the group's email; members are matched only if you add that email to the app


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    from ..ingest import extract_pages

    headers = {"Authorization": f"Bearer {_token(creds)}"}

    q = ["trashed = false"]
    if since:
        q.append(f"modifiedTime > '{since.strftime('%Y-%m-%dT%H:%M:%S')}'")
    if folder := (config.get("folder_id") or "").strip():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", folder):  # it's interpolated into a Drive query
            raise ValueError("Folder ID should look like 1AbC_dEf-123 (from the folder's URL)")
        q.append(f"'{folder}' in parents")
    for f in _list(headers, " and ".join(q)):
        mime = f["mimeType"]
        if mime in EXPORT:
            resp = request("GET", f"{API}/{f['id']}/export", params={"mimeType": EXPORT[mime]}, headers=headers)
            pages = [(1, resp.text)]
        elif mime == "application/pdf" or mime.startswith("text/"):
            resp = request("GET", f"{API}/{f['id']}", params={"alt": "media", "supportsAllDrives": "true"}, headers=headers)
            pages = extract_pages(f["name"] if mime == "application/pdf" else "file.txt", resp.content)
        else:
            continue  # images, videos, folders, etc.
        if any(t.strip() for _, t in pages):
            yield Doc(external_id=f["id"], title=f["name"], pages=pages, url=f.get("webViewLink"), allowed=_allowed(f, headers),
                      updated_at=datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")))
