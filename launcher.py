"""Desktop entry point: starts the local server and opens the UI in the browser.

A small control window shows status and lets the user reopen the UI or quit (which also
stops the background AI engine).
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

import socket

BASE_PORT = int(os.environ.get("POLISCI_RAG_PORT", "8765"))
PORT = BASE_PORT
URL = f"http://127.0.0.1:{PORT}/"


def _set_port(p: int) -> None:
    global PORT, URL
    PORT, URL = p, f"http://127.0.0.1:{p}/"


def _port_free(p: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", p))
            return True
        except OSError:
            return False


def _fix_streams() -> None:
    # In a windowed (no-console) build sys.stdout/stderr are None; give them a log file.
    from polisci_rag.config import LOG_DIR
    if sys.stdout is None or sys.stderr is None:
        f = open(LOG_DIR / "app.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def _already_running() -> bool:
    import httpx
    try:
        r = httpx.get(URL + "api/status", timeout=1.5)
        return r.status_code == 200 and "version" in r.json()
    except Exception:
        return False


def _pick_port() -> bool:
    """Return True if our app is already running (on one of our ports); else pick a free port."""
    for p in range(BASE_PORT, BASE_PORT + 10):
        _set_port(p)
        if _already_running():
            return True
    for p in range(BASE_PORT, BASE_PORT + 10):
        if _port_free(p):
            _set_port(p)
            return False
    _set_port(BASE_PORT)
    return False


def _serve() -> None:
    import uvicorn
    from polisci_rag.server import app
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_config=None, log_level="warning")


def main() -> None:
    _fix_streams()
    if _pick_port():
        webbrowser.open(URL)
        return
    os.environ["POLISCI_RAG_PORT"] = str(PORT)
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    for _ in range(100):
        if _already_running():
            break
        time.sleep(0.2)
    if "--no-browser" not in sys.argv:
        webbrowser.open(URL)
    if "--headless" in sys.argv:
        t.join()
        return
    try:
        import tkinter as tk
    except Exception:
        t.join()
        return
    from polisci_rag.llm_server import runtime

    root = tk.Tk()
    root.title("PoliSci RAG")
    root.geometry("360x170")
    root.resizable(False, False)
    tk.Label(root, text="PoliSci RAG is running", font=("Segoe UI", 13, "bold")).pack(pady=(14, 2))
    status = tk.StringVar(value="Starting…")
    tk.Label(root, textvariable=status, fg="#555").pack()
    row = tk.Frame(root)
    row.pack(pady=14)
    tk.Button(row, text="Open app", width=12, command=lambda: webbrowser.open(URL)).pack(side="left", padx=6)

    def quit_app():
        status.set("Stopping engine…")
        root.update()
        runtime.shutdown()
        root.destroy()
        os._exit(0)

    tk.Button(row, text="Quit", width=12, command=quit_app).pack(side="left", padx=6)
    tk.Label(root, text=URL, fg="#888", font=("Segoe UI", 8)).pack()

    def refresh():
        status.set(f"AI engine: {runtime.status}")
        root.after(2000, refresh)

    refresh()
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()


if __name__ == "__main__":
    main()
