"""Connector checks against canned API responses (no real tokens needed). DB tests need `docker compose up`."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from app import connectors
from app.connectors import base, github, notion, slack, web
from app.db import connect
from app.tools import search_documents


def mock(routes: dict):
    """routes: {(METHOD, url-prefix): json-or-callable}. Longest matching prefix wins."""
    def handler(req: httpx.Request):
        url = str(req.url)
        for (method, prefix), resp in sorted(routes.items(), key=lambda kv: -len(kv[0][1])):
            if req.method == method and url.startswith(prefix):
                body = resp(req) if callable(resp) else resp
                if isinstance(body, httpx.Response):
                    return body
                if isinstance(body, str):
                    return httpx.Response(200, text=body, headers={"content-type": "text/html"})
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": f"no route {req.method} {url}"})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def http(monkeypatch):
    def use(routes):
        monkeypatch.setattr(base, "http", mock(routes))
    return use


CREDS = {"access_token": "x"}


def test_slack_groups_by_day_and_inlines_threads(http):
    day_ts = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc).timestamp()
    msgs = [{"ts": str(day_ts), "user": "U1", "text": "We're moving launch to Friday", "reply_count": 1},
            {"ts": str(day_ts + 60), "user": "U2", "text": "ok"}]
    http({
        ("GET", slack.API + "users.list"): {"ok": True, "members": [{"id": "U1", "name": "ana", "real_name": "Ana"},
                                                                    {"id": "U2", "name": "bo"}]},
        ("GET", slack.API + "auth.test"): {"ok": True, "url": "https://acme.slack.com/"},
        ("GET", slack.API + "conversations.list"): {"ok": True, "channels": [
            {"id": "C1", "name": "launch", "is_member": True}, {"id": "C2", "name": "random", "is_member": False}]},
        ("GET", slack.API + "conversations.history"): {"ok": True, "messages": msgs},
        ("GET", slack.API + "conversations.replies"): {"ok": True, "messages": [msgs[0], {"ts": str(day_ts + 30), "user": "U2", "text": "why?"}]},
    })
    docs = list(slack.fetch({}, None, CREDS))
    assert len(docs) == 1
    d = docs[0]
    assert d.title == "#launch · 2026-11-03" and d.external_id == "C1:2026-11-03"
    assert "Ana: We're moving launch to Friday" in d.text and "↳ [15:00] bo: why?" in d.text
    assert d.url == "https://acme.slack.com/archives/C1"


def test_slack_api_error_surfaces(http):
    http({("GET", slack.API): {"ok": False, "error": "invalid_auth"}})
    with pytest.raises(RuntimeError, match="invalid_auth"):
        list(slack.fetch({}, None, CREDS))


def test_notion_pages_and_incremental_stop(http):
    page = lambda pid, edited: {"id": pid, "url": f"https://notion.so/{pid}", "last_edited_time": edited,
                                "properties": {"Name": {"type": "title", "title": [{"plain_text": f"Page {pid}"}]}}}
    http({
        ("POST", notion.API + "search"): {"results": [page("new", "2026-11-04T10:00:00.000Z"),
                                                      page("old", "2026-01-01T00:00:00.000Z")], "has_more": False},
        ("GET", notion.API + "blocks/"): lambda req: {"has_more": False, "results": [
            {"type": "heading_2", "id": "h", "heading_2": {"rich_text": [{"plain_text": "Runbook"}]}},
            {"type": "bulleted_list_item", "id": "b", "bulleted_list_item": {"rich_text": [{"plain_text": "Restart the API"}]}},
        ]},
    })
    docs = list(notion.fetch({}, datetime(2026, 6, 1, tzinfo=timezone.utc), CREDS))
    assert [d.external_id for d in docs] == ["new"]
    assert docs[0].title == "Page new" and "## Runbook" in docs[0].text and "- Restart the API" in docs[0].text


def test_github_docs_and_issues(http):
    http({
        ("GET", github.API + "repos/acme/app/git/trees/"): {"tree": [
            {"type": "blob", "path": "README.md", "size": 10}, {"type": "blob", "path": "main.py", "size": 10}]},
        ("GET", github.API + "repos/acme/app/issues/7/comments"): [{"user": {"login": "bo"}, "body": "Fixed in #8"}],
        ("GET", github.API + "repos/acme/app/issues"): [{"number": 7, "title": "Login fails", "state": "closed",
                                                         "user": {"login": "ana"}, "body": "500 on /login", "comments": 1,
                                                         "html_url": "https://github.com/acme/app/issues/7",
                                                         "updated_at": "2026-11-01T00:00:00Z"}],
        ("GET", github.API + "repos/acme/app"): {"default_branch": "main", "pushed_at": "2026-11-02T00:00:00Z"},
        ("GET", "https://raw.githubusercontent.com/acme/app/main/README.md"): lambda r: httpx.Response(200, text="# App\nRun make dev"),
    })
    docs = {d.external_id: d for d in github.fetch({"repos": "acme/app"}, None, CREDS)}
    assert set(docs) == {"acme/app:file:README.md", "acme/app:issue:7"}
    assert "Run make dev" in docs["acme/app:file:README.md"].text
    assert "500 on /login" in docs["acme/app:issue:7"].text and "bo: Fixed in #8" in docs["acme/app:issue:7"].text


def test_web_parse_strips_chrome():
    title, text, links = web.parse(
        "<html><head><title>Docs</title><style>x{}</style></head><body><nav>Menu</nav>"
        "<h1>Install</h1><p>Run <b>pip install</b> now.</p><a href='/docs/next'>n</a><script>evil()</script></body></html>")
    assert title == "Docs" and "Menu" not in text and "evil" not in text
    assert "Install" in text and "Run pip install now." in text and links == ["/docs/next"]


def test_web_ssrf_guard():
    for bad in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://localhost:8000/", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            web._check_public(bad)


def test_web_crawl_stays_in_scope_and_checks_redirects(http, monkeypatch):
    monkeypatch.setattr(web, "_check_public", lambda url: None if "evil" not in url else (_ for _ in ()).throw(ValueError()))
    http({
        ("GET", "https://ex.com/docs/a"): "<title>A</title><p>Alpha page</p><a href='b'>b</a><a href='/blog/x'>out</a>",
        ("GET", "https://ex.com/docs/b"): lambda r: httpx.Response(302, headers={"location": "https://ex.com/docs/c"}),
        ("GET", "https://ex.com/docs/c"): "<title>C</title><p>Charlie page</p><a href='http://evil.com/docs/'>x</a>",
    })
    docs = list(web.fetch({"start_url": "https://ex.com/docs/a"}, None))
    assert [d.url for d in docs] == ["https://ex.com/docs/a", "https://ex.com/docs/c"]


def test_sync_indexes_reindexes_and_searches(http, monkeypatch):
    monkeypatch.setattr(web, "_check_public", lambda url: None)
    content = {"v": "The quarzite deploy freeze starts on Thursday."}
    http({("GET", "https://ex.com/docs/"): lambda r: f"<title>Freeze</title><p>{content['v']}</p>"})
    conn_id = connectors.create("web", "pytest-web", {"start_url": "https://ex.com/docs/"})
    try:
        connectors.sync(conn_id)
        hit = search_documents("quarzite deploy freeze", k=1, source="web")[0]
        assert "Thursday" in hit["content"] and hit["url"] == "https://ex.com/docs/"

        content["v"] = "The quarzite deploy freeze moved to Monday."
        connectors.sync(conn_id)
        with connect() as conn:
            status, docs, chunks = conn.execute(
                "SELECT c.status, count(DISTINCT d.id), count(ch.id) FROM connections c JOIN documents d ON d.connection_id = c.id "
                "JOIN chunks ch ON ch.document_id = d.id WHERE c.id = %s GROUP BY c.status", (conn_id,)).fetchone()
        assert status.startswith("ok") and docs == 1 and chunks == 1  # replaced, not duplicated
        assert "Monday" in search_documents("quarzite deploy freeze", k=1, source="web")[0]["content"]
        assert search_documents("quarzite deploy freeze", k=1, source="slack") == []
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM connections WHERE id = %s", (conn_id,))


def test_create_verifies_and_encrypts_credentials(http):
    http({("GET", notion.API + "users/me"): lambda r: httpx.Response(401, json={"message": "invalid token"})})
    with pytest.raises(ValueError, match="rejected"):
        connectors.create("notion", "bad", {}, creds={"access_token": "ntn_bad"})
    with pytest.raises(ValueError, match="required"):
        connectors.create("notion", "none", {})

    http({("GET", notion.API + "users/me"): {"bot": {"workspace_name": "Acme"}},
          ("POST", notion.API + "search"): {"results": [], "has_more": False}})
    conn_id = connectors.create("notion", "ok", {}, creds={"access_token": "ntn_supersecret"})
    try:
        with connect() as conn:
            blob, account = conn.execute("SELECT credentials, account FROM connections WHERE id = %s", (conn_id,)).fetchone()
        assert b"ntn_supersecret" not in bytes(blob) and account == "Acme"  # encrypted at rest
        connectors.sync(conn_id)  # decrypts and passes creds to fetch()
        with connect() as conn:
            assert conn.execute("SELECT status FROM connections WHERE id = %s", (conn_id,)).fetchone()[0].startswith("ok")
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM connections WHERE id = %s", (conn_id,))


def test_paste_parsing():
    assert connectors.parse_paste("github", "  github_pat_abc  ") == {"access_token": "github_pat_abc"}
    with pytest.raises(ValueError):
        connectors.parse_paste("github", "two words")
    with pytest.raises(ValueError, match="service account"):
        connectors.parse_paste("gdrive", '{"type": "authorized_user"}')
    sa = connectors.parse_paste("gdrive", '{"type": "service_account", "private_key": "k", "client_email": "x@y"}')
    assert sa["service_account"]["client_email"] == "x@y"


def test_create_validates_config():
    with pytest.raises(ValueError, match="required"):
        connectors.create("github", "x", {})
    with pytest.raises(ValueError, match="Unknown"):
        connectors.create("myspace", "x", {})
