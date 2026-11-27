"""Long-document pipelines: chapter digests, book reviews and multi-text comparisons."""
from __future__ import annotations

import json
import re
from typing import Callable

from . import prompts
from .config import get_settings
from .llm import complete, est_tokens
from .llm_server import effective_context
from .retrieval import cite, format_hits, search
from .store import get_store

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]
Append = Callable[[str], None]


def _author(doc: dict) -> str:
    return f" by {doc['author']}" if doc.get("author") else ""


def _input_budget_chars() -> int:
    """Characters of source text that fit in one call, leaving room for prompt + output."""
    ctx = effective_context()
    return max(6000, int((ctx - 3500) * 0.8) * 4)


def _tagged_text(doc_id: int, rows) -> str:
    return "\n\n".join(f"{cite(doc_id, r['label'])}\n{r['text']}" for r in rows if r["text"].strip())


def _split(text: str, max_chars: int) -> list[str]:
    parts, cur = [], ""
    for block in re.split(r"(?=\[D\d+ p\.)", text):
        if len(cur) + len(block) > max_chars and cur:
            parts.append(cur)
            cur = ""
        cur += block
    if cur:
        parts.append(cur)
    out = []
    for p in parts:  # hard split anything still too big
        while len(p) > max_chars:
            out.append(p[:max_chars])
            p = p[max_chars:]
        out.append(p)
    return out


def _reduce(title: str, texts: list[str], cancel: Cancel) -> str:
    """Hierarchically merge summaries until they fit into one call."""
    budget = _input_budget_chars()
    while sum(len(t) for t in texts) > budget and len(texts) > 1:
        groups, cur, size = [], [], 0
        for t in texts:
            if size + len(t) > budget and cur:
                groups.append(cur)
                cur, size = [], 0
            cur.append(t)
            size += len(t)
        groups.append(cur)
        if len(groups) == len(texts):  # cannot shrink by grouping: truncate instead
            texts = [t[: budget // len(texts)] for t in texts]
            break
        texts = [complete(prompts.COMBINE_SUMMARIES.format(title=title, text="\n\n".join(g)),
                          max_tokens=1100, cancel=cancel) if len(g) > 1 else g[0] for g in groups]
    return "\n\n".join(texts)


# ------------------------------------------------------------------ digest
def build_digest(doc_id: int, mode: str, progress: Progress, cancel: Cancel) -> None:
    """mode='fast' samples each section; mode='thorough' reads every page (map-reduce)."""
    store = get_store()
    doc = store.document(doc_id)
    if not doc:
        raise ValueError("Unknown document")
    secs = store.sections(doc_id)
    budget = _input_budget_chars()
    fast_budget = min(budget, 12_000)
    labels = store.page_labels(doc_id)
    for i, s in enumerate(secs):
        if cancel():
            raise InterruptedError
        if s["summary"] and (s["summary_mode"] == mode or s["summary_mode"] == "thorough"):
            continue
        progress(i / (len(secs) + 1), f"Digesting section {i + 1}/{len(secs)}: {s['title'][:60]}")
        rows = store.pages_text(doc_id, s["start_pno"], s["end_pno"])
        full = _tagged_text(doc_id, rows)
        pages = f"{labels.get(s['start_pno'], s['start_pno'])}-{labels.get(s['end_pno'], s['end_pno'])}"
        common = dict(title=doc["title"], author=_author(doc), section=s["title"], pages=pages,
                      doc_id=doc_id, example_cite=cite(doc_id, labels.get(s["start_pno"], "1")))
        if len(full) < 200:
            store.set_section_summary(s["id"], "_(No extractable text in this section.)_", mode)
            continue
        if mode == "fast" and len(full) > fast_budget:
            chunks = store.chunks_for_section(s["id"])
            n = max(1, fast_budget // 1900)
            idxs = sorted({round(j * (len(chunks) - 1) / max(1, n - 1)) for j in range(n)}) if chunks else []
            text = "\n\n".join(f"{cite(doc_id, labels.get(chunks[j]['pno_start'], chunks[j]['pno_start']))}\n"
                               f"{chunks[j]['text']}" for j in idxs)
            summary = complete(prompts.SECTION_SUMMARY.format(
                text=text[:fast_budget], sampled=" (evenly sampled excerpts)", **common), max_tokens=900, cancel=cancel)
        else:
            parts = _split(full, budget)
            sums = [complete(prompts.SECTION_SUMMARY.format(
                text=p, sampled=f" (part {k + 1} of {len(parts)})" if len(parts) > 1 else "", **common),
                max_tokens=900, cancel=cancel) for k, p in enumerate(parts) if not cancel()]
            summary = sums[0] if len(sums) == 1 else _reduce(doc["title"], sums, cancel)
        store.set_section_summary(s["id"], summary.strip(), mode)
    if cancel():
        raise InterruptedError
    progress(len(secs) / (len(secs) + 1), "Writing book-level overview")
    secs = store.sections(doc_id)
    texts = [f"### [{s['idx']}] {s['title']}\n{s['summary']}" for s in secs if s["summary"]]
    merged = _reduce(doc["title"], texts, cancel)
    overview = complete(prompts.BOOK_OVERVIEW.format(title=doc["title"], author=_author(doc), doc_id=doc_id,
                                                     text=merged), max_tokens=1600, thinking=False, cancel=cancel)
    store.update_document(doc_id, summary=overview.strip(), digest_mode=mode)
    progress(1.0, "Digest complete")


def ensure_digest(doc_id: int, progress: Progress, cancel: Cancel) -> dict:
    store = get_store()
    doc = store.document(doc_id)
    if not doc["summary"]:
        build_digest(doc_id, "fast", progress, cancel)
        doc = store.document(doc_id)
    return doc


def _section_material(doc_id: int, max_chars: int) -> str:
    store = get_store()
    doc = store.document(doc_id)
    secs = [s for s in store.sections(doc_id) if s["summary"]]
    body = "\n\n".join(f"### {s['title']}\n{s['summary']}" for s in secs)
    if len(doc["summary"] or "") + len(body) > max_chars:
        per = max(300, (max_chars - len(doc["summary"] or "")) // max(1, len(secs)))
        body = "\n\n".join(f"### {s['title']}\n{s['summary'][:per]}" for s in secs)
    return f"## Book overview\n{doc['summary']}\n\n## Chapter digests\n{body}"[:max_chars]


# ------------------------------------------------------------------ review
REVIEW_PROBES = [
    "the central thesis and main argument of the book",
    "methodology, research design, data, sources and case selection",
    "the author's response to critics, rival theories and alternative explanations",
    "limitations, scope conditions and caveats acknowledged by the author",
    "conclusions and implications for theory and policy",
]


def write_review(doc_id: int, style: str, instructions: str, progress: Progress, cancel: Cancel,
                 append: Append) -> tuple[str, str]:
    store = get_store()
    progress(0.02, "Checking digest")
    doc = ensure_digest(doc_id, lambda p, m: progress(0.02 + 0.5 * p, m), cancel)
    style_text = prompts.REVIEW_STYLES.get(style, prompts.REVIEW_STYLES["journal"])
    instr = f"Additional instructions from the student: {instructions}" if instructions.strip() else ""
    budget = _input_budget_chars()
    common = dict(style=style_text, title=doc["title"], author=_author(doc), instructions=instr, doc_id=doc_id)
    header = f"# Review: {doc['title']}{_author(doc)}\n\n"
    append(header)
    progress(0.55, "Writing summary & reconstruction")
    part1 = complete(prompts.REVIEW_PART1.format(material=_section_material(doc_id, budget), **common),
                     max_tokens=2600, thinking=get_settings().thinking, cancel=cancel, on_token=append)
    append("\n\n")
    progress(0.75, "Gathering evidence for the critique")
    seen, ev = set(), []
    for probe in REVIEW_PROBES:
        for h in search(probe, [doc_id], k=3):
            if h["chunk_id"] not in seen:
                seen.add(h["chunk_id"])
                ev.append(h)
    evidence = format_hits(ev, 900)[: budget - len(doc["summary"] or "") - 500]
    progress(0.8, "Writing critical assessment")
    part2 = complete(prompts.REVIEW_PART2.format(overview=doc["summary"], evidence=evidence, **common),
                     max_tokens=2600, thinking=get_settings().thinking, cancel=cancel, on_token=append)
    content = header + part1.strip() + "\n\n" + part2.strip()
    progress(1.0, "Review complete")
    return f"Review — {doc['title']}", content


# ------------------------------------------------------------------ compare
def compare(doc_ids: list[int], question: str, axes: list[str], progress: Progress, cancel: Cancel,
            append: Append) -> tuple[str, str]:
    store = get_store()
    docs = []
    for n, d in enumerate(doc_ids):
        docs.append(ensure_digest(d, lambda p, m, n=n: progress(0.4 * (n + p) / len(doc_ids),
                                                                f"[D{d}] {m}"), cancel))
    titles = ", ".join(f"«{d['title']}» (D{d['id']})" for d in docs)
    question = question.strip() or "Compare the central arguments, methods and contributions of these texts."
    budget = _input_budget_chars()
    per = budget // max(1, len(docs)) - 200
    overviews = "\n\n".join(f"### D{d['id']} «{d['title']}»{_author(d)}\n{(d['summary'] or '')[:per]}" for d in docs)
    if not axes:
        progress(0.42, "Choosing axes of comparison")
        raw = complete(prompts.COMPARE_AXES.format(overviews=overviews, question=question), max_tokens=300)
        m = re.search(r"\[.*\]", raw, re.S)
        try:
            axes = [str(a) for a in json.loads(m.group(0))] if m else []
        except json.JSONDecodeError:
            axes = []
        if not axes:
            axes = ["Central thesis", "Theoretical framework", "Methodology and evidence",
                    "Normative commitments", "Implications"]
    axes = axes[:6]
    header = f"# Comparison: {', '.join(d['title'] for d in docs)}\n\n*Question:* {question}\n\n"
    append(header)
    sections = []
    for i, axis in enumerate(axes):
        if cancel():
            raise InterruptedError
        progress(0.45 + 0.4 * i / len(axes), f"Comparing on: {axis}")
        blocks = []
        k = max(2, 9 // len(docs))
        for d in docs:
            hits = search(f"{axis}. {question}", [d["id"]], k=k)
            blocks.append(f"=== D{d['id']} «{d['title']}» ===\n" + format_hits(hits, max(500, per // (k + 1))))
        evidence = "\n\n".join(blocks)[:budget]
        append(f"## {axis}\n\n")
        txt = complete(prompts.COMPARE_AXIS.format(axis=axis, question=question, evidence=evidence),
                       max_tokens=1300, cancel=cancel, on_token=append)
        append("\n\n")
        sections.append(f"## {axis}\n\n{txt.strip()}")
    progress(0.88, "Writing introduction, table and conclusion")
    append("\n---\n\n")
    framing = complete(prompts.COMPARE_FINAL.format(
        titles=titles, sections="\n\n".join(sections)[:budget], question=question),
        max_tokens=2200, thinking=get_settings().thinking, cancel=cancel, on_token=append)
    # Put intro + table first, then axis sections, then conclusion.
    m = re.search(r"^##\s*Synthesis.*", framing, re.M | re.I)
    intro, concl = (framing[:m.start()], framing[m.start():]) if m else (framing, "")
    content = header + intro.strip() + "\n\n" + "\n\n".join(sections) + "\n\n" + concl.strip()
    progress(1.0, "Comparison complete")
    return f"Comparison — {' vs '.join(d['title'][:40] for d in docs)}", content
