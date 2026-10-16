"""Assembly: scene clips + transition clips -> the finished video (TRANSITIONS-V2-CONTRACT
section 3).

Frame layout (all in whole frames of the timeline's fps, the first scene starting at 0):

* scene ``k`` runs from its start to the next scene's start (the last one to its end):
  ``len_k`` frames;
* the cut between scenes ``i`` and ``j`` is ``n`` frames centred on the boundary ``C``:
  ``h = n // 2`` frames before it and ``n - h`` after, so the change sits on the narration's
  cut point; ``n = round(duration * fps)`` (0 below ``transitions.min_frames``), never more
  than ``transitions.max_share_of_shorter_scene`` (a quarter) of the shorter scene and never
  more than :data:`~..models.transitions.MAX_TRANSITION_S` (the same caps as the timeline
  builder, so a timeline edited by hand or replaced in the review gets them too);
* the last scene ends with an ``n_f``-frame fade to black (white for ``dip_white``) inside
  its own frames;
* scene clip ``k`` holds ``h_in`` frames before the scene, the scene, and ``n_out - h_out``
  frames after it. Its first ``n_in`` frames feed the incoming cut, its last ``n_out`` (or
  ``n_f``) the outgoing one, the rest is the body.

Rendering runs in three phases (progress 55 / 15 / 30 %): scene clips (two at a time, one
at a time above 1080p; cached by hash), transition clips (the overlap frames of both scene
clips decoded side by side through pipes, one frame of each at a time, the transition drawn
in Python, encoded through a pipe; cached by the hash of both scene clips and the
transition), then one FFmpeg pass: the concat demuxer reads ``[clip 1][cut 1][clip 2]...``,
``select`` drops the overlap frames (the cuts replace them; the test is a balanced sum of
``between()`` terms, see :func:`frames_in_expression`), then popups, captions, the audio mix
(the voice track and the music stem of ``render/music.py``, made or found current just before
this pass) and the preset's encoder. :func:`frame_layout` is the pure frame bookkeeping (no
pictures read) that the live preview mirrors. Cancelling kills the running child process and
keeps every finished clip. A cached clip that is not a whole MP4 (cut short or zeroed by a
crash) is removed and drawn again; one that cannot be decoded while a cut is drawn is removed
and the render stops with a plain message, so the next render redraws it. Running out of memory
(Python, or FFmpeg's x264 failing to allocate) stops the render with "The computer ran out of
memory while rendering <preset>" and keeps every finished clip.

One render runs at a time in the whole app (:data:`RENDER_GATE`): the memory budget above
(two clip encoders, one above 1080p) is for the PC, not for each project, so a second
render (another project approved at the same moment) waits, saying "Waiting for another
render to finish", until the first one is done.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..models.timeline import Timeline
from ..models.transitions import MAX_TRANSITION_S
from . import transitions as tr
from .ffmpeg import (
    NO_WINDOW,
    RenderCancelled,
    RenderError,
    RenderOutOfMemory,
    RenderPlan,
    RenderResult,
    _pump_stderr,
    _tail,
    find_ffmpeg,
    looks_out_of_memory,
    oserror_is_memory,
    out_of_memory_message,
    parse_progress_line,
    probe_media,
    resolve_path,
)
from .presets import RenderConfig, RenderPreset
from .scene_clips import (
    CLIPS_DIR,
    ClipEncoding,
    ClipReadError,
    FrameWriter,
    SceneClipJob,
    clip_encoding_for,
    clip_workers,
    decode_frames,
    make_scene_job,
    mp4_is_whole,
    render_scene_clip,
)

log = logging.getLogger(__name__)

CONCAT_FILE = "concat.txt"
PHASE_SHARE = {"scenes": 55.0, "cuts": 15.0, "assembly": 30.0}
CUT_ENGINE_VERSION = "cuts.1"
MAX_SHARE_OF_SCENE = 0.5
"""Upper bound for ``transitions.max_share_of_shorter_scene`` (a cut takes at most half of
each neighbouring scene, so every scene keeps a body)."""
WAITING_MESSAGE = "Waiting for another render to finish before rendering {name}"
GATE_POLL_S = 0.25

RENDER_GATE = threading.BoundedSemaphore(1)
"""Held by :func:`run_pipeline` for a whole render: one render at a time in the app (the
per-render worker pools assume they have the PC to themselves)."""


# Layout --------------------------------------------------------------------------------------


@dataclass
class CutJob:
    """A transition clip: the change from scene ``a`` to scene ``b`` (or, without ``b``, the
    fade at the very end). ``cut_NN`` is the transition out of scene NN."""

    number: int
    frames: int
    a: SceneClipJob
    b: SceneClipJob | None
    transition: tr.ResolvedTransition | None
    fade_colour: tuple[float, float, float] | None
    seed: int
    encoding: ClipEncoding
    key: str = ""
    reused: bool = False
    seconds: float = 0.0
    path: Path | None = None

    @property
    def label(self) -> str:
        if self.b is None:
            return "fade to " + ("white" if self.fade_colour == (1.0, 1.0, 1.0) else "black")
        return self.transition.id if self.transition else "cut"

    @property
    def file_name(self) -> str:
        return f"cut_{self.number:02d}.{self.key}.mp4"

    def make_key(self) -> None:
        """The hash of everything the cut's frames come from. The seed (the scene number)
        only counts for the transitions that use it, so inserting a scene early in the video
        does not redraw every dissolve after it."""
        payload = {
            "engine": CUT_ENGINE_VERSION,
            "a": self.a.key,
            "b": self.b.key if self.b is not None else None,
            "fade": list(self.fade_colour) if self.fade_colour else None,
            "transition": self.transition.cache_token() if self.transition else None,
            "frames": self.frames,
            "seed": self.transition.seed_key(self.seed) if self.transition else None,
            "encoding": self.encoding.token(),
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        self.key = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class Layout:
    """Where every frame of the video comes from (see the module docstring)."""

    fps: int
    size: tuple[int, int]
    clips_dir: str
    """Relative to the render's work folder: ``clips/<preset>``."""
    scenes: list[SceneClipJob] = field(default_factory=list)
    cuts: list[CutJob] = field(default_factory=list)
    order: list[SceneClipJob | CutJob] = field(default_factory=list)
    """Files in concat order."""
    drops: list[tuple[int, int]] = field(default_factory=list)
    """Frame ranges (inclusive) of the joined clips that the cuts replace."""
    total_frames: int = 0
    scene_frames: dict[int, tuple[int, int]] = field(default_factory=dict)
    """Scene number -> (first frame, frame count) in the finished video."""

    @property
    def total_s(self) -> float:
        return self.total_frames / self.fps

    @property
    def concat_path(self) -> str:
        return f"{self.clips_dir}/{CONCAT_FILE}"

    def select_filter(self) -> str:
        """``select`` + ``setpts``: the joined clips without the overlap frames, retimed."""
        retime = f"setpts=N/({self.fps}*TB)"
        if not self.drops:
            return retime
        return f"select='not({frames_in_expression(self.drops)})',{retime}"

    def concat_text(self) -> str:
        lines = ["ffconcat version 1.0"]
        for item in self.order:
            name = item.path.name if item.path is not None else item.file_name
            lines.append(f"file '{name}'")
        return "\n".join(lines) + "\n"


def merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Inclusive frame ranges in order, touching or overlapping ones joined into one."""
    merged: list[list[int]] = []
    for lo, hi in sorted((int(a), int(b)) for a, b in ranges):
        if hi < lo:
            continue
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def frames_in_expression(ranges: Iterable[tuple[int, int]]) -> str:
    """An FFmpeg expression that is 1 for a frame number ``n`` inside one of ``ranges``
    (inclusive) and 0 outside.

    The ``between()`` terms are added as a balanced tree, ``((a+b)+(c+d))``, never as one
    flat sum: FFmpeg 9's expression parser refuses a flat sum of 100 terms or more (and says
    "Cannot allocate memory"), and an 80-scene video needs 159. The tree is only about
    log2(terms) deep, so any number of scenes parses."""
    terms = [f"between(n\\,{lo}\\,{hi})" for lo, hi in merge_ranges(ranges)]
    if not terms:
        return "0"

    def join(items: list[str]) -> str:
        if len(items) == 1:
            return items[0]
        middle = len(items) // 2
        return f"({join(items[:middle])}+{join(items[middle:])})"

    return join(terms)


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (OSError, UnidentifiedImageError) as exc:
        raise RenderError(f"The picture {path} could not be read: {exc}") from exc


def cut_limit(shorter_s: float, shorter_frames: int, fps: int, max_share: float) -> int:
    """The longest cut (in frames) next to a scene of ``shorter_s`` seconds and
    ``shorter_frames`` frames: ``max_share`` of the scene worked out in seconds like the
    timeline builder does it (so a cut the builder made always fits; the 0.6 frame of leeway
    covers the builder's rounding to milliseconds), never more than
    :data:`MAX_TRANSITION_S` and never more than half of the scene's frames."""
    share = min(max(float(max_share), 0.0), MAX_SHARE_OF_SCENE)
    seconds = min(max(float(shorter_s), 0.0) * share, MAX_TRANSITION_S)
    by_share = int(math.floor(seconds * fps + 0.6))
    return max(0, min(by_share, max(int(shorter_frames), 0) // 2))


def cut_frames(duration_s: float, fps: int, min_frames: int, longest: int) -> int:
    frames = int(round(max(float(duration_s), 0.0) * fps))
    frames = min(frames, max(longest, 0))
    return frames if frames >= max(min_frames, 1) else 0


@dataclass(frozen=True)
class ClipSpan:
    """Where scene clip ``index`` sits (all in output frames, the first scene at 0)."""

    index: int
    """The scene's index in the timeline."""
    start: int
    """The scene's first frame."""
    length: int
    """``len_k``: frames from its start to the next scene's start (the last one to its end)."""
    n_in: int
    """Frames of the incoming cut (0: none)."""
    h_in: int
    """Of those, the frames before the scene starts (``n_in // 2``)."""
    n_out: int
    """Frames of the outgoing cut (0 for the last scene)."""
    tail_out: int
    """Of those, the frames after the scene ends (``n_out - n_out // 2``)."""
    tail: int
    """Frames at the clip's end that feed the outgoing cut, or the end fade (last scene)."""
    frames: int
    """The clip's length: ``h_in + length + tail_out``; the camera path spans all of them."""

    @property
    def first_frame(self) -> int:
        """The output frame the clip's frame 0 belongs to (``start - h_in``)."""
        return self.start - self.h_in


@dataclass(frozen=True)
class CutSpan:
    """A cut (or the end fade, ``before is None``): ``frames`` output frames from
    ``first_frame``, ``h`` of them before the incoming scene starts."""

    after: int
    before: int | None
    first_frame: int
    frames: int
    h: int


@dataclass(frozen=True)
class FrameLayout:
    """The pure frame bookkeeping of a timeline (no pictures read): what the renderer lays
    out and what the live preview mirrors (``docs/design/TRANSITION-PARITY.md`` section 2)."""

    fps: int
    total_frames: int
    starts: tuple[int, ...]
    lengths: tuple[int, ...]
    kept: tuple[int, ...]
    cut_n: dict[int, int]
    fade_n: int
    clips: tuple[ClipSpan, ...]
    cuts: tuple[CutSpan, ...]


def frame_layout(timeline: Timeline, config: RenderConfig) -> FrameLayout:
    """Scene starts and lengths in frames, the cut lengths (capped as the timeline builder
    caps them, centred on the boundaries) and each scene clip's frames. Raises
    :class:`RenderError` for an empty timeline or one shorter than a frame."""
    scenes = list(timeline.scenes)
    if not scenes:
        raise RenderError("The timeline has no scenes.")
    fps = int(timeline.fps)
    min_frames = config.transition_min_frames
    first = round(scenes[0].start_s * fps)
    starts = [round(scene.start_s * fps) - first for scene in scenes]
    ends = starts[1:] + [round(scenes[-1].end_s * fps) - first]
    lengths = [max(0, end - start) for start, end in zip(starts, ends, strict=True)]
    kept = [i for i, length in enumerate(lengths) if length > 0]
    if not kept:
        raise RenderError("The timeline is shorter than one frame.")

    # Cut lengths between kept neighbours, then the fade at the end. The same caps as the
    # timeline builder (a share of the shorter scene, at most MAX_TRANSITION_S), so a
    # timeline edited by hand or replaced in the review cannot ask for a longer one.
    share = config.transition_max_share
    seconds = [max(scene.end_s - scene.start_s, 0.0) for scene in scenes]
    cut_n: dict[int, int] = {}
    for left, right in zip(kept, kept[1:], strict=False):
        longest = cut_limit(min(seconds[left], seconds[right]),
                            min(lengths[left], lengths[right]), fps, share)
        cut_n[left] = cut_frames(scenes[left].transition_out.duration_s, fps, min_frames,
                                 longest)
    last = kept[-1]
    fade_n = cut_frames(scenes[last].transition_out.duration_s, fps, min_frames,
                        cut_limit(seconds[last], lengths[last], fps, share))

    def consumed(index: int, before: int | None) -> int:
        """Frames of scene ``index``'s own span taken by its cuts."""
        incoming = 0
        if before is not None and cut_n.get(before, 0):
            n = cut_n[before]
            incoming = n - n // 2
        outgoing = cut_n.get(index, 0) // 2 if index != last else fade_n
        return incoming + outgoing

    previous: int | None = None
    for index in kept:  # a safety net; the caps above already keep every body >= 0
        while consumed(index, previous) > lengths[index]:
            if index != last and cut_n.get(index, 0):
                cut_n[index] = 0
            elif index == last and fade_n:
                fade_n = 0
            elif previous is not None:
                cut_n[previous] = 0
            else:
                break
        previous = index

    clips: list[ClipSpan] = []
    cuts: list[CutSpan] = []
    previous = None
    for position, index in enumerate(kept):
        n_in = cut_n.get(previous, 0) if previous is not None else 0
        is_last = index == last
        n_out = 0 if is_last else cut_n.get(index, 0)
        tail_out = n_out - n_out // 2
        clip = ClipSpan(
            index=index, start=starts[index], length=lengths[index], n_in=n_in,
            h_in=n_in // 2, n_out=n_out, tail_out=tail_out,
            tail=fade_n if is_last else n_out, frames=lengths[index] + n_in // 2 + tail_out,
        )
        clips.append(clip)
        if is_last and fade_n:
            cuts.append(CutSpan(index, None, clip.start + clip.length - fade_n, fade_n, 0))
        elif not is_last and n_out:
            following = kept[position + 1]
            cuts.append(CutSpan(index, following, starts[following] - n_out // 2, n_out,
                                n_out // 2))
        previous = index
    return FrameLayout(
        fps=fps, total_frames=ends[-1], starts=tuple(starts), lengths=tuple(lengths),
        kept=tuple(kept), cut_n=dict(cut_n), fade_n=fade_n, clips=tuple(clips),
        cuts=tuple(cuts),
    )


def plan_layout(
    timeline: Timeline,
    preset: RenderPreset,
    *,
    folder: Path,
    config: RenderConfig,
    encoding: ClipEncoding | None = None,
    final_x264: str | None = None,
) -> Layout:
    """The scene and cut jobs and the frame bookkeeping for one preset. Reads the pictures'
    sizes and dates; renders nothing."""
    scenes = list(timeline.scenes)
    if not scenes:
        raise RenderError("The timeline has no scenes.")
    folder = Path(folder)
    fps = int(timeline.fps)
    size = preset.size(timeline.aspect)
    encoding = encoding or clip_encoding_for(preset, final_x264)
    images: dict[int, tuple[Path, tuple[int, int]]] = {}
    for position, scene in enumerate(scenes):
        image = resolve_path(folder, scene.image)
        if not image.is_file():
            raise RenderError(f"The picture for scene {position + 1} is missing: {image}")
        images[position] = (image, _image_size(image))
    frames_plan = frame_layout(timeline, config)
    kept = list(frames_plan.kept)
    cut_n = frames_plan.cut_n
    fade_n = frames_plan.fade_n
    last = kept[-1]

    layout = Layout(fps=fps, size=size, clips_dir=f"{CLIPS_DIR}/{preset.id}")
    jobs: dict[int, SceneClipJob] = {}
    for span in frames_plan.clips:
        index = span.index
        image, image_size = images[index]
        job = make_scene_job(
            number=index + 1,
            index=index,
            image=image,
            image_size=image_size,
            motion=scenes[index].motion,
            preset=preset,
            aspect=timeline.aspect,
            fps=fps,
            frames=span.frames,
            lead_in=span.h_in,
            tail_out=span.tail_out,
            keyframes=(span.n_in, span.frames - span.tail),
            config=config,
            folder=folder,
            encoding=encoding,
        )
        jobs[index] = job
        layout.scenes.append(job)

    # Concat order, drop ranges and the cut jobs.
    offset = 0
    out_frame = 0
    previous = None
    for index in kept:
        job = jobs[index]
        is_last = index == last
        n_in = cut_n.get(previous, 0) if previous is not None else 0
        tail = fade_n if is_last else cut_n.get(index, 0)
        layout.order.append(job)
        if n_in:
            layout.drops.append((offset, offset + n_in - 1))
        if tail:
            layout.drops.append((offset + job.frames - tail, offset + job.frames - 1))
        body = job.frames - n_in - tail
        layout.scene_frames[job.number] = (out_frame, body)
        out_frame += body
        offset += job.frames
        if is_last and fade_n:
            cut = CutJob(
                number=job.number, frames=fade_n, a=job, b=None, transition=None,
                fade_colour=tr.fade_out_colour(scenes[index].transition_out.type),
                seed=job.number, encoding=encoding,
            )
        elif not is_last and tail:
            following = jobs[kept[kept.index(index) + 1]]
            cut = CutJob(
                number=job.number, frames=tail, a=job, b=following,
                transition=tr.resolve(scenes[index].transition_out.type),
                fade_colour=None, seed=job.number, encoding=encoding,
            )
        else:
            cut = None
        if cut is not None:
            cut.make_key()
            layout.cuts.append(cut)
            layout.order.append(cut)
            offset += cut.frames
            out_frame += cut.frames
        previous = index
    layout.total_frames = out_frame
    expected = frames_plan.total_frames
    if out_frame != expected:  # pragma: no cover - the bookkeeping above guarantees it
        raise RenderError(
            f"Internal frame count mismatch ({out_frame} frames planned, {expected} in the "
            "timeline)."
        )
    return layout


# Rendering ---------------------------------------------------------------------------------


class _Reporter:
    """Thread-safe, monotonic, throttled ``Rendering <name> N%`` messages."""

    def __init__(self, callback: Callable[[str, float], None] | None, name: str) -> None:
        self.callback = callback
        self.name = name
        self.last = -1.0
        self.last_time = 0.0
        self.lock = threading.Lock()

    def __call__(self, pct: float, *, force: bool = False) -> None:
        if self.callback is None:
            return
        pct = min(100.0, max(0.0, float(pct)))
        with self.lock:
            if pct < self.last:
                return
            now = time.monotonic()
            if force or pct == 100.0 or pct >= self.last + 1.0 or now - self.last_time > 2.0:
                self.last, self.last_time = pct, now
                self.callback(f"Rendering {self.name} {int(pct)}%", pct)


def _existing(clips_dir: Path, prefix: str, note: Callable[[str], None] | None = None
              ) -> dict[str, Path]:
    """``hash -> file`` of finished clips (``<prefix>_NN.<hash>.mp4``). A clip that is not a
    whole MP4 (cut short or zeroed by a crash) is removed, so it is drawn again."""
    found: dict[str, Path] = {}
    try:
        entries = list(clips_dir.glob(f"{prefix}_*.mp4"))
    except OSError:
        return found
    for path in entries:
        parts = path.name.split(".")  # scene_NN.<hash>.mp4 (a .part.mp4 has four parts)
        if len(parts) != 3 or parts[2] != "mp4":
            continue
        if mp4_is_whole(path):
            found[parts[1]] = path
            continue
        try:
            path.unlink()
        except OSError:
            continue
        if note is not None:
            note(f"{path.name} was damaged and has been removed; it is drawn again")
    return found


def render_cut(cut: CutJob, clips_dir: Path, *, ffmpeg: str, fps: int,
               size: tuple[int, int], stop: Callable[[], bool],
               on_frame: Callable[[], None]) -> None:
    """Draw one transition clip from the overlap frames of its scene clips. A scene clip
    that cannot be decoded is removed (so the next render draws it again) and the render
    stops with a plain message."""
    started = time.monotonic()
    try:
        _draw_cut(cut, clips_dir, ffmpeg=ffmpeg, fps=fps, size=size, stop=stop,
                  on_frame=on_frame)
    except ClipReadError as exc:
        raise _damaged_clip(cut, clips_dir, exc) from exc
    cut.seconds = round(time.monotonic() - started, 2)


def _damaged_clip(cut: CutJob, clips_dir: Path, exc: ClipReadError) -> RenderError:
    owner = cut.a
    if cut.b is not None and exc.path.name == (cut.b.path or clips_dir / cut.b.file_name).name:
        owner = cut.b
    try:
        exc.path.unlink()
    except OSError:
        return RenderError(
            f"The saved clip of scene {owner.number} ({exc.path.name}) could not be read: "
            f"{exc.detail}"
        )
    return RenderError(
        f"The saved clip of scene {owner.number} could not be read ({exc.detail}), so it "
        "was removed. Render again to draw it afresh."
    )


def _draw_cut(cut: CutJob, clips_dir: Path, *, ffmpeg: str, fps: int,
              size: tuple[int, int], stop: Callable[[], bool],
              on_frame: Callable[[], None]) -> None:
    """Both overlaps are decoded side by side (A from the keyframe where its outgoing overlap
    starts, B from its first frame) and each output frame is drawn as soon as one frame of
    each has arrived: a cut holds a couple of frames whatever its length, never the whole
    overlap. A clip shorter than planned holds its last frame."""
    a_path = cut.a.path or clips_dir / cut.a.file_name
    n = cut.frames
    tail = decode_frames(a_path, size, ffmpeg=ffmpeg, fps=fps,
                         start_frame=cut.a.frames - n, count=n, stop=stop)
    head = None
    if cut.b is not None:
        b_path = cut.b.path or clips_dir / cut.b.file_name
        head = decode_frames(b_path, size, ffmpeg=ffmpeg, fps=fps, count=n, stop=stop)
    out_path = clips_dir / cut.file_name
    writer: FrameWriter | None = None
    cache: dict[str, Any] = {}
    previous_a = previous_b = None
    try:
        writer = FrameWriter(out_path, size, fps, cut.encoding, ffmpeg=ffmpeg, stop=stop)
        for k in range(n):
            if stop():
                raise RenderCancelled("The render was stopped.")
            a = next(tail, None)
            if a is None:
                if previous_a is None:
                    raise RenderError(f"Scene {cut.a.number}'s clip has no frames to make cut "
                                      f"{cut.number}.")
                a = previous_a
            previous_a = a
            if head is None:
                assert cut.fade_colour is not None
                frame = tr.fade_out_frame(a, cut.fade_colour, k, n, cache)
            else:
                b = next(head, None)
                if b is None:
                    if previous_b is None:
                        raise RenderError(f"Scene {cut.b.number if cut.b else '?'}'s clip has "
                                          f"no frames to make cut {cut.number}.")
                    b = previous_b
                previous_b = b
                assert cut.transition is not None
                frame = tr.render_frame(cut.transition, a, b, tr.frame_time(k, n),
                                        duration_s=n / fps, fps=fps, seed=cut.seed,
                                        cache=cache)
            writer.write(frame)
            on_frame()
        writer.close()
    except BaseException:
        if writer is not None:
            writer.abort()
        raise
    finally:
        tail.close()
        if head is not None:
            head.close()
    cut.path = out_path


@dataclass
class _Run:
    """State shared by the phases of one render."""

    name: str
    clips_dir: Path
    ffmpeg: str
    report: _Reporter
    note: Callable[[str], None]
    stop: Callable[[], bool]
    abort: threading.Event
    seconds: dict[str, float] = field(default_factory=dict)


def _scene_phase(layout: Layout, run: _Run) -> tuple[int, int]:
    started = time.monotonic()
    existing = _existing(run.clips_dir, "scene", run.note)
    pending: dict[str, list[SceneClipJob]] = {}
    reused = 0
    for job in layout.scenes:
        found = existing.get(job.key)
        if found is not None:
            job.reused = True
            job.path = found
            reused += 1
            run.note(f"scene {job.number:02d}: reused {found.name}")
        else:
            pending.setdefault(job.key, []).append(job)
    first_of = [jobs[0] for jobs in pending.values()]
    total = sum(job.frames for job in first_of)
    done: dict[int, int] = {}
    lock = threading.Lock()
    share = PHASE_SHARE["scenes"]

    def on_frames(job: SceneClipJob, frames: int) -> None:
        # Keyed by the job itself, not its hash: a provider that fails part-way falls back to
        # zoompan under a new hash, and its frames must replace, not add to, the count.
        with lock:
            done[id(job)] = min(frames, job.frames)
            fraction = sum(done.values()) / total if total else 1.0
        run.report(share * fraction)

    if first_of:
        workers = min(clip_workers(layout.size), len(first_of))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scene-clip") as pool:
            futures = {
                pool.submit(render_scene_clip, job, run.clips_dir, ffmpeg=run.ffmpeg,
                            stop=run.stop, on_frames=partial(on_frames, job)): (job, job.key)
                for job in first_of
            }
            try:
                for future in as_completed(futures):
                    job, planned_key = futures[future]
                    future.result()
                    path = run.clips_dir / job.file_name
                    for twin in pending[planned_key]:
                        twin.path = path
                        twin.renderer = job.renderer
                        twin.key = job.key  # changed when the provider fell back to zoompan
                        twin.fallback = job.fallback
                    how = f"zoompan, {job.fallback}" if job.fallback else job.renderer
                    run.note(
                        f"scene {job.number:02d}: drawn in {job.seconds:.1f} s "
                        f"({how}, {job.frames} frames) -> {job.file_name}"
                    )
            except BaseException:
                run.abort.set()
                for future in futures:
                    future.cancel()
                raise
    # A scene drawn by zoompan after its provider failed has zoompan's key now: the cuts
    # next to it are keyed on that, so a cached cut always matches the frames it shows.
    for cut in layout.cuts:
        cut.make_key()
    run.report(share, force=True)
    run.seconds["scenes"] = round(time.monotonic() - started, 2)
    return reused, len(layout.scenes)


def _cut_phase(layout: Layout, run: _Run) -> tuple[int, int]:
    started = time.monotonic()
    existing = _existing(run.clips_dir, "cut", run.note)
    pending: list[CutJob] = []
    reused = 0
    twins: dict[str, list[CutJob]] = {}
    for cut in layout.cuts:
        found = existing.get(cut.key)
        if found is not None:
            cut.reused = True
            cut.path = found
            reused += 1
            run.note(f"cut {cut.number:02d} ({cut.label}): reused {found.name}")
        elif cut.key in twins:
            twins[cut.key].append(cut)
        else:
            twins[cut.key] = [cut]
            pending.append(cut)
    total = sum(cut.frames for cut in pending)
    done = 0
    lock = threading.Lock()
    base = PHASE_SHARE["scenes"]
    share = PHASE_SHARE["cuts"]

    def on_frame() -> None:
        nonlocal done
        with lock:
            done += 1
            fraction = done / total if total else 1.0
        run.report(base + share * fraction)

    if pending:
        # Cuts are independent: two at a time overlap FFmpeg's start-up with the drawing
        # (one at a time above 1080p, where a cut holds a gigabyte of frames).
        workers = min(clip_workers(layout.size), len(pending))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cut-clip") as pool:
            futures = {
                pool.submit(render_cut, cut, run.clips_dir, ffmpeg=run.ffmpeg, fps=layout.fps,
                            size=layout.size, stop=run.stop, on_frame=on_frame): cut
                for cut in pending
            }
            try:
                for future in as_completed(futures):
                    cut = futures[future]
                    future.result()
                    assert cut.path is not None
                    for twin in twins[cut.key]:
                        twin.path = cut.path
                    run.note(
                        f"cut {cut.number:02d} ({cut.label}, {cut.frames} frames): drawn in "
                        f"{cut.seconds:.1f} s -> {cut.file_name}"
                    )
            except BaseException:
                run.abort.set()
                for future in futures:
                    future.cancel()
                raise
    run.report(base + share, force=True)
    run.seconds["cuts"] = round(time.monotonic() - started, 2)
    return reused, len(layout.cuts)


def _music_stem(plan: RenderPlan, run: _Run, cancel: threading.Event | None) -> None:
    """Make ``07_edit/music_ducked.wav`` for the assembly pass when the one on disk is not
    current for this timeline (the edit step normally made it already: then this is a quick
    check of its key)."""
    audio = plan.audio
    if audio is None or audio.stem is None:
        return
    from .music import ensure_ducked_stem

    info = ensure_ducked_stem(audio.timeline, audio.folder, config=plan.config,
                              ffmpeg=run.ffmpeg, cancel=cancel)
    if info is None:
        raise RenderError("The music track is missing, so the music cannot be mixed in.")
    if info.built:
        run.note(f"music stem: made in {info.seconds:.1f} s -> {info.stem.name}")
    else:
        run.note(f"music stem: reused {info.stem.name}")


def _assembly_phase(plan: RenderPlan, layout: Layout, output: Path, work_dir: Path,
                    log_file: Any, run: _Run) -> int:
    started = time.monotonic()
    (run.clips_dir / CONCAT_FILE).write_text(layout.concat_text(), encoding="utf-8")
    script = work_dir / f"filters_{plan.preset.id}.txt"
    script.write_text(plan.filter_graph, encoding="utf-8")
    command = [
        run.ffmpeg,
        "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats",
        "-progress", "pipe:1", "-stats_period", "0.5", "-y",
    ]
    for item in plan.inputs:
        command.extend(item)
    command += ["-/filter_complex", str(script), *plan.output_args(), str(output)]
    log_file.write("command: " + subprocess.list2cmdline(command) + "\n")
    log_file.flush()
    try:
        process = subprocess.Popen(
            command, cwd=str(work_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
    except OSError as exc:
        if oserror_is_memory(exc):
            raise RenderOutOfMemory(f"FFmpeg could not be started: {exc}") from exc
        raise RenderError(f"FFmpeg could not be started: {exc}") from exc
    killed = threading.Event()
    recent: deque[str] = deque(maxlen=40)

    def watchdog() -> None:
        while process.poll() is None:
            if run.stop():
                killed.set()
                process.kill()
                return
            time.sleep(0.2)

    watcher = threading.Thread(target=watchdog, name=f"render-cancel-{run.name}", daemon=True)
    watcher.start()
    pump = threading.Thread(target=_pump_stderr, args=(process.stderr, log_file, recent),
                            name=f"render-log-{run.name}", daemon=True)
    pump.start()
    base = PHASE_SHARE["scenes"] + PHASE_SHARE["cuts"]
    total_us = max(plan.total_s, 0.01) * 1_000_000
    assert process.stdout is not None
    try:
        for line in process.stdout:
            parsed = parse_progress_line(line)
            if parsed is None:
                continue
            key, value = parsed
            if key in ("out_time_us", "out_time_ms"):
                try:
                    fraction = min(0.99, max(0.0, int(value) / total_us))
                except ValueError:
                    continue
                run.report(base + PHASE_SHARE["assembly"] * fraction)
    finally:
        process.stdout.close()
        returncode = process.wait()
        watcher.join(timeout=1.0)
        pump.join(timeout=5.0)
        if process.stderr is not None:
            process.stderr.close()
    run.seconds["assembly"] = round(time.monotonic() - started, 2)
    log_file.write(
        f"exit code {returncode} after {time.monotonic() - started:.1f} s (assembly)\n"
    )
    log_file.flush()
    if killed.is_set():
        raise RenderCancelled("The render was stopped.")
    if returncode != 0 and looks_out_of_memory("".join(recent)):
        raise RenderOutOfMemory(f"FFmpeg ran out of memory joining the clips (exit code "
                                f"{returncode})")
    return returncode


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _prune(clips_dir: Path, layout: Layout) -> None:
    """Remove clips of this preset the current timeline no longer uses."""
    keep = {CONCAT_FILE}
    for job in layout.scenes:
        keep.add(job.path.name if job.path else job.file_name)
    for cut in layout.cuts:
        keep.add(cut.path.name if cut.path else cut.file_name)
    try:
        entries = list(clips_dir.iterdir())
    except OSError:
        return
    for path in entries:
        if path.is_file() and path.name not in keep and path.suffix in (".mp4", ".txt"):
            try:
                path.unlink()
            except OSError:
                log.info("Could not remove the old clip %s", path)


def run_pipeline(
    plan: RenderPlan,
    output: Path,
    *,
    work_dir: Path,
    log_path: Path,
    progress: Callable[[str, float], None] | None = None,
    cancel: threading.Event | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    label: str | None = None,
) -> RenderResult:
    """Render ``plan`` to ``output`` (what ``ffmpeg.run_render`` does since v2). Waits while
    another render runs in this app (:data:`RENDER_GATE`)."""
    layout: Layout | None = plan.layout
    if layout is None:
        raise RenderError("The render plan has no scene layout.")
    ffmpeg = ffmpeg or find_ffmpeg()
    name = label or plan.preset.id
    wait_for_turn(name, progress, cancel)
    try:
        return _run_pipeline(plan, layout, Path(output), work_dir=Path(work_dir),
                             log_path=log_path, progress=progress, cancel=cancel,
                             ffmpeg=ffmpeg, ffprobe=ffprobe, name=name)
    finally:
        RENDER_GATE.release()


def wait_for_turn(name: str, progress: Callable[[str, float], None] | None,
                  cancel: threading.Event | None) -> None:
    """Take :data:`RENDER_GATE`, saying "Waiting for another render to finish" while another
    render holds it. Raises :class:`RenderCancelled` when the cancel flag is set meanwhile."""
    if RENDER_GATE.acquire(blocking=False):
        return
    log.info("Render %s waits for another render to finish", name)
    if progress is not None:
        progress(WAITING_MESSAGE.format(name=name), 0.0)
    while not RENDER_GATE.acquire(timeout=GATE_POLL_S):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled(f"The {name} render was stopped.")


def _run_pipeline(
    plan: RenderPlan,
    layout: Layout,
    output: Path,
    *,
    work_dir: Path,
    log_path: Path,
    progress: Callable[[str, float], None] | None,
    cancel: threading.Event | None,
    ffmpeg: str,
    ffprobe: str | None,
    name: str,
) -> RenderResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    clips_dir = work_dir / layout.clips_dir
    clips_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    abort = threading.Event()

    def stop() -> bool:
        return abort.is_set() or (cancel is not None and cancel.is_set())

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_lock = threading.Lock()
    with log_path.open("a", encoding="utf-8") as log_file:

        def note(line: str) -> None:
            with log_lock:
                log_file.write(line + "\n")
                log_file.flush()

        log_file.write(
            f"\n===== {stamp}  render {name} -> {output.name} ({plan.width}x{plan.height}, "
            f"{plan.total_s:g} s, x264 {plan.x264_preset} crf {plan.crf}) =====\n"
        )
        log_file.write(
            f"{len(layout.scenes)} scene clips, {len(layout.cuts)} transition clips in "
            f"{layout.clips_dir} ({layout.total_frames} frames at {layout.fps} fps)\n"
        )
        log_file.flush()
        run = _Run(name=name, clips_dir=clips_dir, ffmpeg=ffmpeg,
                   report=_Reporter(progress, name), note=note, stop=stop, abort=abort)
        run.report(0.0, force=True)
        assembling = False
        try:
            scenes_reused, scenes_total = _scene_phase(layout, run)
            if stop():
                raise RenderCancelled("The render was stopped.")
            cuts_reused, cuts_total = _cut_phase(layout, run)
            if stop():
                raise RenderCancelled("The render was stopped.")
            _music_stem(plan, run, cancel)
            if stop():
                raise RenderCancelled("The render was stopped.")
            assembling = True
            returncode = _assembly_phase(plan, layout, output, work_dir, log_file, run)
        except RenderCancelled:
            note(f"stopped after {time.monotonic() - started:.1f} s (finished clips are kept)")
            _remove(output)
            raise RenderCancelled(f"The {name} render was stopped.") from None
        except (RenderOutOfMemory, MemoryError) as exc:
            # Python (numpy) or FFmpeg (x264's "malloc of size N failed") could not get the
            # memory it needed: nothing is wrong with the clips, which are kept.
            abort.set()
            note(f"failed: out of memory ({type(exc).__name__}: {exc})")
            if assembling:
                _remove(output)
            raise RenderOutOfMemory(out_of_memory_message(name)) from exc
        except RenderError as exc:
            note(f"failed: {exc}")
            raise RenderError(f"FFmpeg could not render {name}: {exc}") from exc
        except OSError as exc:  # a full disk, a folder that went away ...
            note(f"failed: {exc}")
            if oserror_is_memory(exc):
                abort.set()
                raise RenderOutOfMemory(out_of_memory_message(name)) from exc
            raise RenderError(
                f"Could not render {name}: a file could not be written ({exc})."
            ) from exc
        note(
            f"phases: scene clips {run.seconds.get('scenes', 0):.1f} s "
            f"({scenes_reused} of {scenes_total} reused), transitions "
            f"{run.seconds.get('cuts', 0):.1f} s ({cuts_reused} of {cuts_total} reused), "
            f"assembly {run.seconds.get('assembly', 0):.1f} s"
        )
    if returncode != 0:
        _remove(output)  # whatever FFmpeg wrote before it failed is not a video
        detail = _tail(log_path)
        raise RenderError(
            f"FFmpeg could not render {name} (exit code {returncode}). The last lines of "
            f"{log_path.name}:\n{detail}"
        )
    _prune(clips_dir, layout)
    info = probe_media(output, ffprobe, cancel)
    run.report(100.0, force=True)
    fallbacks = sum(
        1 for job in layout.scenes
        if getattr(job.request.motion, "style", None) == "parallax" and job.renderer == "zoompan"
    )
    return RenderResult(
        path=output,
        duration_s=info.duration_s,
        size_bytes=info.size_bytes,
        seconds_taken=round(time.monotonic() - started, 1),
        width=info.width,
        height=info.height,
        scene_clips_total=scenes_total,
        scene_clips_reused=scenes_reused,
        cuts_total=cuts_total,
        cuts_reused=cuts_reused,
        phase_seconds=dict(run.seconds),
        parallax_fallbacks=fallbacks,
    )
