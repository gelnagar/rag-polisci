"""Export generated Markdown to .md or .docx, with a numbered source list."""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.shared import Pt

from .config import EXPORT_DIR
from .store import get_store

CITE_RE = re.compile(r"\[D(\d+) p\.([^\]]+)\]")


def humanize_citations(md: str) -> str:
    """Turn [D3 p.217] into (Short Title, p. 217) and append a sources list."""
    store = get_store()
    titles: dict[int, str] = {}
    used: dict[int, str] = {}

    def repl(m):
        d = int(m.group(1))
        if d not in titles:
            doc = store.document(d)
            titles[d] = doc["title"] if doc else f"Document {d}"
            if doc:
                used[d] = f"{doc['author'] + ', ' if doc['author'] else ''}*{doc['title']}*"
        short = titles[d] if len(titles[d]) <= 40 else titles[d][:37].rsplit(" ", 1)[0] + "…"
        return f"({short}, p. {m.group(2)})"

    out = CITE_RE.sub(repl, md)
    if used:
        out += "\n\n## Sources\n\n" + "\n".join(f"- {v}" for v in used.values())
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "", name).strip()[:80] or "export"


def to_markdown(title: str, md: str) -> Path:
    p = EXPORT_DIR / f"{_safe(title)}.md"
    p.write_text(humanize_citations(md), "utf-8")
    return p


def _add_inline(par, text: str) -> None:
    for tok in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            par.add_run(tok[2:-2]).bold = True
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 1:
            par.add_run(tok[1:-1]).italic = True
        else:
            par.add_run(tok)


def to_docx(title: str, md: str) -> Path:
    md = humanize_citations(md)
    doc = Document()
    doc.styles["Normal"].font.name = "Times New Roman"
    doc.styles["Normal"].font.size = Pt(12)
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\|[\s:\-|]+\|$", lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            ncol = max(len(r) for r in rows)
            t = doc.add_table(rows=len(rows), cols=ncol)
            t.style = "Table Grid"
            for r, row in enumerate(rows):
                for c, val in enumerate(row):
                    _add_inline(t.cell(r, c).paragraphs[0], val)
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            doc.add_heading(m.group(2).strip("# "), level=len(m.group(1)) - 1)
        elif re.match(r"^\s*[-*]\s+", line):
            _add_inline(doc.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*]\s+", "", line))
        elif re.match(r"^\s*\d+[.)]\s+", line):
            _add_inline(doc.add_paragraph(style="List Number"), re.sub(r"^\s*\d+[.)]\s+", "", line))
        elif line.startswith(">"):
            p = doc.add_paragraph(style="Quote")
            _add_inline(p, line.lstrip("> "))
        elif line.strip() in ("---", "***"):
            pass
        elif line.strip():
            _add_inline(doc.add_paragraph(), line)
        i += 1
    p = EXPORT_DIR / f"{_safe(title)}.docx"
    doc.save(str(p))
    return p
