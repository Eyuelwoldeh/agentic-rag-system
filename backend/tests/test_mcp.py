"""MCP servers: handshake verification, allowlists, access rules, and what gets sent to Claude."""
import httpx
import pytest

from app import agent, connectors
from app.connectors import base, mcp
from app.db import connect


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setattr(mcp, "_check_public", lambda url: None)  # test URLs don't resolve


def _serve(monkeypatch, responder):
    monkeypatch.setattr(base, "http", httpx.Client(transport=httpx.MockTransport(responder)))


def test_verify_json_and_sse(monkeypatch, public):
    _serve(monkeypatch, lambda r: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "Linear", "version": "1.2"}}}))
    assert mcp.verify({"url": "https://mcp.example.com/mcp"}, None) == "Linear 1.2"

    seen = {}

    def sse(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"Sentry"}}}\n\n',
                              headers={"content-type": "text/event-stream"})
    _serve(monkeypatch, sse)
    assert mcp.verify({"url": "https://mcp.example.com/sse"}, {"access_token": "tok"}) == "Sentry" and seen["auth"] == "Bearer tok"

    _serve(monkeypatch, lambda r: httpx.Response(200, text="<html>hello</html>"))
    with pytest.raises(Exception):
        mcp.verify({"url": "https://example.com"}, None)


def test_url_rules():
    with pytest.raises(ValueError, match="https"):
        mcp._url({"url": "http://mcp.example.com"})
    with pytest.raises(ValueError, match="non-public"):
        mcp._url({"url": "https://127.0.0.1/mcp"})


def test_server_def_allowlist():
    server, toolset = mcp.server_def(7, {"url": "https://x.test/mcp", "tools": "search_issues, get_issue"}, {"access_token": "t"})
    assert server == {"type": "url", "url": "https://x.test/mcp", "name": "mcp-7", "authorization_token": "t"}
    assert toolset["default_config"] == {"enabled": False}
    assert toolset["configs"] == {"search_issues": {"enabled": True}, "get_issue": {"enabled": True}}
    _, open_toolset = mcp.server_def(8, {"url": "https://x.test/mcp"}, None)
    assert "default_config" not in open_toolset
    with pytest.raises(ValueError):
        mcp.server_def(9, {"url": "https://x.test/mcp", "tools": "rm -rf"}, None)


def test_create_verifies_and_token_optional(monkeypatch, public):
    _serve(monkeypatch, lambda r: httpx.Response(200, json={"result": {"serverInfo": {"name": "Docs"}}}))
    cid = connectors.create("mcp", "docs", {"url": "https://mcp.example.com/mcp"})  # no token needed
    try:
        with connect() as conn:
            assert conn.execute("SELECT account FROM connections WHERE id = %s", (cid,)).fetchone()[0] == "Docs"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM connections WHERE id = %s", (cid,))
    _serve(monkeypatch, lambda r: httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(ValueError, match="rejected"):
        connectors.create("mcp", "bad", {"url": "https://mcp.example.com/mcp"})


def test_agent_sends_only_permitted_servers(monkeypatch):
    with connect() as conn:
        open_id = conn.execute("INSERT INTO connections (kind, name, config) VALUES ('mcp', 'mcp-open', %s) RETURNING id",
                               ('{"url": "https://open.test/mcp", "tools": "search"}',)).fetchone()[0]
        locked_id = conn.execute("INSERT INTO connections (kind, name, config, allowed) VALUES ('mcp', 'mcp-locked', %s, %s) RETURNING id",
                                 ('{"url": "https://locked.test/mcp"}', ["boss@acme.test"])).fetchone()[0]
    captured = {}

    class Stop(Exception):
        pass

    def fake_stream(**kwargs):
        captured.update(kwargs)
        raise Stop

    monkeypatch.setattr(agent.client.beta.messages, "stream", fake_stream)
    try:
        for email, expect in [("intern@acme.test", {f"mcp-{open_id}"}), ("boss@acme.test", {f"mcp-{open_id}", f"mcp-{locked_id}"})]:
            captured.clear()
            with pytest.raises(Stop):
                next(agent._agent_loop([{"role": "user", "content": "hi"}], agent.tools.Ctx(email=email), [], {"input_tokens": 0, "output_tokens": 0}))
            assert {s["name"] for s in captured["mcp_servers"]} == expect
            assert {t["mcp_server_name"] for t in captured["tools"] if t.get("type") == "mcp_toolset"} == expect
            assert agent.MCP_BETA in captured["betas"]
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM connections WHERE id IN (%s, %s)", (open_id, locked_id))
    # no MCP servers → no MCP params at all
    captured.clear()
    with pytest.raises(Stop):
        next(agent._agent_loop([{"role": "user", "content": "hi"}], agent.tools.Ctx(email="x@acme.test"), [], {"input_tokens": 0, "output_tokens": 0}))
    assert "mcp_servers" not in captured and agent.MCP_BETA not in captured["betas"]
