"""Start 9Sigma Editor from the desktop shortcut, and say so on screen if it cannot.

    runtime\\pythonw.exe -m sigma_editor.launch [--port N]

The installed app's shortcuts run this (docs/RELEASE-CONTRACT.md section 3). ``pythonw`` has
no console, which has two consequences this module deals with:

* ``sys.stdout`` and ``sys.stderr`` are ``None``. Anything that writes to them (uvicorn's log
  setup asks ``sys.stdout.isatty()``) would fail, so both are pointed at
  ``<app_data_dir>/logs/app.log`` before the app is imported. That file is what to send when
  something goes wrong.
* A failure would otherwise end the process with nothing on screen: the person double-clicks
  and nothing happens. Here a failure to start is shown in a Windows message box with the last
  lines the app wrote and where the log is.

Standard library only until the app itself imports.
"""

from __future__ import annotations

import ctypes
import os
import sys
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

APP_NAME = "9Sigma Editor"
LOG_NAME = "app.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
ICON = Path("assets") / "icons" / "sigma.ico"


def app_data_dir() -> Path:
    """The same folder the settings use (``SGE_APP_DATA_DIR`` or %LOCALAPPDATA%)."""
    override = os.environ.get("SGE_APP_DATA_DIR", "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "9SigmaEditor"


def log_path() -> Path:
    return app_data_dir() / "logs" / LOG_NAME


class LogStream:
    """A text stream that appends to the log file and keeps the last lines in memory, so a
    failure can be shown in the message box."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.recent: deque[str] = deque(maxlen=12)
        self._partial = ""
        self._handle: Any = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_file() and path.stat().st_size > MAX_LOG_BYTES:
                    path.replace(path.with_name(path.name + ".1"))
                self._handle = open(path, "a", encoding="utf-8", errors="replace")  # noqa: SIM115
            except OSError:
                self._handle = None

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            text = str(text)
        if self._handle is not None:
            try:
                self._handle.write(text)
                self._handle.flush()
            except (OSError, ValueError):
                pass
        lines = (self._partial + text).split("\n")
        self._partial = lines.pop()
        for line in lines:
            if line.strip():
                self.recent.append(line.rstrip())
        return len(text)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        if self._handle is not None:
            try:
                self._handle.flush()
            except (OSError, ValueError):
                pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("the app log has no file descriptor")

    @property
    def encoding(self) -> str:
        return "utf-8"

    def last_lines(self, count: int = 6) -> list[str]:
        lines = list(self.recent)
        if self._partial.strip():
            lines.append(self._partial.strip())
        return lines[-count:]


def message_box(title: str, text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001 - not on Windows, or no desktop: the log still has it
        if sys.__stderr__ is not None:
            sys.__stderr__.write(f"{title}\n{text}\n")


def _use_app_icon() -> None:
    """The window shows the 9Sigma Editor icon instead of the Python one (pywebview keeps a
    start-up icon it was given before ``webview.start``)."""
    icon = Path(__file__).resolve().parents[2] / ICON
    if not icon.is_file():
        return
    try:
        import webview

        state = getattr(webview, "_state", None)
        if state is not None and "icon" in state and not state["icon"]:
            state["icon"] = str(icon)
    except Exception:  # noqa: BLE001 - cosmetic; the app works without it
        pass


def _explain(lines: list[str], fallback: str) -> str:
    detail = "\n".join(lines[-4:]) if lines else fallback
    return f"{APP_NAME} did not open.\n\n{detail}"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stream: LogStream | None = None
    if sys.stdout is None or sys.stderr is None:
        stream = LogStream(log_path())
        sys.stdout = sys.stdout or stream  # type: ignore[assignment]
        sys.stderr = sys.stderr or stream  # type: ignore[assignment]
        stream.write(f"\n===== {APP_NAME} starting {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    try:
        from .cli import app

        _use_app_icon()
        code = app(args=["open", *args], prog_name="sge", standalone_mode=False)
    except SystemExit as exc:
        code = exc.code
    except BaseException as exc:  # noqa: BLE001 - showing it is the point
        detail = traceback.format_exc()
        if stream is not None:
            stream.write(detail)
        lines = stream.last_lines() if stream is not None else []
        message_box(
            f"{APP_NAME} could not start",
            _explain(lines, f"{type(exc).__name__}: {exc}")
            + f"\n\nTry: start it again. If it happens again, run the installer again and "
            f"send this file to whoever looks after the app:\n{log_path()}",
        )
        return 1
    status = code if isinstance(code, int) else (0 if code in (None, "") else 1)
    if status != 0 and stream is not None:
        message_box(
            f"{APP_NAME} could not start",
            _explain(stream.last_lines(), "The app stopped with an error.")
            + f"\n\nThe log file is:\n{log_path()}",
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
