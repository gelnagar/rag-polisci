"""Paths and persistent user settings."""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import APP_NAME


def _data_root() -> Path:
    override = os.environ.get("POLISCI_RAG_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def install_dir() -> Path:
    """Directory holding the bundled app (and the bundled llama.cpp binaries)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Directory holding bundled resources such as static/ (PyInstaller-aware)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "polisci_rag"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


DATA_ROOT = _data_root()
MODELS_DIR = DATA_ROOT / "models"
DB_PATH = DATA_ROOT / "library.sqlite3"
PDF_DIR = DATA_ROOT / "pdfs"
EXPORT_DIR = DATA_ROOT / "exports"
LOG_DIR = DATA_ROOT / "logs"
SETTINGS_PATH = DATA_ROOT / "settings.json"

for _d in (DATA_ROOT, MODELS_DIR, PDF_DIR, EXPORT_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    # Context window for the chat model. 0 = automatic from installed RAM (8k / 16k / 32k).
    context_size: int = 0
    # -1 = auto (try GPU, fall back to CPU); 0 = CPU only; N = offload N layers.
    gpu_layers: int = -1
    threads: int = 0  # 0 = let llama.cpp decide
    # Let the model "think" before the final answer (better analysis, much slower on CPU).
    thinking: bool = False
    temperature: float = 0.6
    # Retrieval
    chunk_tokens: int = 450
    chunk_overlap_tokens: int = 60
    top_k: int = 8
    max_agent_steps: int = 8
    # Optional: point at any OpenAI-compatible server instead of the bundled model.
    external_base_url: str = ""
    external_api_key: str = ""
    external_model: str = ""
    chat_model_file: str = ""   # override path to a chat GGUF
    embed_model_file: str = ""  # override path to an embedding GGUF
    extra: dict = field(default_factory=dict)


_lock = threading.Lock()
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    with _lock:
        if _settings is None:
            s = Settings()
            if SETTINGS_PATH.exists():
                try:
                    data = json.loads(SETTINGS_PATH.read_text("utf-8"))
                    for k, v in data.items():
                        if hasattr(s, k):
                            setattr(s, k, v)
                except Exception:
                    pass
            _settings = s
        return _settings


def update_settings(**changes) -> Settings:
    s = get_settings()
    with _lock:
        for k, v in changes.items():
            if hasattr(s, k) and v is not None:
                cur = getattr(s, k)
                if isinstance(cur, bool):
                    v = bool(v)
                elif isinstance(cur, int):
                    v = int(v)
                elif isinstance(cur, float):
                    v = float(v)
                setattr(s, k, v)
        SETTINGS_PATH.write_text(json.dumps(asdict(s), indent=2), "utf-8")
    return s
