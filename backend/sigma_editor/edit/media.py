"""The media library of an edit (docs/CONTRACT-v0.2.md section 2): the files in ``01_media/``
(and the AI voices in ``01_media/voice/``), what the app knows about each (kind, length,
size, a small picture) and where the timeline uses it.

Lengths and sizes are read once (ffprobe for clips and sound, Pillow for pictures) and kept
in ``01_media/.library.json`` by name, size and date. A clip's poster frame is cached in
``07_edit/preview/posters/``. Nothing here ever fails the listing: a file that cannot be read
is listed with what is known.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..audio.errors import AudioError
from ..audio.ffmpeg import probe_duration
from ..models.edit_input import file_kind
from ..models.media import MediaItem, MediaLibrary
from ..models.project import Project
from ..models.timeline import Timeline
from ..render import ffmpeg as ff
from ..render import thumbs
from ..storage.files import atomic_write_text, safe_file_name, unique_path
from ..storage.project_store import IMAGES_DIR, MEDIA_DIR
from .builder import BuildError, IncomingFile, poster_frame

log = logging.getLogger(__name__)

VOICE_SUBDIR = "voice"
"""``01_media/voice/``: the AI voices."""
CACHE_FILE = ".library.json"
POSTERS_DIR = "07_edit/preview/posters"
THUMB_HEIGHT = 128
ALIGNMENT_SUFFIX = ".alignment.json"
SKIPPED_SUFFIXES = (".part", ".json", ".txt", ".md")
LOCK = threading.Lock()


class MediaError(Exception):
    """Plain English, safe to show."""


class MediaInUse(MediaError):
    """The file is on the timeline; it cannot be removed."""


# Paths -----------------------------------------------------------------------------------------


def library_dir(folder: Path | str) -> Path:
    return Path(folder) / MEDIA_DIR


def _relative(folder: Path, path: Path) -> str:
    return path.resolve().relative_to(Path(folder).resolve()).as_posix()


def library_path(folder: Path | str, relative: str) -> Path:
    """The file ``relative`` names inside the edit's library (``01_media/``, or a scene
    picture in ``06_images/``). :class:`MediaError` for anything else."""
    text = str(relative or "").replace("\\", "/").strip()
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts) or text.startswith("/"):
        raise MediaError("That file is not in this edit's library.")
    root = Path(folder).resolve()
    try:
        target = root.joinpath(*parts).resolve()
    except (OSError, RuntimeError) as exc:
        raise MediaError("That file is not in this edit's library.") from exc
    allowed = ((root / MEDIA_DIR).resolve(), (root / IMAGES_DIR).resolve())
    if not any(base == target.parent or base in target.parents for base in allowed):
        raise MediaError("That file is not in this edit's library.")
    if not target.is_file():
        raise MediaError(f"The file {parts[-1]} is not in this edit's library any more.")
    return target


def in_library(folder: Path | str, path: Path | str) -> bool:
    """True when ``path`` already sits inside ``01_media/`` (no copy needed)."""
    try:
        return library_dir(folder).resolve() in Path(path).resolve().parents
    except (OSError, RuntimeError):
        return False


def file_url(project_id: str, relative: str) -> str:
    safe = "/".join(quote(part, safe="") for part in relative.split("/") if part)
    return f"/api/projects/{quote(project_id, safe='')}/files/{safe}"


def thumb_url(project_id: str, relative: str, height: int = THUMB_HEIGHT) -> str:
    return (
        f"/api/projects/{quote(project_id, safe='')}/media/thumb"
        f"?path={quote(relative, safe='/')}&h={height}"
    )


# The cache of what is known about each file ----------------------------------------------------


def _cache_path(folder: Path) -> Path:
    return library_dir(folder) / CACHE_FILE


def _read_cache(folder: Path) -> dict[str, Any]:
    try:
        data = json.loads(_cache_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(folder: Path, data: dict[str, Any]) -> None:
    try:
        atomic_write_text(_cache_path(folder), json.dumps(data, indent=1) + "\n")
    except OSError:
        log.info("The library cache of %s could not be written", folder)


def _cache_key(relative: str, stat: Any) -> str:
    return f"{relative}|{int(stat.st_size)}|{int(stat.st_mtime_ns)}"


def _poster(folder: Path, source: Path, relative: str, stat: Any, settings: Any,
            duration_s: float | None) -> str | None:
    """The clip's poster frame (cached JPEG, relative path), or ``None`` when it fails."""
    token = hashlib.sha1(_cache_key(relative, stat).encode("utf-8")).hexdigest()[:10]
    target = folder / POSTERS_DIR / f"{source.stem}-{token}.jpg"
    if target.is_file():
        return _relative(folder, target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        poster_frame(source, target, settings=settings, duration_s=duration_s)
    except (BuildError, OSError) as exc:
        log.info("No poster frame for %s: %s", source.name, exc)
        return None
    return _relative(folder, target)


def _probe(folder: Path, source: Path, relative: str, kind: str, settings: Any,
           cache: dict[str, Any]) -> dict[str, Any]:
    """Length, size and poster of one file, from the cache or read now."""
    try:
        stat = source.stat()
    except OSError:
        return {}
    key = _cache_key(relative, stat)
    known = cache.get(key)
    if isinstance(known, dict):
        poster = known.get("poster")
        if not poster or (folder / str(poster)).is_file():
            return known
    info: dict[str, Any] = {}
    if kind == "picture":
        try:
            width, height = thumbs.picture_size(source)
            info.update(width=width, height=height)
        except thumbs.NotAPicture as exc:
            log.info("The picture %s could not be read: %s", source.name, exc)
    elif kind == "clip":
        try:
            probed = ff.probe_media(source, ffprobe=ff.find_ffprobe(settings))
            info.update(duration_s=probed.duration_s, width=probed.width, height=probed.height,
                        has_audio=probed.has_audio)
        except ff.RenderError as exc:
            log.info("The clip %s could not be read: %s", source.name, exc)
        poster = _poster(folder, source, relative, stat, settings, info.get("duration_s"))
        if poster:
            info["poster"] = poster
    elif kind == "audio":
        try:
            info["duration_s"] = round(probe_duration(source), 3)
        except AudioError as exc:
            log.info("The sound file %s could not be read: %s", source.name, exc)
    cache[key] = info
    return info


def _sidecar(source: Path) -> dict[str, Any]:
    """An AI voice's ``<name>.alignment.json`` (the text and the voice), when it exists."""
    path = source.with_name(source.name + ALIGNMENT_SUFFIX)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# Listing ---------------------------------------------------------------------------------------


def _files(folder: Path) -> list[Path]:
    root = library_dir(folder)
    found: list[Path] = []
    for base in (root, root / VOICE_SUBDIR):
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if not entry.is_file() or name.startswith(".") or name.endswith(ALIGNMENT_SUFFIX):
                continue
            if name.lower().endswith(SKIPPED_SUFFIXES):
                continue
            if file_kind(name) == "other":
                continue
            found.append(entry)
    return found


def _item(project: Project, folder: Path, source: Path, settings: Any, cache: dict[str, Any],
          timeline: Timeline | None) -> MediaItem:
    relative = _relative(folder, source)
    kind = file_kind(source.name)
    info = _probe(folder, source, relative, kind, settings, cache)
    try:
        stat = source.stat()
        size = int(stat.st_size)
        added = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds")
    except OSError:
        size, added = 0, ""
    if kind == "picture":
        thumb = thumb_url(project.id, relative)
    elif kind == "clip" and info.get("poster"):
        thumb = file_url(project.id, str(info["poster"]))
    else:
        thumb = None
    sidecar = _sidecar(source) if source.parent.name == VOICE_SUBDIR else {}
    return MediaItem(
        path=relative,
        name=source.name,
        kind=kind,  # type: ignore[arg-type]
        size_bytes=size,
        duration_s=info.get("duration_s"),
        width=int(info.get("width") or 0),
        height=int(info.get("height") or 0),
        thumb_url=thumb,
        url=file_url(project.id, relative),
        in_use=timeline.uses_of(relative) if timeline is not None else [],
        added_at=added,
        ai_voice=bool(sidecar),
        text=str(sidecar.get("text") or ""),
        voice=str(sidecar.get("voice_name") or sidecar.get("voice") or ""),
    )


def list_library(project: Project, settings: Any, timeline: Timeline | None) -> MediaLibrary:
    """Every file of the library, newest first."""
    folder = Path(project.folder)
    with LOCK:
        cache = _read_cache(folder)
        before = json.dumps(cache, sort_keys=True)
        items = [_item(project, folder, source, settings, cache, timeline)
                 for source in _files(folder)]
        if json.dumps(cache, sort_keys=True) != before:
            _write_cache(folder, cache)
    items.sort(key=lambda item: item.added_at, reverse=True)
    return MediaLibrary(folder=str(library_dir(folder)), items=items)


def item_for(project: Project, settings: Any, relative: str,
             timeline: Timeline | None = None) -> MediaItem:
    """One file of the library (:class:`MediaError` when it is not there)."""
    folder = Path(project.folder)
    source = library_path(folder, relative)
    with LOCK:
        cache = _read_cache(folder)
        item = _item(project, folder, source, settings, cache, timeline)
        _write_cache(folder, cache)
    return item


# Import and remove -----------------------------------------------------------------------------


def _copy(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    shutil.copyfile(source, part)
    part.replace(target)
    return target


def import_files(project: Project, settings: Any, files: list[IncomingFile],
                 *, subfolder: str = "") -> list[MediaItem]:
    """Copy ``files`` into the library (unique names). Raises :class:`MediaError` for a file
    the editor cannot use, naming it; nothing is copied then."""
    if not files:
        raise MediaError("Choose at least one file.")
    bad = [f.name for f in files if file_kind(f.name) == "other"]
    if bad:
        raise MediaError(
            f"{', '.join(bad)}: not a picture, clip, sound or caption file. Use png, jpg or "
            "webp pictures, mp4 or mov clips, mp3, wav or m4a sound, srt or vtt captions."
        )
    folder = Path(project.folder)
    base = library_dir(folder) / subfolder if subfolder else library_dir(folder)
    copied: list[Path] = []
    for incoming in files:
        kind = file_kind(incoming.name)
        target = unique_path(base, safe_file_name(incoming.name, fallback=kind))
        copied.append(_copy(incoming.path, target))
    return [item_for(project, settings, _relative(folder, path)) for path in copied]


def remove_file(project: Project, timeline: Timeline | None, relative: str) -> None:
    """Delete a library file nothing uses (:class:`MediaInUse` otherwise)."""
    folder = Path(project.folder)
    source = library_path(folder, relative)
    if source.parent.resolve() == (folder / IMAGES_DIR).resolve():
        raise MediaInUse("That picture belongs to a scene. Remove the scene instead.")
    relative = _relative(folder, source)
    uses = timeline.uses_of(relative) if timeline is not None else []
    if uses:
        raise MediaInUse(
            f"{source.name} is used by {', '.join(uses)}. Take it off the timeline first."
        )
    try:
        source.unlink()
        sidecar = source.with_name(source.name + ALIGNMENT_SUFFIX)
        if sidecar.is_file():
            sidecar.unlink()
    except OSError as exc:
        raise MediaError(f"{source.name} could not be removed: {exc}") from exc
    with LOCK:
        cache = _read_cache(folder)
        stale = [key for key in cache if key.startswith(f"{relative}|")]
        for key in stale:
            entry = cache.pop(key)
            poster = entry.get("poster") if isinstance(entry, dict) else None
            if poster:
                try:
                    (folder / str(poster)).unlink(missing_ok=True)
                except OSError:
                    pass
        _write_cache(folder, cache)


def source_info(folder: Path | str, settings: Any, relative: str) -> dict[str, Any]:
    """Length and size of a library file for the timeline (``duration_s``, ``width``,
    ``height``), or an empty dict when it cannot be read."""
    folder = Path(folder)
    try:
        source = library_path(folder, relative)
    except MediaError:
        return {}
    kind = file_kind(source.name)
    with LOCK:
        cache = _read_cache(folder)
        info = _probe(folder, source, _relative(folder, source), kind, settings, cache)
        _write_cache(folder, cache)
    return {key: info[key] for key in ("duration_s", "width", "height", "poster") if key in info}


__all__ = [
    "MediaError", "MediaInUse", "VOICE_SUBDIR", "POSTERS_DIR", "file_url", "thumb_url",
    "import_files", "in_library", "item_for", "library_dir", "library_path", "list_library",
    "remove_file", "source_info",
]
