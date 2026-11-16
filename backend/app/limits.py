"""Spend controls: per-person daily AI token budget, questions-per-minute limit, allowed models per provider.

Stored in app_settings['limits']; 0 or an empty list means no limit.
"""
import json
import re
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from .db import connect

PROVIDERS = ("anthropic", "openai")
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$")


def settings() -> dict:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'limits'").fetchone()
    v = (row[0] if row else None) or {}
    return {"daily_tokens": int(v.get("daily_tokens") or 0), "per_minute": int(v.get("per_minute") or 0),
            "allowed_models": {p: list((v.get("allowed_models") or {}).get(p) or []) for p in PROVIDERS}}


def model_allowed(provider: str, model: str, s: dict | None = None) -> bool:
    allowed = (s or settings())["allowed_models"].get(provider) or []
    return not allowed or model in allowed


def save(daily_tokens: int, per_minute: int, allowed_models: dict[str, list[str]], in_use: dict[str, str]) -> dict:
    """Validate and store. `in_use`: {provider: model} for connected providers; each must stay allowed."""
    if daily_tokens < 0 or per_minute < 0:
        raise ValueError("Limits can't be negative")
    clean = {}
    for p in PROVIDERS:
        models = list(dict.fromkeys(m.strip() for m in allowed_models.get(p) or [] if m.strip()))
        bad = [m for m in models if not MODEL_ID.match(m)]
        if bad:
            raise ValueError(f"Not a model name: {bad[0][:100]}")
        if models and p in in_use and in_use[p] not in models:
            raise ValueError(f"{in_use[p]} is in use for {'Claude' if p == 'anthropic' else 'ChatGPT'}. "
                             "Add it to the allowed list, or choose another model under AI model first.")
        clean[p] = models[:100]
    value = {"daily_tokens": daily_tokens, "per_minute": per_minute, "allowed_models": clean}
    with connect() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('limits', %s) ON CONFLICT (key) DO UPDATE "
                     "SET value = EXCLUDED.value, updated_at = now()", (json.dumps(value),))
    return settings()


def tokens_today(user_id: int) -> int:
    with connect() as conn:
        return conn.execute("SELECT coalesce(sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)), 0) FROM runs "
                            "WHERE user_id = %s AND created_at >= date_trunc('day', now())", (user_id,)).fetchone()[0]


# ponytail: per-process window; with several API replicas each allows the full rate. Move to Postgres or Redis if that matters.
_recent: dict[int, deque] = defaultdict(deque)
_lock = threading.Lock()


def check(user) -> None:
    """Raise 429 if this person is over today's token budget or asking too fast. Counts this question when allowed."""
    s = settings()
    if s["daily_tokens"] and tokens_today(user.id) >= s["daily_tokens"]:
        raise HTTPException(429, f"You've reached today's AI limit ({s['daily_tokens']:,} tokens). It resets at midnight UTC. "
                                 "Ask an administrator if you need more.")
    if s["per_minute"]:
        now = time.monotonic()
        with _lock:
            q = _recent[user.id]
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= s["per_minute"]:
                raise HTTPException(429, f"You've reached the limit of {s['per_minute']} questions per minute. "
                                         "Wait a moment and try again.")
            q.append(now)
