"""JSON fixtures for the live preview's maths, made by the Python code the renderer runs
(docs/M5-CONTRACT.md section 4; design 5.8 "unit level").

    .venv/Scripts/python.exe tools/make_preview_fixtures.py            # writes the files
    .venv/Scripts/python.exe tools/make_preview_fixtures.py --out DIR
    .venv/Scripts/python.exe tools/make_preview_fixtures.py --check    # 1 when out of date

The TypeScript engine (``frontend/src/preview/``) reads these and must agree to 1e-6. Every
number comes from the reference itself (never written out by hand here), so a change to the
renderer's maths shows up as a changed fixture. Files (default folder
``frontend/src/preview/fixtures/``), each with ``generator`` and ``version``:

* ``ease.json``: ``t`` (a grid from -0.25 to 1.25, clamping included) and the value of every
  camera curve (``render/motion.py``: ``ease_in_out_cubic``, ``ease_out_quart``,
  ``ease_in_out_sine``, ``snap``, ``linear``) under ``values``; the transition engine's own
  curves (``render/transitions/easing.py``) under ``transition``, its ``speed`` and the
  ``taper``.
* ``motion.json``: the handheld constants, the presets (fractions) and ``cases``: a scene's
  move in source-picture pixels (``preset``, ``ease`` override, ``image``, ``frame``,
  ``cover``, ``start_rect``, ``end_rect``, ``seed``, ``fps``, ``frames``) with the window of
  every frame of its clip (``t``, ``eased``, ``rects``; ``shake`` for handheld), as
  ``scene_path(...).frame_rect(k, frames, fps)`` gives it.
* ``bounds.json``: three mock timelines (``three_scenes``, ``twelve_scenes``, ``short``) and
  the storyboard-only ``estimated`` one: the inputs (storyboard scenes with their sentence
  ids, estimated lengths and transitions; the timing's sentences; ``tail_s``, ``fps``,
  ``max_share``, ``min_frames``) and the timeline builder's ``bounds`` (``scene_bounds``) and
  ``transitions_s`` (``clip_transitions``), then the renderer's frame ``layout``
  (``render/assembly.py`` ``frame_layout``: scene starts and lengths in frames, cut lengths,
  each clip's frames and overlaps, each cut's first frame, ``h`` and look).
* ``cuts.json``: for every transition id, its easing, whether it uses the seed, its
  parameters, the colour the video ends on when it is the last scene's, and an 18-frame cut
  (0.6 s at 30 fps) frame by frame (``k``, ``t_raw = (k + 0.5) / n``, ``t``); the legacy name
  table; the cut-length rules on sample scenes (``lengths``); the preview's continuous
  ``t_raw`` at sample times (``preview_time``).
* ``popups.json``: popup motion as the assembly pass draws it (``render/ffmpeg.py``):
  for each animation and scale, every frame ``n`` around the popup: ``t = n / fps``,
  ``visible`` (``between(t, start, end)`` and a popup frame exists), the popup frame ``k``
  FFmpeg shows (``n - trunc(start * fps)``), its alpha (fade in and out over
  ``min(0.3, length / 3)``, from ``k / fps``), and ``x``, ``y`` (``travel = max(1, 120 x
  scale)``, ``ease = max(0, 1 - (t - start) / 0.35)^2``).
* ``ducking.json``: the music's dip under the voice (``render/music.py``): a voice RMS per
  10 ms window, the settings, ``gain_db`` (``duck_envelope``), and the envelope read back at
  sample times (``read``: dB and linear gain), plus edge cases.
* ``captions.json``: the caption look presets and sample cues: the cue cut into pieces
  (``tokens``), the words with their times (``words``, ``cue_word_times``) and the ASS events
  of every preset (``events``: start, end, active word, text).

The same folder also gets the preview engine's wider fixtures, ``engine_*.json`` (whole frame
layouts, camera paths, every transition's per-frame terms, noise, popup positions, voice
placement, depth layers) and ``animatic.json`` (the storyboard animatic's timeline), made by
``tools/preview_engine_fixtures.py``; ``--check`` covers them too.

It needs no network and no FFmpeg; it writes only the fixture files.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "backend") not in sys.path:  # runs without `pip install -e .` too
    sys.path.insert(0, str(REPO / "backend"))
if str(Path(__file__).resolve().parent) not in sys.path:  # preview_engine_fixtures
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from preview_engine_fixtures import build as build_engine_fixtures  # noqa: E402
from sigma_editor.models.timeline import (  # noqa: E402
    LEGACY_TRANSITIONS,
    MOTION_EASES,
    TRANSITION_IDS,
    CaptionCue,
    Timeline,
    TimelineMotion,
)
from sigma_editor.render import motion  # noqa: E402
from sigma_editor.render import transitions as tr  # noqa: E402
from sigma_editor.render.assembly import cut_frames, cut_limit, frame_layout  # noqa: E402
from sigma_editor.render.captions import (  # noqa: E402
    caption_events,
    caption_style_for,
    chunk_cues,
    cue_tokens,
    cue_word_times,
    presets_json,
)
from sigma_editor.render.geometry import cover_region, rect_in_pixels  # noqa: E402
from sigma_editor.render.music import (  # noqa: E402
    DEFAULT_VOICE_THRESHOLD,
    ENVELOPE_STEP_S,
    duck_envelope,
    envelope_gain_at,
)
from sigma_editor.render.presets import RenderConfig, load_render_config  # noqa: E402
from sigma_editor.render.timeline_builder import (  # noqa: E402
    Timing,
    clip_transitions,
    parse_timing,
    scene_bounds,
)
from sigma_editor.render.transitions import easing as tr_easing  # noqa: E402
from sigma_editor.render.transitions.common import taper  # noqa: E402

VERSION = 1
GENERATOR = "tools/make_preview_fixtures.py"
DEFAULT_OUT = REPO / "frontend" / "src" / "preview" / "fixtures"
FILES = ("ease", "motion", "bounds", "cuts", "popups", "ducking", "captions")


# Output ------------------------------------------------------------------------------------------


def plain(value: Any) -> Any:
    """Plain JSON values: tuples to lists, numpy numbers to Python ones; no NaN."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return [plain(v) for v in value.tolist()]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating | float):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("a fixture value is not a finite number")
        return 0.0 if number == 0 else number
    return value


def dumps(value: Any, indent: int = 0) -> str:
    """JSON with objects and lists of objects indented and lists of plain values on one line
    (exact float round trip: ``repr``)."""
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        rows = [f"{pad}  {json.dumps(k)}: {dumps(v, indent + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(rows) + "\n" + pad + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(not isinstance(v, dict | list) for v in value):
            return "[" + ", ".join(json.dumps(v) for v in value) + "]"
        rows = [f"{pad}  {dumps(v, indent + 1)}" for v in value]
        return "[\n" + ",\n".join(rows) + "\n" + pad + "]"
    return json.dumps(value)


def document(name: str, body: dict[str, Any]) -> str:
    return dumps(plain({"generator": GENERATOR, "version": VERSION, "fixture": name, **body}))
    # (the trailing newline is added by the writer)


# ease.json ---------------------------------------------------------------------------------------


def ease_fixture() -> dict[str, Any]:
    grid = [-0.25, -0.01] + [k / 200 for k in range(201)] + [1.01, 1.25]
    transition_names = sorted(tr_easing.EASINGS)
    return {
        "t": grid,
        "values": {name: [motion.ease(name, t) for t in grid] for name in MOTION_EASES},
        "transition": {
            "values": {name: [tr_easing.ease(name, t) for t in grid]
                       for name in transition_names},
            "speed": {name: [tr_easing.speed(name, t) for t in grid[2:-2]]
                      for name in tr_easing.CATALOGUE_EASINGS},
            "speed_t": grid[2:-2],
            "taper": [taper(t) for t in grid[2:-2]],
        },
        "notes": "values[name][i] = ease(name, t[i]) with t clamped to [0, 1]; speed is the "
                 "central difference with h = 0.001 (one-sided at the ends); taper(t) = "
                 "smoothstep(0, 0.08, t) * smoothstep(0, 0.08, 1 - t)",
    }


# motion.json -------------------------------------------------------------------------------------


def _motion_case(
    name: str,
    preset: str,
    *,
    image: tuple[int, int],
    frame: tuple[int, int],
    frames: int,
    ease_name: str | None = None,
    seed: int = 0,
    fps: int = 30,
    rects: tuple[Sequence[float], Sequence[float]] | None = None,
) -> dict[str, Any]:
    if rects is None:
        fractions = motion.preset_rects(preset)
        assert fractions is not None, preset
        start = rect_in_pixels(fractions[0], image[0], image[1], frame[0], frame[1])
        end = rect_in_pixels(fractions[1], image[0], image[1], frame[0], frame[1])
    else:
        start, end = list(rects[0]), list(rects[1])
    scene = TimelineMotion(preset=preset, start_rect=start, end_rect=end, ease=ease_name)
    path = motion.scene_path(scene, image, frame, seed=seed)
    times = [motion.frame_progress(k, frames) for k in range(frames)]
    case: dict[str, Any] = {
        "name": name,
        "preset": preset,
        "ease": ease_name,
        "easing": path.easing,
        "handheld": path.handheld,
        "image": list(image),
        "frame": list(frame),
        "cover": list(cover_region(image[0], image[1], frame[0], frame[1])),
        "start_rect": list(scene.start_rect),
        "end_rect": list(scene.end_rect),
        "seed": seed,
        "fps": fps,
        "frames": frames,
        "t": times,
        "eased": [path.eased(t) for t in times],
        "rects": [list(path.frame_rect(k, frames, fps)) for k in range(frames)],
    }
    if path.handheld:
        case["shake"] = [list(motion.shake_offsets(k / fps, seed)) for k in range(frames)]
    return case


def motion_fixture() -> dict[str, Any]:
    config = motion.load_motion_config()
    landscape, portrait = (1920, 1080), (1080, 1920)
    cases = [
        _motion_case("zoom_in", "zoom_in", image=(1024, 576), frame=landscape, frames=46),
        _motion_case("pan_left_linear", "pan_left", image=(1024, 576), frame=landscape,
                     frames=61, ease_name="linear"),
        _motion_case("drift_tall_picture", "drift", image=(576, 1024), frame=landscape,
                     frames=90),
        _motion_case("handheld", "handheld", image=(2048, 1152), frame=landscape, frames=75,
                     seed=5),
        _motion_case("dolly_in_snap_portrait", "dolly_in", image=(1024, 1024), frame=portrait,
                     frames=50, ease_name="snap"),
        _motion_case("tilt_up_quart", "tilt_up", image=(1536, 864), frame=landscape,
                     frames=33, ease_name="ease_out_quart"),
        _motion_case("pan_right_sine", "pan_right", image=(1024, 576), frame=landscape,
                     frames=40, ease_name="ease_in_out_sine"),
        _motion_case("single_frame", "zoom_out", image=(1024, 576), frame=landscape, frames=1),
        _motion_case("hold", "hold", image=(1024, 576), frame=landscape, frames=12),
        _motion_case("clamped_by_the_cover_region", "slow_push", image=(1024, 576),
                     frame=landscape, frames=20,
                     rects=((-60.0, -30.0, 800.0, 450.0), (300.0, 200.0, 760.0, 427.5))),
        _motion_case("handheld_at_the_edge", "handheld", image=(1024, 576), frame=landscape,
                     frames=45, seed=2, rects=((0.0, 0.0, 1024.0, 576.0),
                                               (0.0, 0.0, 1024.0, 576.0))),
    ]
    return {
        "handheld": {
            "amplitude": config.handheld_amplitude,
            "weights": list(motion._SHAKE_WEIGHTS),
            "hz_x": list(motion._SHAKE_HZ_X),
            "hz_y": list(motion._SHAKE_HZ_Y),
            "golden": motion._GOLDEN,
        },
        "presets": motion.presets_json(),
        "cases": cases,
        "notes": "rects[k] = the window (x, y, w, h) in picture pixels of clip frame k: t = "
                 "k / (frames - 1) (0 for one frame), E = ease(t), centre linear in E, size "
                 "w0 * (w1 / w0)^E, handheld wobble amplitude * w * shake(k / fps, seed), then "
                 "kept inside the cover region",
    }


# bounds.json -------------------------------------------------------------------------------------


def _timing(rows: Sequence[tuple[str, float, float]], duration_s: float) -> Timing:
    return parse_timing({
        "duration_s": duration_s,
        "sentences": [{"id": sid, "text": sid, "start_s": a, "end_s": b} for sid, a, b in rows],
    })


class _Scene:
    """What ``scene_bounds`` reads of a storyboard scene."""

    def __init__(self, sentence_ids: list[str], est_duration_s: float) -> None:
        self.sentence_ids = sentence_ids
        self.est_duration_s = est_duration_s


def _bounds_case(
    name: str,
    *,
    aspect: str,
    sentences: Sequence[tuple[str, float, float]],
    duration_s: float,
    scenes: Sequence[tuple[list[str], float, str, float]],
    config: RenderConfig,
) -> dict[str, Any]:
    timing = _timing(sentences, duration_s)
    plans = [_Scene(ids, est) for ids, est, _t, _d in scenes]
    bounds = scene_bounds(plans, timing, config.tail_s)  # type: ignore[arg-type]
    fps = config.fps
    lengths = clip_transitions(
        bounds, [d for _ids, _est, _t, d in scenes], max_share=config.transition_max_share,
        min_s=config.transition_min_frames / max(fps, 1),
    )
    timeline = Timeline.model_validate({
        "project_id": name, "format": "shorts" if aspect == "9:16" else "long",
        "aspect": aspect, "width": 1080 if aspect == "9:16" else 1920,
        "height": 1920 if aspect == "9:16" else 1080, "fps": fps,
        "duration_s": bounds[-1][1],
        "scenes": [
            {"index": i, "image": f"06_images/scene_{i + 1:02d}.png", "start_s": a, "end_s": b,
             "motion": {"preset": "hold", "start_rect": [0, 0, 16, 9],
                        "end_rect": [0, 0, 16, 9]},
             "transition_out": {"type": kind, "duration_s": seconds}}
            for i, ((a, b), (_ids, _est, kind, _d), seconds) in enumerate(
                zip(bounds, scenes, lengths, strict=True))
        ],
    })
    layout = frame_layout(timeline, config)
    return {
        "name": name,
        "aspect": aspect,
        "fps": fps,
        "tail_s": config.tail_s,
        "max_share": config.transition_max_share,
        "min_frames": config.transition_min_frames,
        "timing": {"duration_s": duration_s,
                   "sentences": [{"id": s, "start_s": a, "end_s": b} for s, a, b in sentences]},
        "scenes": [{"sentence_ids": ids, "est_duration_s": est, "transition": kind,
                    "transition_s": seconds} for ids, est, kind, seconds in scenes],
        "bounds": [list(pair) for pair in bounds],
        "transitions_s": lengths,
        "timeline_transitions": [s.transition_out.type for s in timeline.scenes],
        "layout": {
            "total_frames": layout.total_frames,
            "starts": list(layout.starts),
            "lengths": list(layout.lengths),
            "kept": list(layout.kept),
            "cut_n": {str(k): v for k, v in sorted(layout.cut_n.items())},
            "fade_n": layout.fade_n,
            "clips": [
                {"index": c.index, "start": c.start, "length": c.length, "n_in": c.n_in,
                 "h_in": c.h_in, "n_out": c.n_out, "tail_out": c.tail_out, "tail": c.tail,
                 "frames": c.frames, "first_frame": c.first_frame}
                for c in layout.clips
            ],
            "cuts": [
                {"after": c.after, "before": c.before, "first_frame": c.first_frame,
                 "frames": c.frames, "h": c.h,
                 "transition": timeline.scenes[c.after].transition_out.type,
                 "fade_colour": (list(tr.fade_out_colour(
                     timeline.scenes[c.after].transition_out.type))
                     if c.before is None else None)}
                for c in layout.cuts
            ],
        },
    }


def bounds_fixture() -> dict[str, Any]:
    config = load_render_config()
    three = [("s-01", 0.0, 1.6), ("s-02", 1.7, 3.3), ("s-03", 3.4, 5.0)]
    twelve_sentences = [
        ("a1", 0.0, 2.1), ("a2", 2.35, 4.0), ("a3", 4.25, 7.9), ("a4", 8.2, 9.6),
        ("a5", 9.85, 12.4), ("a6", 12.6, 12.85), ("a7", 13.1, 16.0), ("a8", 16.3, 19.75),
        ("a9", 21.0, 23.2), ("a10", 23.45, 26.0), ("a11", 26.3, 28.0), ("a12", 28.2, 31.5),
        ("a13", 31.8, 35.25),
    ]
    twelve_scenes: list[tuple[list[str], float, str, float]] = [
        (["a1", "a2"], 4.0, "film_dissolve", 0.6),
        (["a3"], 3.6, "zoom_through", 0.8),
        (["a4"], 1.4, "whip_left", 0.35),
        (["a5"], 2.5, "push_up", 0.5),
        (["a6"], 0.3, "fade", 0.6),  # a very short scene: the cut is capped
        (["a7"], 2.9, "light_leak_warm", 1.2),
        (["missing"], 2.2, "glitch_subtle", 0.4),  # not in the timing: its estimate
        (["a8"], 3.5, "luma_wipe_diag", 0.05),  # too short a cut: a plain cut
        (["a9", "a10"], 5.0, "cube_left", 0.9),
        (["a11"], 1.7, "spin_zoom", 3.5),  # longer than allowed: capped
        (["a12"], 3.3, "crosswarp", 0.7),
        (["a13"], 3.4, "dip_white", 1.0),  # the end fades to white
    ]
    short_sentences = [("b1", 0.0, 1.1), ("b2", 1.2, 2.0), ("b3", 2.1, 3.6), ("b4", 3.7, 4.4),
                       ("b5", 4.5, 6.2)]
    short_scenes: list[tuple[list[str], float, str, float]] = [
        (["b1"], 1.1, "whip_right", 0.25), (["b2"], 0.8, "push_left", 0.3),
        (["b3"], 1.5, "zoom_through", 0.35), (["b4"], 0.7, "glitch_subtle", 0.2),
        (["b5"], 1.7, "fadeblack", 0.4),
    ]
    estimated: list[tuple[list[str], float, str, float]] = [
        (["x1"], 3.2, "film_dissolve", 0.6), (["x2", "x3"], 5.1, "push_left", 0.5),
        (["x4"], 0.1, "fade", 0.6), (["x5"], 4.4, "dip_black", 0.8),
    ]
    return {
        "cases": [
            _bounds_case("three_scenes", aspect="16:9", sentences=three, duration_s=5.0,
                         scenes=[(["s-01"], 1.6, "fade", 0.6), (["s-02"], 1.6, "wipeleft", 0.5),
                                 (["s-03"], 1.6, "fadeblack", 0.8)], config=config),
            _bounds_case("twelve_scenes", aspect="16:9", sentences=twelve_sentences,
                         duration_s=35.4, scenes=twelve_scenes, config=config),
            _bounds_case("short", aspect="9:16", sentences=short_sentences, duration_s=6.2,
                         scenes=short_scenes, config=config),
            _bounds_case("estimated", aspect="16:9", sentences=[], duration_s=0.0,
                         scenes=estimated, config=config),
        ],
        "notes": "bounds = scene_bounds (first sentence start to the next scene's start, the "
                 "first scene from 0, the last to the voice end + tail_s, at least 0.2 s); a "
                 "scene whose sentences are not in the timing keeps its estimated length; "
                 "transitions_s = clip_transitions (at most max_share of the shorter "
                 "neighbour, under min_frames / fps a plain cut); layout = the renderer's "
                 "frames (render/assembly.py frame_layout)",
    }


# cuts.json ---------------------------------------------------------------------------------------


def cuts_fixture() -> dict[str, Any]:
    config = load_render_config()
    fps, duration = config.fps, 0.6
    n = cut_frames(duration, fps, config.transition_min_frames, 10_000)
    rows = []
    for transition_id in TRANSITION_IDS:
        resolved = tr.resolve(transition_id)
        rows.append({
            "id": transition_id,
            "easing": resolved.easing,
            "seeded": bool(resolved.spec.seeded),
            "default_duration_s": resolved.spec.duration_s,
            "params": dict(resolved.params),
            "fade_colour": list(tr.fade_out_colour(transition_id)),
            "frames": [{"k": k, "t_raw": tr.frame_time(k, n),
                        "t": tr_easing.ease(resolved.easing, tr.frame_time(k, n))}
                       for k in range(n)],
        })
    try:
        from sigma_editor.config_files import transition_catalog

        catalogue_legacy = dict(transition_catalog().legacy)
    except Exception:  # noqa: BLE001 - the contract's own table still applies
        catalogue_legacy = {}
    legacy = {**catalogue_legacy, **LEGACY_TRANSITIONS}
    samples = [
        (0.6, 1.6, 48, 0.25), (0.6, 5.0, 150, 0.25), (3.5, 20.0, 600, 0.25), (0.05, 3.0, 90, 0.25),
        (0.4, 0.3, 9, 0.25), (1.0, 2.0, 3, 0.25), (0.25, 1.1, 33, 0.25), (0.8, 3.0, 90, 0.5),
    ]
    lengths = []
    for seconds, shorter_s, shorter_frames, share in samples:
        longest = cut_limit(shorter_s, shorter_frames, fps, share)
        lengths.append({
            "duration_s": seconds, "fps": fps, "shorter_s": shorter_s,
            "shorter_frames": shorter_frames, "max_share": share,
            "min_frames": config.transition_min_frames, "longest": longest,
            "n": cut_frames(seconds, fps, config.transition_min_frames, longest),
        })
    t0 = 10.0
    preview = []
    for offset in (-0.02, 0.0, 0.01, 1 / 60, 0.1, 0.25, 0.3, 0.5833, 0.6, 0.62):
        moment = t0 + offset
        t_raw = min(max((moment - t0 + 0.5 / fps) / duration, 0.0), 1.0)
        preview.append({"T0": t0, "T": moment, "D": duration, "fps": fps, "t_raw": t_raw})
    return {
        "fps": fps,
        "duration_s": duration,
        "n": n,
        "h": n // 2,
        "transitions": rows,
        "legacy": dict(sorted(legacy.items())),
        "lengths": lengths,
        "preview_time": preview,
        "notes": "a cut of n frames covers frames [C - h, C - h + n) with h = n // 2 around "
                 "the boundary C; frame k is drawn at t_raw = (k + 0.5) / n, t = ease(easing, "
                 "t_raw); n = round(duration * fps), 0 under min_frames, at most "
                 "floor(min(share * shorter_s, 3) * fps + 0.6) and half the shorter scene's "
                 "frames; preview t_raw = clamp((T - T0 + 0.5 / fps) / D)",
    }


# popups.json -------------------------------------------------------------------------------------


def popup_samples(
    *, anim: str, start: float, end: float, x0: int, y0: int, scale: float, fps: int,
    config: RenderConfig,
) -> list[dict[str, Any]]:
    """Every frame around the popup as the assembly pass draws it (see the module
    docstring): the same expressions as ``render/ffmpeg.py`` builds."""
    look = config.popups
    duration = max(end - start, 0.2)
    fade = min(look.fade_s, duration / 3)
    travel = max(1.0, look.slide_px * scale)
    slide = max(look.slide_s, 0.01)
    shift = int(start / (1.0 / fps))  # setpts=PTS+start/TB, truncated like FFmpeg's D2TS
    count = math.ceil(duration * fps - 1e-9)  # -t duration on a 1/fps input
    rows: list[dict[str, Any]] = []
    first = math.floor(start * fps) - 2
    last = math.ceil(end * fps) + 2
    for n in range(max(first, 0), last + 1):
        t = n / fps
        k = n - shift
        exists = 0 <= k < count
        visible = start <= t <= end and exists
        tau = k / fps if exists else None
        alpha = 0.0
        if tau is not None:
            fade_in = min(max(tau / fade, 0.0), 1.0) if fade > 0 else 1.0
            fade_out = 1.0 - (min(max((tau - (duration - fade)) / fade, 0.0), 1.0)
                              if fade > 0 else 0.0)
            alpha = fade_in * fade_out
        ease = max(0.0, 1.0 - (t - start) / slide) ** 2
        x, y = float(x0), float(y0)
        if anim == "slide_left":
            x = x0 + travel * ease
        elif anim == "pop":
            y = y0 + travel / 2 * ease
        rows.append({"n": n, "t": t, "visible": visible, "k": k if exists else None,
                     "alpha": alpha if visible else 0.0, "x": x, "y": y})
    return rows


def popups_fixture() -> dict[str, Any]:
    config = load_render_config()
    fps = config.fps
    cases = []
    for anim in ("slide_left", "fade", "pop"):
        for scale, (x0, y0) in ((1.0, (60, 820)), (0.5, (30, 410))):
            for start, end in ((2.0, 4.5), (2.033, 2.6)):
                cases.append({
                    "anim": anim, "scale": scale, "start_s": start, "end_s": end, "x0": x0,
                    "y0": y0, "fps": fps,
                    "duration_s": max(end - start, 0.2),
                    "fade_s": min(config.popups.fade_s, max(end - start, 0.2) / 3),
                    "travel": max(1.0, config.popups.slide_px * scale),
                    "slide_s": config.popups.slide_s,
                    "frames": popup_samples(anim=anim, start=start, end=end, x0=x0, y0=y0,
                                            scale=scale, fps=fps, config=config),
                })
    return {
        "look": {"fade_s": config.popups.fade_s, "slide_s": config.popups.slide_s,
                 "slide_px": config.popups.slide_px, "margin_px": config.popups.margin_px},
        "cases": cases,
        "notes": "scale = min(frame width, height) / 1080; frame n at t = n / fps shows popup "
                 "frame k = n - trunc(start / (1 / fps)) when 0 <= k < ceil(duration * fps) "
                 "and start <= t <= end; alpha = clamp(tau / fade) * (1 - clamp((tau - "
                 "(duration - fade)) / fade)) with tau = k / fps; x, y use t itself",
    }


# ducking.json ------------------------------------------------------------------------------------


def ducking_fixture() -> dict[str, Any]:
    rng = np.random.default_rng(20261012)
    pattern = ([0.0] * 60 + list(rng.uniform(0.05, 0.3, 140)) + [0.004] * 12
               + list(rng.uniform(0.05, 0.25, 90)) + [0.0] * 45 + list(rng.uniform(0.02, 0.1, 40))
               + [0.0] * 113)
    rms = [round(float(v), 6) for v in pattern]
    settings = {"threshold": DEFAULT_VOICE_THRESHOLD, "duck_db": -12.0, "attack_s": 0.3,
                "release_s": 0.8, "step_s": ENVELOPE_STEP_S}
    gains = duck_envelope(rms, **settings)
    times = [k * 0.0037 - 0.02 for k in range(0, 1300, 7)]
    read = envelope_gain_at(gains, np.array(times))
    cases = []
    for extra in ({"duck_db": -6.0, "attack_s": 0.0, "release_s": 0.0},
                  {"duck_db": -18.0, "attack_s": 0.05, "release_s": 2.0},
                  {"duck_db": 3.0, "attack_s": 0.3, "release_s": 0.8}):
        values = {**settings, **extra}
        cases.append({"settings": values, "gain_db": duck_envelope(rms, **values)})
    return {
        "settings": settings,
        "rms": rms,
        "gain_db": gains,
        "read": {"t": times, "gain_db": read, "gain": [10 ** (g / 20) for g in read]},
        "cases": cases,
        "notes": "window i (10 ms) has voice when rms > threshold; depth = max(0, -duck_db); "
                 "gain = -max(forward, backward): forward[i] = max(want[i], forward[i-1] - "
                 "depth * step / release), backward[i] = max(want[i], backward[i+1] - depth * "
                 "step / attack), rounded to 0.0001 dB; reading: value i at i * step_s, "
                 "linear in dB between, held at the ends",
    }


# captions.json -----------------------------------------------------------------------------------


def captions_fixture() -> dict[str, Any]:
    english = parse_timing({"duration_s": 6.0, "sentences": [
        {"id": "e1", "text": "The diner opened at five every morning without fail.",
         "start_s": 0.0, "end_s": 3.1,
         "words": [{"text": w, "start_s": round(i * 0.34, 3), "end_s": round(i * 0.34 + 0.3, 3)}
                   for i, w in enumerate(
                       "The diner opened at five every morning without fail.".split())]},
        {"id": "e2", "text": "Nobody noticed him.", "start_s": 3.6, "end_s": 4.7},
    ]})
    japanese = parse_timing({"duration_s": 2.0, "sentences": [
        {"id": "j1", "text": "東京へ行く", "start_s": 0.0, "end_s": 1.5,
         "words": [{"text": "東京へ行く", "start_s": 0.0, "end_s": 1.5}]}]})
    cases = []
    for name, timing, language, single in (
        ("english_phrases", english, "English", False),
        ("english_single_words", english, "English", True),
        ("japanese", japanese, "Japanese", False),
    ):
        from sigma_editor.render.captions import rules_for

        rules = rules_for(language)
        cues = chunk_cues(timing.sentences, max_chars_per_line=rules.max_chars_per_line,
                          max_lines=rules.max_lines, space_dependent=rules.space_dependent,
                          single_word=single)
        if name == "english_phrases":
            cues.append(CaptionCue(start_s=5.0, end_s=6.0, text="An edited\\Nline"))
        words = cue_word_times(cues, timing.sentences, rules.space_dependent)
        events: dict[str, list[dict[str, Any]]] = {}
        for preset in ("bold-outline", "active-word", "single-word-pop", "boxed"):
            style = caption_style_for(language, "bold-white", preset)
            rows = []
            for number, (cue, cue_words) in enumerate(zip(cues, words, strict=True)):
                for event in caption_events(cue, cue_words, style,
                                            space_dependent=rules.space_dependent):
                    rows.append({"cue": number, "start_s": event.start_s, "end_s": event.end_s,
                                 "active": event.active, "text": event.text})
            events[preset] = rows
        cases.append({
            "name": name,
            "language": language,
            "space_dependent": rules.space_dependent,
            "sentences": [{"id": s.id, "text": s.text, "start_s": s.start_s, "end_s": s.end_s,
                           "words": [{"text": w.text, "start_s": w.start_s, "end_s": w.end_s}
                                     for w in s.words]} for s in timing.sentences],
            "cues": [{"start_s": c.start_s, "end_s": c.end_s, "text": c.text} for c in cues],
            "tokens": [[[piece, is_word] for piece, is_word in
                        cue_tokens(c.text, rules.space_dependent)] for c in cues],
            "words": [[{"text": w.text, "start_s": w.start_s, "end_s": w.end_s} for w in ws]
                      for ws in words],
            "events": events,
        })
    return {
        "presets": presets_json(),
        "cases": cases,
        "notes": "a cue's words are the timing words starting inside it when there are as "
                 "many as the cue has words, else the cue's time shared out by length; "
                 "active-word and single words show word i from its start to the next word's "
                 "start (the last to the cue's end)",
    }


# Running -----------------------------------------------------------------------------------------


BUILDERS = {
    "ease": ease_fixture,
    "motion": motion_fixture,
    "bounds": bounds_fixture,
    "cuts": cuts_fixture,
    "popups": popups_fixture,
    "ducking": ducking_fixture,
    "captions": captions_fixture,
}


def build_all() -> dict[str, str]:
    """``file name -> text`` of every fixture: the seven of the contract, then the engine's
    wider ones and the animatic (``tools/preview_engine_fixtures.py``)."""
    texts = {f"{name}.json": document(name, BUILDERS[name]()) + "\n" for name in FILES}
    texts.update(build_engine_fixtures())
    return texts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"folder for the JSON files (default {DEFAULT_OUT})")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 when a file is missing or out of date")
    args = parser.parse_args(argv)
    texts = build_all()
    out: Path = args.out
    if args.check:
        stale = [name for name, text in texts.items()
                 if not (out / name).is_file()
                 or (out / name).read_text(encoding="utf-8") != text]
        for name in stale:
            print(f"out of date: {out / name}")
        if not stale:
            print(f"{len(texts)} fixtures in {out} are up to date")
        return 1 if stale else 0
    out.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {out / name} ({len(text) // 1024 + 1} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
