"""Background music from the channel's music folder, and the music stem the video and the
live preview both play.

The pick is deterministic per project (a hash of the project id chooses the track), so a redo
keeps the same music unless the reviewer picks another. A track counts as licensed when a
``<track>.license.txt`` sidecar sits next to it or the folder holds a ``LICENSE*`` file; the
``edit.music_license`` gate warns otherwise.

**The music stem** (docs/M5-CONTRACT.md section 3.1, design 5.4). One ducking function for the
renderer and the preview: :func:`build_ducked_stem` writes ``07_edit/music_ducked.wav`` (48 kHz
stereo, 16-bit), the music exactly as it sounds in the video: the track from ``music.start_s``,
looped, at ``music.gain_db``, faded in and out, and dipped under the voice. The dip comes from
the voice track *as it sits on the timeline* (the whole voice file from ``voice.start_s``, or
the sentence clips of ``voice.segments``, see :func:`voice_placements`):

* the voice's RMS over every 10 ms window (``step_s``); a window whose RMS is above the
  ``ducking.threshold`` of ``config/render.yaml`` (0.03 of full scale, about -30 dBFS) has
  voice;
* the music's gain is ``duck_db`` while the voice is there and 0 dB otherwise, with straight
  ramps in decibels: it starts going down ``duck_attack_s`` *before* the voice starts (so the
  music is fully down when the first word comes; the stem is made offline, so it can look
  ahead) and comes back up over ``duck_release_s`` after the voice stops
  (:func:`duck_envelope`). A pause shorter than the two ramps keeps the music partly down;
* ``07_edit/music_envelope.json`` holds those gains, ``{step_s: 0.01, gain_db: [..]}`` (value
  ``i`` is the gain at ``i * step_s`` seconds; between two values the gain is interpolated in
  decibels, after the last one it holds). The stem applies exactly that, sample by sample;
  the preview draws it on the music lane and can apply it to the raw track while a new stem
  is made.

The renderer (``render/ffmpeg.py``) mixes the stem under the voice and normalises the mix
(``loudnorm``); it no longer runs ``sidechaincompress``. A stem is rebuilt only when
something it is made of changed (:func:`stem_key`: the music block, the track file, the voice
file and its placement, the length, the threshold), see :func:`ensure_ducked_stem`.

**Loudness of the preview.** :func:`ensure_preview_gain` measures the integrated loudness (EBU
R128) of the voice track plus the stem through FFmpeg, the same mix the renderer normalises,
and returns ``preview_gain_db = target - measured`` so the preview plays as loud as the
finished file. The same run puts the mix through the renderer's ``loudnorm`` and keeps the gain
it gives every 100 ms (one-pass ``loudnorm`` follows the loudness of the first 3 s at the
start and the loudness so far later on, up to about 1 dB away from one constant gain): the
preview follows that curve when it has it. Both are cached in ``07_edit/preview/audio.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..models.timeline import MusicTrackInfo, Timeline

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aif", ".aiff"}
)
LICENCE_PREFIXES = ("license", "licence")


def list_tracks(folder: Path | str | None) -> list[Path]:
    """Audio files directly inside the folder, sorted by name; empty when it is unusable."""
    if not folder:
        return []
    root = Path(folder)
    if not root.is_dir():
        return []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    return [p for p in entries if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]


def folder_has_licence(folder: Path) -> bool:
    try:
        return any(
            p.is_file() and p.name.lower().startswith(LICENCE_PREFIXES) for p in folder.iterdir()
        )
    except OSError:
        return False


def has_licence(track: Path | str) -> bool:
    """``<track>.license.txt`` (or ``.licence.txt``, with or without the audio extension) next
    to the file, or a ``LICENSE*`` file anywhere in the folder."""
    path = Path(track)
    folder = path.parent
    for base in (path.name, path.stem):
        for word in LICENCE_PREFIXES:
            if (folder / f"{base}.{word}.txt").is_file():
                return True
    return folder_has_licence(folder)


def pick_track(folder: Path | str | None, project_id: str) -> Path | None:
    """One track from the folder, the same one every time for this project."""
    tracks = list_tracks(folder)
    if not tracks:
        return None
    digest = hashlib.sha256((project_id or "").encode("utf-8")).hexdigest()
    return tracks[int(digest[:8], 16) % len(tracks)]


def track_infos(folder: Path | str | None) -> list[MusicTrackInfo]:
    return [
        MusicTrackInfo(path=str(track), name=track.stem, license_ok=has_licence(track))
        for track in list_tracks(folder)
    ]


def resolve_track(folder: Path | str | None, chosen: str) -> Path | None:
    """The track the reviewer named: an absolute path, or a name inside the music folder."""
    text = (chosen or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_file():
        return candidate
    if folder:
        for track in list_tracks(folder):
            if track.name == text or track.stem == text or track.name == candidate.name:
                return track
    return None


# The voice as it sits on the timeline -----------------------------------------------------------

AUDIO_RATE = 48000
"""Every sample count below is at 48 kHz (the renderer's audio rate)."""
ENVELOPE_STEP_S = 0.01
EDIT_DIR = "07_edit"
STEM_FILE = "music_ducked.wav"
ENVELOPE_FILE = "music_envelope.json"
PREVIEW_DIR = "preview"
PREVIEW_AUDIO_FILE = "audio.json"
STEM_VERSION = "stem.1"
"""Part of every stem's key: change it when the stem is made differently."""
GAIN_VERSION = "gain.2"
"""Part of the preview loudness's key (``gain.2``: with loudnorm's gain curve)."""
DEFAULT_VOICE_THRESHOLD = 0.03
"""Voice RMS (share of full scale) above which the music dips (``ducking.threshold``)."""
DECODE_TIMEOUT_S = 900
SILENCE_LUFS = -70.0
MAX_PREVIEW_GAIN_DB = 30.0


@dataclass(frozen=True)
class Placement:
    """Samples ``src_start`` .. ``src_end`` of the voice file play from sample ``at`` of the
    timeline (all at :data:`AUDIO_RATE`)."""

    src_start: int
    src_end: int
    at: int

    @property
    def length(self) -> int:
        return self.src_end - self.src_start


def timeline_frames(timeline: Timeline) -> int:
    """Whole frames of the scenes (what ``render/assembly.py`` lays out): the last scene's end
    minus the first scene's start, each rounded to a frame."""
    if not timeline.scenes:
        return 0
    fps = int(timeline.fps)
    first = round(timeline.scenes[0].start_s * fps)
    return max(0, round(timeline.scenes[-1].end_s * fps) - first)


def timeline_samples(timeline: Timeline, rate: int = AUDIO_RATE) -> int:
    """Audio samples of the scenes part: :func:`timeline_frames` at ``rate``."""
    return int(round(timeline_frames(timeline) * rate / max(int(timeline.fps), 1)))


def voice_placements(
    timeline: Timeline, file_samples: int | None, total: int, rate: int = AUDIO_RATE
) -> list[Placement]:
    """Where the voice file's samples land on the timeline, in timeline order, never
    overlapping and never past ``total``.

    * ``voice.segments`` null: the whole file from ``voice.start_s`` (delayed in whole
      milliseconds, as FFmpeg's ``adelay`` does);
    * otherwise each segment's ``src_start_s`` .. ``src_end_s`` at ``at_s`` (rounded to the
      nearest sample), sorted by ``at_s``. A segment that starts before the one in front of
      it has finished loses its overlapping start; a segment past the end of the file is cut
      at the end of the file. Gaps between segments are silence.

    ``file_samples`` is the voice file's length at ``rate`` (``None`` when unknown: no cut at
    the file's end)."""
    segments = timeline.voice.segments
    if segments is None:
        delay = int(round(timeline.voice.start_s * 1000)) * rate // 1000
        length = int(file_samples) if file_samples is not None else total
        end = min(length, max(total - delay, 0))
        return [Placement(0, end, delay)] if end > 0 else []
    ordered = sorted(segments, key=lambda seg: (seg.at_s, seg.src_start_s))
    placements: list[Placement] = []
    cursor = 0
    for segment in ordered:
        start = int(round(segment.src_start_s * rate))
        end = int(round(segment.src_end_s * rate))
        if file_samples is not None:
            end = min(end, int(file_samples))
        at = int(round(segment.at_s * rate))
        if at < cursor:
            start += cursor - at
            at = cursor
        if at >= total:
            break
        end = min(end, start + (total - at))
        if end <= start:
            continue
        placements.append(Placement(start, end, at))
        cursor = at + (end - start)
    return placements


# Reading audio ---------------------------------------------------------------------------------


def _ffmpeg(ffmpeg: str | None) -> str:
    from .ffmpeg import find_ffmpeg

    return ffmpeg or find_ffmpeg()


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def read_pcm(
    path: Path | str,
    *,
    rate: int = AUDIO_RATE,
    channels: int = 1,
    ffmpeg: str | None = None,
) -> np.ndarray:
    """The file's samples as 16-bit integers, shape ``(frames, channels)``, at ``rate``. A
    16-bit PCM WAV of that rate and channel count is read directly; anything else is
    decoded (and resampled, remixed) by FFmpeg."""
    from .ffmpeg import RenderError

    file = Path(path)
    if not file.is_file():
        raise RenderError(f"The audio file {file} does not exist.")
    try:
        with wave.open(str(file), "rb") as handle:
            if (
                handle.getsampwidth() == 2
                and handle.getframerate() == rate
                and handle.getnchannels() == channels
                and handle.getcomptype() == "NONE"
            ):
                data = handle.readframes(handle.getnframes())
                usable = len(data) - len(data) % (2 * channels)
                return np.frombuffer(data[:usable], dtype="<i2").reshape(-1, channels)
    except (wave.Error, EOFError, OSError, ValueError):
        pass
    command = [
        _ffmpeg(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(file),
        "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(channels), "-ar", str(rate),
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=DECODE_TIMEOUT_S, creationflags=_no_window(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"{file.name} could not be read: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise RenderError(
            f"{file.name} could not be read: {detail[-1] if detail else 'unknown error'}"
        )
    data = completed.stdout
    usable = len(data) - len(data) % (2 * channels)
    return np.frombuffer(data[:usable], dtype="<i2").reshape(-1, channels)


def resolve_audio(folder: Path | str | None, value: str | None) -> Path | None:
    """``value`` (relative to the project folder, or absolute) as a path; ``None`` for none."""
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute() and folder is not None:
        path = Path(folder) / path
    return path


def compose_voice(
    timeline: Timeline,
    voice_file: Path | str | None,
    total: int,
    *,
    ffmpeg: str | None = None,
) -> np.ndarray:
    """The voice track of the timeline, mono 16-bit at :data:`AUDIO_RATE`, ``total`` samples
    long (silence where no voice plays, and everywhere when the file is missing)."""
    track = np.zeros(max(int(total), 0), dtype=np.int16)
    if voice_file is None or not Path(voice_file).is_file() or total <= 0:
        return track
    source = read_pcm(voice_file, channels=1, ffmpeg=ffmpeg)[:, 0]
    for place in voice_placements(timeline, len(source), total):
        track[place.at:place.at + place.length] = source[place.src_start:place.src_end]
    return track


def window_rms(samples: np.ndarray, window: int) -> np.ndarray:
    """The RMS (share of full scale) of every ``window`` samples of 16-bit ``samples``; the
    last window is padded with silence."""
    window = max(int(window), 1)
    count = math.ceil(len(samples) / window) if len(samples) else 0
    rms = np.zeros(count, dtype=np.float64)
    block = window * 4096
    for first in range(0, count * window, block):
        chunk = samples[first:first + block].astype(np.float64) / 32768.0
        windows = math.ceil(len(chunk) / window)
        padded = np.zeros(windows * window, dtype=np.float64)
        padded[:len(chunk)] = chunk
        rms[first // window:first // window + windows] = np.sqrt(
            np.mean(padded.reshape(windows, window) ** 2, axis=1)
        )
    return rms


# The dip under the voice -----------------------------------------------------------------------


def duck_envelope(
    rms: Sequence[float] | np.ndarray,
    *,
    threshold: float = DEFAULT_VOICE_THRESHOLD,
    duck_db: float = -12.0,
    attack_s: float = 0.3,
    release_s: float = 0.8,
    step_s: float = ENVELOPE_STEP_S,
) -> np.ndarray:
    """The music's gain in dB for every ``step_s`` window of voice RMS ``rms``.

    ``depth = max(0, -duck_db)``; a window with voice (RMS above ``threshold``) wants
    ``depth``, one without wants 0. The gain is ``-max(forward, backward)`` with

    * ``forward[i] = max(want[i], forward[i-1] - depth * step_s / release_s)`` (back up over
      ``release_s`` after the voice), and
    * ``backward[i] = max(want[i], backward[i+1] - depth * step_s / attack_s)`` (down over
      ``attack_s`` before the voice: a look-ahead).

    A zero attack or release is a jump. Values are rounded to 0.0001 dB."""
    levels = np.asarray(rms, dtype=np.float64)
    count = int(levels.size)
    if count == 0:
        return np.zeros(0, dtype=np.float64)
    depth = max(0.0, -float(duck_db))
    want = np.where(levels > float(threshold), depth, 0.0)
    index = np.arange(count, dtype=np.float64)
    if release_s > 0 and depth > 0:
        rate = depth * step_s / float(release_s)
        forward = np.maximum.accumulate(want + rate * index) - rate * index
    else:
        forward = want.copy()
    if attack_s > 0 and depth > 0:
        rate = depth * step_s / float(attack_s)
        backward = np.maximum.accumulate((want - rate * index)[::-1])[::-1] + rate * index
    else:
        backward = want.copy()
    gain = -np.maximum(np.maximum(forward, backward), 0.0)
    gain = np.round(gain, 4)
    gain[gain == 0.0] = 0.0  # no "-0.0" in the file
    return gain


def envelope_gain_at(gain_db: Sequence[float] | np.ndarray, times_s: np.ndarray,
                     step_s: float = ENVELOPE_STEP_S) -> np.ndarray:
    """The envelope read at ``times_s``: value ``i`` sits at ``i * step_s``; linear in dB in
    between; the first value before 0, the last value after the end."""
    values = np.asarray(gain_db, dtype=np.float64)
    if values.size == 0:
        return np.zeros_like(np.asarray(times_s, dtype=np.float64))
    positions = np.asarray(times_s, dtype=np.float64) / float(step_s)
    return np.interp(positions, np.arange(values.size, dtype=np.float64), values)


# The stem --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StemInfo:
    stem: Path
    envelope: Path
    key: str
    built: bool
    """True when it was made now, False when the one on disk was still right."""
    seconds: float = 0.0


def stem_paths(folder: Path | str) -> tuple[Path, Path]:
    """``07_edit/music_ducked.wav`` and ``07_edit/music_envelope.json`` of a project."""
    edit = Path(folder) / EDIT_DIR
    return edit / STEM_FILE, edit / ENVELOPE_FILE


def relative_stem_paths() -> tuple[str, str]:
    """The same two paths relative to the project folder (what ``timeline.json`` holds)."""
    return f"{EDIT_DIR}/{STEM_FILE}", f"{EDIT_DIR}/{ENVELOPE_FILE}"


def music_file(timeline: Timeline, folder: Path | str | None) -> Path | None:
    """The timeline's music track when it has one that exists on this PC."""
    path = resolve_audio(folder, timeline.music.path)
    return path if path is not None and path.is_file() else None


def _fingerprint(path: Path | None) -> list[Any]:
    if path is None:
        return ["", -1, -1]
    try:
        stat = path.stat()
        return [str(path), int(stat.st_size), int(stat.st_mtime_ns)]
    except OSError:
        return [str(path), -1, -1]


def voice_description(timeline: Timeline, folder: Path | str | None) -> dict[str, Any]:
    """The voice file and where it plays (part of the stem and loudness keys)."""
    voice = timeline.voice
    return {
        "file": _fingerprint(resolve_audio(folder, voice.path)),
        "start_s": voice.start_s,
        "segments": None if voice.segments is None else [
            [s.sentence_id, s.src_start_s, s.src_end_s, s.at_s] for s in voice.segments
        ],
    }


def _threshold(config: Any) -> float:
    try:
        value = float(getattr(config, "duck_threshold", DEFAULT_VOICE_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_VOICE_THRESHOLD
    return value if math.isfinite(value) and value >= 0 else DEFAULT_VOICE_THRESHOLD


def stem_key(timeline: Timeline, folder: Path | str | None, config: Any = None) -> str:
    """A short hash of everything the stem is made of."""
    music = timeline.music
    payload = {
        "version": STEM_VERSION,
        "music": [music.path, music.start_s, music.gain_db, music.duck_db, music.duck_attack_s,
                  music.duck_release_s, music.fade_in_s, music.fade_out_s],
        "track": _fingerprint(music_file(timeline, folder)),
        "voice": voice_description(timeline, folder),
        "samples": timeline_samples(timeline),
        "rate": AUDIO_RATE,
        "threshold": _threshold(config),
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


_KEY_RE = re.compile(r'"key"\s*:\s*"([0-9a-f]+)"')


def envelope_key(envelope: Path) -> str | None:
    """The key a stem was made with (read from the top of its envelope file)."""
    try:
        with envelope.open("r", encoding="utf-8") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    match = _KEY_RE.search(head)
    return match.group(1) if match else None


def stem_is_current(timeline: Timeline, folder: Path | str, config: Any = None) -> str | None:
    """The stem key when ``07_edit/music_ducked.wav`` is there and made for this timeline's
    music and voice; ``None`` otherwise (no music, never made, or out of date)."""
    if music_file(timeline, folder) is None:
        return None
    stem, envelope = stem_paths(folder)
    if not stem.is_file():
        return None
    key = stem_key(timeline, folder, config)
    return key if envelope_key(envelope) == key else None


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        name = str(path).lower()
        lock = _LOCKS.get(name)
        if lock is None:
            lock = _LOCKS[name] = threading.Lock()
        return lock


def _music_filter(timeline: Timeline, total: int) -> str:
    """The track from ``start_s``, looped by the input, at ``gain_db``, faded in and out,
    exactly ``total`` samples (the chain the renderer used to run before the dip)."""
    from .ffmpeg import fmt

    music = timeline.music
    seconds = total / AUDIO_RATE
    start = int(round(music.start_s * AUDIO_RATE))
    chain = (
        f"[0:a]aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo,"
        f"atrim=start_sample={start}:end_sample={start + total},asetpts=PTS-STARTPTS,"
        f"volume={fmt(music.gain_db)}dB"
    )
    if music.fade_in_s > 0:
        chain += f",afade=t=in:st=0:d={fmt(min(music.fade_in_s, seconds))}"
    if 0 < music.fade_out_s < seconds:
        chain += f",afade=t=out:st={fmt(seconds - music.fade_out_s)}:d={fmt(music.fade_out_s)}"
    chain += f",apad=whole_len={total},atrim=end_sample={total}[out]"
    return chain


def _replace(part: Path, final: Path, attempts: int = 15) -> None:
    """``os.replace`` that waits a little while another program (the preview playing the old
    stem, a virus scanner) still holds the file."""
    from .ffmpeg import RenderError

    for attempt in range(attempts):
        try:
            os.replace(part, final)
            return
        except PermissionError as exc:
            if attempt == attempts - 1:
                try:
                    part.unlink()
                except OSError:
                    pass
                raise RenderError(
                    f"{final.name} could not be saved: another program is using it ({exc})."
                ) from exc
            time.sleep(0.2)


def build_ducked_stem(
    timeline: Timeline,
    voice_wav: Path | str | None,
    out_wav: Path | str,
    envelope_json: Path | str,
    *,
    folder: Path | str | None = None,
    config: Any = None,
    ffmpeg: str | None = None,
    cancel: threading.Event | None = None,
    key: str | None = None,
) -> StemInfo:
    """Write the music stem and its envelope (see the module docstring). ``voice_wav`` is the
    voice file the timeline's ``voice`` block plays (``None`` or missing: no dip);
    ``folder`` resolves a relative music path. Raises ``RenderError`` in plain English."""
    from ..storage.settings_store import atomic_write_text
    from .ffmpeg import RenderCancelled, RenderError

    started = time.monotonic()
    out_wav, envelope_json = Path(out_wav), Path(envelope_json)
    track = music_file(timeline, folder)
    if track is None:
        raise RenderError(
            "The music track is missing"
            + (f": {timeline.music.path}" if timeline.music.path else ".")
        )
    total = timeline_samples(timeline)
    if total <= 0:
        raise RenderError("The timeline is shorter than one frame, so there is no music to make.")
    window = int(round(ENVELOPE_STEP_S * AUDIO_RATE))
    voice = compose_voice(timeline, voice_wav, total, ffmpeg=ffmpeg)
    rms = window_rms(voice, window)
    del voice
    music = timeline.music
    threshold = _threshold(config)
    gains = duck_envelope(
        rms, threshold=threshold, duck_db=music.duck_db, attack_s=music.duck_attack_s,
        release_s=music.duck_release_s,
    )
    key = key or stem_key(timeline, folder, config)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    try:
        envelope_json.unlink()  # a stem being remade is never taken for the old one
    except OSError:
        pass
    part = out_wav.with_name(out_wav.stem + ".part.wav")
    command = [
        _ffmpeg(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-stream_loop", "-1", "-i", str(track), "-filter_complex", _music_filter(timeline, total),
        "-map", "[out]", "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2",
        "-ar", str(AUDIO_RATE), "pipe:1",
    ]
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            creationflags=_no_window(),
        )
    except OSError as exc:
        raise RenderError(f"FFmpeg could not be started to prepare the music: {exc}") from exc
    errors: list[bytes] = []

    def read_errors() -> None:
        if process.stderr is not None:
            try:
                errors.append(process.stderr.read())
            except (OSError, ValueError):
                pass

    pump = threading.Thread(target=read_errors, name="stem-stderr", daemon=True)
    pump.start()
    written = 0
    stream = process.stdout
    assert stream is not None
    try:
        with wave.open(str(part), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(AUDIO_RATE)
            while written < total:
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled("Preparing the music was stopped.")
                want = min(AUDIO_RATE, total - written) * 8  # a second of stereo float32
                data = bytearray()
                while len(data) < want:
                    piece = stream.read(want - len(data))
                    if not piece:
                        break
                    data.extend(piece)
                if len(data) < 8:
                    break
                usable = len(data) - len(data) % 8
                samples = np.frombuffer(bytes(data[:usable]), dtype="<f4").reshape(-1, 2)
                index = np.arange(written, written + samples.shape[0], dtype=np.float64)
                gain = np.power(10.0, envelope_gain_at(gains, index / AUDIO_RATE) / 20.0)
                mixed = samples.astype(np.float64) * gain[:, None]
                pcm = np.clip(np.round(mixed * 32767.0), -32768, 32767).astype("<i2")
                out.writeframes(pcm.tobytes())
                written += samples.shape[0]
            if written < total:  # the decoder stopped early: the rest is silence
                out.writeframes(bytes((total - written) * 4))
    except BaseException:
        process.kill()
        try:
            part.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            stream.close()
        except OSError:
            pass
        code = process.wait()
        pump.join(timeout=5.0)
    if code != 0 and written == 0:
        try:
            part.unlink()
        except OSError:
            pass
        detail = b"".join(errors).decode("utf-8", errors="replace").strip().splitlines()
        raise RenderError(
            f"The music track {track.name} could not be read: "
            f"{detail[-1] if detail else f'exit code {code}'}"
        )
    _replace(part, out_wav)
    document = {
        "step_s": ENVELOPE_STEP_S,
        "key": key,
        "version": STEM_VERSION,
        "sample_rate": AUDIO_RATE,
        "length_s": round(total / AUDIO_RATE, 6),
        "duck_db": music.duck_db,
        "attack_s": music.duck_attack_s,
        "release_s": music.duck_release_s,
        "threshold": threshold,
        "lookahead": True,
        "gain_db": [float(v) for v in gains],
    }
    atomic_write_text(envelope_json, json.dumps(document, separators=(",", ":")) + "\n")
    seconds = round(time.monotonic() - started, 2)
    log.info("Music stem made in %.2f s (%d windows, key %s)", seconds, len(gains), key)
    return StemInfo(out_wav, envelope_json, key, built=True, seconds=seconds)


def ensure_ducked_stem(
    timeline: Timeline,
    folder: Path | str,
    *,
    config: Any = None,
    ffmpeg: str | None = None,
    cancel: threading.Event | None = None,
    force: bool = False,
) -> StemInfo | None:
    """The project's stem for this timeline: the one on disk while it is current, else made
    now (one at a time per project). ``None`` when the timeline has no music (or its track is
    missing on this PC)."""
    if music_file(timeline, folder) is None:
        return None
    stem, envelope = stem_paths(folder)
    voice = resolve_audio(folder, timeline.voice.path)
    with _lock_for(stem):
        key = stem_key(timeline, folder, config)
        if not force and stem.is_file() and envelope_key(envelope) == key:
            return StemInfo(stem, envelope, key, built=False)
        return build_ducked_stem(
            timeline, voice, stem, envelope, folder=folder, config=config, ffmpeg=ffmpeg,
            cancel=cancel, key=key,
        )


# Loudness of the preview ------------------------------------------------------------------------


def preview_gain_from(integrated_lufs: float | None, target_lufs: float) -> float | None:
    """Decibels that bring a mix measured at ``integrated_lufs`` to ``target_lufs`` (what
    ``loudnorm`` does to the finished file); ``None`` for silence, at most +/-30 dB."""
    if integrated_lufs is None or not math.isfinite(integrated_lufs):
        return None
    if integrated_lufs <= SILENCE_LUFS + 0.05:
        return None
    gain = float(target_lufs) - float(integrated_lufs)
    return round(min(max(gain, -MAX_PREVIEW_GAIN_DB), MAX_PREVIEW_GAIN_DB), 2)


def preview_audio_path(folder: Path | str) -> Path:
    return Path(folder) / EDIT_DIR / PREVIEW_DIR / PREVIEW_AUDIO_FILE


def gain_key(timeline: Timeline, folder: Path | str, config: Any, stem: str | None) -> str:
    """A short hash of what the preview's loudness depends on: the voice and where it plays,
    the stem (its key), the extra sounds of v0.2 (clip sound, sound tracks: each file, and
    where and how it plays; left out when there are none, so older keys stay the same) and
    the loudness target."""
    from .ffmpeg import extra_sounds

    payload = {
        "version": GAIN_VERSION,
        "voice": voice_description(timeline, folder),
        "stem": stem,
        "samples": timeline_samples(timeline),
        "target": float(getattr(config, "loudnorm_i", -16.0)),
        "true_peak": float(getattr(config, "loudnorm_tp", -1.5)),
        "range": float(getattr(config, "loudnorm_lra", 11.0)),
    }
    extras = extra_sounds(timeline, folder, config) if folder is not None else []
    if extras:
        payload["extras"] = [
            [_fingerprint(sound.file), *sound.key_values()] for sound in extras
        ]
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def read_preview_audio(folder: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(preview_audio_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class PreviewLoudness:
    """How loud the preview plays: ``gain_db`` (``target - measured``; ``None`` for a silent
    mix) and ``curve_db``, the gain ``loudnorm`` gives the mix every ``curve_step_s`` seconds
    (step ``i`` covers ``i * step .. (i + 1) * step``; ``None`` when it was not measured)."""

    gain_db: float | None
    curve_db: list[float] | None = None
    curve_step_s: float = 0.1


def _loudness_from(data: dict[str, Any]) -> PreviewLoudness:
    value = data.get("preview_gain_db")
    gain = float(value) if isinstance(value, int | float) else None
    raw = data.get("curve_gain_db")
    step = data.get("curve_step_s")
    curve = None
    if isinstance(raw, list) and raw and all(isinstance(item, int | float) for item in raw):
        curve = [float(item) for item in raw]
    return PreviewLoudness(
        gain_db=gain,
        curve_db=curve,
        curve_step_s=float(step) if isinstance(step, int | float) and step > 0 else 0.1,
    )


def cached_preview_loudness(
    timeline: Timeline, folder: Path | str, config: Any
) -> PreviewLoudness | None:
    """The measured loudness for this timeline's current sound, or ``None`` when it has not
    been measured (nothing is measured here)."""
    stem: str | None = None
    if music_file(timeline, folder) is not None:
        stem = stem_is_current(timeline, folder, config)
        if stem is None:
            return None
    key = gain_key(timeline, folder, config, stem)
    data = read_preview_audio(folder)
    if data.get("key") != key:
        return None
    return _loudness_from(data)


def cached_preview_gain(
    timeline: Timeline, folder: Path | str, config: Any
) -> tuple[bool, float | None]:
    """``(known, gain)``: the measured gain for this timeline's current sound when it has been
    measured (``known``), without measuring anything."""
    loudness = cached_preview_loudness(timeline, folder, config)
    return (False, None) if loudness is None else (True, loudness.gain_db)


def ensure_preview_gain(
    timeline: Timeline,
    folder: Path | str,
    *,
    config: Any,
    stem: StemInfo | None = None,
    ffmpeg: str | None = None,
    cancel: threading.Event | None = None,
) -> float | None:
    """``preview_gain_db`` for this timeline (see the module docstring); measured once per
    voice, stem and loudness settings with ``loudnorm``'s gain curve, then read from
    ``07_edit/preview/audio.json``."""
    from ..storage.settings_store import atomic_write_text
    from .ffmpeg import LOUDNORM_STEP_S, measure_mix

    key = gain_key(timeline, folder, config, stem.key if stem is not None else None)
    data = read_preview_audio(folder)
    if data.get("key") == key and "preview_gain_db" in data:
        return _loudness_from(data).gain_db
    measured = measure_mix(
        timeline, Path(folder), config, stem=stem.stem if stem is not None else None,
        ffmpeg=ffmpeg, cancel=cancel, gain_curve=True,
    )
    lufs = measured.integrated_lufs
    gain = preview_gain_from(lufs, float(getattr(config, "loudnorm_i", -16.0)))
    curve = measured.gain_db if gain is not None else None
    path = preview_audio_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"key": key, "integrated_lufs": lufs, "preview_gain_db": gain}
    if curve:
        record["curve_step_s"] = LOUDNORM_STEP_S
        record["curve_gain_db"] = curve
    atomic_write_text(path, json.dumps(record, separators=(",", ":")) + "\n")
    return gain


# Waveform peaks ---------------------------------------------------------------------------------


def audio_peaks(
    path: Path | str, *, step_s: float = ENVELOPE_STEP_S, ffmpeg: str | None = None
) -> tuple[list[float], list[float], float]:
    """``(min, max, duration_s)``: the lowest and highest sample (-1 .. 1, over all channels)
    of every ``step_s`` of the file, rounded to 4 decimals."""
    file = Path(path)
    rate, channels = AUDIO_RATE, 1
    try:
        with wave.open(str(file), "rb") as handle:
            if handle.getsampwidth() == 2 and handle.getcomptype() == "NONE":
                rate, channels = handle.getframerate(), handle.getnchannels()
    except (wave.Error, EOFError, OSError, ValueError):
        pass
    samples = read_pcm(file, rate=rate, channels=channels, ffmpeg=ffmpeg)
    window = max(int(round(step_s * rate)), 1)
    frames = int(samples.shape[0])
    lows: list[float] = []
    highs: list[float] = []
    block = window * 4096
    for first in range(0, frames, block):
        chunk = samples[first:first + block]
        whole = chunk.shape[0] // window
        if whole:
            shaped = chunk[:whole * window].reshape(whole, window * channels)
            lows.extend(np.round(shaped.min(axis=1) / 32768.0, 4).tolist())
            highs.extend(np.round(shaped.max(axis=1) / 32768.0, 4).tolist())
        rest = chunk[whole * window:]
        if rest.size:  # the last, shorter window
            lows.append(round(float(rest.min()) / 32768.0, 4))
            highs.append(round(float(rest.max()) / 32768.0, 4))
    return lows, highs, round(frames / rate, 6) if rate else 0.0
