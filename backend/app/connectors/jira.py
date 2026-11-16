"""Jira Cloud: issues with description, status, assignee and comments, one searchable document per issue."""
import base64
import re
from collections.abc import Iterator
from datetime import datetime, timedelta

from .base import Doc, check, request

LABEL = "Jira"
BLURB = "Search issues, bugs and project discussions, including comments."
OAUTH = None
PASTE = {"label": "API token", "placeholder": "ATATT…", "help": "id.atlassian.com → Security → API tokens → Create API token"}
GUIDE = [
    "Sign in as a user who can browse the projects you want searchable (a read-only account is best).",
    "id.atlassian.com/manage-profile/security/api-tokens → Create API token (with scopes: read:jira-work and read:jira-user).",
    "Enter your site and that account's email, optionally limit to project keys or a JQL filter, and paste the token below.",
]
CONFIG = [
    {"key": "site", "label": "Jira site", "help": "yourcompany.atlassian.net", "required": True},
    {"key": "email", "label": "Account email", "help": "The Atlassian account that owns the API token", "required": True},
    {"key": "projects", "label": "Project keys", "help": "Optional, comma-separated, e.g. ENG, OPS. Blank = every project you can see."},
    {"key": "jql", "label": "JQL filter", "help": "Optional, e.g. labels = customer. Combined with the project keys."},
]
FIELDS = "summary,description,status,assignee,reporter,priority,issuetype,project,comment,created,updated"


def site(config: dict) -> str:
    return check(config.get("site", ""), r"[a-z0-9][a-z0-9-]{0,62}\.atlassian\.net", "Site (yourcompany.atlassian.net)")


def headers(config: dict, creds: dict) -> dict:
    basic = base64.b64encode(f"{config.get('email', '').strip()}:{creds['access_token']}".encode()).decode()
    return {"Authorization": f"Basic {basic}", "Accept": "application/json"}


def verify(config: dict, creds: dict) -> str:
    me = request("GET", f"https://{site(config)}/rest/api/3/myself", headers=headers(config, creds)).json()
    return f"{me.get('emailAddress') or me.get('displayName')} @ {site(config)}"


def adf_text(node) -> str:
    """Atlassian Document Format (JSON) → plain text."""
    if isinstance(node, str) or node is None:
        return node or ""
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") in ("mention", "emoji"):
        return (node.get("attrs") or {}).get("text", "")
    if node.get("type") == "hardBreak":
        return "\n"
    inner = "".join(adf_text(c) for c in node.get("content", []))
    if node.get("type") == "listItem":
        return "- " + inner.strip() + "\n"
    if node.get("type") in ("paragraph", "heading", "codeBlock", "blockquote", "tableRow"):
        return inner + "\n"
    return inner


def _jql(config: dict, since: datetime | None) -> str:
    clauses = []
    if keys := [k.strip().upper() for k in config.get("projects", "").split(",") if k.strip()]:
        if not all(re.fullmatch(r"[A-Z][A-Z0-9_]{0,49}", k) for k in keys):
            raise ValueError("Project keys look like ENG, OPS (letters, digits, underscore)")
        clauses.append(f"project in ({', '.join(keys)})")
    if jql := config.get("jql", "").strip():
        clauses.append(f"({jql})")
    # JQL dates are in the account's timezone, so step back a day; search also needs a bounded query
    clauses.append(f'updated >= "{(since - timedelta(days=1)).strftime("%Y/%m/%d %H:%M")}"' if since else "updated >= -3650d")
    return " AND ".join(clauses) + " ORDER BY updated DESC"


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    host, hdrs, token = site(config), headers(config, creds), None
    while True:
        params = {"jql": _jql(config, since), "fields": FIELDS, "maxResults": 100, **({"nextPageToken": token} if token else {})}
        data = request("GET", f"https://{host}/rest/api/3/search/jql", params=params, headers=hdrs).json()
        for issue in data.get("issues", []):
            f = issue["fields"]
            name = lambda u: (u or {}).get("displayName") or "Unassigned"  # noqa: E731
            lines = [f"{issue['key']}: {f.get('summary', '')}",
                     f"Project: {(f.get('project') or {}).get('name', '')} · Type: {(f.get('issuetype') or {}).get('name', '')} · "
                     f"Status: {(f.get('status') or {}).get('name', '')} · Priority: {(f.get('priority') or {}).get('name', '')}",
                     f"Assignee: {name(f.get('assignee'))} · Reporter: {name(f.get('reporter'))}", "",
                     adf_text(f.get("description")).strip()]
            comments = (f.get("comment") or {}).get("comments", [])
            if comments:
                lines += ["", "Comments:"] + [f"{name(c.get('author'))}: {adf_text(c.get('body')).strip()}" for c in comments]
            yield Doc(external_id=issue["id"], title=f"{issue['key']} {f.get('summary', '')}"[:300], text="\n".join(lines),
                      url=f"https://{host}/browse/{issue['key']}",
                      updated_at=datetime.strptime(f["updated"], "%Y-%m-%dT%H:%M:%S.%f%z") if f.get("updated") else None)
        token = data.get("nextPageToken")
        if not token or data.get("isLast"):
            return
