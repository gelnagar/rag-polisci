"""PDF -> pages -> sections (chapters) -> overlapping chunks -> embeddings."""
from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Callable

import pymupdf as fitz

from .config import PDF_DIR, get_settings
from .llm import embed
from .store import get_store

Progress = Callable[[float, str], None]

CHAPTER_RE = re.compile(
    r"^\s*(chapter|part|book|section|lecture)\s+([0-9ivxlcdm]+|one|two|three|four|five|six|seven|"
    r"eight|nine|ten|eleven|twelve)\b|^\s*(introduction|conclusion|preface|epilogue|prologue|"
    r"afterword|appendix)\s*$", re.I)
MAX_SECTION_PAGES = 45
FALLBACK_SECTION_PAGES = 25


def _norm_line(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def clean_pages(raw: list[str]) -> list[str]:
    """Remove running headers/footers and fix hyphenation."""
    n = len(raw)
    edge = Counter()
    split = [p.splitlines() for p in raw]
    for lines in split:
        ls = [l for l in lines if l.strip()]
        for l in ls[:2] + ls[-2:]:
            edge[_norm_line(l)] += 1
    threshold = max(4, int(n * 0.15))
    boiler = {k for k, v in edge.items() if v >= threshold and len(k) < 120}
    out = []
    for lines in split:
        ls = [l for l in lines if l.strip()]
        keep = []
        for i, l in enumerate(ls):
            is_edge = i < 2 or i >= len(ls) - 2
            if is_edge and (_norm_line(l) in boiler or re.fullmatch(r"\s*[\divxlc]+\s*", l, re.I)):
                continue
            keep.append(l.rstrip())
        text = "\n".join(keep)
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)          # de-hyphenate
        text = re.sub(r"(?<![.!?:;\"'”)])\n(?=[a-z])", " ", text)  # re-join wrapped lines
        text = re.sub(r"[ \t]+", " ", text)
        out.append(text.strip())
    return out


def detect_sections(doc: fitz.Document, pages: list[str]) -> list[dict]:
    n = len(pages)
    toc = doc.get_toc(simple=True) or []
    entries: list[tuple[int, str, int]] = []
    if toc:
        levels = sorted({lvl for lvl, _, _ in toc})
        for lvl in levels:
            entries = [(lvl2, t.strip(), max(1, min(n, p))) for lvl2, t, p in toc if lvl2 <= lvl and p > 0]
            if len(entries) >= 4:
                break
    if len(entries) < 3:
        entries = []
        for i, text in enumerate(pages):
            head = "\n".join(text.splitlines()[:4])
            for line in head.splitlines():
                if CHAPTER_RE.match(line) and len(line) < 100:
                    entries.append((1, line.strip().title(), i + 1))
                    break
    # Build ranges
    entries = sorted({(p, t, l) for l, t, p in entries})
    secs: list[dict] = []
    if entries and entries[0][0] > 3:
        secs.append({"title": "Front matter", "start": 1, "end": entries[0][0] - 1, "level": 1})
    for i, (p, t, l) in enumerate(entries):
        end = entries[i + 1][0] - 1 if i + 1 < len(entries) else n
        if end < p:
            continue
        secs.append({"title": t or f"Section {i + 1}", "start": p, "end": end, "level": l})
    if not secs:
        secs = [{"title": f"Pages {s}-{min(n, s + FALLBACK_SECTION_PAGES - 1)}", "start": s,
                 "end": min(n, s + FALLBACK_SECTION_PAGES - 1), "level": 1}
                for s in range(1, n + 1, FALLBACK_SECTION_PAGES)]
    # Split very long sections so each digest step fits the context window.
    final: list[dict] = []
    for s in secs:
        length = s["end"] - s["start"] + 1
        if length <= MAX_SECTION_PAGES:
            final.append(s)
            continue
        parts = -(-length // MAX_SECTION_PAGES)
        step = -(-length // parts)
        for k in range(parts):
            a = s["start"] + k * step
            b = min(s["end"], a + step - 1)
            final.append({**s, "title": f"{s['title']} (part {k + 1}/{parts})", "start": a, "end": b})
    return final


def chunk_pages(pages: list[str], sections: list[dict], chunk_tokens: int, overlap_tokens: int) -> list[dict]:
    """Paragraph-aware chunking that tracks page ranges. Chunks never cross sections."""
    max_chars, overlap_chars = chunk_tokens * 4, overlap_tokens * 4
    chunks: list[dict] = []
    for si, sec in enumerate(sections):
        paras: list[tuple[int, str]] = []
        for pno in range(sec["start"], sec["end"] + 1):
            for para in re.split(r"\n\s*\n|\n(?=[A-Z“\"])", pages[pno - 1]):
                para = para.strip()
                if para:
                    while len(para) > max_chars:
                        cut = para.rfind(". ", 0, max_chars)
                        cut = cut + 1 if cut > max_chars // 2 else max_chars
                        paras.append((pno, para[:cut].strip()))
                        para = para[cut:].strip()
                    if para:
                        paras.append((pno, para))
        buf: list[tuple[int, str]] = []
        size = 0

        def flush():
            if not buf:
                return
            text = "\n".join(t for _, t in buf)
            if len(text) > 80:
                chunks.append({"idx": len(chunks), "section_idx": si, "pno_start": buf[0][0],
                               "pno_end": buf[-1][0], "text": text})

        for pno, para in paras:
            if size + len(para) > max_chars and buf:
                flush()
                # carry overlap
                keep, ksize = [], 0
                for item in reversed(buf):
                    if ksize + len(item[1]) > overlap_chars:
                        break
                    keep.insert(0, item)
                    ksize += len(item[1])
                buf, size = keep, ksize
            buf.append((pno, para))
            size += len(para)
        flush()
    return chunks


def ingest_pdf(src: Path, progress: Progress, cancel: Callable[[], bool], doc_id: int | None = None,
               title: str | None = None, author: str | None = None) -> int:
    store = get_store()
    s = get_settings()
    progress(0.01, "Opening PDF")
    dest = PDF_DIR / src.name
    if src.resolve() != dest.resolve():
        i = 1
        while dest.exists():
            dest = PDF_DIR / f"{src.stem}_{i}{src.suffix}"
            i += 1
        shutil.copy2(src, dest)
    doc = fitz.open(str(dest))
    meta = doc.metadata or {}
    title = title or (meta.get("title") or "").strip() or src.stem.replace("_", " ")
    author = author or (meta.get("author") or "").strip()
    if doc_id is None:
        doc_id = store.add_document(title, author, src.name, str(dest))
    store.update_document(doc_id, status="extracting", pages=doc.page_count, title=title, author=author)
    try:
        raw, labels = [], []
        for i, page in enumerate(doc):
            if cancel():
                raise InterruptedError
            raw.append(page.get_text("text", sort=True))
            try:
                labels.append(page.get_label() or str(i + 1))
            except Exception:
                labels.append(str(i + 1))
            if i % 25 == 0:
                progress(0.02 + 0.18 * i / max(1, doc.page_count), f"Extracting text: page {i + 1}/{doc.page_count}")
        pages = clean_pages(raw)
        empty = sum(1 for p in pages if len(p) < 40)
        note = ""
        if pages and empty / len(pages) > 0.5:
            note = (f"{empty} of {len(pages)} pages have no extractable text - this PDF looks scanned. "
                    "Run it through OCR (e.g. Adobe 'Recognize Text' or ocrmypdf) and re-import for best results.")
        store.add_pages(doc_id, [(i + 1, labels[i], pages[i]) for i in range(len(pages))])
        progress(0.2, "Detecting chapters")
        sections = detect_sections(doc, pages)
        store.set_sections(doc_id, sections)
        sec_rows = store.sections(doc_id)
        chunks = chunk_pages(pages, sections, s.chunk_tokens, s.chunk_overlap_tokens)
        for c in chunks:
            c["section_id"] = sec_rows[c["section_idx"]]["id"]
        store.update_document(doc_id, status="embedding", note=note)
        batch = 64
        for i in range(0, len(chunks), batch):
            if cancel():
                raise InterruptedError
            part = chunks[i:i + batch]
            vecs = embed([c["text"] for c in part], kind="document")
            store.add_chunks(doc_id, part, vecs)
            progress(0.22 + 0.76 * (i + len(part)) / max(1, len(chunks)),
                     f"Embedding: {i + len(part)}/{len(chunks)} passages")
        store.update_document(doc_id, status="ready")
        progress(1.0, f"Ready: {len(pages)} pages, {len(sections)} sections, {len(chunks)} passages")
        return doc_id
    except BaseException:
        store.delete_document(doc_id)
        raise
    finally:
        doc.close()
