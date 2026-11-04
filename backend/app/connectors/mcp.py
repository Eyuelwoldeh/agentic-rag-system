"""Any remote MCP server (Linear, Atlassian, HubSpot, Stripe, Sentry, your own...). Claude calls its tools live.

Anthropic's servers make the MCP connection; we only store the URL and token (encrypted) and decide who may use it.
"""
import json
import re
from collections.abc import Iterator

from .base import request
from .web import _check_public

LABEL = "MCP server"
BLURB = "Connect tools like Linear, Jira, HubSpot or Stripe so the assistant can look things up there."
CATEGORY = "tool"
OAUTH = None
PASTE = {"label": "Access token (optional)", "placeholder": "Bearer token, if the server needs one", "optional": True,
         "help": "Sent to the MCP server as a Bearer token"}
CONFIG = [
    {"key": "url", "label": "Server URL", "help": "The server's https URL, e.g. https://mcp.linear.app/mcp", "required": True},
    {"key": "tools", "label": "Allowed tools", "help": "Comma-separated tool names the AI may use. Blank = all tools "
                                                      "(only do this for read-only servers)."},
]
GUIDE = [
    "Find the server URL in the tool's docs (search \"<product> MCP server\"). It must be a public https URL.",
    "If it needs a key, create one with the least access possible (read-only if you can) and paste it below.",
    "List the tools the AI may use. Prefer read-only tools (search, get, list) unless people should be able to make changes by asking.",
]


def parse_paste(text: str) -> dict:
    token = text.strip().removeprefix("Bearer ").strip()
    return {"access_token": token} if token else {}


def _url(config: dict) -> str:
    url = (config.get("url") or "").strip()
    if not url.startswith("https://"):
        raise ValueError("MCP server URL must start with https://")
    _check_public(url)
    return url


def verify(config: dict, creds: dict | None) -> str:
    """MCP `initialize` handshake so a bad URL or token fails at connect time, not mid-conversation."""
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if creds and creds.get("access_token"):
        headers["Authorization"] = f"Bearer {creds['access_token']}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "agentic-rag", "version": "1"}}}
    text = request("POST", _url(config), json=body, headers=headers, retries=2).text
    payload = text if text.lstrip().startswith("{") else next(
        (line[5:].strip() for line in text.splitlines() if line.startswith("data:")), "{}")
    info = json.loads(payload).get("result", {}).get("serverInfo", {})
    if not info and '"result"' not in text:
        raise ValueError("That URL didn't answer like an MCP server")
    return " ".join(filter(None, [info.get("name"), info.get("version")]))


def server_def(conn_id: int, config: dict, creds: dict | None) -> tuple[dict, dict]:
    """→ (mcp_servers entry, mcp_toolset entry) for the Messages API."""
    name = f"mcp-{conn_id}"
    server = {"type": "url", "url": config["url"], "name": name}
    if creds and creds.get("access_token"):
        server["authorization_token"] = creds["access_token"]
    toolset = {"type": "mcp_toolset", "mcp_server_name": name}
    allowed = [t.strip() for t in (config.get("tools") or "").split(",") if t.strip()]
    if allowed:  # allowlist mode: everything off, then switch on the named tools
        if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", t) for t in allowed):
            raise ValueError("Tool names should be plain identifiers")
        toolset["default_config"] = {"enabled": False}
        toolset["configs"] = {t: {"enabled": True} for t in allowed}
    return server, toolset


def fetch(*_a, **_k) -> Iterator:
    return iter(())
