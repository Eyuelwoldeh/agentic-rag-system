"""AI providers (Claude and ChatGPT) behind one interface, with settings an admin edits in the app.

Order of use: the admin's primary provider first, the other as automatic backup when the first is out of
credits or its key stops working. API keys live encrypted in `app_settings` (env vars are a fallback).
"""
import json
import os
from collections.abc import Iterator

import anthropic
import openai

from . import tools
from .db import connect
from .security import decrypt, encrypt

PROVIDERS = {"anthropic": "Claude", "openai": "ChatGPT"}
ENV_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
DEFAULT_MODELS = {"anthropic": os.getenv("AGENT_MODEL", "claude-opus-5-5"), "openai": os.getenv("OPENAI_MODEL", "gpt-5.5")}
CRITIC_MODELS = {"anthropic": os.getenv("CRITIC_MODEL", "claude-haiku-5-5")}  # OpenAI: same model checks itself
EFFORT = os.getenv("AGENT_EFFORT", "medium")
MAX_TURNS = 12
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MCP_BETA = "mcp-client-2025-11-20"


# ---------- settings ----------

def ai_settings() -> dict:
    """{primary, models: {anthropic, openai}, keys: {provider: 'settings'|'env'|None}}. Never returns key values."""
    with connect() as conn:
        rows = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT key, value, secret IS NOT NULL FROM app_settings WHERE key = 'ai' OR key LIKE 'ai_key:%%'").fetchall()}
    value = rows.get("ai", ({}, False))[0] or {}
    keys = {p: ("settings" if rows.get(f"ai_key:{p}", (None, False))[1] else "env" if os.getenv(ENV_KEYS[p]) else None)
            for p in PROVIDERS}
    return {"primary": value.get("primary", "anthropic"),
            "models": {p: (value.get("models") or {}).get(p) or DEFAULT_MODELS[p] for p in PROVIDERS}, "keys": keys}


def api_key(provider: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT secret FROM app_settings WHERE key = %s", (f"ai_key:{provider}",)).fetchone()
    return decrypt(row[0], as_json=False) if row and row[0] else os.getenv(ENV_KEYS[provider])


def save_settings(primary: str | None = None, models: dict | None = None, keys: dict | None = None) -> None:
    cur = ai_settings()
    value = {"primary": primary or cur["primary"], "models": {**cur["models"], **(models or {})}}
    with connect() as conn, conn.transaction():
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('ai', %s) ON CONFLICT (key) DO UPDATE "
                     "SET value = EXCLUDED.value, updated_at = now()", (json.dumps(value),))
        for p, k in (keys or {}).items():
            if k is None:
                continue
            if k == "":  # explicit removal
                conn.execute("DELETE FROM app_settings WHERE key = %s", (f"ai_key:{p}",))
            else:
                conn.execute("INSERT INTO app_settings (key, secret) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE "
                             "SET secret = EXCLUDED.secret, updated_at = now()", (f"ai_key:{p}", encrypt(k)))


def list_models(provider: str, key: str | None = None) -> list[str]:
    """Models this key can use (also proves the key works)."""
    key = key or api_key(provider)
    if not key:
        raise ValueError(f"No {PROVIDERS[provider]} API key set")
    if provider == "anthropic":
        return [m.id for m in anthropic.Anthropic(api_key=key).models.list(limit=100)]
    skip = ("audio", "realtime", "tts", "transcribe", "image", "search", "embedding", "moderation", "whisper", "dall-e", "codex")
    ids = [m.id for m in openai.OpenAI(api_key=key).models.list()]
    return sorted((i for i in ids if i.startswith(("gpt-", "o")) and not any(s in i for s in skip)), reverse=True)


def providers() -> list["Provider"]:
    """Configured providers, primary first."""
    s = ai_settings()
    order = [s["primary"]] + [p for p in PROVIDERS if p != s["primary"]]
    out = []
    for p in order:
        key = api_key(p)
        if key:
            out.append((AnthropicProvider if p == "anthropic" else OpenAIProvider)(key, s["models"][p]))
    return out


def unavailable_reason(e: Exception) -> str | None:
    """Errors meaning "this provider can't be used right now" (vs. a bug): billing, quota, bad key."""
    text = str(e).lower()
    if isinstance(e, (anthropic.AuthenticationError, openai.AuthenticationError)):
        return "API key rejected"
    if isinstance(e, (anthropic.PermissionDeniedError, openai.PermissionDeniedError)):
        return "no access to this model"
    if "credit balance" in text or "insufficient_quota" in text or "exceeded your current quota" in text:
        return "out of API credits"
    if isinstance(e, (anthropic.NotFoundError, openai.NotFoundError)) and "model" in text:
        return "model not found (check Settings)"
    return None


# ---------- shared ----------

def valid_input(args, schema: dict) -> bool:
    """Tool inputs are untrusted model output: validate types, required keys, enums, and reject unknown keys."""
    if not isinstance(args, dict):
        return False
    props = schema["input_schema"]["properties"]
    if any(k not in props for k in args) or any(k not in args for k in schema["input_schema"].get("required", [])):
        return False
    for key, val in args.items():
        typ = props[key].get("type")
        if typ == "string" and (not isinstance(val, str) or not val.strip()):
            return False
        if typ == "integer" and not isinstance(val, int):
            return False
        if typ == "array" and not (isinstance(val, list) and val and all(isinstance(v, str) for v in val)):
            return False
        if "enum" in props[key] and val not in props[key]["enum"]:
            return False
    return True


SCHEMAS = {t["name"]: t for t in tools.CLIENT_TOOLS}


def run_client_tool(name: str, args, ctx: tools.Ctx, evidence: list) -> Iterator[dict]:
    """Execute one of our tools; yields trace events (and any new charts). Returns (content, is_error)."""
    try:
        if name not in SCHEMAS or not valid_input(args, SCHEMAS[name]):
            raise ValueError(f"Invalid input for {name}: {json.dumps(args)[:500]}")
        n_charts = len(ctx.charts)
        out = tools.execute(name, args, ctx)
        evidence.append(f"[{name} {json.dumps(args)}]\n{out}")
        yield {"type": "tool_result", "name": name, "summary": out[:300]}
        for chart in ctx.charts[n_charts:]:
            yield {"type": "chart", "chart": chart}
        return out, False
    except Exception as e:  # tool errors go back to the model so it can recover
        yield {"type": "tool_result", "name": name, "summary": f"Error: {e}", "error": True}
        return f"Error: {e}", True


def critic_prompt(question: str, answer: str, evidence: list[str]) -> str:
    # ponytail: evidence capped at 60k chars for the judge; raise if long documents get cut
    ev = "\n\n".join(evidence)[:60000] or "(no tool evidence: the answer came from model knowledge alone)"
    return ("You are a strict fact-checker reviewing an AI research assistant's answer.\n\n"
            f"<question>\n{question}\n</question>\n\n<evidence>\n{ev}\n</evidence>\n\n<answer>\n{answer}\n</answer>\n\n"
            "Judge: grounded (every factual claim supported by the evidence or a clearly cited source), "
            "complete (all parts of the question answered), relevant (on-topic), and hallucination_risk. "
            "Also check the method: comparing periods of different lengths (a full year vs a partial year), "
            "or a date filter that misses part of the requested period, makes the answer NOT grounded. "
            "An honest 'the sources don't say' counts as grounded. In feedback, list concrete problems and "
            "what information is missing, or 'OK' if none.")


class Provider:
    name = ""
    supports_web = False

    def __init__(self, key: str, model: str):
        self.key, self.model = key, model

    @property
    def label(self) -> str:
        return PROVIDERS[self.name]


# ---------- Claude ----------

class AnthropicProvider(Provider):
    name = "anthropic"
    supports_web = True

    def __init__(self, key: str, model: str, client=None):
        super().__init__(key, model)
        self.client = client or anthropic.Anthropic(api_key=key)

    def start(self, history: list[dict], q: str) -> list:
        return [*history, {"role": "user", "content": q}]

    def add_feedback(self, conv: list, text: str) -> None:
        conv.append({"role": "user", "content": text})

    def loop(self, conv: list, ctx: tools.Ctx, evidence: list, usage: dict, system: str, mcp: tuple) -> Iterator[dict]:
        servers, toolsets = mcp
        extra = {"mcp_servers": servers} if servers else {}
        for _ in range(MAX_TURNS):
            with self.client.beta.messages.stream(
                model=self.model, max_tokens=64000, system=system, tools=tools.TOOLS + toolsets, messages=conv,
                thinking={"type": "adaptive", "display": "summarized"}, output_config={"effort": EFFORT},
                betas=[FALLBACK_BETA] + ([MCP_BETA] if servers else []), fallbacks="default", **extra,
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        yield {"type": "text", "text": event.text}
                    elif event.type == "thinking":
                        yield {"type": "thinking", "text": event.thinking}
                    elif event.type == "content_block_stop":
                        yield from self._block_events(event.content_block, ctx, evidence)
                msg = stream.get_final_message()

            usage["input_tokens"] += msg.usage.input_tokens
            usage["output_tokens"] += msg.usage.output_tokens
            conv.append({"role": "assistant", "content": msg.content})
            if msg.stop_reason == "refusal":
                raise RuntimeError("The model declined this request.")
            if msg.stop_reason == "pause_turn":  # server tool loop hit its limit; re-send to resume
                continue
            if msg.stop_reason == "max_tokens":
                raise RuntimeError("Response hit max_tokens before finishing.")
            if msg.stop_reason != "tool_use":
                for b in msg.content:  # web citations carry the quoted text: give it to the critic
                    for c in getattr(b, "citations", None) or []:
                        if getattr(c, "cited_text", None):
                            evidence.append(f"[web: {getattr(c, 'url', '')}]\n{c.cited_text}")
                return "".join(b.text for b in msg.content if b.type == "text")
            results = []
            for b in msg.content:
                if b.type == "tool_use":
                    out, is_error = yield from run_client_tool(b.name, b.input, ctx, evidence)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, **({"is_error": True} if is_error else {})})
            conv.append({"role": "user", "content": results})
        raise RuntimeError(f"No final answer after {MAX_TURNS} tool turns.")

    @staticmethod
    def _block_events(block, ctx, evidence) -> Iterator[dict]:
        if block.type in ("tool_use", "server_tool_use"):
            yield {"type": "tool", "name": block.name, "input": block.input}
        elif block.type == "mcp_tool_use":
            yield {"type": "tool", "name": f"mcp:{block.name}", "input": block.input}
        elif block.type == "mcp_tool_result":
            text = "".join(getattr(c, "text", "") for c in (block.content if isinstance(block.content, list) else []))
            evidence.append(f"[mcp result]\n{text or block.content}")
            yield {"type": "tool_result", "name": "mcp", "summary": (text or str(block.content))[:300],
                   "error": bool(getattr(block, "is_error", False))}
        elif block.type == "web_search_tool_result" and isinstance(block.content, list):
            for r in block.content:
                if not any(s.get("url") == r.url for s in ctx.sources):
                    ctx.sources.append({"label": f"W{len(ctx.sources) + 1}", "kind": "web", "url": r.url, "title": r.title})
            yield {"type": "tool_result", "name": "web_search", "summary": f"{len(block.content)} results"}
        elif block.type == "bash_code_execution_tool_result":
            out = getattr(block.content, "stdout", "") or getattr(block.content, "stderr", "")
            evidence.append(f"[code output]\n{out}")
            yield {"type": "tool_result", "name": "code_execution", "summary": out[:500]}

    def review(self, question, answer, evidence, usage, verdict_model):
        resp = self.client.messages.parse(
            model=CRITIC_MODELS["anthropic"], max_tokens=4000, output_format=verdict_model,
            messages=[{"role": "user", "content": critic_prompt(question, answer, evidence)}])
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        return resp.parsed_output


# ---------- ChatGPT ----------

class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, key: str, model: str, client=None):
        super().__init__(key, model)
        self.client = client or openai.OpenAI(api_key=key)
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["input_schema"]}} for t in tools.CLIENT_TOOLS]

    def start(self, history: list[dict], q: str) -> list:
        return [*history, {"role": "user", "content": q}]

    def add_feedback(self, conv: list, text: str) -> None:
        conv.append({"role": "user", "content": text})

    def loop(self, conv: list, ctx: tools.Ctx, evidence: list, usage: dict, system: str, mcp: tuple) -> Iterator[dict]:
        # MCP servers are attached through Claude's API; with ChatGPT the assistant uses our own tools only.
        for _ in range(MAX_TURNS):
            stream = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": system}, *conv], tools=self.tools,
                stream=True, stream_options={"include_usage": True})
            text, calls, finish = "", {}, None
            for chunk in stream:
                if chunk.usage:
                    usage["input_tokens"] += chunk.usage.prompt_tokens or 0
                    usage["output_tokens"] += chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text += delta.content
                    yield {"type": "text", "text": delta.content}
                for tc in delta.tool_calls or []:  # tool calls arrive in fragments keyed by index
                    c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    c["id"] = tc.id or c["id"]
                    if tc.function:
                        c["name"] += tc.function.name or ""
                        c["arguments"] += tc.function.arguments or ""
                finish = choice.finish_reason or finish
            if finish == "length":
                raise RuntimeError("Response hit the length limit before finishing.")
            if finish == "content_filter":
                raise RuntimeError("The model declined this request.")
            if not calls:
                conv.append({"role": "assistant", "content": text})
                return text
            ordered = [calls[i] for i in sorted(calls)]
            conv.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in ordered]})
            for c in ordered:
                try:
                    args = json.loads(c["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = None
                yield {"type": "tool", "name": c["name"], "input": args}
                out, _ = yield from run_client_tool(c["name"], args, ctx, evidence)
                conv.append({"role": "tool", "tool_call_id": c["id"], "content": out})
        raise RuntimeError(f"No final answer after {MAX_TURNS} tool turns.")

    def review(self, question, answer, evidence, usage, verdict_model):
        resp = self.client.chat.completions.parse(
            model=self.model, response_format=verdict_model,
            messages=[{"role": "user", "content": critic_prompt(question, answer, evidence)}])
        if resp.usage:
            usage["input_tokens"] += resp.usage.prompt_tokens or 0
            usage["output_tokens"] += resp.usage.completion_tokens or 0
        parsed = resp.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("Fact-check returned no verdict")
        return parsed

