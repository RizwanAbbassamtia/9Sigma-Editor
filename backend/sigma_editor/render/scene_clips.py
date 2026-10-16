"""Per-scene clips: every scene is rendered once per preset to
``07_edit/clips/<preset>/scene_NN.<hash>.mp4`` and reused while nothing it is made from
changes (docs/TRANSITIONS-V2-CONTRACT.md section 3).

A clip holds the scene's own frames plus the frames its transitions need on either side:
the part of the incoming cut that falls before the scene starts and the part of the
outgoing cut after it ends (``assembly.py`` works out the frame counts). The camera move
spans the whole clip, overlaps included, so the slow ends of the eased move fall under the
cuts. Keyframes are forced where the body starts and where the outgoing overlap starts, so
the transition renderer can seek straight to its frames.

Two ways to draw a scene:

* FFmpeg ``zoompan`` on the picture scaled to the preset's canvas, with the eased path of
  ``render/motion.py`` (``MotionPath`` + ``zoompan_filter``; the canvas is planar RGB so the
  window moves in single pixels, not pairs). This is the default.
* A *frame provider* (``register_frame_provider``): Python code that draws the frames (the
  2.5D parallax renderer, or sub-pixel Ken Burns). Its frames go to x264 through a pipe.
* A *clip scene* (9Sigma Editor v0.2, docs/CONTRACT-v0.2.md section 6: ``scene.clip`` is
  set) is one FFmpeg run on the video clip: decoded from ``in_s`` (less the lead-in) at
  ``speed``, fitted to the frame as ``scene.frame`` says (``None``: it fills the frame),
  the first frame held while the lead-in starts before the clip, the last frame held when
  the clip runs out, exactly the planned frames (:func:`render_video_clip`). Its hash
  covers the clip file (path, size, date), ``in_s``, ``speed``, the lead-in and the frame,
  not the camera move (a clip scene has none in v0.2).

The hash covers the picture (path, size, modification time), the whole motion block of the
timeline (preset, rectangles, style, strength), the clip length and keyframes, the preset's
sizes, the encoder settings and which renderer draws it. Clips encode as H.264 CRF 14
(``fast``; the proxy ``ultrafast`` CRF 18), yuv420p, BT.709 tagged, 1/15360 time base:
identical parameters for every clip so the concat demuxer can join them.

Memory (a 16 GB laptop shared with other programs): two clips are drawn at once (one at a
time above 1080p), each x264 encoder gets its share of the CPUs instead of x264's own 1.5
threads per CPU, and looks ahead at most 10 frames. At 1080p that halves an encoder's memory
(about 680 MB -> 325 MB on an 8-thread PC) for the same speed; the clips are the same
pictures, so a cached clip stays valid. A child process that runs out of memory raises
:class:`~.ffmpeg.RenderOutOfMemory`, never "damaged clip".

Optional ``clips:`` settings in ``config/render.yaml``: ``workers`` (2), ``workers_large``
(1, frames above 1080p), ``x264_preset`` / ``crf`` (fast / 14), ``proxy_x264_preset`` /
``proxy_crf`` (ultrafast / 18) and ``ken_burns: python`` (sub-pixel Ken Burns drawn in
Python instead of zoompan).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..models.timeline import ClipSource, FrameAdjust, TimelineMotion
from .ffmpeg import (
    HOLD_LAST_FRAME_S,
    NO_WINDOW,
    RenderCancelled,
    RenderError,
    RenderOutOfMemory,
    fmt,
    frame_fit_parts,
    frame_place_exprs,
    looks_out_of_memory,
    oserror_is_memory,
    resolve_path,
)
from .geometry import cover_region
from .presets import PROXY_ID, RenderConfig, RenderPreset

log = logging.getLogger(__name__)

CLIPS_DIR = "clips"
CLIP_ENGINE_VERSION = "clips.2"
"""Part of every scene clip's hash: change it when the way clips are drawn changes. 2: the
zoompan crop is centred on the sub-pixel window (``motion.ZoompanExprs.centred``)."""
PARALLAX_DRAW_VERSION = "parallax.2"
"""Part of every 2.5D parallax clip's hash (and of the finished render's key): change it when
``render/parallax`` draws frames differently. 2: the overscan is worked out per axis, and a
move too strong for the overscan cap is softened instead of mirroring the picture's edge."""
TIME_SCALE = 15360
COLOUR_TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
TO_YUV = (
    "scale=out_color_matrix=bt709:out_range=tv:flags=bicubic+accurate_rnd,format=yuv420p,"
    f"{COLOUR_TAGS},setsar=1"
)
TO_RGB = "scale=in_color_matrix=bt709:in_range=tv:flags=bicubic+accurate_rnd+full_chroma_int"
STDERR_LINES = 40

LARGE_FRAME_PIXELS = 1920 * 1080 * 3 // 2
"""Frames with more pixels than this (2160p) are drawn one clip at a time."""
MAX_ENCODER_THREADS = 8
DECODE_THREADS = 2
"""Threads of the decoder that reads a scene clip's overlap frames back (FFmpeg's default of
one per CPU holds more frames in memory and is no faster for a few dozen frames)."""
CLIP_LOOKAHEAD = 10
"""Frames an intermediate clip's x264 looks ahead (``fast`` alone looks 30 ahead, each frame
kept in memory several times over, for no visible gain at CRF 14)."""
X264_LOOKAHEAD: dict[str, int] = {
    "ultrafast": 0, "superfast": 0, "veryfast": 10, "faster": 20, "fast": 30, "medium": 40,
    "slow": 50, "slower": 60, "veryslow": 60, "placebo": 60,
}
"""x264's own ``rc-lookahead`` per speed preset (only lowered, never raised)."""

Stop = Callable[[], bool]
VIDEO_RENDERER = "video"
"""The ``renderer`` of a clip scene (drawn from its video clip, v0.2)."""


# Encoding --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClipEncoding:
    """x264 settings shared by every intermediate clip of one preset."""

    x264_preset: str = "fast"
    crf: int = 14
    profile: str = "high"
    pix_fmt: str = "yuv420p"
    threads: int = field(default=0, compare=False)
    """x264 threads per encoder (0 = x264 decides). How the work is shared out, not what is
    encoded, so it is left out of comparisons and of the clips' hash."""

    def args(self) -> list[str]:
        return [
            "-c:v", "libx264",
            "-preset", self.x264_preset,
            "-crf", str(self.crf),
            "-profile:v", self.profile,
            "-pix_fmt", self.pix_fmt,
            *self.memory_args(),
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-color_range", "tv",
            "-video_track_timescale", str(TIME_SCALE),
            "-movflags", "+faststart",
            "-an",
        ]

    def memory_args(self) -> list[str]:
        """``-threads`` and a shorter ``-rc-lookahead``: the encoder's memory, not its
        pictures (see the module docstring)."""
        args: list[str] = []
        if self.threads > 0:
            args += ["-threads", str(self.threads)]
        if X264_LOOKAHEAD.get(self.x264_preset, CLIP_LOOKAHEAD + 1) > CLIP_LOOKAHEAD:
            args += ["-rc-lookahead", str(CLIP_LOOKAHEAD)]
        return args

    def token(self) -> str:
        return f"{self.x264_preset}/{self.crf}/{self.profile}/{self.pix_fmt}"


@lru_cache(maxsize=1)
def _clip_settings() -> dict[str, Any]:
    """The optional ``clips`` section of ``config/render.yaml`` (read once, like the rest of
    the render settings; :func:`clear_caches` forgets it)."""
    try:
        from ..config_files import load_yaml

        data = load_yaml("render.yaml")
    except Exception:  # noqa: BLE001 - the built-in defaults are always usable
        return {}
    section = data.get("clips") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def clear_caches() -> None:
    _clip_settings.cache_clear()


def clip_encoding_for(preset: RenderPreset, final_x264: str | None = None) -> ClipEncoding:
    """Clips for a final render: ``fast`` CRF 14, or the final's own x264 speed when that is
    faster (a clip never takes longer to encode than the video it is for); for the proxy:
    ``ultrafast`` CRF 18. ``clips:`` in render.yaml may change the defaults. Each encoder
    gets its share of the CPUs (:func:`encoder_threads`)."""
    from .presets import X264_PRESETS

    settings = _clip_settings()
    proxy = preset.id == PROXY_ID
    speed = str(settings.get("proxy_x264_preset" if proxy else "x264_preset") or "")
    default = ClipEncoding("ultrafast", 18) if proxy else ClipEncoding()
    try:
        crf = int(settings.get("proxy_crf" if proxy else "crf", default.crf))
    except (TypeError, ValueError):
        crf = default.crf
    chosen = speed if speed in X264_PRESETS else default.x264_preset
    if final_x264 in X264_PRESETS and X264_PRESETS.index(final_x264) < X264_PRESETS.index(chosen):
        chosen = str(final_x264)
    threads = encoder_threads(clip_workers(preset.landscape))
    return ClipEncoding(chosen, min(max(crf, 0), 51), threads=threads)


def _setting_int(name: str, default: int) -> int:
    try:
        return int(_clip_settings().get(name, default))
    except (TypeError, ValueError):
        return default


def clip_workers(size: tuple[int, int] | None = None) -> int:
    """How many scene clips (and transition clips) are drawn at once: ``clips.workers``
    (default 2); for frames bigger than 1080p ``clips.workers_large`` (default 1), since a
    single 4K encoder already needs more than a gigabyte of memory."""
    workers = _setting_int("workers", 2)
    if size is not None and int(size[0]) * int(size[1]) > LARGE_FRAME_PIXELS:
        workers = min(workers, _setting_int("workers_large", 1))
    return min(max(workers, 1), max(1, (os.cpu_count() or 2)))


def encoder_threads(workers: int) -> int:
    """x264 threads for each of ``workers`` encoders running at once: the logical CPUs
    shared out (2 to 8). x264 alone takes 1.5 threads per CPU, and every thread holds frames
    in memory: on an 8-thread laptop two 1080p clip encoders would use 24 threads."""
    cpus = os.cpu_count() or 2
    return max(2, min(MAX_ENCODER_THREADS, cpus // max(int(workers), 1)))


def keyframe_args(keyframes: Sequence[int]) -> list[str]:
    frames = sorted({int(k) for k in keyframes if int(k) > 0})
    if not frames:
        return []
    # An option value, not a filter graph: the commas stay as they are.
    return ["-force_key_frames", "expr:" + "+".join(f"eq(n,{k})" for k in frames)]


# Frame providers (render/motion.py, render/parallax/) ----------------------------------------


@dataclass(frozen=True)
class SceneFrameRequest:
    """What a frame provider gets to draw one scene clip."""

    index: int
    """The scene's index in the timeline (0-based)."""
    image: Path
    image_size: tuple[int, int]
    region: tuple[float, float, float, float]
    """The part of the picture that covers the frame, ``(x, y, w, h)`` in picture pixels."""
    size: tuple[int, int]
    """Output frame size."""
    canvas: tuple[int, int]
    fps: int
    frames: int
    """Frames to draw: the incoming overlap + the scene + the outgoing overlap."""
    lead_in: int
    """Frames before the scene's own start (the incoming overlap)."""
    tail_out: int
    """Frames after the scene's own end (the outgoing overlap)."""
    motion: TimelineMotion
    seed: int
    preset_id: str
    folder: Path
    clip: ClipSource | None = None
    """A clip scene (v0.2): the video clip the scene plays (``image`` is its poster frame),
    relative to ``folder``."""
    frame: FrameAdjust | None = None
    """A clip scene: where the clip sits in the frame (``None``: it fills the frame)."""

    @property
    def clip_file(self) -> Path | None:
        """The clip scene's video file, or ``None`` for a picture scene."""
        return resolve_path(self.folder, self.clip.path) if self.clip is not None else None

    def path(self) -> Any:
        """The scene's ``MotionPath`` in picture pixels (kept inside the cover region), with
        the handheld wobble of ``config/render.yaml`` and the scene's ``motion.ease`` when it
        overrides the preset's curve (M5)."""
        from .motion import MotionPath, load_motion_config, scene_easing

        motion = self.motion
        return MotionPath.for_preset(
            motion.preset, motion.start_rect, motion.end_rect, seed=self.seed,
            bounds=self.region,
            handheld_amplitude=load_motion_config().handheld_amplitude,
            easing=scene_easing(motion),
        )


def motion_settings_token() -> str:
    """A fingerprint of the motion settings (``motion:`` and ``parallax:`` in render.yaml):
    part of every clip's hash, so a changed setting redraws the clips."""
    try:
        from .motion import load_motion_config

        blob = repr(load_motion_config()).encode("utf-8")
    except Exception:  # noqa: BLE001 - without the motion module there is nothing to track
        return ""
    return hashlib.sha256(blob).hexdigest()[:10]


@runtime_checkable
class FrameProvider(Protocol):
    """Draws scene clips in Python. ``cache_token`` decides: a string (part of the clip's
    hash, so a new model or setting re-renders) means "I draw this scene", ``None`` means
    "use zoompan". ``frames`` yields ``request.frames`` RGB ``uint8`` arrays of
    ``(height, width, 3)``."""

    name: str

    def cache_token(self, request: SceneFrameRequest) -> str | None: ...

    def frames(self, request: SceneFrameRequest) -> Iterable[np.ndarray]: ...


_PROVIDERS: list[FrameProvider] = []
_PROVIDERS_LOCK = threading.Lock()


def register_frame_provider(provider: FrameProvider, *, first: bool = False) -> None:
    """Add a provider (asked in order; ``first`` puts it in front). Registering the same
    object twice keeps one entry."""
    with _PROVIDERS_LOCK:
        if provider in _PROVIDERS:
            _PROVIDERS.remove(provider)
        if first:
            _PROVIDERS.insert(0, provider)
        else:
            _PROVIDERS.append(provider)


def unregister_frame_provider(provider: FrameProvider) -> None:
    with _PROVIDERS_LOCK:
        if provider in _PROVIDERS:
            _PROVIDERS.remove(provider)


def frame_providers() -> list[FrameProvider]:
    with _PROVIDERS_LOCK:
        return list(_PROVIDERS)


class MotionFramesProvider:
    """The built-in provider: ``render/parallax``'s ``iter_motion_frames`` (the motion
    module's own Python renderer). It draws scenes set to 2.5D parallax that have a depth map
    (``scene_NN.depth.png``); without one the scene stays a zoompan Ken Burns move and the
    edit review says parallax was not available. With ``clips.ken_burns: python`` in
    render.yaml it also draws the Ken Burns moves (sub-pixel, slower than zoompan)."""

    name = "motion"

    def cache_token(self, request: SceneFrameRequest) -> str | None:
        style = getattr(request.motion, "style", "ken_burns")
        python_ken_burns = str(_clip_settings().get("ken_burns") or "").lower() == "python"
        if style != "parallax" and not python_ken_burns:
            return None
        try:
            from .parallax import depth_fingerprint

            depth = depth_fingerprint(request.image) if style == "parallax" else ""
        except Exception:  # noqa: BLE001 - no motion module: zoompan draws the scene
            return None
        if depth:
            strength = float(getattr(request.motion, "strength", 0.0))
            return f"{PARALLAX_DRAW_VERSION}/{depth}/{strength:.4f}"
        return "ken_burns" if python_ken_burns else None

    def frames(self, request: SceneFrameRequest) -> Iterator[np.ndarray]:
        from .motion import PARALLAX, load_motion_config
        from .parallax import iter_motion_frames
        from .parallax.clip import load_depth
        from .parallax.depth import load_rgb

        config = load_motion_config()
        motion = request.motion
        style = getattr(motion, "style", "ken_burns")
        depth = load_depth(None, request.image) if style == PARALLAX else None
        path = request.path()
        interpolation = config.proxy_interpolation if request.preset_id == PROXY_ID else None
        yield from iter_motion_frames(
            load_rgb(request.image), size=request.size, fps=request.fps,
            frames=request.frames, path=path,
            style=PARALLAX if depth is not None else "ken_burns",
            strength=float(getattr(motion, "strength", 0.0)), depth=depth,
            interpolation=interpolation, config=config,
        )


_FALLBACK_PROVIDERS: tuple[FrameProvider, ...] = (MotionFramesProvider(),)


def choose_provider(request: SceneFrameRequest) -> tuple[FrameProvider | None, str]:
    """The provider that draws this scene and its cache token, or ``(None, "zoompan")``.
    Registered providers are asked first, then the built-in motion renderer."""
    for provider in [*frame_providers(), *_FALLBACK_PROVIDERS]:
        try:
            token = provider.cache_token(request)
        except Exception as exc:  # noqa: BLE001 - a broken provider falls back to zoompan
            log.warning("Frame provider %s failed on scene %d: %s",
                        getattr(provider, "name", provider), request.index + 1, exc)
            continue
        if token:
            return provider, f"{getattr(provider, 'name', 'provider')}:{token}"
    return None, "zoompan"


# Jobs ------------------------------------------------------------------------------------------


@dataclass
class SceneClipJob:
    """One scene clip to render (or reuse)."""

    number: int
    """1-based scene number in the video (the file name's NN)."""
    request: SceneFrameRequest
    encoding: ClipEncoding
    keyframes: tuple[int, ...]
    scale_flags: str = "lanczos"
    provider: FrameProvider | None = None
    renderer: str = "zoompan"
    key: str = ""
    reused: bool = False
    seconds: float = 0.0
    path: Path | None = None
    """The clip on disk once rendered or found (a reused clip may carry another NN)."""
    image_stat: os.stat_result | None = None
    clip_stat: os.stat_result | None = None
    """The clip scene's video file (size and date go into the hash)."""
    fallback: str = ""
    """Why the frame provider could not draw this scene (it was drawn by zoompan instead)."""

    @property
    def frames(self) -> int:
        return self.request.frames

    @property
    def file_name(self) -> str:
        return f"scene_{self.number:02d}.{self.key}.mp4"

    def rekey(self) -> None:
        """Work out :attr:`key` from what the clip is made of (and drawn by)."""
        blob = json.dumps(self.hash_payload(self.image_stat), sort_keys=True, default=str)
        self.key = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def fall_back_to_zoompan(self, reason: str) -> None:
        """The provider failed: the scene is drawn by zoompan and saved under zoompan's own
        hash, so the provider's hash never names a zoompan clip (the provider is asked again
        next time, and the cuts next to this scene are keyed on what they really show)."""
        self.fallback = f"{self.renderer} failed: {reason}"
        self.provider = None
        self.renderer = "zoompan"
        self.rekey()

    def hash_payload(self, image_stat: os.stat_result | None) -> dict[str, Any]:
        request = self.request
        motion = request.motion.model_dump(mode="json")
        if motion.get("ease") is None:  # M5's motion.ease: no override, same clip as before
            motion.pop("ease", None)
        payload: dict[str, Any] = {
            "engine": CLIP_ENGINE_VERSION,
            "image": str(request.image),
            "image_mtime_ns": int(image_stat.st_mtime_ns) if image_stat else 0,
            "image_bytes": int(image_stat.st_size) if image_stat else 0,
            "motion": motion,
            "frames": request.frames,
            "keyframes": list(self.keyframes),
            "size": list(request.size),
            "canvas": list(request.canvas),
            "fps": request.fps,
            "seed": request.seed if wobbles(request.motion) else 0,
            "motion_settings": motion_settings_token(),
            "scale_flags": self.scale_flags,
            "encoding": self.encoding.token(),
            "renderer": self.renderer,
        }
        clip = request.clip
        if clip is not None:
            # A clip scene (v0.2): the clip file, where and how fast it plays, the lead-in
            # (it moves the decode offset) and the frame; no camera move in v0.2.
            stat = self.clip_stat
            payload["clip"] = {
                "file": str(request.clip_file),
                "mtime_ns": int(stat.st_mtime_ns) if stat else 0,
                "bytes": int(stat.st_size) if stat else 0,
                "in_s": float(clip.in_s),
                "speed": float(clip.speed),
                "lead_in": request.lead_in,
                "frame": (
                    request.frame.model_dump(mode="json") if request.frame is not None else None
                ),
            }
            payload["motion"] = None
            payload["seed"] = 0
            payload["motion_settings"] = ""
        return payload


def wobbles(motion: TimelineMotion) -> bool:
    """Whether the move depends on its seed (the ``handheld`` wobble, seeded by the scene's
    index as docs/design/MOTION-PARITY.md says): only then is the seed part of the hash, so
    inserting a scene does not redraw the ordinary clips after it."""
    try:
        from .motion import preset_spec

        spec = preset_spec(motion.preset)
    except (ImportError, AttributeError):
        return motion.preset == "handheld"
    return bool(spec is not None and getattr(spec, "handheld", False))


def make_scene_job(
    *,
    number: int,
    index: int,
    image: Path,
    image_size: tuple[int, int],
    motion: TimelineMotion,
    preset: RenderPreset,
    aspect: str,
    fps: int,
    frames: int,
    lead_in: int,
    tail_out: int,
    keyframes: Sequence[int],
    config: RenderConfig,
    folder: Path,
    encoding: ClipEncoding,
    clip: ClipSource | None = None,
    frame: FrameAdjust | None = None,
) -> SceneClipJob:
    """The job of one scene clip. ``clip`` (v0.2) makes it a clip scene drawn from the
    video clip by :func:`render_video_clip` (``image`` is then the poster frame); otherwise
    a frame provider or zoompan draws the picture."""
    width, height = preset.size(aspect)
    canvas = preset.canvas(aspect)
    region = cover_region(image_size[0], image_size[1], width, height)
    request = SceneFrameRequest(
        index=index,
        image=image,
        image_size=image_size,
        region=region,
        size=(width, height),
        canvas=canvas,
        fps=fps,
        frames=max(int(frames), 1),
        lead_in=int(lead_in),
        tail_out=int(tail_out),
        motion=motion,
        seed=index,
        preset_id=preset.id,
        folder=Path(folder),
        clip=clip,
        frame=frame,
    )
    if clip is not None:
        provider, renderer = None, VIDEO_RENDERER
    else:
        provider, renderer = choose_provider(request)
    job = SceneClipJob(
        number=number,
        request=request,
        encoding=encoding,
        keyframes=tuple(sorted({int(k) for k in keyframes if 0 < int(k) < request.frames})),
        scale_flags=config.scale_flags,
        provider=provider,
        renderer=renderer,
    )
    try:
        job.image_stat = image.stat()
    except OSError:
        job.image_stat = None
    clip_file = request.clip_file
    if clip_file is not None:
        try:
            job.clip_stat = clip_file.stat()
        except OSError:
            job.clip_stat = None
    job.rekey()
    return job


# The eased zoompan path ------------------------------------------------------------------------


def _ease_expr(t: str) -> str:
    return f"if(lt({t}\\,0.5)\\,4*pow({t}\\,3)\\,1-pow(2-2*{t}\\,3)/2)"


def _fallback_zoompan(job: SceneClipJob) -> str:
    """The eased move without ``render/motion.py`` (linear centre, linear width)."""
    from .geometry import rect_to_canvas

    request = job.request
    canvas_w, canvas_h = request.canvas
    x0, y0, w0, _h0 = rect_to_canvas(request.motion.start_rect, request.region, canvas_w,
                                     canvas_h)
    x1, y1, w1, _h1 = rect_to_canvas(request.motion.end_rect, request.region, canvas_w,
                                     canvas_h)
    n = request.frames
    t = f"min(on/{n - 1}\\,1)" if n > 1 else "0"
    e = _ease_expr(t)
    zoom = f"{fmt(canvas_w)}/({fmt(w0)}+({fmt(w1 - w0)})*{e})"
    return (
        f"zoompan=z='{zoom}':x='{fmt(x0)}+({fmt(x1 - x0)})*{e}':"
        f"y='{fmt(y0)}+({fmt(y1 - y0)})*{e}':d={n}:s={request.size[0]}x{request.size[1]}"
        f":fps={request.fps}"
    )


def zoompan_part(job: SceneClipJob) -> str:
    """The ``zoompan=...`` filter for this scene: ``motion.zoompan_filter`` on the path
    scaled from picture pixels to canvas pixels."""
    request = job.request
    try:
        from .motion import zoompan_filter

        path = request.path()
        rx, ry, rw, _rh = request.region
        canvas_path = path.scaled(request.canvas[0] / rw if rw else 1.0, origin=(rx, ry))
        return zoompan_filter(canvas_path, frames=request.frames, canvas=request.canvas,
                              size=request.size, fps=request.fps)
    except (ImportError, AttributeError, TypeError) as exc:
        log.warning("render/motion.py is not usable (%s); using the built-in eased move.", exc)
        return _fallback_zoompan(job)


def zoompan_graph(job: SceneClipJob) -> str:
    """The whole filter graph of a zoompan scene clip (picture -> canvas -> move -> yuv)."""
    request = job.request
    canvas_w, canvas_h = request.canvas
    return (
        f"[0:v]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase"
        f":flags={job.scale_flags},crop={canvas_w}:{canvas_h},format=gbrp,setsar=1,"
        f"{zoompan_part(job)},{TO_YUV}[v]"
    )


# Processes -------------------------------------------------------------------------------------


class _Watched:
    """A child process killed as soon as ``stop()`` is true; its stderr kept (last lines)."""

    def __init__(self, command: list[str], *, stdin: Any = subprocess.DEVNULL,
                 stdout: Any = subprocess.DEVNULL, stop: Stop | None = None,
                 cwd: str | None = None) -> None:
        self.command = command
        self.lines: deque[str] = deque(maxlen=STDERR_LINES)
        self.killed = threading.Event()
        try:
            self.process = subprocess.Popen(
                command, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, cwd=cwd,
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            if oserror_is_memory(exc):
                raise RenderOutOfMemory(
                    f"The computer is too short of memory to start FFmpeg ({exc})."
                ) from exc
            raise RenderError(f"FFmpeg could not be started: {exc}") from exc
        self._stop = stop
        self._pump = threading.Thread(target=self._read_stderr, daemon=True,
                                      name="clip-stderr")
        self._watch = threading.Thread(target=self._watchdog, daemon=True, name="clip-cancel")
        try:
            self._pump.start()
            self._watch.start()
        except RuntimeError as exc:  # no thread to be had (the PC is out of memory)
            self.kill()
            raise RenderOutOfMemory(
                "The computer is too short of memory to keep rendering. Close some programs "
                "and try again."
            ) from exc

    def _read_stderr(self) -> None:
        stream = self.process.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                text = raw.decode("utf-8", errors="replace").rstrip()
                if text:
                    self.lines.append(text)
        except (OSError, ValueError):
            pass

    def _watchdog(self) -> None:
        while self.process.poll() is None:
            if self._stop is not None and self._stop():
                self.killed.set()
                self.kill()
                return
            time.sleep(0.1)

    def kill(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.kill()
        except OSError:
            pass

    def wait(self) -> int:
        code = self.process.wait()
        if self._pump.is_alive():
            self._pump.join(timeout=5.0)
        if self._watch.is_alive():
            self._watch.join(timeout=1.0)
        if self.process.stderr is not None:
            try:
                self.process.stderr.close()
            except OSError:
                pass
        return int(code)

    def tail(self, lines: int = 8) -> str:
        return "\n".join(list(self.lines)[-lines:])

    def out_of_memory(self) -> bool:
        """Whether the child's messages say an allocation failed (x264's ``malloc of size N
        failed``, ``Cannot allocate memory`` ...)."""
        return looks_out_of_memory("\n".join(self.lines))

    def failure(self, message: str) -> RenderError:
        """``message`` with the child's last lines, as :class:`RenderOutOfMemory` when the
        child ran out of memory (nothing is wrong with the clip or the picture then)."""
        detail = f"{message}: {self.tail()}"
        return RenderOutOfMemory(detail) if self.out_of_memory() else RenderError(detail)


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def part_path(final: Path) -> Path:
    return final.with_name(final.stem + ".part.mp4")


def mp4_is_whole(path: Path) -> bool:
    """A quick look at an MP4's top-level boxes before a cached clip is reused: it starts
    with ``ftyp``, holds a ``moov`` and an ``mdat``, and its boxes add up to exactly the
    file's size. A clip cut short (a crash, a full disk) or left full of zeros (a power cut
    just after it was saved) fails. Reads a few header bytes, not the video."""
    try:
        size = path.stat().st_size
        seen: set[bytes] = set()
        offset = 0
        with path.open("rb") as handle:
            while offset < size:
                handle.seek(offset)
                header = handle.read(8)
                if len(header) < 8:
                    return False
                box_size = int.from_bytes(header[:4], "big")
                kind = header[4:8]
                if box_size == 1:  # a 64-bit size follows the type
                    large = handle.read(8)
                    if len(large) < 8:
                        return False
                    box_size = int.from_bytes(large, "big")
                elif box_size == 0:  # the box runs to the end of the file
                    box_size = size - offset
                if box_size < 8 or (offset == 0 and kind != b"ftyp"):
                    return False
                seen.add(kind)
                offset += box_size
    except OSError:
        return False
    return offset == size and {b"moov", b"mdat"} <= seen


def finish_clip(part: Path, final: Path, attempts: int = 10) -> None:
    """Rename a finished ``.part.mp4`` to its clip name. A virus scanner may hold a fresh
    file for a moment, so a refused rename is tried again for about two seconds."""
    for attempt in range(attempts):
        try:
            os.replace(part, final)
            return
        except PermissionError as exc:
            if attempt == attempts - 1:
                _discard(part)
                raise RenderError(
                    f"{final.name} could not be saved: another program is using it ({exc})."
                ) from exc
            time.sleep(0.2)
        except OSError as exc:
            _discard(part)
            raise RenderError(f"{final.name} could not be saved: {exc}") from exc


class FrameWriter:
    """RGB frames in, an H.264 clip out (written to ``<name>.part.mp4`` and renamed when
    complete, so a clip on disk is always whole)."""

    def __init__(self, path: Path, size: tuple[int, int], fps: int, encoding: ClipEncoding,
                 *, ffmpeg: str, keyframes: Sequence[int] = (), stop: Stop | None = None
                 ) -> None:
        self.path = Path(path)
        self.part = part_path(self.path)
        self.size = size
        self.frame_bytes = size[0] * size[1] * 3
        self.count = 0
        command = [
            ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
            "-framerate", str(fps), "-i", "pipe:0",
            "-vf", TO_YUV,
            *encoding.args(), *keyframe_args(keyframes),
            str(self.part),
        ]
        self.child = _Watched(command, stdin=subprocess.PIPE, stop=stop)
        self.closed = False

    def write(self, frame: np.ndarray) -> None:
        data = np.ascontiguousarray(frame, dtype=np.uint8)
        if data.shape != (self.size[1], self.size[0], 3):
            raise RenderError(
                f"A frame of {data.shape[1]}x{data.shape[0]} was drawn for a "
                f"{self.size[0]}x{self.size[1]} clip."
            )
        stdin = self.child.process.stdin
        assert stdin is not None
        try:
            stdin.write(memoryview(data).cast("B"))
        except (BrokenPipeError, OSError, ValueError) as exc:
            if self.child.killed.is_set():
                raise RenderCancelled("The render was stopped.") from exc
            self.child.wait()
            if not self.child.lines:
                raise RenderError(f"FFmpeg stopped while writing {self.path.name}: {exc}") from exc
            raise self.child.failure(f"FFmpeg stopped while writing {self.path.name}") from exc
        self.count += 1

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        stdin = self.child.process.stdin
        try:
            if stdin is not None:
                stdin.close()
        except OSError:
            pass
        code = self.child.wait()
        if self.child.killed.is_set():
            _discard(self.part)
            raise RenderCancelled("The render was stopped.")
        if code != 0 or not self.part.is_file():
            _discard(self.part)
            raise self.child.failure(f"FFmpeg could not write {self.path.name} (exit code {code})")
        finish_clip(self.part, self.path)

    def abort(self) -> None:
        self.closed = True
        try:
            if self.child.process.stdin is not None:
                self.child.process.stdin.close()
        except OSError:
            pass
        self.child.kill()
        self.child.wait()
        _discard(self.part)


class ClipReadError(RenderError):
    """A finished clip could not be decoded (damaged, or locked by another program)."""

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(f"FFmpeg could not read {Path(path).name}: {detail}")
        self.path = Path(path)
        self.detail = detail


def decode_frames(
    path: Path,
    size: tuple[int, int],
    *,
    ffmpeg: str,
    fps: int,
    start_frame: int = 0,
    count: int | None = None,
    stop: Stop | None = None,
) -> Iterator[np.ndarray]:
    """RGB frames of a clip, from frame ``start_frame`` on, for at most ``count`` frames.

    The seek jumps to the keyframe at or before ``start_frame`` (the scene clips force one
    where each overlap starts, so nothing needs decoding twice) and a ``select`` on the
    frames' times drops anything before ``start_frame``, so the first frame is exactly that
    one wherever the seek landed. Raises :class:`ClipReadError` when the clip cannot be
    decoded, :class:`~.ffmpeg.RenderOutOfMemory` when FFmpeg ran out of memory and
    :class:`~.ffmpeg.RenderCancelled` when ``stop`` ended it."""
    width, height = size
    command = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
               "-threads", str(DECODE_THREADS)]
    filters = f"{TO_RGB},format=rgb24"
    if start_frame > 0:
        # Times count from the seek point, start_frame + 1/4 frame: start_frame is at -1/4
        # of a frame, the frame before it at -5/4; keep from -3/4 on.
        command += ["-noaccurate_seek", "-ss", f"{(start_frame + 0.25) / fps:.6f}"]
        filters = f"select='gte(t\\,{-0.75 / fps:.6f})',{filters}"
    command += ["-i", str(path)]
    if count is not None:
        command += ["-frames:v", str(int(count))]
    command += ["-vf", filters, "-fps_mode", "passthrough", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "pipe:1"]
    child = _Watched(command, stdout=subprocess.PIPE, stop=stop)
    stream = child.process.stdout
    assert stream is not None
    frame_bytes = width * height * 3
    finished = False
    try:
        while True:
            if stop is not None and stop():
                raise RenderCancelled("The render was stopped.")
            buffer = bytearray(frame_bytes)
            view = memoryview(buffer)
            got = 0
            while got < frame_bytes:
                read = stream.readinto(view[got:])
                if not read:
                    break
                got += read
            if got == 0:
                finished = True
                break
            if got < frame_bytes:
                if child.killed.is_set() or (stop is not None and stop()):
                    # Cut off by a cancel (or another clip's failure): the clip is fine.
                    raise RenderCancelled("The render was stopped.")
                raise ClipReadError(path, "the clip ends in the middle of a frame")
            yield np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 3)
    finally:
        if not finished:
            child.kill()
        try:
            stream.close()
        except OSError:
            pass
        code = child.wait()
    if child.killed.is_set():
        raise RenderCancelled("The render was stopped.")
    if code != 0:
        if child.out_of_memory():  # the clip is fine: keep it
            raise child.failure(f"FFmpeg ran out of memory while reading {Path(path).name}")
        raise ClipReadError(path, child.tail() or f"exit code {code}")


# Rendering one clip ------------------------------------------------------------------------------


@dataclass
class ClipOutcome:
    frames: int
    seconds: float
    renderer: str
    notes: list[str] = field(default_factory=list)


def _progress_frames(stream: Any, on_frames: Callable[[int], None]) -> None:
    for raw in iter(stream.readline, b""):
        key, sep, value = raw.decode("utf-8", errors="replace").strip().partition("=")
        if sep and key == "frame":
            try:
                on_frames(int(value))
            except ValueError:
                continue


def render_zoompan(job: SceneClipJob, out_path: Path, *, ffmpeg: str, stop: Stop,
                   on_frames: Callable[[int], None]) -> None:
    request = job.request
    part = part_path(out_path)
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats",
        "-progress", "pipe:1", "-stats_period", "0.5", "-y",
        "-i", str(request.image),
        "-filter_complex", zoompan_graph(job),
        "-map", "[v]", "-frames:v", str(request.frames),
        *job.encoding.args(), *keyframe_args(job.keyframes),
        str(part),
    ]
    child = _Watched(command, stdout=subprocess.PIPE, stop=stop)
    assert child.process.stdout is not None
    try:
        _progress_frames(child.process.stdout, on_frames)
    finally:
        code = child.wait()
    if child.killed.is_set():
        _discard(part)
        raise RenderCancelled("The render was stopped.")
    if code != 0 or not part.is_file():
        _discard(part)
        raise child.failure(f"FFmpeg could not draw scene {job.number} (exit code {code})")
    finish_clip(part, out_path)


# Clip scenes (v0.2) ------------------------------------------------------------------------------


def clip_decode_start(clip: ClipSource, lead_in: int, fps: int) -> tuple[float, float]:
    """Where decoding starts in the clip's file for a scene clip that begins ``lead_in``
    frames before its scene, and for how many output seconds the first frame is held
    first: the scene's start shows the clip at ``in_s``, so the clip starts
    ``lead_in / fps * speed`` seconds earlier in the file; when that is before the file's
    start, decoding starts at 0 and the first frame fills the rest."""
    lead_s = int(lead_in) / max(int(fps), 1)
    offset = float(clip.in_s) - lead_s * float(clip.speed)
    if offset < 0:
        return 0.0, round(-offset / float(clip.speed), 6)
    return round(offset, 6), 0.0


def video_clip_graph(job: SceneClipJob) -> str:
    """The filter graph of a clip scene: the clip at the scene's speed, a constant frame
    rate, the first frame held while the lead-in starts before the clip does and the last
    one held when the clip runs out, fitted to the frame (``frame``; without one, or with
    one that only says ``cover``, the clip fills the frame), yuv420p with the BT.709 tags
    of every scene clip."""
    request = job.request
    clip, frame = request.clip, request.frame
    assert clip is not None
    width, height = request.size
    _offset, hold = clip_decode_start(clip, request.lead_in, request.fps)
    pad = ["stop_mode=clone", f"stop_duration={HOLD_LAST_FRAME_S}"]
    if hold > 0:
        pad = ["start_mode=clone", f"start_duration={fmt(hold)}", *pad]
    source = (
        f"[0:v]setpts=(PTS-STARTPTS)/{fmt(clip.speed)},fps={request.fps},"
        f"tpad={':'.join(pad)}"
    )
    if frame is None or (frame.is_identity and frame.fit == "cover"):
        return (
            f"{source},scale={width}:{height}:force_original_aspect_ratio=increase"
            f":flags={job.scale_flags}:out_color_matrix=bt709:out_range=tv,"
            f"crop={width}:{height},format=yuv420p,{COLOUR_TAGS},setsar=1[v]"
        )
    x_expr, y_expr = frame_place_exprs(frame, request.size)
    parts = [
        f"{source}[src]",
        *frame_fit_parts("[src]", "[pic]", frame, request.size, scale_flags=job.scale_flags),
        f"color=c=black:s={width}x{height}:r={request.fps}[bg]",
        f"[bg][pic]overlay=x='{x_expr}':y='{y_expr}':format=rgb,{TO_YUV}[v]",
    ]
    return ";".join(parts)


def render_video_clip(job: SceneClipJob, out_path: Path, *, ffmpeg: str, stop: Stop,
                      on_frames: Callable[[int], None]) -> None:
    """Draw a clip scene: one FFmpeg run that decodes the clip from its offset (``-ss`` at
    the input), runs :func:`video_clip_graph` and writes exactly the planned frames,
    encoded and keyframed like every other scene clip (no sound: the clip's own sound is
    mixed in the final pass)."""
    request = job.request
    clip = request.clip
    file = request.clip_file
    assert clip is not None and file is not None
    offset, _hold = clip_decode_start(clip, request.lead_in, request.fps)
    part = part_path(out_path)
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats",
        "-progress", "pipe:1", "-stats_period", "0.5", "-y",
        "-ss", fmt(offset), "-i", str(file),
        "-filter_complex", video_clip_graph(job),
        "-map", "[v]", "-frames:v", str(request.frames),
        *job.encoding.args(), *keyframe_args(job.keyframes),
        str(part),
    ]
    child = _Watched(command, stdout=subprocess.PIPE, stop=stop)
    assert child.process.stdout is not None
    try:
        _progress_frames(child.process.stdout, on_frames)
    finally:
        code = child.wait()
    if child.killed.is_set():
        _discard(part)
        raise RenderCancelled("The render was stopped.")
    if code != 0 or not mp4_is_whole(part):
        _discard(part)
        if child.out_of_memory():
            raise child.failure(f"FFmpeg ran out of memory drawing scene {job.number}")
        detail = child.tail(1) or (f"exit code {code}" if code else "it gave no frames")
        raise RenderError(
            f"The clip {file.name} for scene {job.number} could not be read: {detail}"
        )
    finish_clip(part, out_path)


def render_with_provider(job: SceneClipJob, out_path: Path, *, ffmpeg: str, stop: Stop,
                         on_frames: Callable[[int], None]) -> None:
    provider = job.provider
    assert provider is not None
    request = job.request
    writer = FrameWriter(out_path, request.size, request.fps, job.encoding, ffmpeg=ffmpeg,
                         keyframes=job.keyframes, stop=stop)
    last: np.ndarray | None = None
    try:
        for frame in provider.frames(request):
            if stop():
                raise RenderCancelled("The render was stopped.")
            if writer.count >= request.frames:
                break
            writer.write(frame)
            last = frame
            on_frames(writer.count)
        if writer.count < request.frames:
            if last is None:
                raise RenderError(f"Scene {job.number}: the {job.renderer} renderer drew no "
                                  "frames.")
            log.warning("Scene %d: %s drew %d of %d frames; the last one is held.",
                        job.number, job.renderer, writer.count, request.frames)
            while writer.count < request.frames:
                writer.write(last)
            on_frames(writer.count)
        writer.close()
    except BaseException:
        writer.abort()
        raise


def render_scene_clip(job: SceneClipJob, clips_dir: Path, *, ffmpeg: str, stop: Stop,
                      on_frames: Callable[[int], None]) -> ClipOutcome:
    """Draw one scene clip into ``clips_dir`` (the caller has checked it is not cached)."""
    started = time.monotonic()
    if job.request.clip is not None:
        render_video_clip(job, clips_dir / job.file_name, ffmpeg=ffmpeg, stop=stop,
                          on_frames=on_frames)
    elif job.provider is not None:
        try:
            render_with_provider(job, clips_dir / job.file_name, ffmpeg=ffmpeg, stop=stop,
                                 on_frames=on_frames)
        except (RenderCancelled, RenderError):
            raise
        except Exception as exc:  # noqa: BLE001 - a provider bug (or a numpy allocation)
            # must not lose the render: zoompan needs no Python memory.
            log.warning("Scene %d: %s failed (%s); drawing it with zoompan instead.",
                        job.number, job.renderer, exc)
            job.fall_back_to_zoompan(f"{type(exc).__name__}: {exc}")
            target = clips_dir / job.file_name
            if mp4_is_whole(target):  # drawn by zoompan when the provider failed before
                on_frames(job.frames)
            else:
                render_zoompan(job, target, ffmpeg=ffmpeg, stop=stop, on_frames=on_frames)
    else:
        render_zoompan(job, clips_dir / job.file_name, ffmpeg=ffmpeg, stop=stop,
                       on_frames=on_frames)
    job.seconds = round(time.monotonic() - started, 2)
    return ClipOutcome(job.frames, job.seconds, job.renderer,
                       [job.fallback] if job.fallback else [])
