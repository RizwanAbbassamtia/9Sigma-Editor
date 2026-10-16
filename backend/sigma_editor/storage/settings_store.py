"""Reads and writes the per-user files in the app data folder.

* ``settings.json`` keeps the folder choices and the render and caption defaults made in the
  Settings screen (``config.py`` turns them into the effective settings).
* ``.env`` may hold ``FFMPEG_PATH`` / ``FFPROBE_PATH`` lines for a tool that is not on PATH;
  they are loaded into the process environment before the settings are read.

Every read-modify-write of these files runs under :data:`LOCK`. FastAPI serves the settings
endpoint from a thread pool, so two quick saves in the Settings screen can overlap; without
the lock the second save would silently undo the first.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from .files import atomic_write_text

__all__ = ["LOCK", "PATH_KEYS", "SECTION_KEYS", "SettingsStore", "atomic_write_text", "parse_env"]

log = logging.getLogger(__name__)

KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PATH_KEYS = ("projects_dir", "exports_dir")
SECTION_KEYS = ("render", "captions")
"""Nested objects in settings.json (Settings > Render, Settings > Captions); see config.py."""
TOOL_PATH_NAMES = ("FFMPEG_PATH", "FFPROBE_PATH")
"""The only ``.env`` entries this app reads: where the tools are."""

LOCK = threading.RLock()


def parse_env(text: str) -> dict[str, str]:
    """Parse ``NAME=value`` lines. Blank values and unknown line shapes are skipped."""
    keys: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if KEY_NAME_RE.match(name) and value:
            keys[name] = value
    return keys


class SettingsStore:
    """The two per-user files under ``app_data_dir``."""

    def __init__(self, app_data_dir: Path | str) -> None:
        self.app_data_dir = Path(app_data_dir)
        self.settings_file = self.app_data_dir / "settings.json"
        self.env_file = self.app_data_dir / ".env"

    # settings.json --------------------------------------------------------------------

    def read_all(self) -> dict[str, Any]:
        """The whole settings.json mapping. Missing or unreadable file -> empty dict."""
        try:
            data: Any = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def read_paths(self) -> dict[str, str | None]:
        """Folder choices saved earlier. Missing or unreadable file -> empty dict."""
        data = self.read_all()
        paths: dict[str, str | None] = {}
        for key in PATH_KEYS:
            value = data.get(key)
            if key in data and (value is None or isinstance(value, str)):
                paths[key] = value
        return paths

    def read_sections(self) -> dict[str, dict[str, Any]]:
        """The nested ``render`` / ``captions`` objects that exist."""
        data = self.read_all()
        return {
            key: value
            for key, value in data.items()
            if key in SECTION_KEYS and isinstance(value, dict)
        }

    def _write_merged(self, changes: dict[str, Any]) -> None:
        """Replace the given top-level keys and keep every other key in the file."""
        with LOCK:
            data = self.read_all()
            data.update(changes)
            atomic_write_text(self.settings_file, json.dumps(data, indent=2) + "\n")

    def write_paths(self, paths: dict[str, str | None]) -> None:
        """Save the folders; the nested sections in the file are kept."""
        self._write_merged({key: paths.get(key) for key in PATH_KEYS})

    def write_sections(self, sections: dict[str, dict[str, Any]]) -> None:
        """Save one or more nested objects whole; the folders and other sections are kept."""
        clean = {
            key: value
            for key, value in sections.items()
            if key in SECTION_KEYS and isinstance(value, dict)
        }
        if clean:
            self._write_merged(clean)

    # .env -----------------------------------------------------------------------------

    def read_tool_paths(self) -> dict[str, str]:
        """``FFMPEG_PATH`` / ``FFPROBE_PATH`` from ``.env`` (other lines are ignored)."""
        try:
            text = self.env_file.read_text(encoding="utf-8")
        except OSError:
            return {}
        return {name: value for name, value in parse_env(text).items() if name in TOOL_PATH_NAMES}

    def load_into_environ(self) -> dict[str, str]:
        """Put the tool paths from ``.env`` into ``os.environ`` (the file wins over stale
        values). Returns what was loaded."""
        with LOCK:
            loaded: dict[str, str] = {}
            for name, value in self.read_tool_paths().items():
                try:
                    os.environ[name] = value
                except (ValueError, OSError):
                    log.warning("The entry %s in %s could not be loaded.", name, self.env_file)
                    continue
                loaded[name] = value
            return loaded
