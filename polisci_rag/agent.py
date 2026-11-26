"""Tool-using research agent (ReAct style, OpenAI tool-calling over llama.cpp)."""
from __future__ import annotations

import json
import re
from typing import Callable

from . import prompts
from .config import get_settings
from .llm import chat, est_tokens
from .retrieval import cite, format_hits, search
from .store import get_store

Emit = Callable[[dict], None]

TOOLS = [
    {"type": "function", "function": {
        "name": "list_documents",
        "description": "List every document in the library with id, title, author, page count and whether a chapter digest exists.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "search_documents",
        "description": "Hybrid semantic + keyword search over the library. Returns passages with citation tags.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look for; use the author's vocabulary."},
            "document_ids": {"type": "array", "items": {"type": "integer"},
                             "description": "Restrict to these document ids (optional)."},
            "k": {"type": "integer", "description": "Number of passages (default 6, max 10)."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_pages",
        "description": "Read the exact text of a page range (max 4 pages). Pages use the same numbers as citation tags.",
        "parameters": {"type": "object", "properties": {
            "document_id": {"type": "integer"},
            "start_page": {"type": "string", "description": "Page number as shown in citations, e.g. '217' or 'xii'."},
            "num_pages": {"type": "integer", "description": "How many pages (1-4)."}},
            "required": ["document_id", "start_page"]}}},
    {"type": "function", "function": {
        "name": "get_outline",
        "description": "Chapter/section outline of a document with page ranges.",
        "parameters": {"type": "object", "properties": {"document_id": {"type": "integer"}},
                       "required": ["document_id"]}}},
    {"type": "function", "function": {
        "name": "get_chapter_summaries",
        "description": "Book overview plus chapter digests (only if a digest was built). Optionally a section index range.",
        "parameters": {"type": "object", "properties": {
            "document_id": {"type": "integer"},
            "from_section": {"type": "integer"}, "to_section": {"type": "integer"}},
            "required": ["document_id"]}}},
    {"type": "function", "function": {
        "name": "compare_documents",
        "description": "Gather side-by-side evidence on one topic from each of several documents.",
        "parameters": {"type": "object", "properties": {
            "document_ids": {"type": "array", "items": {"type": "integer"}},
            "topic": {"type": "string"}},
            "required": ["document_ids", "topic"]}}},
    {"type": "function", "function": {
        "name": "ready_to_answer",
        "description": "Call when you have gathered enough evidence to write the final answer.",
        "parameters": {"type": "object", "properties": {
            "plan": {"type": "string", "description": "One-line outline of the answer."}}}}},
]


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class ToolBox:
    def __init__(self) -> None:
        self.store = get_store()
        self.citations: dict[str, dict] = {}

    def _remember(self, hits: list[dict]) -> None:
        for h in hits:
            self.citations.setdefault(h["citation"], {
                "doc_id": h["doc_id"], "doc_title": h["doc_title"], "page": h["page_label"],
                "pno": h["pno_start"], "text": h["text"][:600]})

    def run(self, name: str, args: dict) -> str:
        fn = getattr(self, "t_" + name, None)
        if not fn:
            return f"Unknown tool {name}."
        try:
            return fn(**args)
        except TypeError as e:
            return f"Bad arguments for {name}: {e}"

    def t_list_documents(self, **_) -> str:
        rows = []
        for d in self.store.documents():
            rows.append(f"D{d['id']}: «{d['title']}»{' by ' + d['author'] if d['author'] else ''} — "
                        f"{d['pages']} pages, {d['n_sections']} sections, status {d['status']}, "
                        f"digest {'yes' if d['summary'] else 'partial' if d['n_summarized'] else 'no'}")
        return "\n".join(rows) or "The library is empty."

    def t_search_documents(self, query: str = "", document_ids=None, k=6, **_) -> str:
        ids = [_int(i) for i in (document_ids or []) if _int(i) is not None] or None
        hits = search(query, ids, k=max(1, min(10, _int(k, 6))))
        self._remember(hits)
        return format_hits(hits)

    def t_read_pages(self, document_id=None, start_page="1", num_pages=2, **_) -> str:
        d = _int(document_id)
        if d is None or not self.store.document(d):
            return "Unknown document id."
        pno = self.store.pno_for_label(d, str(start_page)) or _int(start_page, 1)
        n = max(1, min(4, _int(num_pages, 2)))
        rows = self.store.pages_text(d, pno, pno + n - 1)
        if not rows:
            return "No such pages."
        title = self.store.document(d)["title"]
        out = []
        for r in rows:
            c = cite(d, r["label"])
            self.citations.setdefault(c, {"doc_id": d, "doc_title": title, "page": r["label"],
                                          "pno": r["pno"], "text": r["text"][:600]})
            out.append(f"{c}\n{r['text'][:3500]}")
        return "\n\n".join(out)

    def t_get_outline(self, document_id=None, **_) -> str:
        d = _int(document_id)
        if d is None:
            return "document_id required."
        labels = self.store.page_labels(d)
        secs = self.store.sections(d)
        if not secs:
            return "No outline."
        return "\n".join(
            f"[{s['idx']}] {s['title']} — pp. {labels.get(s['start_pno'], s['start_pno'])}-"
            f"{labels.get(s['end_pno'], s['end_pno'])}{' (digest)' if s['summary'] else ''}" for s in secs)

    def t_get_chapter_summaries(self, document_id=None, from_section=None, to_section=None, **_) -> str:
        d = _int(document_id)
        doc = self.store.document(d) if d is not None else None
        if not doc:
            return "Unknown document id."
        secs = [s for s in self.store.sections(d) if s["summary"]]
        if not secs and not doc["summary"]:
            return ("No digest has been built for this document yet. Use get_outline + search_documents instead "
                    "(the student can build a digest from the Library tab).")
        a, b = _int(from_section, 0), _int(to_section, 10_000)
        out = []
        if doc["summary"] and from_section is None:
            out.append("BOOK OVERVIEW\n" + doc["summary"])
        budget = int(get_settings().context_size * 4 * 0.35)
        used = sum(len(x) for x in out)
        for s in secs:
            if a <= s["idx"] <= b:
                block = f"[{s['idx']}] {s['title']}\n{s['summary']}"
                if used + len(block) > budget:
                    out.append(f"(…more sections available: call again with from_section={s['idx']})")
                    break
                out.append(block)
                used += len(block)
        return "\n\n".join(out)

    def t_compare_documents(self, document_ids=None, topic: str = "", **_) -> str:
        ids = [_int(i) for i in (document_ids or []) if _int(i) is not None]
        if not ids:
            return "document_ids required."
        per = max(2, 8 // len(ids))
        parts = []
        for d in ids:
            hits = search(topic, [d], k=per)
            self._remember(hits)
            doc = self.store.document(d)
            parts.append(f"=== D{d} «{doc['title'] if doc else '?'}» ===\n" + format_hits(hits, 1000))
        return "\n\n".join(parts)

    def t_ready_to_answer(self, **_) -> str:
        return "OK"


def _compact(messages: list[dict], limit_tokens: int) -> None:
    """Shrink the oldest tool results until the conversation fits the context budget."""
    def total():
        return sum(est_tokens(m.get("content") or "") + 20 for m in messages)
    for m in messages:
        if total() <= limit_tokens:
            return
        if m["role"] == "tool" and len(m["content"]) > 700:
            tags = list(dict.fromkeys(re.findall(r"\[D\d+ p\.[^\]]+\]", m["content"])))
            m["content"] = m["content"][:500] + f"\n…[trimmed to save context; citations seen: {' '.join(tags[:12])}]"


def run_agent(question: str, history: list[dict], doc_ids: list[int] | None, emit: Emit,
              cancel: Callable[[], bool]) -> tuple[str, dict]:
    s = get_settings()
    tb = ToolBox()
    system = prompts.AGENT_SYSTEM + "\n\nLibrary:\n" + tb.t_list_documents()
    if doc_ids:
        system += f"\n\nThe student has focused this conversation on documents: {', '.join('D%d' % i for i in doc_ids)}."
    messages: list[dict] = [{"role": "system", "content": system}]
    for h in history[-6:]:
        messages.append({"role": h["role"], "content": h["content"][:4000]})
    messages.append({"role": "user", "content": question})
    budget = int(s.context_size * 0.62)

    ready = False
    for step in range(s.max_agent_steps):
        if cancel():
            break
        _compact(messages, budget)
        emit({"type": "status", "text": f"Thinking (step {step + 1})…"})
        res = chat(messages, tools=TOOLS, max_tokens=1024, thinking=False, temperature=0.3, cancel=cancel)
        if not res.tool_calls:
            if step == 0 and tb.store.documents():
                # Never answer ungrounded: run a retrieval pass on the question itself.
                args = {"query": question, "k": 6}
                if doc_ids:
                    args["document_ids"] = doc_ids
                emit({"type": "tool", "name": "search_documents", "args": args})
                out = tb.run("search_documents", args)
                messages.append({"role": "assistant", "content": "", "tool_calls": [
                    {"id": "auto_0", "type": "function",
                     "function": {"name": "search_documents", "arguments": json.dumps(args)}}]})
                messages.append({"role": "tool", "tool_call_id": "auto_0", "content": out})
            elif res.content.strip() and not s.thinking:
                emit({"type": "token", "text": res.content})
                return res.content, tb.citations
            ready = True
            break
        messages.append({"role": "assistant", "content": res.content or "",
                         "tool_calls": [{"id": c["id"], "type": "function",
                                         "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                        for c in res.tool_calls]})
        for c in res.tool_calls:
            try:
                args = json.loads(c["arguments"] or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            if c["name"] == "ready_to_answer":
                ready = True
            emit({"type": "tool", "name": c["name"], "args": args})
            out = tb.run(c["name"], args)
            emit({"type": "tool_result", "name": c["name"], "preview": out[:300]})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
        if ready:
            break

    # Final, tool-free answer (with optional deep thinking), streamed to the UI.
    _compact(messages, int(s.context_size * 0.55))
    messages.append({"role": "user", "content": prompts.FINAL_ANSWER})
    emit({"type": "status", "text": "Writing answer…"})
    res = chat(messages, max_tokens=3000, thinking=s.thinking, cancel=cancel,
               on_token=lambda t: emit({"type": "token", "text": t}),
               on_reasoning=lambda t: emit({"type": "reasoning", "text": t}))
    return res.content, tb.citations
