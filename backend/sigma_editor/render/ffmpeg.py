"""The FFmpeg side of the edit stage: render plans, child processes with progress and a cancel
flag, probing.

Since Transitions v2 (docs/TRANSITIONS-V2-CONTRACT.md section 3) a render has three phases,
run by ``render/assembly.py`` behind :func:`run_render`:

1. every scene becomes a cached clip (``render/scene_clips.py``: ``zoompan`` on the picture
   with the eased move of ``render/motion.py``, or frames drawn in Python);
2. every cut becomes a cached transition clip drawn by the transition engine
   (``render/transitions/``) from the overlap frames of its two scene clips;
3. one FFmpeg pass joins ``[clip][cut][clip]...`` with the concat demuxer, drops the overlap
   frames with ``select``, lays the popups over with ``overlay`` (alpha fade, optional
   slide), burns the captions with ``subtitles`` (libass, the vendored Noto fonts), mixes
   the voice track and the music stem (``amix``), normalises the loudness (``loudnorm``)
   and encodes the preset.

The audio (M5.1, docs/M5-CONTRACT.md section 3): the voice track is the whole voice file from
``voice.start_s``, or, when ``voice.segments`` is set, the sentence clips cut from it with
``atrim`` (in samples) and joined with ``concat`` at their ``at_s``, with silence in the gaps
(:func:`audio_graph`; the same placement as ``render/music.py``'s
:func:`~.music.voice_placements`, so the stem's dip and the preview hear the same voice).
The music is ``07_edit/music_ducked.wav`` (``render/music.py``): already offset, looped,
faded and dipped under the voice, so the graph only mixes it; the stem is made (or found
current) just before the assembly pass. ``sidechaincompress`` is no longer used.

:func:`build_plan` is pure: it works out the frame layout and writes the assembly graph,
which goes to FFmpeg as a file (``-/filter_complex <file>``, FFmpeg 7+) so an 80-scene
video never hits the Windows command-line limit. Progress comes from ``-progress pipe:1``;
setting the cancel flag kills the running child process within a quarter of a second and
keeps every finished clip.
"""

from __future__ import annotations

import errno
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..models.timeline import Timeline, TimelinePopup
from .fonts import fonts_dir
from .presets import RenderConfig, RenderPreset

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float], None]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROBE_TIMEOUT_S = 60
PEAK_TIMEOUT_S = 600
REQUIRED_FILTERS: dict[str, str] = {
    "subtitles": "libass (burnt-in captions)",
    "zoompan": "camera moves",
    "select": "joining scene and transition clips",
    "setparams": "colour tags of the clips",
    "amix": "mixing the voice and the music",
    "loudnorm": "loudness normalisation",
    "overlay": "popups",
}
LOUDNESS_TIMEOUT_S = 900
LOUDNESS_SCRIPT = "loudness_{}.txt"
"""The graph of one loudness measurement (a name of its own per run, removed afterwards)."""
LEVELS_FILE = "levels_{}_{}.txt"
"""The levels of the mix before and after ``loudnorm`` in one measurement (removed afterwards)."""
LOUDNORM_STEP_S = 0.1
"""The step of the measured ``loudnorm`` gain: the filter works in 100 ms frames."""
RMS_KEY = "lavfi.astats.Overall.RMS_level"


class RenderError(Exception):
    """FFmpeg could not be run or failed; the message is plain English."""


class RenderCancelled(RenderError):
    """The cancel flag was set while FFmpeg ran; the output file is incomplete."""


class RenderOutOfMemory(RenderError):
    """FFmpeg or the transition engine could not get the memory it needed (the PC is short of
    memory: other programs, or a very large frame). Finished clips are kept; the person sees
    :func:`out_of_memory_message` and FFmpeg's own lines go to render.log."""


_MEMORY_SIGNS = re.compile(
    r"malloc of size \d+ failed|out of memory|not enough memory"
    r"|paging file is too small|insufficient system resources|winerror 1455|bad_alloc",
    re.IGNORECASE,
)
_ENOMEM_SIGN = re.compile(r"cannot allocate memory", re.IGNORECASE)
"""FFmpeg's text for ``ENOMEM``: a real shortage, unless the filter graph could not be set
up (see :data:`_GRAPH_SETUP_FAILED`)."""
_GRAPH_SETUP_FAILED = re.compile(
    r"error while parsing expression|error initializing filters?\b|error parsing (?:a )?filter",
    re.IGNORECASE,
)
"""FFmpeg also ends with "Cannot allocate memory" when its expression parser refuses an
expression (FFmpeg 9 does for a sum of 100 terms or more): that is a broken graph, not a PC
short of memory."""
_MEMORY_WINERRORS = frozenset({8, 14, 1450, 1455})
"""ERROR_NOT_ENOUGH_MEMORY, ERROR_OUTOFMEMORY, ERROR_NO_SYSTEM_RESOURCES and
ERROR_COMMITMENT_LIMIT ("The paging file is too small for this operation to complete")."""


def looks_out_of_memory(text: str | None) -> bool:
    """Whether FFmpeg's (or Windows') messages say an allocation failed: x264's ``malloc of
    size N failed``, FFmpeg's ``Cannot allocate memory``, Windows' ``The paging file is too
    small`` and the like. Such a failure says nothing about the clips or the pictures.

    FFmpeg's bare "Cannot allocate memory" after "Error while parsing expression" or "Error
    initializing filters" is a filter graph FFmpeg refused, not a memory shortage."""
    if not text:
        return False
    message = str(text)
    if _MEMORY_SIGNS.search(message) is not None:
        return True
    return (
        _ENOMEM_SIGN.search(message) is not None
        and _GRAPH_SETUP_FAILED.search(message) is None
    )


def oserror_is_memory(exc: BaseException) -> bool:
    """Whether starting a program failed because Windows had no memory to give it."""
    if isinstance(exc, MemoryError):
        return True
    if not isinstance(exc, OSError):
        return False
    if getattr(exc, "winerror", None) in _MEMORY_WINERRORS or exc.errno == errno.ENOMEM:
        return True
    return looks_out_of_memory(str(exc))


def out_of_memory_message(name: str) -> str:
    """What the person reads when a render ran out of memory."""
    return (
        f"The computer ran out of memory while rendering {name}. Close some programs and "
        "try again."
    )


# Tools -----------------------------------------------------------------------------------------


def _which(configured: str, name: str) -> str | None:
    for candidate in (configured, name):
        if candidate:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def find_ffmpeg(settings: Any = None) -> str:
    configured = str(getattr(settings, "ffmpeg_path", "") or os.environ.get("FFMPEG_PATH", ""))
    found = _which(configured, "ffmpeg")
    if not found:
        raise RenderError(
            "FFmpeg was not found on this PC. Install it (winget install Gyan.FFmpeg) or set "
            "FFMPEG_PATH in Settings, then run the health checks."
        )
    return found


def find_ffprobe(settings: Any = None) -> str:
    configured = str(getattr(settings, "ffprobe_path", "") or os.environ.get("FFPROBE_PATH", ""))
    found = _which(configured, "ffprobe")
    if not found:
        ffmpeg = _which(
            str(getattr(settings, "ffmpeg_path", "") or os.environ.get("FFMPEG_PATH", "")),
            "ffmpeg",
        )
        if ffmpeg:
            sibling = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
            if sibling.is_file():
                return str(sibling)
        raise RenderError(
            "FFprobe was not found on this PC. It normally comes with FFmpeg; set FFPROBE_PATH "
            "in Settings if it lives somewhere else."
        )
    return found


def ffmpeg_filters(ffmpeg: str | None = None) -> set[str]:
    """Names of the filters this FFmpeg build offers (``ffmpeg -filters``)."""
    completed = subprocess.run(
        [ffmpeg or find_ffmpeg(), "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=PROBE_TIMEOUT_S,
        creationflags=NO_WINDOW,
        check=False,
    )
    names: set[str] = set()
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return names


def missing_filters(ffmpeg: str | None = None) -> list[str]:
    present = ffmpeg_filters(ffmpeg)
    return [name for name in REQUIRED_FILTERS if name not in present]


# Probing ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolRun:
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False


def run_tool(
    command: list[str],
    *,
    timeout_s: float,
    cancel: threading.Event | None = None,
) -> ToolRun:
    """Run ffmpeg/ffprobe for a short job and capture its output. The child is killed as
    soon as ``cancel`` is set (the project was archived or the app closed), so a probe or a
    loudness scan never keeps a file in the project folder open after a cancel."""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
    except OSError as exc:
        if oserror_is_memory(exc):
            raise RenderOutOfMemory(
                f"The computer is too short of memory to start {Path(command[0]).name}. Close "
                "some programs and try again."
            ) from exc
        raise RenderError(f"{Path(command[0]).name} could not be started: {exc}") from exc
    killed = threading.Event()

    def watchdog() -> None:
        while process.poll() is None:
            if cancel is not None and cancel.wait(0.25):
                killed.set()
                process.kill()
                return
            if cancel is None:
                time.sleep(0.25)

    watcher = threading.Thread(target=watchdog, name="tool-cancel", daemon=True)
    watcher.start()
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise RenderError(
            f"{Path(command[0]).name} took longer than {timeout_s:.0f} seconds and was stopped."
        ) from None
    finally:
        watcher.join(timeout=1.0)
    return ToolRun(
        returncode=int(process.returncode or 0), stdout=stdout or "", stderr=stderr or "",
        cancelled=killed.is_set(),
    )


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int = 0
    height: int = 0
    has_video: bool = False
    has_audio: bool = False
    size_bytes: int = 0


def probe_media(
    path: Path | str, ffprobe: str | None = None, cancel: threading.Event | None = None
) -> MediaInfo:
    """Duration, size and stream kinds of a media file (``ffprobe`` JSON)."""
    file = Path(path)
    if not file.is_file():
        raise RenderError(f"The file {file} does not exist.")
    completed = run_tool(
        [
            ffprobe or find_ffprobe(),
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height",
            "-of", "json",
            str(file),
        ],
        timeout_s=PROBE_TIMEOUT_S,
        cancel=cancel,
    )
    if completed.cancelled:
        raise RenderCancelled(f"Reading {file.name} was stopped.")
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        raise RenderError(
            f"{file.name} could not be read by ffprobe: {detail[-1] if detail else 'unknown error'}"
        )
    try:
        data = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise RenderError(f"ffprobe gave no readable answer for {file.name}.") from exc
    streams = data.get("streams") or []
    width = height = 0
    has_video = has_audio = False
    for stream in streams:
        kind = stream.get("codec_type")
        if kind == "video" and not has_video:
            has_video = True
            width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        elif kind == "audio":
            has_audio = True
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return MediaInfo(
        duration_s=round(duration, 3),
        width=width,
        height=height,
        has_video=has_video,
        has_audio=has_audio,
        size_bytes=file.stat().st_size,
    )


def probe_duration(path: Path | str, ffprobe: str | None = None) -> float:
    return probe_media(path, ffprobe).duration_s


_PEAK_RE = re.compile(r"Peak:\s*(-?[0-9.]+|-inf)\s*dBFS", re.IGNORECASE)


def measure_true_peak(
    path: Path | str, ffmpeg: str | None = None, cancel: threading.Event | None = None
) -> float | None:
    """The true peak of a file's audio in dBTP (``ebur128=peak=true``); ``None`` for silence,
    when FFmpeg cannot measure it, or when the scan was cancelled."""
    try:
        completed = run_tool(
            [
                ffmpeg or find_ffmpeg(),
                "-hide_banner", "-nostats", "-nostdin",
                "-i", str(path),
                "-vn", "-af", "ebur128=peak=true", "-f", "null", "-",
            ],
            timeout_s=PEAK_TIMEOUT_S,
            cancel=cancel,
        )
    except RenderError:
        return None
    if completed.cancelled:
        return None
    text = completed.stderr or ""
    marker = text.rfind("True peak:")
    if marker < 0:
        return None
    match = _PEAK_RE.search(text, marker)
    if not match or match.group(1).lower() == "-inf":
        return None
    try:
        return round(float(match.group(1)), 2)
    except ValueError:
        return None


_INTEGRATED_RE = re.compile(r"I:\s*(-?[0-9.]+|-inf)\s*LUFS", re.IGNORECASE)


def parse_integrated_loudness(text: str) -> float | None:
    """The integrated loudness (LUFS) from the summary ``ebur128`` prints when it ends."""
    marker = (text or "").rfind("Integrated loudness:")
    if marker < 0:
        return None
    match = _INTEGRATED_RE.search(text, marker)
    if not match or match.group(1).lower() == "-inf":
        return None
    try:
        return round(float(match.group(1)), 2)
    except ValueError:
        return None


@dataclass(frozen=True)
class MixLoudness:
    """One measurement of the mix: its integrated loudness (LUFS; ``None`` when FFmpeg cannot
    say) and, when asked for, the gain ``loudnorm`` gives it in every 100 ms
    (:data:`LOUDNORM_STEP_S`, dB; ``None`` for a silent mix)."""

    integrated_lufs: float | None
    gain_db: list[float] | None = None


def loudnorm_filter(config: RenderConfig) -> str:
    """The ``loudnorm`` the assembly pass runs on the mix (one pass, dynamic)."""
    return (
        f"loudnorm=I={fmt(config.loudnorm_i)}:TP={fmt(config.loudnorm_tp)}"
        f":LRA={fmt(config.loudnorm_lra)}"
    )


def read_levels(path: Path) -> list[float | None]:
    """The RMS levels (dBFS) that ``ametadata=mode=print`` wrote, one per frame (``None`` for
    silence)."""
    levels: list[float | None] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return levels
    for line in text.splitlines():
        if not line.startswith(RMS_KEY + "="):
            continue
        try:
            value = float(line.split("=", 1)[1])
        except ValueError:
            value = float("nan")
        levels.append(value if math.isfinite(value) else None)
    return levels


def gain_curve_from(
    before: Sequence[float | None], after: Sequence[float | None], *, floor_db: float = -70.0
) -> list[float] | None:
    """The gain (dB) from the levels of the mix ``before`` and ``after`` ``loudnorm``, step by
    step; a silent step takes the gain of the step before it (at the start, the first
    audible step's); ``None`` when every step is silent."""
    steps = min(len(before), len(after))
    gains: list[float | None] = []
    for level_in, level_out in zip(before[:steps], after[:steps], strict=True):
        if level_in is None or level_out is None or level_in <= floor_db:
            gains.append(None)
        else:
            gains.append(round(level_out - level_in, 3))
    known = [gain for gain in gains if gain is not None]
    if not known:
        return None
    last = known[0]
    curve: list[float] = []
    for gain in gains:
        if gain is not None:
            last = gain
        curve.append(last)
    return curve


def measure_mix_loudness(
    timeline: Timeline,
    folder: Path,
    config: RenderConfig | Any,
    *,
    stem: Path | None = None,
    ffmpeg: str | None = None,
    cancel: threading.Event | None = None,
) -> float | None:
    """The integrated loudness (EBU R128, LUFS) of the voice track plus the music ``stem``
    exactly as the assembly pass mixes them before ``loudnorm``; ``None`` when FFmpeg cannot
    say (silence reads as -70)."""
    return measure_mix(timeline, folder, config, stem=stem, ffmpeg=ffmpeg,
                       cancel=cancel).integrated_lufs


def measure_mix(
    timeline: Timeline,
    folder: Path,
    config: RenderConfig | Any,
    *,
    stem: Path | None = None,
    ffmpeg: str | None = None,
    cancel: threading.Event | None = None,
    gain_curve: bool = False,
) -> MixLoudness:
    """The integrated loudness of the voice track plus the music ``stem`` exactly as the
    assembly pass mixes them before ``loudnorm`` and, with ``gain_curve``, what ``loudnorm``
    does to that mix every 100 ms: the mix also goes through the assembly pass's
    ``loudnorm`` and the levels of the two sides are compared.

    One-pass ``loudnorm`` does not keep one gain: over the first 3 s it follows the loudness
    of the first 3 s, later the loudness measured so far, so one constant gain is up to
    about 1 dB off at the start of a video and in short ones. The curve costs about a
    twentieth of the video's length on a laptop (``loudnorm`` works at 192 kHz). The graph
    goes through a script file next to the preview caches (``07_edit/preview/``)."""
    from .music import EDIT_DIR, PREVIEW_DIR, timeline_frames

    config = config if isinstance(config, RenderConfig) else RenderConfig()
    graph = audio_graph(timeline, Path(folder), config, frames=timeline_frames(timeline),
                        stem=stem)
    work = Path(folder) / EDIT_DIR / PREVIEW_DIR
    work.mkdir(parents=True, exist_ok=True)
    tag = os.urandom(4).hex()
    script = work / LOUDNESS_SCRIPT.format(tag)
    parts = list(graph.parts)
    outputs = ["[out]"]
    levels: list[Path] = []
    if gain_curve:
        rate = config.audio_sample_rate
        aformat = f"aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts=stereo"
        levels = [work / LEVELS_FILE.format(tag, "in"), work / LEVELS_FILE.format(tag, "out")]

        def level_chain(path: Path) -> str:
            return (
                f"asetnsamples=n={round(rate * LOUDNORM_STEP_S)}:p=0,"
                "astats=metadata=1:reset=1,"
                f"ametadata=mode=print:key={RMS_KEY}:file={escape_filter_path(path)}"
            )

        parts.append(f"{graph.label}asplit=3[measure][level_in][normalise]")
        parts.append("[measure]ebur128=framelog=quiet[out]")
        parts.append(f"[level_in]{level_chain(levels[0])}[levels_in]")
        parts.append(
            f"[normalise]{loudnorm_filter(config)},{aformat},{level_chain(levels[1])}"
            "[levels_out]"
        )
        outputs += ["[levels_in]", "[levels_out]"]
    else:
        parts.append(f"{graph.label}ebur128=framelog=quiet[out]")
    script.write_text(";\n".join(parts) + "\n", encoding="utf-8")
    command = [ffmpeg or find_ffmpeg(), "-hide_banner", "-nostdin", "-nostats"]
    for item in graph.inputs:
        command.extend(item)
    command += ["-/filter_complex", str(script)]
    for label in outputs:
        command += ["-map", label, "-f", "null", "-"]
    try:
        completed = run_tool(command, timeout_s=LOUDNESS_TIMEOUT_S, cancel=cancel)
        curve = gain_curve_from(*(read_levels(path) for path in levels)) if levels else None
    finally:
        for path in (script, *levels):
            try:
                path.unlink()
            except OSError:
                pass
    if completed.cancelled:
        raise RenderCancelled("Measuring the loudness was stopped.")
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        raise RenderError(
            "The loudness of the voice and the music could not be measured: "
            f"{detail[-1] if detail else f'exit code {completed.returncode}'}"
        )
    return MixLoudness(parse_integrated_loudness(completed.stderr or ""), curve)


# Filter graph ------------------------------------------------------------------------------------


def fmt(value: float) -> str:
    """A number for an FFmpeg expression: no exponent, no trailing zeros, no locale."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


def escape_filter_path(path: Path | str) -> str:
    """A file path as a quoted filter option value (what FFmpeg's option parser accepts on
    Windows): forward slashes, the drive colon, commas and brackets escaped for the option
    parser, and the whole value single-quoted for the graph parser.

    Nothing can be escaped inside a quoted graph token, so an apostrophe is written as
    ``'\\\\\\''``: the quote closes, ``\\\\`` and ``\\'`` give the option parser ``\\'`` (a
    literal apostrophe), and the quote opens again (``o'brien`` -> ``'o'\\\\\\''brien'``).
    """
    text = str(path).replace("\\", "/")
    for char in (":", ",", "[", "]", ";"):
        text = text.replace(char, "\\" + char)
    text = text.replace("'", "'\\\\\\''")
    return f"'{text}'"


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (OSError, UnidentifiedImageError) as exc:
        raise RenderError(f"The picture {path} could not be read: {exc}") from exc


def resolve_path(folder: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(folder) / path


@dataclass(frozen=True)
class PopupAsset:
    """A popup PNG rendered for one preset, with its place in the frame."""

    popup: TimelinePopup
    png: Path
    width: int
    height: int
    x: int
    y: int


@dataclass
class AudioPlan:
    """What the assembly pass needs besides its graph: the music stem it mixes, made (or
    found current) by ``render/music.py`` just before the pass runs."""

    timeline: Timeline
    folder: Path
    stem: Path | None = None


@dataclass
class AudioGraph:
    """The audio part of a graph: the inputs it adds (in order, from ``first_input``), its
    filter chains and the label of the mix (voice + music, before ``loudnorm``)."""

    inputs: list[list[str]] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    label: str = "[mix]"


@dataclass
class RenderPlan:
    preset: RenderPreset
    width: int
    height: int
    fps: int
    canvas: tuple[int, int]
    inputs: list[list[str]] = field(default_factory=list)
    filter_graph: str = ""
    total_s: float = 0.0
    """Length of the finished file (intro + scenes + outro)."""
    x264_preset: str = "medium"
    crf: int = 20
    config: RenderConfig = field(default_factory=RenderConfig)
    layout: Any = None
    """The scene and transition clips and the frame bookkeeping (``assembly.Layout``)."""
    audio: AudioPlan | None = None
    """The music stem to make before the assembly pass (``None``: no music)."""
    # 9Sigma Editor (docs/CONTRACT-v0.1.md section 5): the upload presets' ceilings.
    maxrate_kbps: int | None = None
    """``-maxrate`` / ``-bufsize`` (twice the maxrate) of an upload preset; ``None`` = none."""
    audio_bitrate_kbps: int | None = None
    """The AAC bitrate of an upload preset; ``None`` = the render config's."""

    def output_args(self) -> list[str]:
        config = self.config
        args = [
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", self.x264_preset,
            "-crf", str(self.crf),
            "-profile:v", config.x264_profile,
            "-pix_fmt", config.pix_fmt,
            "-r", str(self.fps),
        ]
        if self.maxrate_kbps:
            args += ["-maxrate", f"{int(self.maxrate_kbps)}k",
                     "-bufsize", f"{int(self.maxrate_kbps) * 2}k"]
        args += [
            "-c:a", config.audio_codec,
            "-b:a", f"{int(self.audio_bitrate_kbps or config.audio_bitrate_kbps)}k",
            "-ar", str(config.audio_sample_rate),
            "-ac", "2",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-color_range", "tv",
            "-movflags", config.movflags,
            "-t", fmt(self.total_s),
        ]
        return args


def _wav_samples(path: Path, rate: int) -> int | None:
    """The length of a PCM WAV in samples at ``rate`` (``None`` when it cannot be read)."""
    import wave

    try:
        with wave.open(str(path), "rb") as handle:
            frames, own = handle.getnframes(), handle.getframerate()
    except (wave.Error, EOFError, OSError, ValueError):
        return None
    if own <= 0:
        return None
    return frames if own == rate else int(frames * rate // own)


def _segment_voice_chain(
    timeline: Timeline, voice: Path, voice_index: int, frames: int, config: RenderConfig
) -> list[str]:
    """``[voice]`` from ``voice.segments``: each clip cut by ``atrim`` in samples, padded
    with the silence up to the next clip (``apad``), all joined by ``concat`` (the first clip
    delayed to its place by ``adelay`` in samples); then ``aformat`` and exactly the scenes'
    length. Sample-exact: the same placement as :func:`.music.voice_placements`."""
    from .music import AUDIO_RATE, voice_placements

    total = int(round(frames * AUDIO_RATE / max(int(timeline.fps), 1)))
    rate = config.audio_sample_rate
    aformat = f"aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts=stereo"
    out_len = int(round(frames * rate / max(int(timeline.fps), 1)))
    placements = voice_placements(timeline, _wav_samples(voice, AUDIO_RATE), total)
    if not placements:
        return [f"anullsrc=r={rate}:cl=stereo,atrim=end_sample={out_len}[voice]"]
    count = len(placements)
    labels = [f"[vsrc{i}]" for i in range(count)]
    parts = [
        f"[{voice_index}:a]aformat=sample_fmts=s16:sample_rates={AUDIO_RATE}"
        f":channel_layouts=mono,asplit={count}{''.join(labels)}"
    ]
    pieces: list[str] = []
    for i, place in enumerate(placements):
        following = placements[i + 1].at if i + 1 < count else total
        chain = (
            f"{labels[i]}atrim=start_sample={place.src_start}:end_sample={place.src_end},"
            "asetpts=PTS-STARTPTS"
        )
        length = following - place.at
        if i == 0 and place.at > 0:
            chain += f",adelay=delays={place.at}S:all=1"
            length = following
        parts.append(f"{chain},apad=whole_len={length}[vseg{i}]")
        pieces.append(f"[vseg{i}]")
    parts.append(
        f"{''.join(pieces)}concat=n={count}:v=0:a=1,{aformat},apad=whole_len={out_len},"
        f"atrim=end_sample={out_len}[voice]"
    )
    return parts


def audio_graph(
    timeline: Timeline,
    folder: Path,
    config: RenderConfig,
    *,
    frames: int,
    first_input: int = 0,
    stem: Path | None = None,
) -> AudioGraph:
    """The voice track (whole file or sentence clips) mixed with the music ``stem`` (when
    given), ``frames`` frames of the timeline long, ending in ``[mix]``. Raises
    :class:`RenderError` when the voice file is missing."""
    graph = AudioGraph()
    rate = config.audio_sample_rate
    aformat = f"aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts=stereo"
    scenes_s = frames / max(int(timeline.fps), 1)
    voice = resolve_path(folder, timeline.voice.path)
    if not voice.is_file():
        raise RenderError(f"The voice file is missing: {voice}")
    graph.inputs.append(["-i", str(voice)])
    voice_index = first_input
    if timeline.voice.segments is None:
        voice_chain = f"[{voice_index}:a]{aformat}"
        if timeline.voice.start_s > 0:
            voice_chain += f",adelay={int(round(timeline.voice.start_s * 1000))}:all=1"
        voice_chain += f",atrim=0:{fmt(scenes_s)},apad=whole_dur={fmt(scenes_s)}"
        if stem is None:
            graph.parts.append(f"{voice_chain}[mix]")
            return graph
        graph.parts.append(f"{voice_chain}[voice]")
    else:
        graph.parts.extend(_segment_voice_chain(timeline, voice, voice_index, frames, config))
        if stem is None:
            graph.parts.append("[voice]anull[mix]")
            return graph
    graph.inputs.append(["-i", str(stem)])
    stem_index = first_input + len(graph.inputs) - 1
    graph.parts.append(
        f"[{stem_index}:a]{aformat},atrim=0:{fmt(scenes_s)},asetpts=PTS-STARTPTS,"
        f"apad=whole_dur={fmt(scenes_s)}[music]"
    )
    graph.parts.append(
        "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
    )
    return graph


def _popup_exprs(
    asset: PopupAsset, scale: float, config: RenderConfig
) -> tuple[str, str]:
    """``x`` and ``y`` overlay expressions: slide_left eases in from the right over
    ``slide_s``; pop rises into place; fade stays put (the alpha fade is on the input)."""
    look = config.popups
    travel = max(1.0, look.slide_px * scale)
    start = fmt(asset.popup.start_s)
    ease = f"pow(max(0\\,1-(t-{start})/{fmt(max(look.slide_s, 0.01))})\\,2)"
    x, y = str(asset.x), str(asset.y)
    if asset.popup.anim == "slide_left":
        return f"{x}+{fmt(travel)}*{ease}", y
    if asset.popup.anim == "pop":
        return x, f"{y}+{fmt(travel / 2)}*{ease}"
    return x, y


def build_plan(
    timeline: Timeline,
    preset: RenderPreset,
    *,
    folder: Path,
    config: RenderConfig,
    popups: Sequence[PopupAsset] = (),
    ass_path: Path | None = None,
    captions: bool = True,
    x264_preset: str | None = None,
    fonts_folder: Path | None = None,
) -> RenderPlan:
    """The clips to make and the assembly graph for one preset. Pure: nothing is run here
    (the pictures' sizes and dates are read)."""
    from .assembly import plan_layout

    folder = Path(folder)
    width, height = preset.size(timeline.aspect)
    canvas_w, canvas_h = preset.canvas(timeline.aspect)
    fps = int(timeline.fps)
    plan = RenderPlan(
        preset=preset,
        width=width,
        height=height,
        fps=fps,
        canvas=(canvas_w, canvas_h),
        x264_preset=config.x264_preset_for(preset, x264_preset),
        crf=preset.crf,
        config=config,
    )
    if not timeline.scenes:
        raise RenderError("The timeline has no scenes.")
    layout = plan_layout(timeline, preset, folder=folder, config=config,
                         final_x264=plan.x264_preset)
    plan.layout = layout
    parts: list[str] = []
    # Input 0: the scene and transition clips joined by the concat demuxer (the list is
    # written next to the clips when the render runs; the path is relative to work_dir).
    plan.inputs.append(["-f", "concat", "-safe", "0", "-i", layout.concat_path])
    parts.append(f"[0:v]{layout.select_filter()},setsar=1[base]")
    current = "[base]"
    scenes_s = layout.total_frames / fps

    scale = min(width, height) / 1080.0
    for number, asset in enumerate(popups):
        duration = max(asset.popup.end_s - asset.popup.start_s, 0.2)
        fade = min(config.popups.fade_s, duration / 3)
        plan.inputs.append(
            ["-loop", "1", "-framerate", str(fps), "-t", fmt(duration), "-i", str(asset.png)]
        )
        input_index = len(plan.inputs) - 1
        x_expr, y_expr = _popup_exprs(asset, scale, config)
        parts.append(
            f"[{input_index}:v]format=rgba,fade=t=in:st=0:d={fmt(fade)}:alpha=1,"
            f"fade=t=out:st={fmt(duration - fade)}:d={fmt(fade)}:alpha=1,"
            f"setpts=PTS+{fmt(asset.popup.start_s)}/TB[p{number}]"
        )
        parts.append(
            f"{current}[p{number}]overlay=x='{x_expr}':y='{y_expr}':eof_action=pass"
            f":enable='between(t\\,{fmt(asset.popup.start_s)}\\,{fmt(asset.popup.end_s)})'"
            f"[vp{number}]"
        )
        current = f"[vp{number}]"

    if captions and ass_path is not None and timeline.captions.cues:
        parts.append(
            f"{current}subtitles=filename={escape_filter_path(ass_path)}"
            f":fontsdir={escape_filter_path(fonts_folder or fonts_dir())}[vcap]"
        )
        current = "[vcap]"
    main_video = current

    # Audio: the voice track (the whole file, or its sentence clips), the music stem (made
    # by render/music.py just before the pass: offset, looped, faded, dipped under the
    # voice) mixed under it, the loudness normalised.
    from .music import music_file, stem_paths

    rate = config.audio_sample_rate
    aformat = f"aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts=stereo"
    stem = stem_paths(folder)[0] if music_file(timeline, folder) is not None else None
    audio = audio_graph(timeline, folder, config, frames=layout.total_frames,
                        first_input=len(plan.inputs), stem=stem)
    plan.inputs.extend(audio.inputs)
    parts.extend(audio.parts)
    if stem is not None:
        plan.audio = AudioPlan(timeline=timeline, folder=folder, stem=stem)
    parts.append(f"[mix]{loudnorm_filter(config)},{aformat}[amain]")

    # Intro and outro clips around the scenes (optional).
    total = scenes_s
    sequence_v: list[str] = []
    sequence_a: list[str] = []
    for label, clip in (("intro", timeline.intro), ("outro", timeline.outro)):
        if not clip.path:
            continue
        file = resolve_path(folder, clip.path)
        if not file.is_file():
            raise RenderError(f"The {label} video is missing: {file}")
        info = probe_media(file)
        if not info.has_video or info.duration_s <= 0:
            raise RenderError(f"The {label} file {file.name} has no video.")
        plan.inputs.append(["-i", str(file)])
        clip_index = len(plan.inputs) - 1
        parts.append(
            f"[{clip_index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease"
            f":flags={config.scale_flags},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            f"setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,"
            f"trim=0:{fmt(info.duration_s)},setpts=PTS-STARTPTS[{label}_v]"
        )
        if info.has_audio:
            parts.append(
                f"[{clip_index}:a]{aformat},atrim=0:{fmt(info.duration_s)},asetpts=PTS-STARTPTS,"
                f"apad=whole_dur={fmt(info.duration_s)}[{label}_a]"
            )
        else:
            parts.append(
                f"anullsrc=r={rate}:cl=stereo,atrim=0:{fmt(info.duration_s)}[{label}_a]"
            )
        sequence_v.append(f"[{label}_v]")
        sequence_a.append(f"[{label}_a]")
        total += info.duration_s
        if label == "intro":
            sequence_v.append(main_video)
            sequence_a.append("[amain]")
    if sequence_v:
        if "[amain]" not in sequence_a:
            sequence_v.insert(0, main_video)
            sequence_a.insert(0, "[amain]")
        pairs = "".join(f"{v}{a}" for v, a in zip(sequence_v, sequence_a, strict=True))
        parts.append(f"{pairs}concat=n={len(sequence_v)}:v=1:a=1[vout][aout]")
    else:
        parts.append(f"{main_video}null[vout]")
        parts.append("[amain]anull[aout]")

    plan.filter_graph = ";\n".join(parts) + "\n"
    plan.total_s = round(total, 3)
    return plan


# Running ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderResult:
    path: Path
    duration_s: float
    size_bytes: int
    seconds_taken: float
    width: int = 0
    height: int = 0
    scene_clips_total: int = 0
    scene_clips_reused: int = 0
    cuts_total: int = 0
    cuts_reused: int = 0
    phase_seconds: dict[str, float] = field(default_factory=dict)
    parallax_fallbacks: int = 0
    """Scenes set to 2.5D parallax that were drawn with the Ken Burns move instead."""


def parse_progress_line(line: str) -> tuple[str, str] | None:
    key, sep, value = line.strip().partition("=")
    if not sep:
        return None
    return key.strip(), value.strip()


BENIGN_LOG_LINES: tuple[re.Pattern[str], ...] = (
    # libass tries every file in fontsdir as a font; the licence text is not one.
    re.compile(r"Error opening memory font '(OFL|LICEN[CS]E)[^']*'"),
    re.compile(r"Guessed Channel Layout"),
)


def is_benign_log_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in BENIGN_LOG_LINES)


def _pump_stderr(stream: Any, log_file: Any, recent: Any = None) -> None:
    """Copy FFmpeg's messages to the log, leaving out the lines that only confuse people;
    ``recent`` (a bounded deque) also keeps the last of them for the error message."""
    try:
        for line in stream:
            if not is_benign_log_line(line):
                log_file.write(line)
                if recent is not None:
                    recent.append(line)
    except (OSError, ValueError):
        pass


TAIL_LINE_CHARS = 300
"""A log line quoted in an error message is cut to this length (FFmpeg repeats a whole filter
expression when it cannot parse it)."""


def _tail(path: Path, lines: int = 12) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    rows = [row for row in text.splitlines() if row.strip()]
    return "\n".join(
        row if len(row) <= TAIL_LINE_CHARS else row[:TAIL_LINE_CHARS] + " ..."
        for row in rows[-lines:]
    )


def run_render(
    plan: RenderPlan,
    output: Path,
    *,
    work_dir: Path,
    log_path: Path,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    label: str | None = None,
) -> RenderResult:
    """Run one render to ``output``: scene clips, transition clips, then the assembly pass
    (``render/assembly.py``). Clips are cached under ``work_dir/clips/<preset>/``; FFmpeg's
    messages and the per-scene and per-cut timings go to ``log_path`` (appended), the
    assembly graph to ``filters_<preset>.txt`` in ``work_dir``. Progress is reported as
    ``Rendering <preset> N%`` over the three phases. Raises :class:`RenderCancelled` when
    the cancel flag is set (finished clips are kept), :class:`RenderError` when FFmpeg
    fails."""
    from .assembly import run_pipeline

    return run_pipeline(
        plan, output, work_dir=work_dir, log_path=log_path, progress=progress, cancel=cancel,
        ffmpeg=ffmpeg, ffprobe=ffprobe, label=label,
    )
