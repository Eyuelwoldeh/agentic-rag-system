"""Google BigQuery. Queried live with a cost cap: every query is dry-run first and refused if it would scan too much."""
import json
from collections.abc import Iterator

from .base import assert_read_query, request

LABEL = "BigQuery"
BLURB = "Ask questions about your BigQuery data and get charts. Read-only, with a cost limit per query."
CATEGORY = "warehouse"
DIALECT = "BigQuery GoogleSQL (quote tables as `project.dataset.table`)"
OAUTH = None
PASTE = {"label": "Service account key (JSON)", "placeholder": '{"type": "service_account", …}', "multiline": True,
         "help": "Grant it BigQuery Data Viewer (on the datasets) + BigQuery Job User (on the project)"}
CONFIG = [
    {"key": "datasets", "label": "Datasets", "help": "Comma-separated, e.g. analytics, sales", "required": True},
    {"key": "project", "label": "Project ID", "help": "Defaults to the service account's project."},
    {"key": "max_gb", "label": "Max GB scanned per query", "help": "Default 10 GB (about $0.06 at on-demand pricing)."},
    {"key": "location", "label": "Location", "help": "Optional, e.g. US or EU."},
]
GUIDE = [
    "Google Cloud console → IAM → Service accounts → Create. Give it BigQuery Job User on the project.",
    "On each dataset you want to expose: Share → add the service account as BigQuery Data Viewer (read-only).",
    "Service account → Keys → Add key → JSON. Paste the file contents below.",
]
API = "https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
SCOPES = ["https://www.googleapis.com/auth/bigquery.readonly"]


def parse_paste(text: str) -> dict:
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("Paste the whole service account JSON key file")
    if info.get("type") != "service_account" or "private_key" not in info:
        raise ValueError("That JSON isn't a service account key")
    return {"service_account": info}


def _token(creds: dict) -> str:
    import google.auth.transport.requests
    from google.oauth2 import service_account

    sa = service_account.Credentials.from_service_account_info(creds["service_account"], scopes=SCOPES)
    sa.refresh(google.auth.transport.requests.Request())
    return sa.token


def _project(config: dict, creds: dict) -> str:
    return (config.get("project") or creds["service_account"]["project_id"]).strip()


def _run(config: dict, creds: dict, sql: str, max_rows: int, dry: bool = False, token: str | None = None) -> dict:
    max_bytes = int(float(config.get("max_gb") or 10) * 1e9)
    body = {"query": sql, "useLegacySql": False, "dryRun": dry, "maxResults": max_rows + 1, "timeoutMs": 30000,
            "maximumBytesBilled": str(max_bytes)}
    if config.get("location"):
        body["location"] = config["location"].strip()
    resp = request("POST", API.format(project=_project(config, creds)), json=body,
                   headers={"Authorization": f"Bearer {token or _token(creds)}"}, retries=2)
    return resp.json()


def whoami(creds: dict) -> str:
    _token(creds)  # proves the key works
    return creds["service_account"]["client_email"]


def list_tables(config: dict, creds: dict) -> dict[str, dict[str, str]]:
    project, token = _project(config, creds), _token(creds)
    out: dict[str, dict[str, str]] = {}
    for ds in [d.strip() for d in config["datasets"].split(",") if d.strip()]:
        if not ds.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid dataset name: {ds}")
        data = _run(config, creds, f"SELECT table_name, column_name, data_type FROM `{project}.{ds}.INFORMATION_SCHEMA.COLUMNS` "
                                   "ORDER BY table_name, ordinal_position", 5000, token=token)
        for r in data.get("rows", []):
            t, c, typ = (f["v"] for f in r["f"])
            out.setdefault(f"{project}.{ds}.{t}", {})[c] = typ
    return out


def query(config: dict, creds: dict, sql: str, max_rows: int) -> dict:
    sql = assert_read_query(sql)
    token = _token(creds)
    dry = _run(config, creds, sql, max_rows, dry=True, token=token)
    scanned = int(dry.get("totalBytesProcessed", 0))
    cap = float(config.get("max_gb") or 10)
    if scanned > cap * 1e9:
        raise ValueError(f"Query would scan {scanned / 1e9:.1f} GB (limit {cap:g} GB). "
                         "Select fewer columns or filter on the partition/date column.")
    data = _run(config, creds, sql, max_rows, token=token)
    if not data.get("jobComplete", True):
        raise ValueError("Query took longer than 30 seconds. Try a narrower query.")
    fields = data.get("schema", {}).get("fields", [])
    cols = [f["name"] for f in fields]
    rows = [[_cell(c["v"], f["type"]) for c, f in zip(r["f"], fields)] for r in data.get("rows", [])]
    return {"columns": cols, "rows": rows[:max_rows], "truncated": len(rows) > max_rows,
            "note": f"scanned {scanned / 1e6:.1f} MB"}


def _cell(v, typ: str):
    """BigQuery's REST API returns every value as a string; convert using the column's declared type."""
    if v is None or not isinstance(v, str):
        return v
    try:
        if typ in ("INTEGER", "INT64"):
            return int(v)
        if typ in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
            return float(v)
    except ValueError:
        pass
    return v


def fetch(*_a, **_k) -> Iterator:
    return iter(())
