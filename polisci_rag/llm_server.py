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


def _kill_on_close_job():
    """Windows: a job object that kills llama-server if the app crashes or is killed."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rt", "wt", "ot")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = EXT()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        return k32, job
    except Exception:
        return None


_JOB = _kill_on_close_job()


def _assign_to_job(proc: subprocess.Popen) -> None:
    if _JOB:
        try:
            k32, job = _JOB
            k32.AssignProcessToJobObject(job, int(proc._handle))  # type: ignore[attr-defined]
        except Exception:
            pass


_help_cache: dict[str, str] = {}


def binary_help(binary: Path) -> str:
    key = str(binary)
    if key not in _help_cache:
        try:
            flags = 0x08000000 if sys.platform == "win32" else 0
            r = subprocess.run([key, "--help"], capture_output=True, text=True, timeout=30,
                               creationflags=flags, cwd=str(binary.parent))
            _help_cache[key] = (r.stdout or "") + (r.stderr or "")
        except Exception:
            _help_cache[key] = ""
    return _help_cache[key]


def total_ram_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except Exception:
        return 16.0


def effective_context() -> int:
    """User setting, or (0 = auto) a size that fits this machine's RAM."""
    n = get_settings().context_size
    if n and n > 0:
        return n
    ram = total_ram_gb()
    if ram < 11:
        return 8192
    if ram < 24:
        return 16384
    return 32768


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
        self.gpu = False
        self.crashes = 0
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
        _assign_to_job(self.proc)
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
              timeout: float = 300, force_cpu: bool = False) -> None:
        self.stop()
        self.error = None
        if not model.exists():
            self.error = f"Model file not found: {model}. Download the models first."
            raise RuntimeError(self.error)
        bins = candidate_binaries()
        if not bins:
            self.error = ("llama-server binary not found. Run the repair/update script, or set "
                          "LLAMA_SERVER_BIN to a llama.cpp llama-server executable.")
            raise RuntimeError(self.error)
        attempts: list[tuple[str, Path, list[str]]] = []
        for label, b in bins:
            gpu_build = label in ("vulkan", "cuda")
            if gpu_build and (gpu_layers == 0 or force_cpu):
                continue
            if label == "cpu" or force_cpu:
                gargs = ["-ngl", "0"]
            elif gpu_layers > 0:
                gargs = ["-ngl", str(gpu_layers)]
            elif "--fit" in binary_help(b):
                gargs = []  # newer llama.cpp sizes GPU offload to the free VRAM automatically
            else:
                gargs = ["-ngl", "999"]
            attempts.append((label, b, gargs))
        for label, b, gargs in attempts:
            if self._launch(b, ["-m", str(model), *gargs, *extra_args], timeout):
                self.backend = f"{label} ({' '.join(gargs) or 'auto GPU offload'})"
                self.gpu = label != "cpu" and gargs != ["-ngl", "0"]
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

    def _check_crash(self, sp: ServerProc) -> None:
        """A server that was running but died (often a GPU driver/VRAM problem) -> CPU next time."""
        if sp.proc is not None and sp.proc.poll() is not None:
            sp.crashes += 1
            sp.proc = None

    def ensure_started(self) -> None:
        with self.lock:
            s = get_settings()
            for sp in (self.chat, self.embed):
                self._check_crash(sp)
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
                                      "-np", "1"], s.gpu_layers, timeout=180,
                                     force_cpu=self.embed.crashes > 0)
                if need_chat:
                    args = ["-c", str(effective_context()), "-np", "1", "--jinja"]
                    if s.threads:
                        args += ["-t", str(s.threads)]
                    self.chat.start(model_path(CHAT_MODEL), args, s.gpu_layers, timeout=600,
                                    force_cpu=self.chat.crashes > 0)
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
            "context": effective_context(),
            "ram_gb": round(total_ram_gb(), 1),
        }


runtime = LLMRuntime()
