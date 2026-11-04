"""Slack: one document per channel per day, thread replies inlined. Invite the bot to each channel you want indexed."""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from .base import Doc, request

LABEL = "Slack"
BLURB = "Search team conversations and decisions. Private channels stay private to their members."
OAUTH = {
    "authorize_url": "https://slack.com/oauth/v2/authorize",
    "token_url": "https://slack.com/api/oauth.v2.access",
    "scopes": ["channels:read", "channels:history", "groups:read", "groups:history", "users:read", "users:read.email"],
    "scope_sep": ",",
    "console": "https://api.slack.com/apps",
    "note": "Slack requires an https redirect URL: use a tunnel (e.g. ngrok) or a deployed PUBLIC_URL.",
}
PASTE = {"label": "Bot token", "placeholder": "xoxb-…", "help": "Slack app → OAuth & Permissions → Bot User OAuth Token"}
GUIDE = [
    "Go to api.slack.com/apps → Create New App → From scratch, and pick your workspace.",
    "OAuth & Permissions → Bot Token Scopes: add channels:read, channels:history, groups:read, groups:history, users:read, users:read.email.",
    "Install to Workspace, then copy the Bot User OAuth Token (starts with xoxb-) and paste it below.",
    "In each Slack channel you want searchable, type /invite @your-app-name.",
]
CONFIG = [
    {"key": "channels", "label": "Channels", "help": "Comma-separated names. Blank = every channel the bot is in."},
    {"key": "days", "label": "Backfill days", "help": "How far back the first sync goes (default 90)."},
]
API = "https://slack.com/api/"


def _call(token: str, method: str, **params) -> dict:
    data = request("GET", API + method, params=params, headers={"Authorization": f"Bearer {token}"}).json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method}: {data.get('error')}")
    return data


def _paged(token: str, method: str, key: str, **params) -> Iterator[dict]:
    cursor = None
    while True:
        data = _call(token, method, limit=200, **params, **({"cursor": cursor} if cursor else {}))
        yield from data[key]
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return


def _day(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def whoami(creds: dict) -> str:
    data = _call(creds["access_token"], "auth.test")
    return f"{data['team']} workspace"


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    t = creds["access_token"]
    wanted = {c.strip().lstrip("#") for c in (config.get("channels") or "").split(",") if c.strip()}
    oldest = since or datetime.now(timezone.utc) - timedelta(days=int(config.get("days") or 90))
    members = list(_paged(t, "users.list", "members"))
    users = {u["id"]: u.get("real_name") or u["name"] for u in members}
    emails = {u["id"]: u["profile"]["email"].lower() for u in members if u.get("profile", {}).get("email")}
    team_url = _call(t, "auth.test")["url"].rstrip("/")

    for ch in _paged(t, "conversations.list", "channels", types="public_channel,private_channel", exclude_archived="true"):
        if not ch.get("is_member") or (wanted and ch["name"] not in wanted):
            continue
        # Private channels: only their members may see the content (matched to app users by email).
        allowed = None
        if ch.get("is_private"):
            allowed = [emails[m] for m in _paged(t, "conversations.members", "members", channel=ch["id"]) if m in emails]
        # Find days with new activity, then rebuild each of those days in full so a day-doc is never partial.
        touched = {_day(m["ts"]) for m in _paged(t, "conversations.history", "messages", channel=ch["id"],
                                                     oldest=str(oldest.timestamp()))}
        for day in sorted(touched):
            msgs = list(_paged(t, "conversations.history", "messages", channel=ch["id"], inclusive="true",
                               oldest=str(day.timestamp()), latest=str((day + timedelta(days=1)).timestamp())))
            lines = []
            for m in sorted(msgs, key=lambda m: float(m["ts"])):
                lines.append(_fmt(m, users))
                if m.get("reply_count"):
                    for r in list(_paged(t, "conversations.replies", "messages", channel=ch["id"], ts=m["ts"]))[1:]:
                        lines.append("    ↳ " + _fmt(r, users))
            if lines:
                yield Doc(
                    external_id=f"{ch['id']}:{day.date()}",
                    title=f"#{ch['name']} · {day.date()}",
                    text="\n\n".join(lines),
                    url=f"{team_url}/archives/{ch['id']}",
                    updated_at=day,
                    allowed=allowed,
                )


def _fmt(m: dict, users: dict) -> str:
    t = datetime.fromtimestamp(float(m["ts"]), timezone.utc).strftime("%H:%M")
    return f"[{t}] {users.get(m.get('user'), m.get('username', 'bot'))}: {m.get('text', '')}"
