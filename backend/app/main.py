import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import agent, auth, connectors, dashboards, datasources, insights, llm, oauth, tableacl
from .db import connect, migrate
from .ingest import drop_tables, ingest_document, ingest_spreadsheet
from .security import FRONTEND_URL, User, csrf_middleware, current_user, require_admin


@asynccontextmanager
async def lifespan(_app):
    migrate()
    connectors.start_scheduler()
    yield


app = FastAPI(title="Agentic RAG", lifespan=lifespan)
app.middleware("http")(csrf_middleware)
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_URL], allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["content-type", "x-requested-with"])
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(dashboards.router)
app.include_router(insights.router)

MAX_UPLOAD = 25 * 1024 * 1024
DOC_TYPES = (".pdf", ".txt", ".md")
SHEET_TYPES = (".csv", ".xlsx", ".xlsm")


# ---------- chat ----------

class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class ChatRequest(BaseModel):
    question: str
    history: list[Turn] = Field(default_factory=list, max_length=20)
    analyze: str | None = Field(None, pattern=r"^[a-z0-9_]{1,63}$")  # spreadsheet table for the Analyze button


@app.post("/api/chat")
def chat(req: ChatRequest, user: User = Depends(current_user)):
    def sse():
        for ev in agent.run(req.question, [t.model_dump() for t in req.history], user_id=user.id, email=user.email,
                            analyze=req.analyze):
            yield f"data: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


# ---------- uploads & library (any signed-in user) ----------

@app.post("/api/upload")
async def upload(file: UploadFile, visibility: Literal["everyone", "private"] = Form("everyone"),
                 user: User = Depends(current_user)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 25MB)")
    name = (file.filename or "upload")[:200]
    try:
        if name.lower().endswith(SHEET_TYPES):
            tables = ingest_spreadsheet(name, data, user_id=user.id, allowed=[user.email] if visibility == "private" else None)
            return {"kind": "tables", "visibility": visibility, "tables": tables}
        if name.lower().endswith(DOC_TYPES):
            doc = ingest_document(name, data, user_id=user.id, allowed=[user.email] if visibility == "private" else None)
            return {"kind": "document", "visibility": visibility, **doc}
    except ValueError as e:
        raise HTTPException(422, str(e))
    raise HTTPException(415, f"Unsupported file type. Use {', '.join(DOC_TYPES + SHEET_TYPES)}")


@app.get("/api/sources")
def list_sources(user: User = Depends(current_user)):
    with connect() as conn:
        docs = conn.execute(
            "SELECT d.id, d.name, count(c.id), d.created_at, d.uploaded_by = %s, d.allowed IS NOT NULL FROM documents d "
            "LEFT JOIN chunks c ON c.document_id = d.id WHERE d.source = 'upload' "
            "AND (d.allowed IS NULL OR %s = ANY(d.allowed)) GROUP BY d.id ORDER BY d.created_at DESC",
            (user.id, user.email)).fetchall()
        mine = {r[0] for r in conn.execute("SELECT name FROM data_tables WHERE uploaded_by = %s", (user.id,)).fetchall()}
    schemas = datasources.list_tables(datasources.UPLOADS, user.email)
    titles = tableacl.visible_tables(user.email)
    return {
        "documents": [{"id": r[0], "name": r[1], "chunks": r[2], "created_at": r[3], "mine": r[4], "private": r[5]}
                      for r in docs],
        "tables": [{"name": t, "title": titles[t]["title"], "source": titles[t]["source"], "private": titles[t]["restricted"],
                    "mine": t in mine, "columns": cols} for t, cols in schemas.items() if t in titles],
    }


@app.delete("/api/tables/{name}")
def delete_table(name: str, user: User = Depends(current_user)):
    """Admins can delete any uploaded spreadsheet; members only their own. Synced sheets go with their connection."""
    n = drop_tables("name = %s AND source = 'upload' AND (%s OR uploaded_by = %s)", (name, user.role == "admin", user.id))
    if not n:
        raise HTTPException(403, "You can only delete spreadsheets you uploaded")
    return {"ok": True}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int, user: User = Depends(current_user)):
    """Admins can delete any upload; members only their own."""
    with connect() as conn:
        deleted = conn.execute(
            "DELETE FROM documents WHERE id = %s AND source = 'upload' AND (%s OR uploaded_by = %s) RETURNING id",
            (doc_id, user.role == "admin", user.id)).fetchone()
    if not deleted:
        raise HTTPException(403, "You can only delete documents you uploaded")
    return {"ok": True}


# ---------- observability ----------

@app.get("/api/runs")
def list_runs(limit: int = 50, user: User = Depends(current_user)):
    """Admins see every run; members see their own."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT r.id, r.question, r.answer, r.events, r.input_tokens, r.output_tokens, r.latency_ms, r.attempts, "
            "r.critic, r.created_at, u.email, r.feedback FROM runs r LEFT JOIN users u ON u.id = r.user_id "
            "WHERE %s OR r.user_id = %s ORDER BY r.id DESC LIMIT %s",
            (user.role == "admin", user.id, min(limit, 200)),
        ).fetchall()
    keys = ["id", "question", "answer", "events", "input_tokens", "output_tokens", "latency_ms", "attempts", "critic",
            "created_at", "user", "feedback"]
    return [dict(zip(keys, r)) for r in rows]


# ---------- connectors (admin) ----------

class NewConnection(BaseModel):
    kind: str
    name: str = Field("", max_length=100)
    config: dict[str, str] = Field(default_factory=dict)
    credential: str = Field("", max_length=20000)  # pasted token / key; encrypted before storage, never returned
    allowed: list[str] | None = Field(None, max_length=500)  # who can see this source's content; None = everyone


class Access(BaseModel):
    allowed: list[str] | None = Field(None, max_length=500)


@app.get("/api/connectors")
def connector_catalog(_: User = Depends(require_admin)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.id, c.kind, c.name, c.config, c.enabled, c.last_synced_at, c.status, c.error, c.account, "
            "c.allowed, count(d.id) FROM connections c LEFT JOIN documents d ON d.connection_id = c.id GROUP BY c.id ORDER BY c.id"
        ).fetchall()
    keys = ["id", "kind", "name", "config", "enabled", "last_synced_at", "status", "error", "account", "allowed", "documents"]
    conns = [dict(zip(keys, r)) | {"running": connectors.is_running(r[0])} for r in rows]
    return {"available": connectors.catalog(oauth.configured_providers()), "connections": conns,
            "interval_minutes": connectors.SYNC_INTERVAL_MIN}


@app.post("/api/connections")
def create_connection(body: NewConnection, user: User = Depends(require_admin)):
    """Manual connection (website, or a pasted token). OAuth connections are created in the OAuth callback."""
    try:
        creds = connectors.parse_paste(body.kind, body.credential) if body.credential.strip() else None
        conn_id = connectors.create(body.kind, body.name, body.config, creds=creds, user_id=user.id, allowed=body.allowed)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    connectors.sync_in_background(conn_id)
    return {"id": conn_id}


@app.post("/api/connections/{conn_id}/sync")
def sync_connection(conn_id: int, full: bool = False, _: User = Depends(require_admin)):
    connectors.sync_in_background(conn_id, full)
    return {"ok": True}


@app.put("/api/connections/{conn_id}/access")
def set_access(conn_id: int, body: Access, _: User = Depends(require_admin)):
    """Change who can see a source. Takes effect immediately for search and queries (no re-sync needed)."""
    try:
        allowed = connectors.clean_allowed(body.allowed)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with connect() as conn:
        conn.execute("UPDATE connections SET allowed = %s WHERE id = %s", (allowed, conn_id))
    return {"allowed": allowed}


@app.patch("/api/connections/{conn_id}")
def toggle_connection(conn_id: int, enabled: bool, _: User = Depends(require_admin)):
    with connect() as conn:
        conn.execute("UPDATE connections SET enabled = %s WHERE id = %s", (enabled, conn_id))
    return {"ok": True}


@app.delete("/api/connections/{conn_id}")
def delete_connection(conn_id: int, _: User = Depends(require_admin)):
    """Removes the connection, its stored credentials, and everything it indexed (documents and tables)."""
    drop_tables("connection_id = %s", (conn_id,))
    with connect() as conn:
        conn.execute("DELETE FROM connections WHERE id = %s", (conn_id,))
    return {"ok": True}


# ---------- home: getting-started checklist + suggested questions ----------

@app.get("/api/home")
def home(user: User = Depends(current_user)):
    with connect() as conn:
        docs = conn.execute(
            "SELECT d.name, d.source FROM documents d LEFT JOIN connections c ON c.id = d.connection_id "
            "WHERE (d.allowed IS NULL OR %s = ANY(d.allowed)) AND (c.allowed IS NULL OR %s = ANY(c.allowed)) "
            "ORDER BY d.created_at DESC LIMIT 200", (user.email, user.email)).fetchall()
        asked, tiles, users = conn.execute(
            "SELECT (SELECT count(*) FROM runs WHERE user_id = %s), (SELECT count(*) FROM dashboard_tiles WHERE owner_id = %s), "
            "(SELECT count(*) FROM users)", (user.id, user.id)).fetchone()
    data = datasources.available(user.email)
    tables = []
    for src in data:
        try:
            tables += [(src["name"], t) for t in list(datasources.list_tables(src["id"], user.email))[:3]]
        except Exception:
            pass  # a broken warehouse shouldn't break the home page

    suggestions = []
    names = list(dict.fromkeys(t.split(".")[-1].replace("_", " ") for _, t in tables))  # "monthly_revenue" → "monthly revenue"
    if names:
        suggestions.append(f"Chart {names[0]} over time")
    if len(names) > 1:
        suggestions.append(f"What stands out in {names[1]}?")
    elif names:
        suggestions.append(f"Summarize the key numbers in {names[0]}")
    sources = {s for _, s in docs}
    if "slack" in sources:
        suggestions.append("What decisions were made in Slack this week?")
    if docs:
        uploads = [n for n, s in docs if s == "upload"]
        suggestions.append(f"Summarize {uploads[0]}" if uploads else "What are the most important things documented in our knowledge base?")
    if len(sources) > 1:
        suggestions.append("Where do our docs disagree with each other?")
    return {
        "checklist": [
            {"id": "knowledge", "label": "Add knowledge: upload a file" + (" or connect a source" if user.role == "admin" else ""),
             "done": bool(docs or tables)},
            {"id": "ask", "label": "Ask your first question", "done": asked > 0},
            {"id": "pin", "label": "Ask a data question and pin the chart to your dashboard", "done": tiles > 0},
            *([{"id": "team", "label": "Invite your team (Settings → Team)", "done": users > 1}] if user.role == "admin" else []),
        ],
        "suggestions": suggestions[:4],
        "ai": [p.label for p in llm.providers()],
        "counts": {"documents": len(docs), "tables": len(tables), "data_sources": len(data)},
    }


# ---------- AI provider settings (admin) ----------

class AISettings(BaseModel):
    primary: Literal["anthropic", "openai"] | None = None
    models: dict[Literal["anthropic", "openai"], str] | None = None
    keys: dict[Literal["anthropic", "openai"], str] | None = None  # "" removes a stored key


@app.get("/api/ai")
def get_ai(_: User = Depends(require_admin)):
    s = llm.ai_settings()
    return {**s, "labels": llm.PROVIDERS}  # key values are never returned


@app.put("/api/ai")
def put_ai(body: AISettings, _: User = Depends(require_admin)):
    for provider, key in (body.keys or {}).items():
        if key:  # verify before saving: a typo'd key should fail here, not mid-conversation
            try:
                llm.list_models(provider, key.strip())
            except Exception as e:
                raise HTTPException(422, f"{llm.PROVIDERS[provider]} rejected that key: {str(e)[:200]}")
    llm.save_settings(body.primary, {k: v.strip() for k, v in (body.models or {}).items() if v.strip()},
                      {k: v.strip() for k, v in (body.keys or {}).items()})
    return llm.ai_settings()


@app.get("/api/ai/models/{provider}")
def ai_models(provider: Literal["anthropic", "openai"], _: User = Depends(require_admin)):
    try:
        return {"models": llm.list_models(provider)}
    except Exception as e:
        raise HTTPException(422, str(e)[:300])
