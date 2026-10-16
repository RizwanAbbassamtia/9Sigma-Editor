"""Delete a folder to the Recycle Bin (docs/CONTRACT-v0.1.md section 2: "Delete to the
Recycle Bin"), so a deleted edit can be brought back from Explorer.

On Windows the shell does the move (``SHFileOperationW`` with ``FOF_ALLOWUNDO``); anywhere
else, or when the shell refuses (a network drive without a bin), the folder is moved into
``_deleted/`` next to the edits instead, never removed outright.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path

FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FALLBACK_DIR = "_deleted"


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def _shell_recycle(path: Path) -> bool:
    """True when the Windows shell moved ``path`` to the Recycle Bin."""
    if sys.platform != "win32":
        return False
    try:
        operation = _SHFILEOPSTRUCTW()
        operation.hwnd = None
        operation.wFunc = FO_DELETE
        operation.pFrom = str(path) + "\0\0"  # a double-NUL-ended list of one path
        operation.pTo = None
        operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    except (AttributeError, OSError, ValueError):
        return False
    return result == 0 and not operation.fAnyOperationsAborted and not path.exists()


def recycle(path: Path | str, fallback_root: Path | str | None = None) -> Path | None:
    """Move ``path`` to the Recycle Bin. Returns ``None`` when the bin took it, or the folder
    it was moved to under ``<fallback_root>/_deleted/`` when the bin could not be used.
    Raises ``FileNotFoundError`` when there is nothing at ``path``."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"There is nothing at {target} to delete.")
    if _shell_recycle(target):
        return None
    root = Path(fallback_root) if fallback_root is not None else target.parent
    bin_dir = root / FALLBACK_DIR
    bin_dir.mkdir(parents=True, exist_ok=True)
    destination = bin_dir / target.name
    counter = 2
    while destination.exists():
        destination = bin_dir / f"{target.name}-{counter}"
        counter += 1
    try:
        os.rename(target, destination)
    except OSError:
        shutil.move(str(target), str(destination))
    return destination
