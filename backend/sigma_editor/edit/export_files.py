"""Export: copy the chosen renders, a poster frame and ``export.txt`` with checksums to the
export folder (docs/CONTRACT-v0.1.md section 5).

Pure file work, no FFmpeg: the renders are what the editor rendered, the poster frame is
made by the caller (the first scene's picture or a frame grab). ``export.txt`` is for people
("Name, length, sizes, quality, checksums"); ``checksums.sha256`` is the usual
``<sha256>  <file>`` list a checksum tool verifies; ``export.json`` records the same for the
app (where the export went, so "Open export folder" works).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from slugify import slugify

from ..storage.files import atomic_write_text, safe_file_name, sha256_of, unique_path

EXPORT_TEXT = "export.txt"
CHECKSUMS = "checksums.sha256"
EXPORT_JSON = "export.json"
MAX_FOLDER_SLUG = 48


class ExportError(Exception):
    """Plain English, safe to show."""


@dataclass(frozen=True)
class ExportFile:
    """One file to export: ``source`` on disk, ``label`` for export.txt ("1080p, High quality"),
    ``name`` the file name wanted in the export folder."""

    source: Path
    label: str
    name: str


@dataclass(frozen=True)
class ExportedFile:
    name: str
    label: str
    size_bytes: int
    sha256: str

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "label": self.label, "size_bytes": self.size_bytes,
                "sha256": self.sha256}


@dataclass(frozen=True)
class ExportResult:
    folder: Path
    files: list[ExportedFile]
    text_file: Path
    checksums_file: Path
    json_file: Path


def format_length(seconds: float) -> str:
    """``3:45`` or ``1:02:03``."""
    total = int(round(max(0.0, seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def export_folder_for(exports_dir: Path | str, name: str, when: datetime | None = None) -> Path:
    """``<exports_dir>/<YYYY-MM-DD>_<name-slug>`` (``-2``, ``-3`` ... when it exists)."""
    stamp = (when or datetime.now()).strftime("%Y-%m-%d")
    slug = slugify(name or "", max_length=MAX_FOLDER_SLUG, word_boundary=True) or "edit"
    base = Path(exports_dir) / f"{stamp}_{slug}"
    candidate = base
    counter = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{counter}")
        counter += 1
    return candidate


def export_edit(
    *,
    exports_dir: Path | str,
    name: str,
    duration_s: float,
    files: list[ExportFile],
    poster: Path | None = None,
    quality: str = "",
    sizes: list[str] | None = None,
    notes: list[str] | None = None,
    when: datetime | None = None,
) -> ExportResult:
    """Copy ``files`` (and the poster) into a new folder under ``exports_dir`` and write the
    three records. Raises :class:`ExportError` when a file is missing or cannot be copied."""
    if not files:
        raise ExportError("Nothing to export: render at least one size first.")
    for item in files:
        if not Path(item.source).is_file():
            raise ExportError(
                f"The file {Path(item.source).name} is missing; render that size again."
            )
    when = when or datetime.now()
    folder = export_folder_for(exports_dir, name, when)
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise ExportError(
            f"The export folder {folder} could not be made: {exc}. Check the export folder in "
            "Settings."
        ) from exc
    wanted = list(files)
    if poster is not None and Path(poster).is_file():
        suffix = Path(poster).suffix.lower() or ".jpg"
        wanted.append(ExportFile(Path(poster), "Poster frame", f"poster{suffix}"))
    exported: list[ExportedFile] = []
    for item in wanted:
        target = unique_path(folder, safe_file_name(item.name, fallback="file"))
        try:
            shutil.copy2(item.source, target)
        except OSError as exc:
            raise ExportError(
                f"{Path(item.source).name} could not be copied to {folder}: {exc}"
            ) from exc
        exported.append(
            ExportedFile(target.name, item.label, target.stat().st_size, sha256_of(target))
        )
    text_file = folder / EXPORT_TEXT
    checksums_file = folder / CHECKSUMS
    json_file = folder / EXPORT_JSON
    atomic_write_text(
        text_file,
        export_text(name=name, duration_s=duration_s, quality=quality, sizes=sizes or [],
                    files=exported, notes=notes or [], when=when),
    )
    atomic_write_text(
        checksums_file, "".join(f"{item.sha256}  {item.name}\n" for item in exported)
    )
    atomic_write_text(
        json_file,
        json.dumps(
            {
                "name": name,
                "exported_at": when.isoformat(timespec="seconds"),
                "duration_s": round(duration_s, 3),
                "quality": quality,
                "sizes": list(sizes or []),
                "files": [item.to_json() for item in exported],
                "notes": list(notes or []),
            },
            indent=2,
        ) + "\n",
    )
    return ExportResult(folder, exported, text_file, checksums_file, json_file)


def export_text(
    *,
    name: str,
    duration_s: float,
    quality: str,
    sizes: list[str],
    files: list[ExportedFile],
    notes: list[str],
    when: datetime,
) -> str:
    width = max((len(item.name) for item in files), default=4)
    label_width = max((len(item.label) for item in files), default=5)
    lines = [
        "9Sigma Editor export",
        f"Name: {name}",
        f"Length: {format_length(duration_s)}",
        f"Exported: {when:%Y-%m-%d %H:%M}",
    ]
    if quality:
        lines.append(f"Quality: {quality}")
    if sizes:
        lines.append(f"Sizes: {', '.join(sizes)}")
    lines.append("")
    lines.append("Files (name, what it is, size, SHA-256 checksum)")
    for item in files:
        lines.append(
            f"  {item.name:<{width}}  {item.label:<{label_width}}  "
            f"{format_size(item.size_bytes):>9}  {item.sha256}"
        )
    if notes:
        lines.append("")
        lines.append("Notes")
        lines.extend(f"  {note}" for note in notes)
    lines.append("")
    lines.append(
        f"To check a file: compare its SHA-256 with {CHECKSUMS} (for example with "
        "'certutil -hashfile <file> SHA256' in a command window)."
    )
    return "\n".join(lines) + "\n"
