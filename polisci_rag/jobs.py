"""Background jobs with progress, partial output and cancellation."""
from __future__ import annotations

import itertools
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_ids = itertools.count(1)
# One queue for embedding-only work (imports) and one for LLM-heavy work, so a long
# digest does not block importing new PDFs.
_pools = {"embed": ThreadPoolExecutor(1), "llm": ThreadPoolExecutor(1)}


class Job:
    def __init__(self, kind: str, title: str) -> None:
        self.id = next(_ids)
        self.kind, self.title = kind, title
        self.status = "queued"
        self.progress = 0.0
        self.message = "Waiting in queue…"
        self.output = ""
        self.error: str | None = None
        self.result: dict = {}
        self.created = time.time()
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()
        if self.status == "queued":
            self.status = "cancelled"

    def set_progress(self, p: float, msg: str) -> None:
        self.progress, self.message = max(0.0, min(1.0, p)), msg

    def append(self, text: str) -> None:
        with self._lock:
            self.output += text

    def to_dict(self, with_output: bool = True) -> dict:
        d = {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
             "progress": round(self.progress, 4), "message": self.message, "error": self.error,
             "result": self.result, "created": self.created}
        if with_output:
            d["output"] = self.output
        return d


JOBS: dict[int, Job] = {}


def submit(kind: str, title: str, fn: Callable[[Job], dict | None], pool: str = "llm") -> Job:
    job = Job(kind, title)
    JOBS[job.id] = job

    def runner():
        if job.cancelled():
            return
        job.status = "running"
        try:
            job.result = fn(job) or {}
            job.status = "done"
            job.progress = 1.0
        except InterruptedError:
            job.status, job.message = "cancelled", "Cancelled"
        except Exception as e:
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}"
            job.message = "Failed"
            traceback.print_exc()

    _pools[pool].submit(runner)
    return job
