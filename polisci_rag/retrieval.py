"""Hybrid retrieval: dense vectors + BM25 (SQLite FTS5) fused with reciprocal-rank fusion."""
from __future__ import annotations

import re

import numpy as np

from .llm import embed
from .store import get_store

STOP = set("""a an and are as at be but by for from has have he her his i in is it its of on or
that the their them they this to was were what when where which who why will with how does do
did about into than then there these those not no can could would should may might also its our
we you your between according author authors book books text argue argues""".split())


def fts_query(q: str) -> str:
    words = [w for w in re.findall(r"[\w']+", q.lower()) if w not in STOP and len(w) > 2]
    words = list(dict.fromkeys(words))[:24]
    return " OR ".join(f'"{w}"' for w in words)


def cite(doc_id: int, label: str) -> str:
    return f"[D{doc_id} p.{label}]"


def search(query: str, doc_ids: list[int] | None = None, k: int = 8,
           per_doc_cap: int | None = None) -> list[dict]:
    store = get_store()
    docs = {d["id"]: d for d in store.documents() if d["status"] == "ready"}
    ids = [i for i in (doc_ids or docs.keys()) if i in docs]
    if not ids:
        return []
    pool = max(40, k * 5)
    ranks: dict[int, float] = {}
    # dense
    qv = embed([query], kind="query")[0]
    dense: list[tuple[float, int]] = []
    for d in ids:
        cids, mat = store.vectors(d)
        if len(cids) == 0:
            continue
        sims = mat @ qv
        top = np.argsort(-sims)[:pool]
        dense.extend((float(sims[t]), int(cids[t])) for t in top)
    dense.sort(reverse=True)
    for r, (_, cid) in enumerate(dense[:pool]):
        ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (60 + r)
    # sparse
    match = fts_query(query)
    if match:
        for r, (cid, _) in enumerate(store.fts(match, ids, pool)):
            ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (60 + r)
    ordered = sorted(ranks.items(), key=lambda kv: -kv[1])
    out: list[dict] = []
    per_doc: dict[int, int] = {}
    seen: set[tuple[int, int]] = set()
    labels_cache: dict[int, dict[int, str]] = {}
    sec_cache: dict[int, dict[int, str]] = {}
    for cid, score in ordered:
        c = store.chunk(cid)
        if not c:
            continue
        key = (c["doc_id"], c["idx"])
        if key in seen or (c["doc_id"], c["idx"] - 1) in seen or (c["doc_id"], c["idx"] + 1) in seen:
            continue  # skip near-duplicate neighbours
        if per_doc_cap and per_doc.get(c["doc_id"], 0) >= per_doc_cap:
            continue
        seen.add(key)
        per_doc[c["doc_id"]] = per_doc.get(c["doc_id"], 0) + 1
        d = c["doc_id"]
        if d not in labels_cache:
            labels_cache[d] = store.page_labels(d)
            sec_cache[d] = {s["id"]: s["title"] for s in store.sections(d)}
        lab_a = labels_cache[d].get(c["pno_start"], str(c["pno_start"]))
        lab_b = labels_cache[d].get(c["pno_end"], str(c["pno_end"]))
        out.append({
            "chunk_id": cid, "doc_id": d, "doc_title": docs[d]["title"], "score": round(score, 4),
            "section": sec_cache[d].get(c["section_id"], ""),
            "pno_start": c["pno_start"], "pno_end": c["pno_end"],
            "page_label": lab_a if lab_a == lab_b else f"{lab_a}-{lab_b}",
            "citation": cite(d, lab_a), "text": c["text"],
        })
        if len(out) >= k:
            break
    return out


def format_hits(hits: list[dict], max_chars: int = 1400) -> str:
    if not hits:
        return "No matching passages found."
    parts = []
    for h in hits:
        t = h["text"]
        if len(t) > max_chars:
            t = t[:max_chars].rsplit(" ", 1)[0] + " …"
        parts.append(f"{h['citation']} «{h['doc_title']}» — {h['section']} (pp. {h['page_label']})\n{t}")
    return "\n\n---\n\n".join(parts)
