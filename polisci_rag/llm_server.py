"""Starts and supervises the bundled llama.cpp `llama-server` processes.

Two processes are run: one for the chat model and one for the embedding model. Each
exposes an OpenAI-compatible HTTP API on localhost. GPU (Vulkan) is tried first when
available; if it fails we transparently fall back to the CPU build.
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from .config import LOG_DIR, get_settings, install_dir
from .models import CHAT_MODEL, EMBED_MODEL, model_path

EXE = "llama-server.exe" if sys.platform == "win32" else "llama-server"


def candidate_binaries() -> list[tuple[str, Path]]:
    """Return (label, path) for each llama-server build we can find, best first."""
    out: list[tuple[str, Path]] = []
    env = os.environ.get("LLAMA_SERVER_BIN")
    if env and Path(env).exists():
        out.append(("custom", Path(env)))
    base = install_dir() / "llama"
    for label in ("vulkan", "cuda", "cpu"):
        p = base / label / EXE
        if p.exists():
            out.append((label, p))
    which = shutil.which("llama-server")
    if which:
        out.append(("path", Path(which)))
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProc:
    def __init__(self, name: str) -> None:
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.backend: str | None = None
        self.error: str | None = None
        self.log_path = LOG_DIR / f"llama-{name}.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def _launch(self, binary: Path, args: list[str], timeout: float) -> bool:
        self.port = _free_port()
        cmd = [str(binary), *args, "--host", "127.0.0.1", "--port", str(self.port)]
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        log = open(self.log_path, "ab")
        log.write(f"\n==== {time.ctime()} {' '.join(cmd)}\n".encode())
        log.flush()
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=flags,
                                     cwd=str(binary.parent))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                r = httpx.get(self.base_url + "/health", timeout=2)
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        self.stop()
        return False

    def start(self, model: Path, extra_args: list[str], gpu_layers: int,
              timeout: float = 300) -> None:
        self.stop()
        self.error = None
        bins = candidate_binaries()
        if not bins:
            self.error = ("llama-server binary not found. Reinstall the app, or set "
                          "LLAMA_SERVER_BIN to a llama.cpp llama-server executable.")
            raise RuntimeError(self.error)
        attempts: list[tuple[str, Path, int]] = []
        for label, b in bins:
            if gpu_layers == 0 and label in ("vulkan", "cuda"):
                continue
            ngl = 0 if label == "cpu" else (999 if gpu_layers < 0 else gpu_layers)
            attempts.append((label, b, ngl))
        for label, b, ngl in attempts:
            args = ["-m", str(model), "-ngl", str(ngl), *extra_args]
            if self._launch(b, args, timeout):
                self.backend = f"{label} (gpu layers: {ngl})"
                return
        tail = ""
        try:
            tail = self.log_path.read_text("utf-8", "replace")[-1500:]
        except OSError:
            pass
        self.error = f"llama-server ({self.name}) failed to start. Log tail:\n{tail}"
        raise RuntimeError(self.error)


class LLMRuntime:
    """Owns the chat and embedding servers (or an external OpenAI-compatible endpoint)."""

    def __init__(self) -> None:
        self.chat = ServerProc("chat")
        self.embed = ServerProc("embed")
        self.lock = threading.Lock()
        self.status = "stopped"
        self.error: str | None = None
        atexit.register(self.shutdown)

    def chat_base_url(self) -> str:
        s = get_settings()
        if s.external_base_url:
            return s.external_base_url.rstrip("/").removesuffix("/v1")
        return self.chat.base_url

    def ensure_started(self) -> None:
        with self.lock:
            s = get_settings()
            need_chat = not s.external_base_url and not self.chat.alive()
            need_embed = not self.embed.alive()
            if not (need_chat or need_embed):
                self.status = "ready"
                return
            self.status = "starting"
            try:
                if need_embed:
                    self.embed.start(model_path(EMBED_MODEL),
                                     ["--embedding", "-c", "2048", "-b", "2048", "-ub", "2048",
                                      "-np", "1"], s.gpu_layers, timeout=180)
                if need_chat:
                    args = ["-c", str(s.context_size), "-np", "1", "--jinja"]
                    if s.threads:
                        args += ["-t", str(s.threads)]
                    self.chat.start(model_path(CHAT_MODEL), args, s.gpu_layers, timeout=600)
                self.status = "ready"
                self.error = None
            except Exception as e:
                self.status = "error"
                self.error = str(e)
                raise

    def restart(self) -> None:
        self.shutdown()
        self.ensure_started()

    def shutdown(self) -> None:
        self.chat.stop()
        self.embed.stop()
        self.status = "stopped"

    def info(self) -> dict:
        s = get_settings()
        return {
            "status": self.status,
            "error": self.error,
            "chat_backend": "external: " + s.external_base_url if s.external_base_url
            else self.chat.backend,
            "embed_backend": self.embed.backend,
            "binaries": [f"{l}: {p}" for l, p in candidate_binaries()],
        }


runtime = LLMRuntime()
