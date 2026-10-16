"""The API key, saved on this laptop only: ``<app_data_dir>/secrets.json``
(docs/CONTRACT-v0.2.md section 0, rule 3). Never in the repo, never in ``settings.json``
(which the Settings screen shows), never in an edit's folder.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..storage.files import atomic_write_text

SECRETS_FILE = "secrets.json"
KEY_NAME = "elevenlabs_api_key"
HINT_CHARS = 4
LOCK = threading.RLock()


def secrets_path(app_data_dir: Path | str) -> Path:
    return Path(app_data_dir) / SECRETS_FILE


def _read_all(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_api_key(app_data_dir: Path | str) -> str:
    """The saved ElevenLabs key, or an empty string."""
    with LOCK:
        value = _read_all(secrets_path(app_data_dir)).get(KEY_NAME)
    return value.strip() if isinstance(value, str) else ""


def write_api_key(app_data_dir: Path | str, key: str) -> None:
    """Save the key (an empty key removes the entry); other entries of the file are kept."""
    path = secrets_path(app_data_dir)
    cleaned = (key or "").strip()
    with LOCK:
        data = _read_all(path)
        if cleaned:
            data[KEY_NAME] = cleaned
        else:
            data.pop(KEY_NAME, None)
        atomic_write_text(path, json.dumps(data, indent=2) + "\n")


def key_hint(key: str) -> str:
    """The key's last characters for the Settings screen (``...a1b2``); empty without a key."""
    cleaned = (key or "").strip()
    return f"...{cleaned[-HINT_CHARS:]}" if cleaned else ""


__all__ = ["KEY_NAME", "SECRETS_FILE", "key_hint", "read_api_key", "secrets_path", "write_api_key"]
