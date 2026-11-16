"""Confluence Cloud: pages from the spaces you choose, as plain text with links back to Confluence."""
import re
from collections.abc import Iterator
from datetime import datetime

from . import jira, web
from .base import Doc, request

LABEL = "Confluence"
BLURB = "Search your wiki, runbooks and team pages in Confluence."
OAUTH = None
PASTE = {"label": "API token", "placeholder": "ATATT…", "help": "id.atlassian.com → Security → API tokens → Create API token"}
GUIDE = [
    "Sign in as a user who can view the spaces you want searchable (a read-only account is best).",
    "id.atlassian.com/manage-profile/security/api-tokens → Create API token (with scopes: read:confluence-content.all, read:confluence-space.summary).",
    "Enter your site, that account's email and optionally the space keys, then paste the token below.",
]
CONFIG = [
    {"key": "site", "label": "Confluence site", "help": "yourcompany.atlassian.net", "required": True},
    {"key": "email", "label": "Account email", "help": "The Atlassian account that owns the API token", "required": True},
    {"key": "spaces", "label": "Space keys", "help": "Optional, comma-separated, e.g. ENG, HR. Blank = every space you can see."},
]


def _get(config: dict, creds: dict, url: str, **params) -> dict:
    return request("GET", url, params=params, headers=jira.headers(config, creds)).json()


def _space_ids(config: dict, creds: dict) -> list[str] | None:
    keys = [k.strip() for k in config.get("spaces", "").split(",") if k.strip()]
    if not keys:
        return None
    if not all(re.fullmatch(r"~?[A-Za-z0-9_]{1,255}", k) for k in keys):
        raise ValueError("Space keys look like ENG, HR (letters, digits, underscore)")
    found = _get(config, creds, f"https://{jira.site(config)}/wiki/api/v2/spaces", keys=",".join(keys), limit=250)["results"]
    if missing := set(keys) - {s["key"] for s in found}:
        raise ValueError(f"Spaces not found or not visible: {', '.join(sorted(missing))}")
    return [s["id"] for s in found]


def verify(config: dict, creds: dict) -> str:
    ids = _space_ids(config, creds)
    if ids is None:
        _get(config, creds, f"https://{jira.site(config)}/wiki/api/v2/spaces", limit=1)
    return f"{config.get('email', '').strip()} @ {jira.site(config)}" + (f" · {len(ids)} spaces" if ids else "")


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    host = jira.site(config)
    ids = _space_ids(config, creds)
    for url in [f"https://{host}/wiki/api/v2/spaces/{i}/pages" for i in ids] if ids else [f"https://{host}/wiki/api/v2/pages"]:
        params = {"body-format": "storage", "sort": "-modified-date", "limit": 100, "status": "current"}
        while url:
            data = _get(config, creds, url, **params)
            for page in data.get("results", []):
                edited = datetime.fromisoformat(page["version"]["createdAt"].replace("Z", "+00:00"))
                if since and edited < since:
                    url = None  # newest-first: the rest is unchanged
                    break
                _, text, _ = web.parse(((page.get("body") or {}).get("storage") or {}).get("value", ""))
                if text.strip():
                    yield Doc(external_id=page["id"], title=page.get("title") or "Untitled", text=text, updated_at=edited,
                              url=f"https://{host}/wiki{(page.get('_links') or {}).get('webui', '')}")
            else:
                nxt = (data.get("_links") or {}).get("next")  # relative, carries the cursor and our params
                url, params = (f"https://{host}{nxt}", {}) if nxt and nxt.startswith("/wiki/") else (None, {})
