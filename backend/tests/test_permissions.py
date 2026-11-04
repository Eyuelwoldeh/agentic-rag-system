"""Permission-aware retrieval: nobody can find content they aren't allowed to see."""
from datetime import datetime, timezone

import httpx
import pytest

from app import connectors
from app.connectors import base, gdrive, slack
from app.db import connect
from app.ingest import ingest_document, upsert_document
from app.tools import search_documents

Q = "zephyrine launch codename"


@pytest.fixture
def clean():
    yield
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE name LIKE 'perm-%'")
        conn.execute("DELETE FROM connections WHERE name LIKE 'perm-%'")


@pytest.fixture
def uids():
    """Two real users (uploads reference users by id)."""
    with connect() as conn:
        ids = [conn.execute("INSERT INTO users (email, password_hash, role) VALUES (%s, 'x', 'member') "
                            "ON CONFLICT (email) DO UPDATE SET role = 'member' RETURNING id", (e,)).fetchone()[0]
               for e in ("perm-ana@acme.test", "perm-bo@acme.test")]
    return ids


def names(email):
    return {h["document"] for h in search_documents(Q, k=15, email=email)}


def test_private_upload_only_visible_to_uploader(clean, uids):
    ingest_document("perm-private.md", b"The zephyrine launch codename is Bluebird.", user_id=uids[0], allowed=["ana@acme.test"])
    ingest_document("perm-public.md", b"Everyone knows the zephyrine launch codename list.", user_id=uids[1])
    assert names("ana@acme.test") == {"perm-private.md", "perm-public.md"}
    assert names("bo@acme.test") == {"perm-public.md"}
    assert names(None) == {"perm-public.md"}  # no identity = public content only
    assert names("ANA@acme.test") == {"perm-private.md", "perm-public.md"}  # emails are case-insensitive


def test_same_filename_from_two_users_does_not_overwrite(clean, uids):
    a = ingest_document("perm-notes.md", b"zephyrine launch codename: Alpha", user_id=uids[0], allowed=["ana@acme.test"])
    b = ingest_document("perm-notes.md", b"zephyrine launch codename: Beta", user_id=uids[1])
    assert a["id"] != b["id"]
    texts = {h["content"] for h in search_documents(Q, k=15, email="ana@acme.test")}
    assert any("Alpha" in t for t in texts) and any("Beta" in t for t in texts)


def test_connection_and_document_access_both_apply(clean):
    with connect() as conn:
        restricted = conn.execute("INSERT INTO connections (kind, name, allowed) VALUES ('web', 'perm-sales', %s) RETURNING id",
                                  (["ana@acme.test", "cy@acme.test"],)).fetchone()[0]
    upsert_document("perm-deal.md", [(1, "zephyrine launch codename deal notes")], source="web",
                    external_id="d1", connection_id=restricted)
    upsert_document("perm-board.md", [(1, "zephyrine launch codename board memo")], source="web",
                    external_id="d2", connection_id=restricted, allowed=["cy@acme.test"])
    assert names("ana@acme.test") == {"perm-deal.md"}  # board memo is narrowed to cy at the document level
    assert names("cy@acme.test") == {"perm-deal.md", "perm-board.md"}
    assert names("bo@acme.test") == set()  # not on the connection's list at all

    # changing the connection's access applies immediately, no re-sync
    with connect() as conn:
        conn.execute("UPDATE connections SET allowed = NULL WHERE id = %s", (restricted,))
    assert names("bo@acme.test") == {"perm-deal.md"}


def test_reindex_updates_access(clean):
    upsert_document("perm-doc.md", [(1, "zephyrine launch codename v1")], external_id="x", allowed=["ana@acme.test"])
    assert names("bo@acme.test") == set()
    upsert_document("perm-doc.md", [(1, "zephyrine launch codename v2")], external_id="x", allowed=None)
    assert names("bo@acme.test") == {"perm-doc.md"}


def test_clean_allowed():
    assert connectors.clean_allowed(None) is None and connectors.clean_allowed([" ", ""]) is None
    assert connectors.clean_allowed([" Ana@Acme.test", "ana@acme.test", "bo@acme.test"]) == ["ana@acme.test", "bo@acme.test"]
    with pytest.raises(ValueError):
        connectors.clean_allowed(["not-an-email"])


def _mock(monkeypatch, routes):
    def handler(req):
        for (method, prefix), body in sorted(routes.items(), key=lambda kv: -len(kv[0][1])):
            if req.method == method and str(req.url).startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={})
    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(handler)))


def test_slack_private_channel_limited_to_members(monkeypatch):
    ts = str(datetime(2026, 11, 3, 12, tzinfo=timezone.utc).timestamp())
    _mock(monkeypatch, {
        ("GET", slack.API + "users.list"): {"ok": True, "members": [
            {"id": "U1", "name": "ana", "profile": {"email": "Ana@acme.test"}},
            {"id": "U2", "name": "bo", "profile": {"email": "bo@acme.test"}},
            {"id": "B1", "name": "bot", "profile": {}}]},
        ("GET", slack.API + "auth.test"): {"ok": True, "url": "https://acme.slack.com/"},
        ("GET", slack.API + "conversations.list"): {"ok": True, "channels": [
            {"id": "C1", "name": "general", "is_member": True},
            {"id": "G1", "name": "exec", "is_member": True, "is_private": True}]},
        ("GET", slack.API + "conversations.members"): {"ok": True, "members": ["U1", "B1"]},
        ("GET", slack.API + "conversations.history"): {"ok": True, "messages": [{"ts": ts, "user": "U1", "text": "hi"}]},
    })
    docs = {d.title.split(" ")[0]: d for d in slack.fetch({}, None, {"access_token": "x"})}
    assert docs["#general"].allowed is None
    assert docs["#exec"].allowed == ["ana@acme.test"]  # bot without email is skipped; bo isn't a member


def test_drive_permissions(monkeypatch):
    assert gdrive._allowed({"permissions": [{"type": "anyone"}]}, {}) is None
    assert gdrive._allowed({"permissions": [{"type": "domain"}, {"type": "user", "emailAddress": "a@x.test"}]}, {}) is None
    assert gdrive._allowed({"permissions": [{"type": "user", "emailAddress": "A@x.test"},
                                            {"type": "group", "emailAddress": "team@x.test"}]}, {}) == ["a@x.test", "team@x.test"]
    _mock(monkeypatch, {("GET", gdrive.API + "/f1/permissions"): {"permissions": [{"type": "user", "emailAddress": "c@x.test"}]}})
    assert gdrive._allowed({"id": "f1"}, {}) == ["c@x.test"]  # shared-drive file: fetched separately
