"""Thin OpenAI-compatible client for the chat and embedding servers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

import httpx
import numpy as np

from .config import get_settings
from .llm_server import runtime

THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.S)


STRAY_RE = re.compile(r"</?(tool_call|tool_response|ready_to_answer)[^>]*>")


def strip_think(text: str) -> str:
    return STRAY_RE.sub("", THINK_RE.sub("", text or "")).strip()


def est_tokens(text: str) -> int:
    """Cheap token estimate (English prose averages ~4 chars/token)."""
    return max(1, len(text) // 4)


@dataclass
class ChatResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None


def _headers() -> dict:
    s = get_settings()
    h = {"Content-Type": "application/json"}
    if s.external_base_url and s.external_api_key:
        h["Authorization"] = f"Bearer {s.external_api_key}"
    return h


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         max_tokens: int = 2048, temperature: float | None = None,
         thinking: bool | None = None,
         on_token: Callable[[str], None] | None = None,
         on_reasoning: Callable[[str], None] | None = None,
         cancel: Callable[[], bool] | None = None) -> ChatResult:
    """Run one chat completion, streaming tokens to the callbacks."""
    runtime.ensure_started()
    s = get_settings()
    body: dict = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": s.temperature if temperature is None else temperature,
        "stream": True,
    }
    if s.external_base_url:
        body["model"] = s.external_model or "default"
    else:
        think = s.thinking if thinking is None else thinking
        body["chat_template_kwargs"] = {"enable_thinking": bool(think)}
        body["top_p"] = 0.95 if think else 0.8
        body["top_k"] = 20
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    res = ChatResult()
    calls: dict[int, dict] = {}
    in_think = False
    url = runtime.chat_base_url() + "/v1/chat/completions"
    with httpx.stream("POST", url, json=body, headers=_headers(),
                      timeout=httpx.Timeout(30.0, read=1800.0)) as r:
        if r.status_code >= 400:
            r.read()
            raise RuntimeError(f"LLM error {r.status_code}: {r.text[:500]}")
        for line in r.iter_lines():
            if cancel and cancel():
                break
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not ev.get("choices"):
                continue
            ch = ev["choices"][0]
            d = ch.get("delta") or {}
            if ch.get("finish_reason"):
                res.finish_reason = ch["finish_reason"]
            rc = d.get("reasoning_content")
            if rc:
                res.reasoning += rc
                if on_reasoning:
                    on_reasoning(rc)
            c = d.get("content")
            if c:
                # Some servers leave <think> tags inline; route them to reasoning.
                if "<think>" in c:
                    in_think = True
                    c = c.replace("<think>", "")
                if in_think:
                    if "</think>" in c:
                        before, c = c.split("</think>", 1)
                        in_think = False
                        res.reasoning += before
                        if on_reasoning:
                            on_reasoning(before)
                    else:
                        res.reasoning += c
                        if on_reasoning:
                            on_reasoning(c)
                        c = ""
                if c:
                    res.content += c
                    if on_token:
                        on_token(c)
            for tc in d.get("tool_calls") or []:
                i = tc.get("index", 0)
                slot = calls.setdefault(i, {"id": tc.get("id") or f"call_{i}",
                                            "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
    res.tool_calls = [calls[i] for i in sorted(calls)]
    if not res.tool_calls:
        res.tool_calls = _parse_inline_tool_calls(res.content)
        if res.tool_calls:
            res.content = re.sub(r"<tool_call>.*?(</tool_call>|$)", "", res.content,
                                 flags=re.S).strip()
    res.content = strip_think(res.content)
    return res


def _parse_inline_tool_calls(text: str) -> list[dict]:
    """Fallback for models that print <tool_call>{json}</tool_call> as plain text."""
    out = []
    for i, m in enumerate(re.finditer(r"<tool_call>\s*(\{.*?\})\s*(</tool_call>|$)",
                                      text or "", re.S)):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        args = obj.get("arguments", {})
        out.append({"id": f"inline_{i}", "name": obj.get("name", ""),
                    "arguments": args if isinstance(args, str) else json.dumps(args)})
    return out


def complete(prompt: str, *, system: str | None = None, max_tokens: int = 1500,
             thinking: bool = False, temperature: float | None = None,
             cancel: Callable[[], bool] | None = None,
             on_token: Callable[[str], None] | None = None) -> str:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return chat(msgs, max_tokens=max_tokens, thinking=thinking, temperature=temperature,
                cancel=cancel, on_token=on_token).content


# ---------------------------------------------------------------- embeddings
_PREFIX = {"document": "search_document: ", "query": "search_query: "}
MAX_EMBED_CHARS = 6000


def embed(texts: Iterable[str], kind: str = "document", batch: int = 16) -> np.ndarray:
    runtime.ensure_started()
    texts = [_PREFIX[kind] + t[:MAX_EMBED_CHARS] for t in texts]
    vecs: list[list[float]] = []
    url = runtime.embed.base_url + "/v1/embeddings"
    with httpx.Client(timeout=600) as client:
        for i in range(0, len(texts), batch):
            part = texts[i:i + batch]
            r = client.post(url, json={"input": part, "model": "embed"})
            if r.status_code >= 400:
                raise RuntimeError(f"Embedding error {r.status_code}: {r.text[:300]}")
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            vecs.extend(d["embedding"] for d in data)
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.size == 0:
        return arr.reshape(0, 0)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
    return arr
