"""The live preview's pictures: JPEGs of a project's pictures at the heights the preview engine
decodes them at, cached in ``07_edit/preview/thumbs`` (docs/M5-CONTRACT.md 8.2.8 and 8.7.1).

The engine (``frontend/src/preview/pictures.ts`` ``decodeHeight``) asks for a picture at twice
the height it draws it, rounded up to one of a few steps (:data:`PREVIEW_STEPS`, so Half quality
and other window sizes share the same files), never taller than the picture and never wider than
1920 pixels. :func:`preview_heights` works out the same heights for the drawing sizes the app
uses, so the JPEGs can be made before the preview asks for them: making one from a 2048 pixel PNG
takes 150 to 450 ms on the team's laptops, longer than a seek may take. The edit step makes them
while it renders (:func:`prepare_preview_thumbnails`) and ``GET /timeline`` makes any that are
missing in the background (:func:`prepare_in_background`), one picture at a time, each picture
decoded once for all its heights. The maker steps aside while a request is making a JPEG
(the viewer is waiting for that one) and goes on from the picture asked for last, so after a
jump the scenes next to it come first.

A JPEG is never taller than its picture (a taller ask gets the picture's own height, so the
heights above it share one file), is named after the picture, the picture's version (size and
date) and its height, and the thumbnails of a replaced picture are removed when its new ones are
written. Two requests for the same JPEG make it once (the second waits for the first).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import math
import re
import sys
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .geometry import cover_region

log = logging.getLogger(__name__)

THUMBS_DIR = "07_edit/preview/thumbs"
THUMB_HEIGHT = 128
"""The scene strip's and the lanes' thumbnails."""
THUMB_MIN_HEIGHT = 16
THUMB_MAX_HEIGHT = 2160
"""The tallest thumbnail (the preview asks for up to twice its own height: 1920 for a 9:16
monitor 960 high, or a portrait picture under a 16:9 camera window)."""
THUMB_QUALITY = 82
THUMB_QUALITY_LARGE = 95
"""The live preview's pictures (taller than :data:`THUMB_SMALL_MAX`) are seen at full size under
the camera's moves, so they are kept close to the picture itself: at quality 82 a portrait
picture shown 1:1 lost about 2.5 % SSIM against the PNG; at 95 about 1 %."""
THUMB_SMALL_MAX = 360
THUMB_VERSION = "thumb.3"
"""``thumb.3``: bicubic after an integer reduce, no Huffman optimisation (about a third faster)."""
PREVIEW_STEPS = (540, 720, 1080, 1440, 1920, 2160)
"""The heights the engine decodes pictures at (``pictures.ts`` ``PICTURE_STEPS``)."""
STEP_TOLERANCE = 1.02
"""A height up to 2 % over a step still uses that step (``pictures.ts`` ``STEP_TOLERANCE``)."""
MAX_DECODE_WIDTH = 1920
DRAWING_HEIGHTS = {
    "landscape": (540, 720, 270, 360),
    "portrait": (960, 1280, 480, 640),
}
"""The drawing heights the app's monitor uses, most likely first: Full quality at the budget's
size (960 x 540) and at the largest drawing size (1280 x 720), then Half quality of each
(``engine.ts`` ``drawingSize``; a 9:16 video the other way round)."""
HEIGHTS_PER_PICTURE = 8
"""JPEGs kept per picture; the oldest go beyond that (a few window sizes and the strip's)."""
FLIGHT_WAIT_S = 30.0
"""How long a request waits for the same JPEG being made by another one."""
DEMAND_POLL_S = 0.25
"""How often the maker looks again while requests are making JPEGs."""
NAME = re.compile(r"^(?P<picture>[0-9a-f]{10})-(?P<version>[0-9a-f]{8})-(?P<height>\d{1,4})\.jpg$")
OLD_NAME = re.compile(r"^[0-9a-f]{10}-[0-9a-f]{16}\.jpg$")
"""The names of ``thumb.2`` (removed when a folder gets its first new JPEG)."""
PICTURE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class NotAPicture(Exception):
    """The file cannot be read as a picture (or is too big for Pillow to open safely)."""


def _hash(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# The heights ----------------------------------------------------------------------------------


def step_height(height: float) -> float:
    """``height`` rounded up to the next of :data:`PREVIEW_STEPS` (2 % over a step still uses
    it); a height over the last step is left as it is (the engine then reads the file)."""
    for step in PREVIEW_STEPS:
        if height <= step * STEP_TOLERANCE:
            return float(step)
    return float(height)


def decode_height(image_w: float, image_h: float, preview_height: float,
                  cover_height: float | None) -> int:
    """How tall the engine decodes a picture for a drawing ``preview_height`` pixels tall
    (``pictures.ts`` ``decodeHeight``, the same sums in the same order): twice the preview over
    the camera's cover region, rounded up to a step, never taller than the picture or wider
    than 1920; rounded half up like ``Math.round``."""
    height = float(preview_height) * 2
    if image_h > 0 and cover_height and cover_height > 0:
        height = 2 * preview_height * image_h / cover_height
    height = step_height(height)
    if image_h > 0:
        height = min(height, float(image_h))
    if image_w > 0 and image_h > 0:
        height = min(height, MAX_DECODE_WIDTH * image_h / image_w)
    return max(THUMB_MIN_HEIGHT, math.floor(height + 0.5))


def preview_heights(image_size: tuple[int, int], frame_size: tuple[int, int]) -> list[int]:
    """The heights the engine asks for this picture at the app's usual drawing sizes, most
    likely first; heights over :data:`THUMB_MAX_HEIGHT` are left out (the engine reads those
    from the file)."""
    image_w, image_h = image_size
    frame_w, frame_h = frame_size
    if image_w <= 0 or image_h <= 0:
        return []
    cover = cover_region(image_w, image_h, frame_w, frame_h)[3] if frame_w > 0 and frame_h > 0 \
        else None
    drawings = DRAWING_HEIGHTS["portrait" if 0 < frame_w < frame_h else "landscape"]
    heights: list[int] = []
    for drawing in drawings:
        height = decode_height(image_w, image_h, drawing, cover)
        if height <= THUMB_MAX_HEIGHT and height not in heights:
            heights.append(height)
    return heights


# Picture sizes (header only, cached by path, size and date) -----------------------------------

_SIZES: dict[str, tuple[int, int]] = {}
_SIZES_LOCK = threading.Lock()
_SIZES_MAX = 4096


def _version(source: Path) -> tuple[int, int]:
    stat = source.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


def picture_size(source: Path) -> tuple[int, int]:
    """``(width, height)`` of a picture from its header; :class:`NotAPicture` when it cannot
    be opened (not a picture, unreadable, or over Pillow's decompression-bomb limit)."""
    try:
        version = _version(source)
    except OSError as exc:
        raise NotAPicture(str(exc)) from exc
    key = f"{source}|{version[0]}|{version[1]}"
    with _SIZES_LOCK:
        known = _SIZES.get(key)
    if known is not None:
        return known
    try:
        with Image.open(source) as image:
            size = (int(image.width), int(image.height))
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
        raise NotAPicture(str(exc)) from exc
    with _SIZES_LOCK:
        if len(_SIZES) >= _SIZES_MAX:
            _SIZES.clear()
        _SIZES[key] = size
    return size


# The cache ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedThumb:
    """Where the JPEG of ``source`` at ``height`` is kept, and its ETag."""

    path: Path
    picture: str
    version: str
    height: int

    @property
    def etag(self) -> str:
        return f"{self.picture}-{self.version}-{self.height}"


def thumbs_folder(folder: Path) -> Path:
    return Path(folder) / THUMBS_DIR


def cached_thumb(folder: Path, source: Path, height: int) -> CachedThumb:
    """The cache entry of ``source`` (a resolved path inside ``folder``) at ``height`` (already
    clamped to the picture's height)."""
    size, mtime = _version(source)
    picture = _hash(str(source).lower())[:10]
    version = _hash(size, mtime, THUMB_VERSION)[:8]
    name = f"{picture}-{version}-{int(height)}.jpg"
    return CachedThumb(thumbs_folder(folder) / name, picture, version, int(height))


def wanted_height(source: Path, height: int) -> int:
    """``height`` clamped to the picture's own height (a JPEG is never enlarged, so every
    taller ask shares the picture-sized one)."""
    _width, own = picture_size(source)
    return max(1, min(int(height), own))


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


_CLEANED: set[str] = set()


def _tidy(entry: CachedThumb) -> None:
    """After writing ``entry``: remove the JPEGs of the picture's older versions, keep at most
    :data:`HEIGHTS_PER_PICTURE` of this version, and (once per folder) the old-named ones."""
    folder = entry.path.parent
    try:
        mine = []
        for path in folder.glob(f"{entry.picture}-*.jpg"):
            match = NAME.match(path.name)
            if match is None or path == entry.path:
                continue
            if match.group("version") != entry.version:
                path.unlink(missing_ok=True)
            else:
                mine.append(path)
        if len(mine) >= HEIGHTS_PER_PICTURE:
            mine.sort(key=lambda path: path.stat().st_mtime)
            for path in mine[: len(mine) - HEIGHTS_PER_PICTURE + 1]:
                path.unlink(missing_ok=True)
        key = str(folder).lower()
        if key not in _CLEANED:
            _CLEANED.add(key)
            for path in folder.glob("*.jpg"):
                if OLD_NAME.match(path.name):
                    path.unlink(missing_ok=True)
    except OSError:
        pass


def _store(project_folder: Path, entry: CachedThumb, data: bytes) -> None:
    """Writes the JPEG (a unique part file, then a rename). Nothing is written when the
    project folder has gone (moved or archived meanwhile): the folders are made one level at a
    time below it, never the project folder itself."""
    if not Path(project_folder).is_dir():
        return
    folder = entry.path.parent
    try:
        level = Path(project_folder)
        for part_name in Path(THUMBS_DIR).parts:
            level = level / part_name
            level.mkdir(exist_ok=True)
        part = folder / f"{entry.path.stem}.{uuid.uuid4().hex[:8]}.part"
        part.write_bytes(data)
        try:
            part.replace(entry.path)
        except OSError:
            part.unlink(missing_ok=True)
            return
    except OSError as exc:  # a full disk: the JPEG is still sent
        log.info("Could not keep the preview picture %s: %s", entry.path.name, exc)
        return
    _tidy(entry)


# Making JPEGs ----------------------------------------------------------------------------------


def _encode(picture: Image.Image, height: int) -> bytes:
    if picture.height > height:
        width = max(1, round(picture.width * height / picture.height))
        # an integer reduce (box filter) first, then bicubic: much faster than Lanczos on the
        # whole picture and as sharp at these sizes
        picture = picture.resize((width, height), Image.Resampling.BICUBIC, reducing_gap=2.0)
    buffer = io.BytesIO()
    quality = THUMB_QUALITY if height <= THUMB_SMALL_MAX else THUMB_QUALITY_LARGE
    picture.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def make_thumbnails(source: Path, heights: Iterable[int],
                    done: Callable[[int, bytes], None] | None = None) -> dict[int, bytes]:
    """JPEGs of ``source`` at each of ``heights`` (never enlarged), the picture decoded once.
    ``done(height, data)`` is called as each one is ready."""
    wanted = [int(h) for h in dict.fromkeys(heights) if int(h) > 0]
    if not wanted:
        return {}
    try:
        with Image.open(source) as image:
            tallest = min(max(wanted), int(image.height))
            image.draft("RGB", (image.width * tallest // max(image.height, 1) + 1, tallest))
            picture = image.convert("RGB")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
        raise NotAPicture(str(exc)) from exc
    made: dict[int, bytes] = {}
    for height in wanted:
        made[height] = _encode(picture, height)
        if done is not None:
            done(height, made[height])
    return made


def make_thumbnail(source: Path, height: int) -> bytes:
    """A JPEG of ``source`` ``height`` pixels tall (never enlarged), its width to scale."""
    return make_thumbnails(source, [height])[int(height)]


# One JPEG made once, however many ask -----------------------------------------------------------

_FLIGHTS: dict[str, threading.Event] = {}
_FLIGHTS_LOCK = threading.Lock()


def _claim(path: Path) -> threading.Event | None:
    """An event to set when ``path`` is written (this caller makes it), or ``None`` when
    another caller is making it already."""
    with _FLIGHTS_LOCK:
        if str(path) in _FLIGHTS:
            return None
        event = threading.Event()
        _FLIGHTS[str(path)] = event
        return event


def _finish(path: Path, event: threading.Event) -> None:
    with _FLIGHTS_LOCK:
        if _FLIGHTS.get(str(path)) is event:
            del _FLIGHTS[str(path)]
    event.set()


def _flight(path: Path) -> threading.Event | None:
    with _FLIGHTS_LOCK:
        return _FLIGHTS.get(str(path))


# Requests come first ----------------------------------------------------------------------------

_DEMAND_LOCK = threading.Lock()
_DEMAND = 0
_NO_DEMAND = threading.Event()
_NO_DEMAND.set()
_HINT: str | None = None
"""The picture asked for last (its path in lower case): the maker goes on from there."""


def _asked(source: Path) -> None:
    global _HINT
    _HINT = str(source).lower()


@contextlib.contextmanager
def _on_demand() -> Any:
    """A request is making a JPEG: the background maker waits meanwhile."""
    global _DEMAND
    with _DEMAND_LOCK:
        _DEMAND += 1
        _NO_DEMAND.clear()
    try:
        yield
    finally:
        with _DEMAND_LOCK:
            _DEMAND -= 1
            if _DEMAND <= 0:
                _DEMAND = 0
                _NO_DEMAND.set()


def _wait_for_requests(cancel: Callable[[], bool] | None = None) -> None:
    while not _NO_DEMAND.wait(DEMAND_POLL_S):
        if cancel is not None and cancel():
            return


def thumbnail(folder: Path, source: Path, height: int) -> tuple[bytes, str]:
    """The JPEG of ``source`` (a resolved picture inside ``folder``) at ``height`` and its
    ETag: from the cache, from another request making it now, or made here and kept."""
    _asked(source)
    entry = cached_thumb(folder, source, wanted_height(source, height))
    data = _read(entry.path)
    if data is not None:
        return data, entry.etag
    for _attempt in range(2):
        event = _claim(entry.path)
        if event is None:
            other = _flight(entry.path)
            if other is not None:
                other.wait(FLIGHT_WAIT_S)
            data = _read(entry.path)
            if data is not None:
                return data, entry.etag
            continue
        try:
            with _on_demand():
                data = make_thumbnail(source, entry.height)
            _store(folder, entry, data)
            return data, entry.etag
        finally:
            _finish(entry.path, event)
    # the other maker failed twice: make it here without the cache's bookkeeping
    with _on_demand():
        return make_thumbnail(source, entry.height), entry.etag


# Making them ahead -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PictureJob:
    """A picture of the project and the heights to make of it, most likely first."""

    source: Path
    heights: tuple[int, ...]


def picture_jobs(
    folder: Path, images: Sequence[str], frame_size: tuple[int, int]
) -> list[PictureJob]:
    """The preview pictures of ``images`` (paths as the timeline names them, in scene order)
    at the heights the engine will ask for; pictures outside the project folder, missing or
    unreadable are left out."""
    root = Path(folder).resolve()
    jobs: list[PictureJob] = []
    seen: set[str] = set()
    for image in images:
        if not image:
            continue
        try:
            path = Path(image)
            source = (path if path.is_absolute() else root / path).resolve()
        except (OSError, ValueError, RuntimeError):
            continue
        if root not in source.parents or source.suffix.lower() not in PICTURE_SUFFIXES:
            continue
        if str(source).lower() in seen or not source.is_file():
            continue
        seen.add(str(source).lower())
        try:
            size = picture_size(source)
        except NotAPicture:
            continue
        heights = preview_heights(size, frame_size)
        if heights:
            jobs.append(PictureJob(source, tuple(heights)))
    return jobs


def prepare_pictures(folder: Path, jobs: Sequence[PictureJob],
                     cancel: Callable[[], bool] | None = None) -> int:
    """Makes the missing JPEGs of ``jobs``, one picture at a time (each decoded once for all
    its heights), in scene order but going on from the picture a request asked for last; it
    waits while requests are making JPEGs, and a JPEG another request is making is left to it.
    Returns how many were made. Never raises: a picture that cannot be read is skipped (the
    preview shows its slate)."""
    made = 0
    order = {str(job.source).lower(): index for index, job in enumerate(jobs)}
    pending = list(range(len(jobs)))
    cursor = 0
    followed: str | None = None
    while pending:
        if (cancel is not None and cancel()) or not Path(folder).is_dir():
            break
        # between pictures only: once a picture's JPEGs are claimed a request may be waiting
        # for them, so they are made without a pause
        _wait_for_requests(cancel)
        hint = _HINT
        if hint is not None and hint != followed and hint in order:
            followed = hint
            cursor = order[hint]
        index = next((i for i in pending if i >= cursor), pending[0])
        pending.remove(index)
        cursor = index + 1
        job = jobs[index]
        try:
            entries = {}
            for height in job.heights:
                entry = cached_thumb(folder, job.source, min(height, picture_size(job.source)[1]))
                if entry.height not in entries and not entry.path.is_file():
                    entries[entry.height] = entry
        except (OSError, NotAPicture):
            continue
        claims = {}
        for height, entry in entries.items():
            event = _claim(entry.path)
            if event is not None:
                claims[height] = (entry, event)
        if not claims:
            continue

        def done(height: int, data: bytes, claims: dict = claims) -> None:
            entry, event = claims.pop(height)
            try:
                _store(folder, entry, data)
            finally:
                _finish(entry.path, event)

        try:
            made += len(make_thumbnails(job.source, list(claims), done))
        except (NotAPicture, OSError) as exc:
            log.info("A preview picture could not be made from %s: %s", job.source.name, exc)
        except Exception:  # noqa: BLE001 - the preview's pictures never stop anything else
            log.exception("A preview picture could not be made from %s", job.source.name)
        finally:
            for entry, event in claims.values():
                _finish(entry.path, event)
    return made


def prepare_preview_thumbnails(folder: Path, timeline: Any,
                               cancel: Callable[[], bool] | None = None) -> int:
    """Makes the preview pictures of a timeline's scenes (the edit step calls this while it
    renders). Returns how many JPEGs were made."""
    jobs = picture_jobs(folder, [scene.image for scene in timeline.scenes],
                        (int(timeline.width), int(timeline.height)))
    return prepare_pictures(folder, jobs, cancel)


# In the background ------------------------------------------------------------------------------

_QUEUE: dict[str, tuple[Path, list[str], tuple[int, int]]] = {}
_QUEUE_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_IDLE = threading.Event()
_IDLE.set()


def _lower_priority() -> None:
    """The background maker runs below normal priority on Windows, so the preview's own
    drawing and decoding (and the owner's other programs) come first."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
    except (AttributeError, OSError):
        pass


def _run_queue() -> None:
    global _WORKER
    _lower_priority()
    while True:
        with _QUEUE_LOCK:
            if not _QUEUE:
                _WORKER = None
                _IDLE.set()
                return
            key = next(iter(_QUEUE))
            folder, images, frame_size = _QUEUE.pop(key)
        try:
            prepare_pictures(folder, picture_jobs(folder, images, frame_size))
        except Exception:  # noqa: BLE001 - keep the worker alive for the next project
            log.exception("The preview pictures of %s could not be made", folder)


def prepare_in_background(folder: Path, images: Sequence[str],
                          frame_size: tuple[int, int]) -> None:
    """Queues the preview pictures of ``images`` (in scene order) for the one background
    maker and returns at once; a later call for the same project replaces its waiting one."""
    global _WORKER
    if not any(images):
        return
    with _QUEUE_LOCK:
        _QUEUE[str(folder).lower()] = (Path(folder), list(images), frame_size)
        _IDLE.clear()
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_run_queue, name="sge-preview-pictures",
                                       daemon=True)
            _WORKER.start()


def wait_for_background(timeout: float = 30.0) -> bool:
    """Waits until the background maker has nothing left to do (tests)."""
    return _IDLE.wait(timeout)


__all__ = [
    "PICTURE_SUFFIXES",
    "PREVIEW_STEPS",
    "THUMB_HEIGHT",
    "THUMB_MAX_HEIGHT",
    "THUMB_MIN_HEIGHT",
    "NotAPicture",
    "PictureJob",
    "cached_thumb",
    "decode_height",
    "make_thumbnail",
    "make_thumbnails",
    "picture_jobs",
    "picture_size",
    "prepare_in_background",
    "prepare_pictures",
    "prepare_preview_thumbnails",
    "preview_heights",
    "step_height",
    "thumbnail",
    "wait_for_background",
]
