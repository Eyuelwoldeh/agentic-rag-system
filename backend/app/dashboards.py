"""Dashboards: pinned charts that re-run their query (no AI involved, so refreshing costs nothing)."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import audit, datasources
from .db import connect
from .security import User, current_user
from .tools import _jsonable, build_chart

router = APIRouter()
CACHE_TTL = timedelta(minutes=15)


class NewTile(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    type: str = Field(pattern="^(line|bar|number|table)$")
    x: str | None = Field(None, max_length=200)
    y: list[str] = Field(min_length=1, max_length=4)
    source: str = Field(max_length=50)
    sql: str = Field(min_length=1, max_length=20000)
    shared: bool = False


class TileUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    shared: bool | None = None
    position: int | None = None


def _run(source: str, sql: str, user: User) -> dict:
    result = datasources.run(source, sql, user.email)
    return {**result, "rows": [[_jsonable(v) for v in r] for r in result["rows"]], "source": source, "sql": sql}


def _visible_tile(tile_id: int, user: User, write: bool = False):
    with connect() as conn:
        row = conn.execute("SELECT id, owner_id, title, spec, source, sql, shared, last_result, refreshed_at, error "
                           "FROM dashboard_tiles WHERE id = %s", (tile_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Tile not found")
    owner, shared = row[1], row[6]
    if write and owner != user.id and user.role != "admin":
        raise HTTPException(403, "Only the owner can change this tile")
    if not write and owner != user.id and not shared:
        raise HTTPException(404, "Tile not found")
    return row


@router.get("/api/dashboard")
def list_tiles(user: User = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT t.id, t.title, t.spec, t.source, t.shared, t.owner_id = %s, u.email, t.position "
            "FROM dashboard_tiles t JOIN users u ON u.id = t.owner_id "
            "WHERE t.owner_id = %s OR t.shared ORDER BY t.position, t.id", (user.id, user.id)).fetchall()
    return [{"id": r[0], "title": r[1], "spec": r[2], "source": r[3], "shared": r[4], "mine": r[5], "owner": r[6]}
            for r in rows]


@router.post("/api/dashboard/tiles")
def create_tile(body: NewTile, user: User = Depends(current_user)):
    """Validates by running the query as the user, so nobody can pin data they can't read."""
    try:
        result = _run(body.source, body.sql, user)
        build_chart(result, {"type": body.type, "title": body.title, "x": body.x, "y": body.y})
    except datasources.AccessDenied as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(422, f"Couldn't create the tile: {str(e)[:300]}")
    spec = {"type": body.type, "x": body.x, "y": body.y}
    with connect() as conn:
        pos = conn.execute("SELECT coalesce(max(position), 0) + 1 FROM dashboard_tiles WHERE owner_id = %s", (user.id,)).fetchone()[0]
        tid = conn.execute(
            "INSERT INTO dashboard_tiles (owner_id, title, spec, source, sql, shared, position, last_result, refreshed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id",
            (user.id, body.title, json.dumps(spec), body.source, body.sql, body.shared, pos,
             json.dumps({"columns": result["columns"], "rows": result["rows"], "truncated": result.get("truncated", False)})),
        ).fetchone()[0]
    if body.shared:
        audit.record("dashboard.share", user, f"tile:{tid}", {"title": body.title})
    return {"id": tid}


@router.get("/api/dashboard/tiles/{tile_id}/data")
def tile_data(tile_id: int, refresh: bool = False, user: User = Depends(current_user)):
    row = _visible_tile(tile_id, user)
    _, _, title, spec, source, sql, _, cached, refreshed_at, error = row
    if not any(s["id"] == source for s in datasources.available(user.email)):
        return {"no_access": True, "title": title}  # cache is never served to someone without access to the source
    fresh = refreshed_at and datetime.now(timezone.utc) - refreshed_at < CACHE_TTL
    if cached and fresh and not refresh:
        return {**cached, "spec": spec, "title": title, "refreshed_at": refreshed_at, "error": error}
    try:
        result = _run(source, sql, user)
        data = {"columns": result["columns"], "rows": result["rows"], "truncated": result.get("truncated", False)}
        with connect() as conn:
            conn.execute("UPDATE dashboard_tiles SET last_result = %s, refreshed_at = now(), error = NULL WHERE id = %s",
                         (json.dumps(data), tile_id))
        return {**data, "spec": spec, "title": title, "refreshed_at": datetime.now(timezone.utc), "error": None}
    except Exception as e:
        msg = str(e)[:300]
        with connect() as conn:
            conn.execute("UPDATE dashboard_tiles SET error = %s WHERE id = %s", (msg, tile_id))
        return {**(cached or {"columns": [], "rows": []}), "spec": spec, "title": title, "refreshed_at": refreshed_at,
                "error": msg, "stale": bool(cached)}


@router.patch("/api/dashboard/tiles/{tile_id}")
def update_tile(tile_id: int, body: TileUpdate, user: User = Depends(current_user)):
    _visible_tile(tile_id, user, write=True)
    sets = {k: v for k, v in body.model_dump().items() if v is not None}
    if sets:
        cols = ", ".join(f"{k} = %s" for k in sets)  # keys come from the pydantic model, not the client
        with connect() as conn:
            conn.execute(f"UPDATE dashboard_tiles SET {cols} WHERE id = %s", (*sets.values(), tile_id))
    if body.shared is not None:
        audit.record("dashboard.share" if body.shared else "dashboard.unshare", user, f"tile:{tile_id}")
    return {"ok": True}


@router.delete("/api/dashboard/tiles/{tile_id}")
def delete_tile(tile_id: int, user: User = Depends(current_user)):
    _visible_tile(tile_id, user, write=True)
    with connect() as conn:
        conn.execute("DELETE FROM dashboard_tiles WHERE id = %s", (tile_id,))
    return {"ok": True}
