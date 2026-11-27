"""Checks GitHub Releases for a newer version and launches the in-place updater."""
from __future__ import annotations

import subprocess
import sys
import threading

import httpx

from . import __version__

REPO = "gelnagar/rag-polisci"
INSTALL_SCRIPT = f"https://github.com/{REPO}/releases/latest/download/install.ps1"
_state: dict = {"latest": None, "available": False, "url": f"https://github.com/{REPO}/releases/latest"}


def _ver(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.lstrip("vV").split(".")[:3])
    except ValueError:
        return (0,)


def check() -> None:
    try:
        r = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "PoliSciRAG"})
        if r.status_code == 200:
            tag = r.json().get("tag_name", "")
            _state["latest"] = tag
            _state["available"] = _ver(tag) > _ver(__version__)
    except Exception:
        pass


def check_async() -> None:
    threading.Thread(target=check, daemon=True).start()


def status() -> dict:
    return dict(_state)


def launch_updater() -> None:
    """Start the update script in its own console window. It closes this app, replaces the
    program files (keeping models, library and settings) and starts the new version."""
    if sys.platform != "win32":
        raise RuntimeError("In-app update is only available on Windows.")
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
           f"[Net.ServicePointManager]::SecurityProtocol='Tls12'; irm '{INSTALL_SCRIPT}' | iex"]
    CREATE_NEW_CONSOLE, CREATE_BREAKAWAY_FROM_JOB = 0x10, 0x01000000
    try:
        subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE | CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
