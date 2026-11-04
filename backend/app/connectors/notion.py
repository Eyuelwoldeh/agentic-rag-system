"""Notion: every page shared with the integration, as plain text. You pick which pages to share when connecting."""
from collections.abc import Iterator
from datetime import datetime

from .base import Doc, request

LABEL = "Notion"
BLURB = "Search your wiki, specs and meeting notes. You choose which pages to include."
OAUTH = {
    "authorize_url": "https://api.notion.com/v1/oauth/authorize",
    "token_url": "https://api.notion.com/v1/oauth/token",
    "scopes": [],
    "extra": {"owner": "user"},
    "token_auth": "basic_json",
    "console": "https://www.notion.so/profile/integrations",
}
PASTE = {"label": "Internal integration secret", "placeholder": "ntn_…",
         "help": "notion.so/profile/integrations → new internal integration. Then share pages with it (page ⋯ → Connections)."}
GUIDE = [
    "Go to notion.so/profile/integrations → New integration → type Internal, capabilities: Read content only.",
    "Copy the Internal Integration Secret and paste it below.",
    "In Notion, open each top-level page you want searchable → ⋯ menu → Connections → add your integration (sub-pages are included).",
]
CONFIG = []
API = "https://api.notion.com/v1/"
MAX_DEPTH = 3


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}


def _text(rich: list) -> str:
    return "".join(r.get("plain_text", "") for r in rich)


def _blocks(token: str, block_id: str, depth: int = 0) -> list[str]:
    out, cursor = [], None
    while True:
        params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        data = request("GET", f"{API}blocks/{block_id}/children", headers=_headers(token), params=params).json()
        for b in data["results"]:
            body = b.get(b["type"], {})
            line = _text(body.get("rich_text", []))
            if b["type"].startswith("heading"):
                line = "#" * int(b["type"][-1]) + " " + line
            elif b["type"] in ("bulleted_list_item", "numbered_list_item", "to_do"):
                line = "  " * depth + "- " + line
            elif b["type"] == "child_page":
                line = f"[subpage: {body.get('title', '')}]"
            if line.strip():
                out.append(line)
            if b.get("has_children") and b["type"] != "child_page" and depth < MAX_DEPTH:
                out.extend(_blocks(token, b["id"], depth + 1))
        if not data.get("has_more"):
            return out
        cursor = data["next_cursor"]


def _title(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return _text(prop["title"]) or "Untitled"
    return "Untitled"


def whoami(creds: dict) -> str:
    me = request("GET", API + "users/me", headers=_headers(creds["access_token"])).json()
    return me.get("bot", {}).get("workspace_name") or me.get("name") or "Notion"


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    token = creds["access_token"]
    cursor = None
    while True:
        body = {"filter": {"property": "object", "value": "page"},
                "sort": {"direction": "descending", "timestamp": "last_edited_time"}, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = request("POST", API + "search", headers=_headers(token), json=body).json()
        for page in data["results"]:
            edited = datetime.fromisoformat(page["last_edited_time"].replace("Z", "+00:00"))
            if since and edited < since:
                return  # results are sorted newest-first: everything after this is unchanged
            text = "\n\n".join(_blocks(token, page["id"]))
            if text.strip():
                yield Doc(external_id=page["id"], title=_title(page), text=text, url=page.get("url"), updated_at=edited)
        if not data.get("has_more"):
            return
        cursor = data["next_cursor"]
