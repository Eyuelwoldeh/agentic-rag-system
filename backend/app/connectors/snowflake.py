"""Snowflake via the SQL API, authenticated with a programmatic access token (PAT). Queried live."""
import re
import time
from collections.abc import Iterator

from .base import assert_read_query, request

LABEL = "Snowflake"
BLURB = "Ask questions about your Snowflake data and get charts. Read-only."
CATEGORY = "warehouse"
DIALECT = "Snowflake SQL"
OAUTH = None
PASTE = {"label": "Programmatic access token", "placeholder": "eyJ…",
         "help": "Create it for a user whose default role is read-only"}
CONFIG = [
    {"key": "account", "label": "Account identifier", "help": "e.g. myorg-myaccount (from your Snowflake URL)", "required": True},
    {"key": "warehouse", "label": "Warehouse", "help": "e.g. ANALYTICS_WH", "required": True},
    {"key": "database", "label": "Database", "help": "e.g. PROD", "required": True},
    {"key": "schemas", "label": "Schemas", "help": "Comma-separated. Default: PUBLIC."},
    {"key": "role", "label": "Role", "help": "A read-only role, e.g. ANALYST_RO."},
]
GUIDE = [
    "In Snowflake, create a role with USAGE on the warehouse/database/schemas and SELECT on their tables, nothing more.",
    "Snowsight → your profile → Settings → Authentication → Programmatic access tokens → Generate, restricted to that role.",
    "Fill in the account, warehouse and database below and paste the token.",
]
TIMEOUT_S = 30


def _post(config: dict, creds: dict, sql: str) -> dict:
    account = config["account"].strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", account):
        raise ValueError("Account identifier looks wrong (letters, digits, dots, dashes)")
    base = f"https://{account}.snowflakecomputing.com/api/v2/statements"
    headers = {"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json",
               "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN"}
    body = {"statement": sql, "timeout": TIMEOUT_S, "warehouse": config.get("warehouse"), "database": config.get("database"),
            "parameters": {"MULTI_STATEMENT_COUNT": "1"}}
    if config.get("role"):
        body["role"] = config["role"]
    data = request("POST", base, json=body, headers=headers, retries=2).json()
    deadline = time.time() + TIMEOUT_S
    while data.get("statementHandle") and "resultSetMetaData" not in data and time.time() < deadline:
        time.sleep(1)  # 202: still running
        data = request("GET", f"{base}/{data['statementHandle']}", headers=headers, retries=2).json()
    if "resultSetMetaData" not in data:
        raise ValueError(data.get("message") or "Snowflake query didn't finish in time")
    return data


def whoami(creds: dict) -> str:
    return ""  # the SQL API needs the account config, so create() verifies by listing tables instead


def _schemas(config: dict) -> list[str]:
    return [s.strip().upper() for s in (config.get("schemas") or "PUBLIC").split(",") if s.strip()]


def list_tables(config: dict, creds: dict) -> dict[str, dict[str, str]]:
    schemas = _schemas(config)
    if not all(re.fullmatch(r"[A-Z0-9_$]+", s) for s in schemas):
        raise ValueError("Schema names should be plain identifiers")
    in_list = ", ".join(f"'{s}'" for s in schemas)
    data = _post(config, creds, "SELECT table_schema || '.' || table_name, column_name, data_type "
                                f"FROM information_schema.columns WHERE table_schema IN ({in_list}) "
                                "ORDER BY 1, ordinal_position")
    out: dict[str, dict[str, str]] = {}
    for t, c, typ in data.get("data", []):
        out.setdefault(t, {})[c] = typ
    return out


def query(config: dict, creds: dict, sql: str, max_rows: int) -> dict:
    data = _post(config, creds, assert_read_query(sql))
    cols = [c["name"] for c in data["resultSetMetaData"]["rowType"]]
    types = [c["type"] for c in data["resultSetMetaData"]["rowType"]]
    rows = [[_cast(v, t) for v, t in zip(r, types)] for r in data.get("data", [])]
    return {"columns": cols, "rows": rows[:max_rows], "truncated": len(rows) > max_rows}


def _cast(v, typ: str):
    if v is None or typ not in ("fixed", "real"):
        return v
    try:
        return float(v) if typ == "real" or "." in v else int(v)
    except ValueError:
        return v


def fetch(*_a, **_k) -> Iterator:
    return iter(())
