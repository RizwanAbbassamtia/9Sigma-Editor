"""Small file helpers shared by the stores and the export: atomic writes, checksums, safe names.

Windows notes: a file is replaced with ``os.replace`` (all-or-nothing on the same drive); an
open file (Explorer preview, a player) can make the replace fail for a moment, so it is tried
a few times before giving up.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

REPLACE_ATTEMPTS = 8
REPLACE_WAIT_S = 0.15
_UNSAFE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> Path:
    """Write ``text`` to a ``.part`` file next to ``path`` and move it into place."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + f".{os.getpid()}.part")
    part.write_text(text, encoding=encoding, newline="\n")
    _replace(part, target)
    return target


def atomic_write_bytes(path: Path | str, data: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + f".{os.getpid()}.part")
    part.write_bytes(data)
    _replace(part, target)
    return target


def _replace(part: Path, target: Path) -> None:
    last: OSError | None = None
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(part, target)
            return
        except PermissionError as exc:  # the old file is open in another program
            last = exc
            time.sleep(REPLACE_WAIT_S * (attempt + 1))
    try:
        part.unlink()
    except OSError:
        pass
    raise OSError(f"The file {target} could not be written; it may be open in another "
                  f"program. {last}")


def sha256_of(path: Path | str, chunk_size: int = 1 << 20) -> str:
    """The SHA-256 of a file as 64 hex characters."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def safe_file_name(name: str, fallback: str = "file") -> str:
    """A file name Windows accepts: the base name only, unsafe characters replaced, at most
    120 characters, never empty or a reserved device name."""
    base = Path(str(name or "").replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    if not cleaned or not cleaned.strip("_"):
        cleaned = fallback
    stem, dot, suffix = cleaned.rpartition(".")
    if not dot:
        stem, suffix = cleaned, ""
    reserved = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {
        f"lpt{i}" for i in range(1, 10)
    }
    # Windows reserves the part before the FIRST dot: "com1.tar.gz" is the COM1 device too.
    if stem.split(".", 1)[0].lower() in reserved:
        stem = f"{stem}_"
    if len(stem) > 110:
        stem = stem[:110].rstrip(" .") or fallback
    return f"{stem}.{suffix}" if suffix else stem


def unique_path(folder: Path, name: str) -> Path:
    """``folder/name``, or ``folder/name-2.ext`` ... when that exists already."""
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 2
    while True:
        candidate = folder / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
