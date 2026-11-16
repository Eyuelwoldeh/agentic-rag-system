"""Replacing files in place, and describing what changed between versions (computed, no AI needed).

The change summary is stored on the document/table row; search results and data tools pass it to the assistant
as quiet context, so answers rely on the current version and can mention what changed when it matters.
"""
import difflib

from psycopg import sql

from .analysis import compact
from .db import connect
from .ingest import drop_tables, extract_pages, ingest_spreadsheet, upsert_document
from .previews import save_original

MAX_EXAMPLES = 8
NUMERIC = ("bigint", "integer", "double precision", "numeric", "real")
DATES = ("date", "timestamp without time zone", "timestamp with time zone")


# ---------- documents ----------

def _clip(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe_text_change(old: str, new: str) -> str:
    """Line-level summary: counts plus concrete before/after examples."""
    a = [line for line in (old or "").splitlines() if line.strip()]
    b = [line for line in (new or "").splitlines() if line.strip()]
    changed, added, removed, examples = 0, 0, 0, []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "replace":
            changed += max(i2 - i1, j2 - j1)
            for x, y in zip(a[i1:i2], b[j1:j2]):
                examples.append(f'Changed: "{_clip(x)}" → "{_clip(y)}"')
        elif op == "insert":
            added += j2 - j1
            examples += [f'Added: "{_clip(y)}"' for y in b[j1:j2]]
        elif op == "delete":
            removed += i2 - i1
            examples += [f'Removed: "{_clip(x)}"' for x in a[i1:i2]]
    if not (changed or added or removed):
        return "No text changes (the new file has the same content)."
    head = ", ".join(p for p in (f"{changed} line{'s' * (changed != 1)} changed" if changed else "",
                                 f"{added} added" if added else "", f"{removed} removed" if removed else "") if p)
    more = len(examples) - MAX_EXAMPLES
    return head + ".\n" + "\n".join(examples[:MAX_EXAMPLES]) + (f"\n…and {more} more." if more > 0 else "")


def replace_document(doc_id: int, filename: str, data: bytes, user_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT external_id, allowed, uploaded_by, content, name FROM documents WHERE id = %s AND source = 'upload'",
                           (doc_id,)).fetchone()
    if not row:
        raise LookupError("Document not found")
    key, allowed, owner, old_text, old_name = row
    pages = extract_pages(filename, data)
    doc = upsert_document(filename, pages, external_id=key, allowed=allowed, uploaded_by=owner)  # same identity, same access
    with connect() as conn:
        new_text = conn.execute("SELECT content FROM documents WHERE id = %s", (doc_id,)).fetchone()[0]
    note = describe_text_change(old_text or "", new_text or "")
    if filename != old_name:
        note = f"Renamed from {old_name}. " + note
    _record(key, "document", filename, user_id, note)
    with connect() as conn:
        conn.execute("UPDATE documents SET replaced_at = now(), change_note = %s WHERE id = %s", (note, doc_id))
    save_original(key, filename, data, owner or user_id)
    return {**doc, "change_note": note}


# ---------- spreadsheets ----------

def _stats(table: str) -> dict:
    """Row count, totals of number columns, and date ranges: the facts people compare between versions."""
    with connect() as conn:
        cols = conn.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'data' "
                            "AND table_name = %s ORDER BY ordinal_position", (table,)).fetchall()
        exprs = [sql.SQL("count(*)")]
        for c, t in cols:
            if t in NUMERIC:
                exprs.append(sql.SQL("sum({})").format(sql.Identifier(c)))
            elif t in DATES:
                exprs += [sql.SQL("min({})").format(sql.Identifier(c)), sql.SQL("max({})").format(sql.Identifier(c))]
        vals = list(conn.execute(sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(exprs), sql.Identifier("data", table))).fetchone())
    out = {"rows": vals.pop(0), "columns": [c for c, _ in cols], "sums": {}, "dates": {}}
    for c, t in cols:
        if t in NUMERIC:
            out["sums"][c] = float(vals.pop(0) or 0)
        elif t in DATES:
            out["dates"][c] = (vals.pop(0), vals.pop(0))
    return out


def describe_table_change(old: dict, new: dict) -> str:
    lines = []
    if old["rows"] != new["rows"]:
        d = new["rows"] - old["rows"]
        lines.append(f"Rows: {old['rows']:,} → {new['rows']:,} ({'+' if d > 0 else ''}{d:,})")
    added = [c for c in new["columns"] if c not in old["columns"]]
    gone = [c for c in old["columns"] if c not in new["columns"]]
    if added:
        lines.append("Columns added: " + ", ".join(added))
    if gone:
        lines.append("Columns removed: " + ", ".join(gone))
    for c, (lo, hi) in new["dates"].items():
        if c in old["dates"] and old["dates"][c] != (lo, hi) and lo and hi:
            olo, ohi = old["dates"][c]
            lines.append(f"{c} range: {olo} to {ohi} → {lo} to {hi}")
    for c, v in new["sums"].items():
        if c in old["sums"] and abs(v - old["sums"][c]) > 1e-9:
            o = old["sums"][c]
            pct = f" ({(v - o) / abs(o) * 100:+.1f}%)" if o else ""
            lines.append(f"{c} total: {compact(o)} → {compact(v)}{pct}")
    return "\n".join(lines) or "No changes in row count, columns, totals or date ranges."


def replace_spreadsheet(table: str, filename: str, data: bytes, user_id: int) -> dict:
    """Replace the whole workbook a table came from. Matching tabs keep their tables (and pinned charts)."""
    with connect() as conn:
        row = conn.execute("SELECT external_id, allowed, uploaded_by FROM data_tables WHERE name = %s AND source = 'upload'",
                           (table,)).fetchone()
        if not row or not row[0]:
            raise LookupError("Spreadsheet not found")
        key, allowed, owner = row[0].split("::")[0], row[1], row[2]
        old = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT external_id, name, title FROM data_tables WHERE external_id LIKE %s", (key + "::%",)).fetchall()}
    before = {ext: _stats(name) for ext, (name, _) in old.items()}
    tabs = ingest_spreadsheet(filename, data, user_id=owner, allowed=allowed, identity=key)
    notes = []
    with connect() as conn:
        current = {r[0]: r[1] for r in conn.execute(
            "SELECT external_id, name FROM data_tables WHERE external_id LIKE %s", (key + "::%",)).fetchall()}
    new_exts = {t["external_id"] for t in tabs}
    for ext, name in current.items():
        if ext not in new_exts:  # tab no longer in the file
            drop_tables("name = %s", (name,))
            notes.append(f"Sheet removed: {old.get(ext, (name, name))[1]}")
            continue
        sheet = ext.split("::", 1)[1] or "sheet"
        if ext in before:
            note = describe_table_change(before[ext], _stats(name))
        else:
            note = "New sheet in this version."
        notes.append(f"{sheet}: {note}" if len(new_exts) > 1 else note)
        with connect() as conn:
            conn.execute("UPDATE data_tables SET replaced_at = now(), change_note = %s WHERE name = %s", (note, name))
    summary = "\n".join(notes)
    _record(key, "sheet", filename, user_id, summary)
    save_original(key, filename, data, owner or user_id)
    return {"tables": tabs, "change_note": summary}


def _record(key: str, kind: str, filename: str, user_id: int, note: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO file_versions (key, kind, filename, replaced_by, change_note) VALUES (%s, %s, %s, %s, %s)",
                     (key, kind, filename, user_id, note))


def history(key: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT v.filename, v.change_note, v.created_at, u.email FROM file_versions v "
                            "LEFT JOIN users u ON u.id = v.replaced_by WHERE v.key = %s ORDER BY v.id DESC LIMIT 20", (key,)).fetchall()
    return [{"filename": r[0], "change_note": r[1], "replaced_at": r[2], "by": r[3]} for r in rows]

