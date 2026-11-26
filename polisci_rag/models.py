"""Model registry and resumable first-run downloader.

Total download is ~5.3 GB:
  * Qwen3-8B (Q4_K_M, 5.03 GB) - the reasoning / writing model. Strong at long-form
    analysis, tool calling and following structured instructions; 32k native context.
  * nomic-embed-text-v1.5 (f16, 0.27 GB) - the embedding model used for semantic search.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import MODELS_DIR, get_settings


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    filename: str
    url: str
    size: int


CHAT_MODEL = ModelSpec(
    key="chat",
    label="Qwen3-8B Instruct (Q4_K_M)",
    filename="Qwen3-8B-Q4_K_M.gguf",
    url="https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
    size=5_027_783_488,
)
EMBED_MODEL = ModelSpec(
    key="embed",
    label="nomic-embed-text v1.5 (f16)",
    filename="nomic-embed-text-v1.5.f16.gguf",
    url="https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.f16.gguf",
    size=274_290_560,
)
ALL_MODELS = (CHAT_MODEL, EMBED_MODEL)


def model_path(spec: ModelSpec) -> Path:
    s = get_settings()
    override = s.chat_model_file if spec.key == "chat" else s.embed_model_file
    if override and Path(override).exists():
        return Path(override)
    return MODELS_DIR / spec.filename


def is_ready(spec: ModelSpec) -> bool:
    p = model_path(spec)
    if not p.exists():
        return False
    if p.parent == MODELS_DIR and p.name == spec.filename:
        return p.stat().st_size == spec.size
    return p.stat().st_size > 0


def all_ready() -> bool:
    return all(is_ready(m) for m in ALL_MODELS)


class Downloader:
    """Downloads every missing model in a background thread, resuming partial files."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.state = {"running": False, "error": None, "current": None,
                      "done_bytes": 0, "total_bytes": 0, "speed": 0.0, "finished": False}

    def status(self) -> dict:
        with self.lock:
            st = dict(self.state)
        st["models"] = [
            {"key": m.key, "label": m.label, "size": m.size, "ready": is_ready(m),
             "path": str(model_path(m))}
            for m in ALL_MODELS
        ]
        st["ready"] = all_ready()
        return st

    def start(self) -> None:
        with self.lock:
            if self.state["running"]:
                return
            self.state.update(running=True, error=None, finished=False)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            todo = [m for m in ALL_MODELS if not is_ready(m)]
            total = sum(m.size for m in todo)
            done = 0
            with self.lock:
                self.state.update(total_bytes=total, done_bytes=0)
            for m in todo:
                with self.lock:
                    self.state["current"] = m.label
                done = self._download(m, done)
            with self.lock:
                self.state.update(finished=True, current=None)
        except Exception as e:  # pragma: no cover - network dependent
            with self.lock:
                self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            with self.lock:
                self.state["running"] = False

    def _download(self, m: ModelSpec, base_done: int) -> int:
        dest = MODELS_DIR / m.filename
        part = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(6):
            have = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            try:
                with httpx.stream("GET", m.url, headers=headers, follow_redirects=True,
                                  timeout=httpx.Timeout(30.0, read=120.0)) as r:
                    if r.status_code == 200 and have:
                        have = 0  # server ignored range: start over
                        part.unlink(missing_ok=True)
                    elif r.status_code == 416:
                        break
                    r.raise_for_status()
                    mode = "ab" if have else "wb"
                    t0, b0 = time.time(), have
                    with open(part, mode) as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                            have += len(chunk)
                            dt = time.time() - t0
                            with self.lock:
                                self.state["done_bytes"] = base_done + have
                                if dt > 1:
                                    self.state["speed"] = (have - b0) / dt
                break
            except (httpx.HTTPError, OSError):
                if attempt == 5:
                    raise
                time.sleep(3 * (attempt + 1))
        size = part.stat().st_size
        if size != m.size:
            raise RuntimeError(f"{m.filename}: expected {m.size} bytes, got {size}. "
                               "Click download again to resume.")
        part.replace(dest)
        return base_done + size


downloader = Downloader()
