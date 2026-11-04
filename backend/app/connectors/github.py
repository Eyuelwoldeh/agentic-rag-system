"""GitHub: markdown docs plus issues and PRs (with comments) from the repositories you list."""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from .base import Doc, request

LABEL = "GitHub"
BLURB = "Search READMEs, docs, issues and pull-request discussions."
OAUTH = {
    "authorize_url": "https://github.com/login/oauth/authorize",
    "token_url": "https://github.com/login/oauth/access_token",
    "scopes": ["repo"],  # OAuth apps have no read-only scope for private repos; a pasted fine-grained token can be read-only
    "scope_sep": " ",
    "console": "https://github.com/settings/developers",
}
PASTE = {"label": "Fine-grained personal access token", "placeholder": "github_pat_…",
         "help": "github.com/settings/personal-access-tokens → read-only Contents + Issues on the repos you want"}
GUIDE = [
    "Go to github.com/settings/personal-access-tokens → Generate new token (fine-grained).",
    "Repository access: only the repos you want. Permissions: Contents → Read-only, Issues → Read-only, Pull requests → Read-only.",
    "Generate, copy the token (starts with github_pat_) and paste it below, then list the same repos.",
]
CONFIG = [
    {"key": "repos", "label": "Repositories", "help": "Comma-separated owner/name, e.g. vercel/next.js", "required": True},
    {"key": "days", "label": "Issue backfill days", "help": "How far back the first sync reads issues (default 180)."},
]
API = "https://api.github.com/"
DOC_EXT = (".md", ".mdx", ".rst", ".txt")
MAX_FILE_BYTES = 200_000


def _get(token: str, path: str, **params):
    return request("GET", API + path, params=params, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})


def _paged(token: str, path: str, **params) -> Iterator[dict]:
    page = 1
    while True:
        items = _get(token, path, per_page=100, page=page, **params).json()
        yield from items
        if len(items) < 100:
            return
        page += 1


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def whoami(creds: dict) -> str:
    return "@" + _get(creds["access_token"], "user").json()["login"]


def fetch(config: dict, since: datetime | None, creds: dict) -> Iterator[Doc]:
    t = creds["access_token"]
    repos = [r.strip() for r in (config.get("repos") or "").split(",") if r.strip()]
    if not repos:
        raise ValueError("Set at least one repository")
    issues_since = since or datetime.now(timezone.utc) - timedelta(days=int(config.get("days") or 180))
    for repo in repos:
        yield from _docs(t, repo, since)
        yield from _issues(t, repo, issues_since)


def _docs(t: str, repo: str, since: datetime | None) -> Iterator[Doc]:
    info = _get(t, f"repos/{repo}").json()
    branch = info["default_branch"]
    if since and _ts(info["pushed_at"]) < since:
        return  # nothing pushed since last sync
    tree = _get(t, f"repos/{repo}/git/trees/{branch}", recursive="1").json()["tree"]
    for f in tree:
        if f["type"] != "blob" or not f["path"].lower().endswith(DOC_EXT) or f.get("size", 0) > MAX_FILE_BYTES:
            continue
        raw = request("GET", f"https://raw.githubusercontent.com/{repo}/{branch}/{f['path']}",
                      headers={"Authorization": f"Bearer {t}"}).text
        if raw.strip():
            # ponytail: re-indexes every doc file on each push; compare blob sha if repos are large
            yield Doc(external_id=f"{repo}:file:{f['path']}", title=f"{repo}/{f['path']}", text=raw,
                      url=f"https://github.com/{repo}/blob/{branch}/{f['path']}", updated_at=_ts(info["pushed_at"]))


def _issues(t: str, repo: str, since: datetime) -> Iterator[Doc]:
    for it in _paged(t, f"repos/{repo}/issues", state="all", since=since.isoformat(), sort="updated"):
        kind = "PR" if "pull_request" in it else "Issue"
        parts = [f"{kind} #{it['number']}: {it['title']} ({it['state']}) by {it['user']['login']}", it.get("body") or ""]
        if it.get("comments"):
            for c in _paged(t, f"repos/{repo}/issues/{it['number']}/comments"):
                parts.append(f"{c['user']['login']}: {c.get('body') or ''}")
        yield Doc(external_id=f"{repo}:issue:{it['number']}", title=f"{repo} {kind} #{it['number']}: {it['title']}",
                  text="\n\n".join(parts), url=it["html_url"], updated_at=_ts(it["updated_at"]))
