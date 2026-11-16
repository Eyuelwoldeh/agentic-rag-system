"""Export a saved conversation as Markdown or PDF: questions, answers, chart data as tables, sources."""
import html
import io
import re
from datetime import datetime
from typing import Literal

import pymupdf
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from .conversations import get_conversation
from .security import User, current_user

router = APIRouter()
TABLE_ROWS = 50


def _cell(v) -> str:
    return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")


def to_markdown(conv: dict) -> str:
    out = [f"# {conv['title']}", f"Exported {datetime.now():%B %d, %Y}"]
    for t in conv["turns"]:
        out += [f"## {t['question']}", t["answer"].strip() or "_No answer was produced._"]
        for c in t["charts"]:
            rows = [c["columns"], *c["rows"][:TABLE_ROWS]]
            lines = ["| " + " | ".join(map(_cell, r)) + " |" for r in rows]
            out += [f"### {c['title']}", "\n".join([lines[0], "|" + " --- |" * len(c["columns"]), *lines[1:]])]
            if len(c["rows"]) > TABLE_ROWS:
                out.append(f"_First {TABLE_ROWS} of {len(c['rows'])} rows._")
        if t["sources"]:
            lines = []
            for s in t["sources"]:
                name = s.get("title") if s.get("kind") == "web" else s.get("document")
                where = s.get("url") or (f"page {s['page']}" if s.get("page") else s.get("source", ""))
                lines.append(f"- [{s.get('label', '')}] {name}" + (f" ({where})" if where else ""))
            out += ["**Sources**", "\n".join(lines)]
    return "\n\n".join(out) + "\n"


def _inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)  # links → their text
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", text)


def md_html(md: str) -> str:
    """Enough Markdown for answers: headings, paragraphs, bullet and numbered lists, tables, bold, code."""
    out, para, lst, table = [], [], None, []

    def flush():
        nonlocal lst
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if lst:
            out.append(f"</{lst}>")
            lst = None
        if table:
            rows = [[c.strip() for c in re.split(r"(?<!\\)\|", r.strip().strip("|"))] for r in table
                    if not re.fullmatch(r"\|?[\s:|-]+\|?", r.strip())]
            cells = lambda r, tag: "".join(f"<{tag}>{_inline(c.replace(chr(92) + '|', '|'))}</{tag}>" for c in r)  # noqa: E731
            out.append("<table>" + "".join(f"<tr>{cells(r, 'th' if i == 0 else 'td')}</tr>" for i, r in enumerate(rows)) + "</table>")
            table.clear()

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|"):
            if not table:
                flush()
            table.append(s)
            continue
        if table:
            flush()
        h = re.match(r"(#{1,6})\s+(.*)", s)
        item = re.match(r"([-*]|\d+[.)])\s+(.*)", s)
        if not s:
            flush()
        elif h:
            flush()
            out.append(f"<h{len(h[1])}>{_inline(h[2])}</h{len(h[1])}>")
        elif item:
            kind = "ol" if item[1][0].isdigit() else "ul"
            if para or lst != kind:
                flush()
                out.append(f"<{kind}>")
                lst = kind
            out.append(f"<li>{_inline(item[2])}</li>")
        else:
            if lst:
                flush()
            para.append(s.lstrip("> "))
    flush()
    return "\n".join(out)


CSS = """body { font-family: sans-serif; font-size: 10pt; line-height: 1.4; }
h1 { font-size: 18pt; } h2 { font-size: 13pt; margin-top: 16pt; } h3 { font-size: 11pt; }
table { border-collapse: collapse; margin: 4pt 0; } th, td { border: 0.5pt solid #999; padding: 2pt 4pt; font-size: 8.5pt; }
th { background-color: #eee; } code { font-family: monospace; }"""


def to_pdf(md: str) -> bytes:
    story = pymupdf.Story(html=md_html(md), user_css=CSS)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    page = pymupdf.paper_rect("letter")
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(page + (54, 54, -54, -54))
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


@router.get("/api/conversations/{conversation_id}/export")
def export_conversation(conversation_id: int, format: Literal["pdf", "md"] = "md", user: User = Depends(current_user)):
    conv = get_conversation(conversation_id, user)  # 404 unless it's yours
    md = to_markdown(conv)
    name = re.sub(r"[^\w-]+", "_", conv["title"]).strip("_")[:60] or "conversation"
    if format == "md":
        return Response(md, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
    return Response(to_pdf(md), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
