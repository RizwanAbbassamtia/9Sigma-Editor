"""The preview engine's wider fixtures (``engine_*.json``) and the animatic fixture, made by
the Python code the renderer runs. ``tools/make_preview_fixtures.py`` writes them next to the
seven contract fixtures (docs/M5-CONTRACT.md section 4) and checks them with ``--check``.

The families (each a JSON file in ``frontend/src/preview/fixtures/``):

* ``engine_motion``: every camera curve at awkward times, the presets, cover regions,
  rectangles in pixels, the handheld wobble, whole camera paths (rects and camera terms) and
  the parallax offsets (``render/motion.py``, ``render/geometry.py``).
* ``engine_layout``: the frame layout of ``render/assembly.py`` (``plan_layout``) for nine
  timelines (every transition, a Short, gaps, 25 and 60 fps, plain cuts ...), frame by frame:
  which clip and clip frame FFmpeg shows at every output frame.
* ``engine_bounds``: ``scene_bounds``, ``clip_transitions``, popup windows and animations
  (``render/timeline_builder.py``).
* ``engine_transitions``: per transition id, its easing, parameters and per-frame terms (the
  values each GLSL filter computes) at three sizes, the legacy names, easing curves and
  helpers (``render/transitions/``).
* ``engine_noise``: the integer hash and fBm noise of ``render/transitions/noise.py``.
* ``engine_popups``: popup motion and alpha as the assembly pass overlays them, and popup
  positions (``render/ffmpeg.py``, ``render/popups.py``).
* ``engine_audio``: voice placement in samples, the envelope read-back and the dip
  (``render/music.py``).
* ``engine_extras``: the linear-light mix table and the parallax overscan fit.
* ``engine_layers``: the depth-layer helpers of ``render/parallax/warp.py`` against OpenCV.
* ``animatic``: a storyboard, its voice timing and picture sizes, and the timeline
  ``render/timeline_builder.py`` builds from them (the storyboard's "Play animatic" builds
  the same timeline in the browser, ``components/preview/animatic.test.ts``).

No network and no FFmpeg: small placeholder pictures are written to a temporary folder (the
frame layout reads picture sizes from disk) and removed afterwards.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "backend") not in sys.path:  # runs without `pip install -e .` too
    sys.path.insert(0, str(REPO / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from sigma_editor.models.storyboard import StoryboardDoc  # noqa: E402
from sigma_editor.models.timeline import Timeline  # noqa: E402
from sigma_editor.render import motion as M  # noqa: E402
from sigma_editor.render import timeline_builder as TB  # noqa: E402
from sigma_editor.render.assembly import CutJob, plan_layout  # noqa: E402
from sigma_editor.render.ffmpeg import fmt  # noqa: E402
from sigma_editor.render.geometry import cover_region, rect_in_pixels  # noqa: E402
from sigma_editor.render.popups import popup_position, popup_scale  # noqa: E402
from sigma_editor.render.presets import load_render_config  # noqa: E402
from sigma_editor.render.scene_clips import SceneClipJob  # noqa: E402
from sigma_editor.render.transitions import common as C  # noqa: E402
from sigma_editor.render.transitions import cube as CUBE  # noqa: E402
from sigma_editor.render.transitions import easing as E  # noqa: E402
from sigma_editor.render.transitions import glitch as G  # noqa: E402
from sigma_editor.render.transitions import noise as N  # noqa: E402
from sigma_editor.render.transitions import push_whip as PW  # noqa: E402
from sigma_editor.render.transitions import registry as R  # noqa: E402
from sigma_editor.render.transitions import spin as SPIN  # noqa: E402
from sigma_editor.render.transitions import warps as WARPS  # noqa: E402
from sigma_editor.render.transitions import zoom as ZOOM  # noqa: E402

GENERATOR = "tools/make_preview_fixtures.py"
VERSION = 1
NAMES = ("motion", "layout", "bounds", "transitions", "noise", "popups", "audio", "extras",
         "layers")
"""The engine families, written as ``engine_<name>.json``."""

WORK = Path(".")
"""The folder the layout's placeholder pictures go in (set by :func:`build`)."""
_RESULTS: dict[str, dict[str, Any]] = {}


def dump(name: str, data: dict[str, Any]) -> None:
    """Keep one family's body (``name`` is its short file name, ``motion.json``)."""
    _RESULTS[name.removesuffix(".json")] = data


def text_of(data: dict[str, Any]) -> str:
    """The file text of one family: compact JSON with ``generator`` and ``version``."""
    payload = {"generator": GENERATOR, "version": VERSION, **data}
    return json.dumps(payload, indent=None, separators=(",", ":"), allow_nan=False) + "\n"


# motion.json ------------------------------------------------------------------------------


def motion_fixture() -> None:
    ts = [-0.2, 0.0, 1e-9, 0.1, 0.25, 1 / 3, 0.49999, 0.5, 0.50001, 0.7, 0.9, 0.999999, 1.0,
          1.3]
    ease_cases = [
        {"name": name, "t": t, "value": M.ease(name, t)}
        for name in [*M.EASINGS, "bogus", None]
        for t in ts
    ]
    presets = [p.to_json() for p in M.PRESETS.values()]
    pictures = [(1536, 1024), (1024, 1536), (1920, 1080), (2048, 2048), (1080, 1920),
                (800, 600), (4000, 1000)]
    frames_sizes = [(1920, 1080), (1080, 1920), (854, 480)]
    cover = [
        {"image": [iw, ih], "frame": [fw, fh], "region": list(cover_region(iw, ih, fw, fh))}
        for iw, ih in [*pictures, (0, 100), (100, 0)]
        for fw, fh in frames_sizes
    ]
    rects = []
    fracs = [p.start for p in M.PRESETS.values()] + [p.end for p in M.PRESETS.values()] + [
        (0.5, 0.5, 0.9, 0.9), (-0.2, 0.1, 0.5, 0.3), (0.3, 0.3, 0.001, 0.4), (0, 0, 2, 2),
    ]
    for iw, ih in pictures[:5]:
        for fw, fh in frames_sizes[:2]:
            for frac in fracs:
                rects.append({
                    "frac": list(frac), "image": [iw, ih], "frame": [fw, fh],
                    "rect": rect_in_pixels(frac, iw, ih, fw, fh),
                })
    shakes = [
        {"time_s": t, "seed": seed, "xy": list(M.shake_offsets(t, seed))}
        for seed in (0, 1, 2, 5, 11, 79)
        for t in (0.0, 1 / 30, 0.5, 1.5, 10.2, 123.4, 600.0)
    ]
    paths = []
    sample_k = lambda n: sorted({0, 1, n // 3, n // 2, (2 * n) // 3, n - 2, n - 1} & set(range(n)))  # noqa: E731
    combos = []
    for preset in M.PRESETS:
        for picture in ((1536, 1024), (1080, 1920), (2048, 2048)):
            for frame in ((1920, 1080), (1080, 1920)):
                combos.append((preset, picture, frame, None, 3, 47, 30))
    for ease_name in M.EASINGS:
        combos.append(("pan_left", (1536, 1024), (1920, 1080), ease_name, 0, 61, 30))
        combos.append(("handheld", (1536, 1024), (1920, 1080), ease_name, 7, 61, 30))
        combos.append(("drift", (1920, 1080), (1920, 1080), ease_name, 2, 30, 25))
    for frames in (1, 2, 3):
        combos.append(("zoom_in", (1536, 1024), (1920, 1080), None, 0, frames, 30))
    combos.append(("handheld", (1536, 1024), (1920, 1080), None, 12, 300, 60))
    combos.append(("unknown_preset", (1536, 1024), (1920, 1080), None, 0, 20, 30))
    for preset, (iw, ih), (fw, fh), ease_name, seed, frames, fps in combos:
        spec = M.preset_spec(preset)
        start_f, end_f = (spec.start, spec.end) if spec else ((0.1, 0.1, 0.5, 0.5),
                                                                (0.2, 0.2, 0.6, 0.6))
        start = rect_in_pixels(start_f, iw, ih, fw, fh)
        end = rect_in_pixels(end_f, iw, ih, fw, fh)
        region = cover_region(iw, ih, fw, fh)
        path = M.MotionPath.for_preset(preset, start, end, seed=seed, bounds=region,
                                       easing=ease_name, handheld_amplitude=0.004)
        samples = []
        for k in sample_k(frames):
            cam = path.frame_camera(k, frames, fps)
            samples.append({"k": k, "rect": list(path.frame_rect(k, frames, fps)),
                            "camera": [cam.px, cam.py, cam.zoom]})
        paths.append({
            "preset": preset, "ease": ease_name, "seed": seed, "image": [iw, ih],
            "frame": [fw, fh], "frames": frames, "fps": fps, "start_rect": start,
            "end_rect": end, "bounds": list(region), "easing": path.easing,
            "handheld": path.handheld, "samples": samples,
        })
    # Clamping: rectangles that leave the cover region, no bounds, a still move.
    odd = [
        ((-200.0, -100.0, 900.0, 506.25), (900.0, 600.0, 900.0, 506.25)),
        ((100.0, 50.0, 3000.0, 1687.5), (0.0, 0.0, 400.0, 225.0)),
        ((300.0, 200.0, 800.0, 450.0), (300.0, 200.0, 800.0, 450.0)),
    ]
    for start, end in odd:
        for bounded in (True, False):
            region = cover_region(1536, 1024, 1920, 1080) if bounded else None
            path = M.MotionPath.for_preset("pan_right", start, end, bounds=region)
            samples = []
            for k in sample_k(25):
                cam = path.frame_camera(k, 25, 30)
                samples.append({"k": k, "rect": list(path.frame_rect(k, 25, 30)),
                                "camera": [cam.px, cam.py, cam.zoom]})
            paths.append({
                "preset": "pan_right", "ease": None, "seed": 0, "image": [1536, 1024],
                "frame": [1920, 1080], "frames": 25, "fps": 30, "start_rect": list(start),
                "end_rect": list(end), "bounds": list(region) if region else None,
                "easing": path.easing, "handheld": path.handheld, "samples": samples,
            })
    parallax = []
    for strength, width in ((0.035, 1920), (0.1, 960), (0.0, 1280)):
        cam = M.CameraTerms(px=0.07, py=-0.02, zoom=0.11)
        for depth, mean, nx, ny in ((0.9, 0.4, 0.5, -0.2), (0.1, 0.4, -1.0, 0.56),
                                    (0.4, 0.4, 0.3, 0.3)):
            ox, oy = M.parallax_offsets(depth, mean, nx, ny, cam, strength=strength,
                                        out_width=width)
            parallax.append({
                "strength": strength, "out_width": width, "camera": [cam.px, cam.py, cam.zoom],
                "depth": depth, "mean": mean, "n": [nx, ny], "offset": [ox, oy],
                "gain": M.parallax_gain(strength, width),
            })
    dump("motion.json", {
        "ease": ease_cases, "presets": presets, "cover_region": cover, "rect_in_pixels": rects,
        "shake": shakes, "paths": paths, "parallax": parallax,
        "handheld_amplitude": M.HANDHELD_AMPLITUDE,
    })


# layout.json ------------------------------------------------------------------------------


def _picture(name: str, size: tuple[int, int]) -> str:
    folder = WORK / "06_images"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if not path.is_file() or Image.open(path).size != size:
        Image.new("RGB", size, (90, 120, 160)).save(path)
    return f"06_images/{name}"


ALL_IDS = list(R.SPECS)


def _timeline(name: str, scenes: list[dict], *, aspect: str = "16:9", fps: int = 30,
              width: int = 1920, height: int = 1080) -> Timeline:
    built = []
    for index, scene in enumerate(scenes):
        size = scene.get("picture", (1536, 1024))
        image = _picture(f"{name}_{index + 1:02d}_{size[0]}x{size[1]}.png", size)
        preset = scene.get("preset", "zoom_in")
        spec = M.preset_spec(preset)
        start_f, end_f = (spec.start, spec.end) if spec else ((0, 0, 1, 1), (0, 0, 1, 1))
        motion = {
            "preset": preset,
            "start_rect": rect_in_pixels(start_f, size[0], size[1], width, height),
            "end_rect": rect_in_pixels(end_f, size[0], size[1], width, height),
        }
        built.append({
            "index": index,
            "image": image,
            "start_s": scene["start"],
            "end_s": scene["end"],
            "motion": motion,
            "transition_out": {"type": scene.get("type", "film_dissolve"),
                               "duration_s": scene.get("dur", 0.7)},
        })
    return Timeline.model_validate({
        "project_id": name, "format": "shorts" if aspect == "9:16" else "long",
        "aspect": aspect, "fps": fps, "width": width, "height": height,
        "duration_s": scenes[-1]["end"], "scenes": built,
    })


def _layout_cases() -> list[tuple[str, Timeline]]:
    cases: list[tuple[str, Timeline]] = []
    cases.append(("three", _timeline("three", [
        {"start": 0.0, "end": 4.2, "type": "film_dissolve", "dur": 0.7, "preset": "zoom_in"},
        {"start": 4.2, "end": 9.05, "type": "push_left", "dur": 0.5, "preset": "pan_right"},
        {"start": 9.05, "end": 14.6, "type": "dip_black", "dur": 0.8, "preset": "handheld"},
    ])))
    every = []
    clock = 0.0
    presets = list(M.PRESETS)
    for i, transition in enumerate(ALL_IDS):
        length = [2.3, 3.1, 1.15, 4.0, 2.75, 0.9][i % 6]
        every.append({"start": round(clock, 3), "end": round(clock + length, 3),
                      "type": transition, "dur": [0.7, 0.5, 0.35, 0.8, 0.45, 1.2][i % 6],
                      "preset": presets[i % len(presets)],
                      "picture": [(1536, 1024), (1024, 1536), (2048, 2048)][i % 3]})
        clock += length
    cases.append(("every", _timeline("every", every)))
    cases.append(("shorts", _timeline("shorts", [
        {"start": 0.0, "end": 2.5, "type": "fade", "dur": 0.5, "preset": "pan_up",
         "picture": (1024, 1536)},
        {"start": 2.5, "end": 4.9, "type": "wipeleft", "dur": 0.4, "preset": "tilt_down",
         "picture": (1024, 1536)},
        {"start": 4.9, "end": 7.25, "type": "slideleft", "dur": 0.3, "preset": "dolly_in",
         "picture": (1536, 1024)},
        {"start": 7.25, "end": 9.0, "type": "zoomin", "dur": 0.45, "preset": "zoom_out",
         "picture": (1024, 1536)},
        {"start": 9.0, "end": 11.1, "type": "fadewhite", "dur": 0.6, "preset": "drift",
         "picture": (1024, 1536)},
    ], aspect="9:16", width=1080, height=1920)))
    cases.append(("late_start_gaps", _timeline("late", [
        {"start": 0.5, "end": 2.0, "type": "luma_wipe_diag", "dur": 0.6},
        {"start": 2.4, "end": 2.41, "type": "cube_left", "dur": 0.45},  # under one frame
        {"start": 2.41, "end": 5.333, "type": "glitch_subtle", "dur": 0.3},
        {"start": 5.6, "end": 6.1, "type": "light_leak_warm", "dur": 3.0},
        {"start": 6.1, "end": 9.9, "type": "dip_white", "dur": 1.0},
    ])))
    cases.append(("fps25", _timeline("fps25", [
        {"start": 0.0, "end": 3.02, "type": "whip_left", "dur": 0.35},
        {"start": 3.02, "end": 6.5, "type": "spin_zoom", "dur": 0.45},
        {"start": 6.5, "end": 8.0, "type": "morph_blend", "dur": 0.1},
        {"start": 8.0, "end": 10.0, "type": "film_burn", "dur": 0.8},
    ], fps=25)))
    cases.append(("fps60", _timeline("fps60", [
        {"start": 0.0, "end": 1.7, "type": "crosswarp", "dur": 0.7},
        {"start": 1.7, "end": 3.3, "type": "directional_warp", "dur": 0.45},
        {"start": 3.3, "end": 5.0, "type": "blur_dissolve", "dur": 0.7},
    ], fps=60)))
    cases.append(("single", _timeline("single", [
        {"start": 0.0, "end": 3.0, "type": "zoom_through", "dur": 0.7},
    ])))
    cases.append(("plain_cuts", _timeline("plain", [
        {"start": 0.0, "end": 1.0, "type": "film_dissolve", "dur": 0.03},
        {"start": 1.0, "end": 2.0, "type": "film_dissolve", "dur": 0.0},
        {"start": 2.0, "end": 3.0, "type": "dip_black", "dur": 0.0},
    ])))
    cases.append(("half_frames", _timeline("half", [
        {"start": 0.0, "end": 1.25, "type": "film_dissolve", "dur": 0.75},
        {"start": 1.25, "end": 2.55, "type": "push_up", "dur": 0.55},
        {"start": 2.55, "end": 3.85, "type": "dip_white", "dur": 0.25},
    ])))
    return cases


def _frame_map(layout) -> list[dict]:
    """What FFmpeg shows at every output frame: the concat order minus the dropped frames."""
    stream: list[tuple] = []
    for item in layout.order:
        if isinstance(item, SceneClipJob):
            stream.extend(("scene", item, i) for i in range(item.frames))
        else:
            stream.extend(("cut", item, j) for j in range(item.frames))
    dropped = set()
    for lo, hi in layout.drops:
        dropped.update(range(lo, hi + 1))
    out = [entry for index, entry in enumerate(stream) if index not in dropped]
    assert len(out) == layout.total_frames, (len(out), layout.total_frames)
    frames = []
    for n, (kind, item, index) in enumerate(out):
        if kind == "scene":
            frames.append({"n": n, "kind": "scene", "scene": item.number, "clip_frame": index})
            continue
        cut: CutJob = item
        a_frame = cut.a.frames - cut.frames + index
        entry = {"n": n, "kind": "cut" if cut.b is not None else "fade", "cut": cut.number,
                 "k": index, "frames": cut.frames, "a": cut.a.number, "a_frame": a_frame}
        if cut.b is not None:
            t_raw = R.frame_time(index, cut.frames)
            entry.update({"b": cut.b.number, "b_frame": index, "t_raw": t_raw,
                          "t": E.ease(cut.transition.easing, t_raw)})
        else:
            entry.update({"weight": (index + 1) / max(cut.frames, 1)})
        frames.append(entry)
    return frames


def layout_fixture() -> None:
    config = load_render_config()
    cases = []
    for name, timeline in _layout_cases():
        preset = config.preset("1080p")
        layout = plan_layout(timeline, preset, folder=WORK, config=config)
        scenes = []
        for job in layout.scenes:
            request = job.request
            path = request.path()
            first, body = layout.scene_frames[job.number]
            samples = []
            for k in sorted({0, 1, request.frames // 2, request.frames - 1}):
                samples.append({"k": k, "rect": list(path.frame_rect(k, request.frames,
                                                                    request.fps))})
            scenes.append({
                "number": job.number, "index": request.index, "frames": request.frames,
                "lead_in": request.lead_in, "tail_out": request.tail_out,
                "body_start": first, "body": body, "region": list(request.region),
                "image_size": list(request.image_size), "samples": samples,
            })
        cuts = []
        for cut in layout.cuts:
            cuts.append({
                "number": cut.number, "frames": cut.frames, "a": cut.a.number,
                "b": cut.b.number if cut.b is not None else None,
                "transition": cut.transition.id if cut.transition else None,
                "easing": cut.transition.easing if cut.transition else None,
                "fade_colour": list(cut.fade_colour) if cut.fade_colour else None,
                "seed": cut.seed,
            })
        cases.append({
            "name": name,
            "timeline": timeline.model_dump(mode="json"),
            "total_frames": layout.total_frames,
            "scenes": scenes,
            "cuts": cuts,
            "drops": [list(d) for d in layout.drops],
            "frames": _frame_map(layout),
        })
    dump("layout.json", {
        "settings": {"min_frames": config.transition_min_frames,
                     "max_share": config.transition_max_share},
        "cases": cases,
    })


# bounds.json ------------------------------------------------------------------------------


def _timing(sentences: list[tuple[str, float, float]], duration: float) -> TB.Timing:
    return TB.Timing(duration_s=duration, sentences=tuple(
        TB.TimedSentence(id=i, text=i, start_s=s, end_s=e) for i, s, e in sentences
    ))


def bounds_fixture() -> None:
    S = SimpleNamespace
    timing_a = _timing([("s1", 0.12, 2.4), ("s2", 2.6, 5.1), ("s3", 5.3, 7.95),
                        ("s4", 8.4, 11.0), ("s5", 11.2, 12.05)], 12.3)
    scene_sets = [
        ("plain", [S(sentence_ids=["s1"], est_duration_s=2.0), S(sentence_ids=["s2", "s3"],
                                                                     est_duration_s=5.0),
                   S(sentence_ids=["s4", "s5"], est_duration_s=4.0)], timing_a, 0.8),
        ("missing", [S(sentence_ids=["s1"], est_duration_s=2.0),
                     S(sentence_ids=["zz"], est_duration_s=1.7),
                     S(sentence_ids=["s4"], est_duration_s=3.0),
                     S(sentence_ids=["yy"], est_duration_s=0.0)], timing_a, 0.8),
        ("overlap", [S(sentence_ids=["s3"], est_duration_s=2.0),
                     S(sentence_ids=["s1", "s2"], est_duration_s=5.0),
                     S(sentence_ids=["s5"], est_duration_s=1.0)], timing_a, 0.0),
        ("tight", [S(sentence_ids=["a"], est_duration_s=0.1),
                   S(sentence_ids=["b"], est_duration_s=0.1)],
         _timing([("a", 0.0, 0.05), ("b", 0.06, 0.1)], 0.1), 0.8),
        ("single", [S(sentence_ids=["s1", "s2", "s3", "s4", "s5"], est_duration_s=9.0)],
         timing_a, 1.5),
    ]
    bounds_cases = []
    for name, scenes, timing, tail in scene_sets:
        bounds_cases.append({
            "name": name,
            "scenes": [{"sentence_ids": s.sentence_ids, "est_duration_s": s.est_duration_s}
                       for s in scenes],
            "timing": {"duration_s": timing.duration_s,
                       "sentences": [{"id": s.id, "start_s": s.start_s, "end_s": s.end_s}
                                     for s in timing.sentences]},
            "tail_s": tail,
            "bounds": [list(b) for b in TB.scene_bounds(scenes, timing, tail)],
        })
    clip_cases = []
    for bounds, durations, share, min_s in (
        ([(0, 4.2), (4.2, 9.05), (9.05, 14.6)], [0.7, 0.5, 0.8], 0.25, 2 / 30),
        ([(0, 1.0), (1.0, 1.4), (1.4, 5.0)], [0.7, 0.7, 3.5], 0.25, 2 / 30),
        ([(0, 0.2), (0.2, 0.5)], [0.6, 0.6], 0.25, 2 / 30),
        ([(0, 3.0), (3.0, 6.0)], [-1.0, 0.04], 0.5, 0.05),
        ([(0, 3.0)], [2.0, 9.0], 0.25, 0.0),
    ):
        clip_cases.append({
            "bounds": [list(b) for b in bounds], "durations": durations, "max_share": share,
            "min_s": min_s,
            "result": TB.clip_transitions(bounds, durations, max_share=share, min_s=min_s),
        })
    popup_cases = []
    for key, start, end, kwargs in (
        (0.12, 0.0, 4.2, {}),
        (4.3, 4.2, 9.05, {}),
        (9.0, 9.05, 10.0, {}),
        (3.0, 2.0, 3.6, {}),
        (3.0, 2.0, 3.3, {}),
        (1.0, 0.0, 20.0, {"lead_s": 0.5, "max_show_s": 2.0}),
        (5.0, 4.0, 6.0, {"min_show_s": 1.5, "end_margin_s": 0.4}),
    ):
        window = TB.popup_window(key, start, end, **kwargs)
        popup_cases.append({"key_start_s": key, "scene_start_s": start, "scene_end_s": end,
                            "options": kwargs, "window": list(window) if window else None})
    anim_cases = [{"style": s, "anim": TB.popup_anim_for(s)}
                  for s in ("", None, "pop", "Pop up", "fade", "fade slide", "slide",
                            "bold-box")]
    dump("bounds.json", {"scene_bounds": bounds_cases, "clip_transitions": clip_cases,
                         "popup_window": popup_cases, "popup_anim": anim_cases})


# transitions.json --------------------------------------------------------------------------


def _ctx(spec_id: str, t_raw: float, n: int, size: tuple[int, int], seed: int, fps: float = 30.0):
    resolved = R.resolve(spec_id)
    return C.FrameContext(
        width=size[0], height=size[1], t=E.ease(resolved.easing, t_raw), t_raw=t_raw,
        duration_s=n / fps, fps=fps, easing=resolved.easing, params=resolved.params,
        seed=seed, cache={},
    ), resolved


def _push_terms(ctx: C.FrameContext, direction: str) -> dict:
    t = ctx.t
    overshoot = max(ctx.num("overshoot", 0.02), 0.0)
    p = PW.push_position(t, overshoot)
    amount = max(ctx.num("motion_blur", PW.PUSH_MOTION_BLUR), 0.0)
    h = 1e-3
    lo, hi = max(t - h, 0.0), min(t + h, 1.0)
    dp_dt = (PW.push_position(hi, overshoot) - PW.push_position(lo, overshoot)) / (hi - lo)
    shutter = max(ctx.num("shutter_s", 1.0 / 30.0), 0.0) * amount
    horizontal = PW.DIRECTIONS[direction][0] != 0
    length = ctx.width if horizontal else ctx.height
    travel = length * abs(dp_dt) * ctx.speed * shutter
    blur = min(travel, 0.25 * length) * C.taper(ctx.t) if amount > 0 else 0.0
    return {"p": p, "c": PW.overshoot_coefficient(min(overshoot, 0.25)), "dp_dt": dp_dt,
            "blur": blur, "box": _box(blur)}


def _box(length: float) -> int:
    size = int(round(length))
    return 0 if size < 2 else size | 1


def _whip_terms(ctx: C.FrameContext, direction: str) -> dict:
    shutter = max(ctx.num("shutter_s", 1.0 / 30.0), 0.0) * max(ctx.num("motion_blur", 1.0), 0.0)
    max_share = min(max(ctx.num("max_blur", 0.5), 0.0), 1.0)
    horizontal = PW.DIRECTIONS[direction][0] != 0
    length = ctx.width if horizontal else ctx.height
    travel = length * 1.0 * ctx.speed * shutter
    blur = min(travel, max_share * length) * C.taper(ctx.t)
    return {"p": ctx.t, "blur": blur, "box": _box(blur)}


def _glitch_terms(ctx: C.FrameContext) -> dict:
    count, total, cut = G.glitch_window(ctx)
    terms = {"count": count, "total": total, "cut": cut, "clean": ctx.t_raw >= cut}
    if ctx.t_raw >= cut:
        return terms
    eased_cut = max(E.ease(ctx.easing, cut), 1e-6)
    env = math.sqrt(min(1.0, ctx.t / eased_cut))
    step = min(int(ctx.t_raw * total), count - 1)
    seed = int(ctx.seed)
    height = ctx.height
    max_shift = ctx.px(ctx.num(("max_shift_px", "split_px"), 6.0))
    split = int(round(max_shift * env * (0.6 + 0.4 * N.rand(step, 0, seed))))
    lo, hi = ctx.span("slices", (3.0, 5.0))
    lo_i, hi_i = max(0, int(round(lo))), max(0, int(round(hi)))
    bands = lo_i + min(int(N.rand(step, 1, seed) * (hi_i - lo_i + 1)), hi_i - lo_i)
    reach = ctx.px(ctx.num("slice_px", 40.0)) * env
    band_list = []
    for band in range(bands):
        top = int(N.rand(step, 2 + 3 * band, seed) * height)
        size = max(2, int((0.02 + 0.08 * N.rand(step, 3 + 3 * band, seed)) * height))
        shift = int(round((2.0 * N.rand(step, 4 + 3 * band, seed) - 1.0) * reach))
        band_list.append([top, size, shift])
    terms.update({"env": env, "step": step, "split": split, "bands": band_list})
    return terms


def _cube_terms(ctx: C.FrameContext, direction: str) -> dict:
    captured: list[list[list[float]]] = []
    original = CUBE.cv2.getPerspectiveTransform

    def spy(src, dst):  # noqa: ANN001, ANN202
        captured.append(dst.tolist())
        return original(src, dst)

    brightness: list[float] = []
    original_scale = CUBE.cv2.convertScaleAbs

    def spy_scale(frame, alpha=1.0, beta=0.0):  # noqa: ANN001, ANN202
        brightness.append(float(alpha))
        return original_scale(frame, alpha=alpha, beta=beta)

    faces = []
    CUBE.cv2.getPerspectiveTransform = spy
    CUBE.cv2.convertScaleAbs = spy_scale
    try:
        a = np.zeros((ctx.height, ctx.width, 3), np.uint8)
        aspect = ctx.width / ctx.height
        size = max(aspect, 1.0)
        distance = 2.5 - 1.5 * min(max(ctx.num("perspective", 0.7), 0.0), 1.0)
        z0 = max(distance, 0.5) * size
        lift = max(ctx.num("zoom_out", 0.35), 0.0) * size * math.sin(math.pi * ctx.t)
        shade = min(max(ctx.num("shade", 0.45), 0.0), 1.0)
        sign = 1.0 if direction == "left" else -1.0
        phi = sign * (math.pi / 2.0) * ctx.t
        centre_z = z0 + aspect / 2.0 + lift
        side, side_normal = ((CUBE._RIGHT, (1.0, 0.0, 0.0)) if direction == "left"
                             else (CUBE._LEFT, (-1.0, 0.0, 0.0)))
        for label, corners, normal in (("a", CUBE._FRONT, (0.0, 0.0, -1.0)),
                                       ("b", side, side_normal)):
            captured.clear()
            brightness.clear()
            face = CUBE._face(a, corners, normal, phi, centre_z, z0, aspect, shade)
            faces.append({
                "face": label, "visible": face is not None,
                "dst": captured[0] if captured and face is not None else None,
                "brightness": brightness[0] if brightness else 1.0,
            })
    finally:
        CUBE.cv2.getPerspectiveTransform = original
        CUBE.cv2.convertScaleAbs = original_scale
    return {"z0": z0, "phi": phi, "centre_z": centre_z, "faces": faces}


def _family_terms(spec_id: str, ctx: C.FrameContext) -> dict:
    t = ctx.t
    family = R.SPECS[spec_id].family
    if spec_id == "film_dissolve":
        return {"gain": 1.0 + ctx.num(("luma_lift", "lift"), 0.03) * math.sin(math.pi * t)}
    if family == "dips":
        hold = min(max(ctx.num("hold", 0.08), 0.0), 0.9)
        h0, h1 = 0.5 - hold / 2.0, 0.5 + hold / 2.0
        if t < h0:
            return {"phase": "a", "weight": E.ease_in_quad(t / h0),
                    "colour": list(ctx.colour(("color", "colour"), (0, 0, 0)))}
        if t <= h1:
            return {"phase": "hold", "weight": 1.0,
                    "colour": list(ctx.colour(("color", "colour"), (0, 0, 0)))}
        return {"phase": "b", "weight": E.ease_out_quad((t - h1) / (1.0 - h1)),
                "colour": list(ctx.colour(("color", "colour"), (0, 0, 0)))}
    if spec_id == "blur_dissolve":
        sigma = ctx.px(ctx.num(("max_blur_px", "blur_px"), 24.0))
        return {"sigma_a": sigma * t, "sigma_b": sigma * (1.0 - t)}
    if spec_id == "zoom_through":
        end_a = max(ctx.num(("a_scale_end",), 1.0 + ctx.num("scale_a", 0.25)), 1.0)
        start_b = max(ctx.num(("b_scale_start", "scale_b"), 1.15), 1.0)
        width = min(max(ctx.num(("crossfade_window", "crossfade"), 0.4), 0.0), 1.0)
        span = ZOOM.blur_span(ctx) * math.sin(math.pi * t)
        return {"scale_a": 1.0 + (end_a - 1.0) * t, "scale_b": start_b + (1.0 - start_b) * t,
                "m": E.smoothstep(0.5 - width / 2.0, 0.5 + width / 2.0, t), "span": span,
                "samples": C.blur_samples(ctx.width, ctx.height, span, 0.0)}
    if spec_id.startswith("push_"):
        return _push_terms(ctx, spec_id[5:])
    if spec_id.startswith("whip_"):
        return _whip_terms(ctx, spec_id[5:])
    if family == "luma_wipe":
        feather = min(max(ctx.num("feather", 0.12), 1e-3), 1.0)
        return {"edge": t * (1.0 + feather), "feather": feather}
    if spec_id == "directional_warp":
        vx, vy = WARPS._direction(ctx)
        smooth = max(ctx.num("smoothness", 0.5), 1e-3)
        return {"v": [vx, vy], "d": vx * 0.5 + vy * 0.5, "smoothness": smooth,
                "offset": vx * 0.5 + vy * 0.5 - 0.5 + t * (1.0 + smooth)}
    if spec_id == "light_leak_warm":
        peak = min(max(ctx.num("peak_t", 0.5), 0.05), 0.95)
        shaped = t ** (math.log(0.5) / math.log(peak))
        strength = min(max(ctx.num(("strength", "intensity"), 0.85), 0.0), 1.0)
        intensity = strength * math.sin(math.pi * shaped) ** 2
        mirror = bool(ctx.seed % 2)
        c1 = [-0.15 + 1.1 * t, 0.30 + 0.10 * math.sin(2.0 * math.pi * t)]
        c2 = [1.10 - 0.90 * t, 0.85 - 0.35 * t]
        if mirror:
            c1[0], c2[0] = 1.0 - c1[0], 1.0 - c2[0]
        return {"shaped": shaped, "intensity": intensity, "c1": c1, "c2": c2,
                "colour_1": list(ctx.colour(("color", "colour_1"), (1.0, 176 / 255, 96 / 255))),
                "skip": intensity < 1.0 / 1024.0}
    if spec_id == "film_burn":
        if t >= 0.5:
            s = (t - 0.5) / 0.5
            return {"phase": "b", "s": s, "weight": E.ease_out_cubic(s)}
        s = t / 0.5
        w = min(max(ctx.num("edge", 0.06), 1e-3), 0.5)
        return {"phase": "a", "s": s, "e": -w + s * (1.0 + 2.0 * w), "w": w,
                "char": 0.5 * E.smoothstep(0.0, 0.15, s)}
    if spec_id == "glitch_subtle":
        return _glitch_terms(ctx)
    if spec_id == "spin_zoom":
        terms = SPIN.spin_terms(ctx)
        return {"scale_a": terms.scale_a, "degrees_a": terms.degrees_a,
                "scale_b": terms.scale_b, "degrees_b": terms.degrees_b,
                "blur_scale": terms.blur_scale, "blur_degrees": terms.blur_degrees,
                "mix": terms.mix,
                "samples": C.blur_samples(ctx.width, ctx.height, terms.blur_scale,
                                          terms.blur_degrees)}
    if spec_id == "morph_blend":
        factor = 4
        size = (max(16, ctx.width // factor), max(9, ctx.height // factor))
        return {"small": list(size),
                "sigma_small": ctx.px(ctx.num("smooth_px", 12.0)) * size[0] / ctx.width,
                "amount": ctx.px(ctx.num(("max_displacement_px", "max_px"), 18.0))}
    if spec_id.startswith("cube_"):
        return _cube_terms(ctx, spec_id[5:])
    return {}


def transitions_fixture() -> None:
    specs = []
    for spec_id in R.SPECS:
        resolved = R.resolve(spec_id)
        specs.append({"id": spec_id, "easing": resolved.easing, "params": dict(resolved.params),
                      "seeded": resolved.spec.seeded, "family": resolved.spec.family,
                      "duration_s": resolved.spec.duration_s,
                      "fade_out_colour": list(R.fade_out_colour(spec_id))})
    frames = []
    sizes = [(960, 540), (1920, 1080), (540, 960)]
    size_dependent = ("blur", "zoom", "push_whip", "glitch", "spin", "cube", "warps")
    for spec_id in R.SPECS:
        runs = [(9, 30.0, 3, (960, 540)), (21, 30.0, 4, (960, 540))]
        if R.SPECS[spec_id].family in size_dependent:
            runs += [(14, 25.0, 7, (1920, 1080)), (14, 25.0, 8, (540, 960))]
        else:
            runs += [(14, 25.0, 7, (540, 960))]
        for n, fps, seed, size in runs:
            if True:
                for k in range(n):
                    t_raw = R.frame_time(k, n)
                    ctx, resolved = _ctx(spec_id, t_raw, n, size, seed, fps)
                    frames.append({
                        "id": spec_id, "n": n, "fps": fps, "k": k, "seed": seed,
                        "size": list(size), "t_raw": t_raw, "t": ctx.t,
                        "speed": ctx.speed, "taper": C.taper(ctx.t),
                        "terms": _family_terms(spec_id, ctx),
                    })
    legacy = {name: R.resolve_id(name) for name in (
        "fade", "wipeleft", "slideleft", "zoomin", "fadeblack", "fadewhite", "wiperight",
        "slideup", "diagtl", "circleopen", "pixelize", "distance", "Film Dissolve",
        "push-left", "PUSH_LEFT", "nonsense", "", "dip_white",
    )}
    easing_cases = []
    for name in (*E.EASINGS, "bogus"):
        for x in (-0.1, 0.0, 0.05, 0.3, 0.5, 0.77, 1.0, 1.2):
            easing_cases.append({"name": name, "x": x, "value": E.ease(name, x),
                                 "speed": E.speed(name, x)})
    misc = {
        "frame_time": [{"k": k, "n": n, "value": R.frame_time(k, n)}
                       for n in (1, 2, 9, 21) for k in range(n)],
        "overshoot": [{"overshoot": o, "c": PW.overshoot_coefficient(o)}
                      for o in (0.0, 0.005, 0.02, 0.05, 0.1, 0.25)],
        "blur_samples": [{"size": list(s), "span": sp, "degrees": dg,
                          "value": C.blur_samples(s[0], s[1], sp, dg)}
                         for s in sizes for sp, dg in ((0.0, 0.0), (0.001, 0.0), (0.06, 0.0),
                                                       (0.12, 0.0), (0.05, 3.0), (0.0, 1.0))],
        "cover_scale": [{"size": list(s), "degrees": d, "value": SPIN.cover_scale(s[0], s[1], d)}
                        for s in sizes for d in (0.0, 5.0, -8.0, 15.0, 95.0)],
    }
    dump("transitions.json", {"specs": specs, "frames": frames, "legacy": legacy,
                              "easing": easing_cases, "misc": misc})


# noise.json --------------------------------------------------------------------------------


def noise_fixture() -> None:
    hashes = []
    for x, y, seed in ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (12, 7, 3), (99, 0, 5),
                       (4095, 2047, 1013), (123456, 654321, 4294967295), (5, 9, 2026),
                       (17, 3, 3039)):
        hashes.append({"x": x, "y": y, "seed": seed, "u32": int(N.hash_u32(x, y, seed)),
                       "h01": float(N.hash01(x, y, seed)), "rand": N.rand(x, y, seed)})
    fbm_cases = []
    for (width, height), cells, octaves, seed in (((96, 54), 3.0, 4, 3), ((54, 96), 3.0, 4, 8),
                                                  ((64, 64), 2.5, 2, 0)):
        field = N.fbm(width, height, cells, octaves, seed)
        points = [(0, 0), (width - 1, height - 1), (width // 2, height // 3), (7, 5),
                  (width // 3, height - 2)]
        fbm_cases.append({"size": [width, height], "cells": cells, "octaves": octaves,
                          "seed": seed, "points": [[x, y, float(field[y, x])]
                                                   for x, y in points]})
    dump("noise.json", {"hash": hashes, "fbm": fbm_cases})


# popups.json -------------------------------------------------------------------------------


def _popup_eval(anim: str, start: float, end: float, x0: int, y0: int, frame: tuple[int, int],
                fps: int, n: int) -> dict:
    """The overlay at output frame ``n`` as ``render/ffmpeg.py`` builds it: enable
    ``between(t, start, end)``, x/y from ``_popup_exprs``, the alpha fades of the PNG input
    (frames at ``k / fps`` local, shifted by ``setpts=PTS+start/TB`` with a 1/fps time base
    and truncated to whole frames), the overlay position floored to even pixels (yuv420)."""
    config = load_render_config()
    look = config.popups
    scale = min(frame) / 1080.0
    t = n / fps
    start_f = float(fmt(start))
    end_f = float(fmt(end))
    visible = start_f <= t <= end_f
    travel = max(1.0, look.slide_px * scale)
    slide = float(fmt(max(look.slide_s, 0.01)))
    ease = max(0.0, 1.0 - (t - start_f) / slide) ** 2
    x, y = float(x0), float(y0)
    if anim == "slide_left":
        x = x0 + float(fmt(travel)) * ease
    elif anim == "pop":
        y = y0 + float(fmt(travel / 2)) * ease
    duration = max(end - start, 0.2)
    fade = min(look.fade_s, duration / 3)
    fade_f = float(fmt(fade))
    out_st = float(fmt(duration - fade))
    shift = int(start_f * fps)  # setpts=PTS+start/TB, truncated to whole time-base units
    k = n - shift
    local = k / fps
    last = math.ceil(float(fmt(duration)) * fps) - 1
    alpha_in = min(max(local / fade_f, 0.0), 1.0) if fade_f > 0 else 1.0
    alpha_out = 1.0 - min(max((local - out_st) / fade_f, 0.0), 1.0) if fade_f > 0 else 1.0
    present = 0 <= k <= last
    return {"n": n, "t": t, "visible": bool(visible and present), "enabled": visible,
            "x": x, "y": y, "x_px": int(x) & ~1, "y_px": int(y) & ~1, "local": local,
            "k": k, "alpha": alpha_in * alpha_out if present else 0.0}


def popups_fixture() -> None:
    config = load_render_config()
    cases = []
    for anim in ("slide_left", "pop", "fade"):
        for start, end, frame, fps in ((1.234, 4.6, (1920, 1080), 30),
                                       (0.3, 0.5, (854, 480), 30),
                                       (12.0, 14.3, (1080, 1920), 30),
                                       (2.05, 6.05, (1280, 720), 25)):
            x0, y0 = 60, 772
            frames = range(max(0, int(start * fps) - 2), int(end * fps) + 3)
            cases.append({
                "anim": anim, "start_s": start, "end_s": end, "frame": list(frame), "fps": fps,
                "x0": x0, "y0": y0,
                "samples": [_popup_eval(anim, start, end, x0, y0, frame, fps, n) for n in frames],
            })
    positions = []
    for position in ("top-left", "top-right", "bottom-left", "bottom-right", "center", "middle",
                     "Bottom_Right"):
        for frame, popup, reserved in (((1920, 1080), (420, 116), 0),
                                       ((1920, 1080), (420, 116), 194),
                                       ((854, 480), (190, 52), 86),
                                       ((1080, 1920), (700, 260), 194)):
            scale = popup_scale(*frame)
            margin = round(config.popups.margin_px * scale)
            positions.append({
                "position": position, "frame": list(frame), "popup": list(popup),
                "reserved": reserved, "margin": margin, "scale": scale,
                "xy": list(popup_position(position, popup, frame, margin_px=margin,
                                          bottom_reserved_px=reserved)),
            })
    look = config.popups
    dump("popups.json", {
        "look": {"fade_s": look.fade_s, "slide_s": look.slide_s, "slide_px": look.slide_px,
                 "margin_px": look.margin_px},
        "cases": cases, "positions": positions,
    })


def audio_fixture() -> None:
    """Voice placement and the music envelope (render/music.py)."""
    from sigma_editor.render import music as MU

    base = _timeline("audio", [
        {"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 9.5},
    ]).model_dump(mode="json")
    cases = []
    variants = [
        ("whole", None, 0.0, 400000),
        ("whole_delayed", None, 0.2345, 400000),
        ("whole_short_file", None, 0.5, 100000),
        ("whole_unknown_length", None, 0.25, None),
        ("segments", [
            {"sentence_id": "s1", "src_start_s": 0.1, "src_end_s": 2.3, "at_s": 0.0},
            {"sentence_id": "s2", "src_start_s": 2.6, "src_end_s": 5.0, "at_s": 2.5},
            {"sentence_id": "s3", "src_start_s": 5.2, "src_end_s": 8.1, "at_s": 5.4},
        ], 0.0, 480000),
        ("swapped_overlapping", [
            {"sentence_id": "s2", "src_start_s": 2.6, "src_end_s": 5.0, "at_s": 0.0},
            {"sentence_id": "s1", "src_start_s": 0.1, "src_end_s": 2.3, "at_s": 2.0},
            {"sentence_id": "s3", "src_start_s": 5.2, "src_end_s": 9.9, "at_s": 7.0},
            {"sentence_id": "s4", "src_start_s": 9.9, "src_end_s": 10.5, "at_s": 12.0},
        ], 0.0, 440000),
        ("same_start", [
            {"sentence_id": "b", "src_start_s": 3.0, "src_end_s": 4.0, "at_s": 1.0},
            {"sentence_id": "a", "src_start_s": 1.0, "src_end_s": 2.5, "at_s": 1.0},
        ], 0.0, None),
    ]
    for name, segments, start, file_samples in variants:
        data = json.loads(json.dumps(base))
        data["voice"] = {"path": "05_voice/voice.wav", "start_s": start, "segments": segments}
        timeline = Timeline.model_validate(data)
        total = MU.timeline_samples(timeline)
        placements = MU.voice_placements(timeline, file_samples, total)
        cases.append({
            "name": name, "segments": segments, "start_s": start, "file_samples": file_samples,
            "frames": MU.timeline_frames(timeline), "fps": timeline.fps, "total": total,
            "placements": [[p.src_start, p.src_end, p.at] for p in placements],
        })
    gains = [0.0, -1.5, -6.0, -12.0, -12.0, -9.0, 0.0]
    times = [-0.5, 0.0, 0.004, 0.01, 0.015, 0.031, 0.06, 0.5]
    envelope = {
        "step_s": 0.01, "gain_db": gains, "times": times,
        "values": [float(v) for v in MU.envelope_gain_at(gains, np.asarray(times))],
    }
    rms = [0.0, 0.01, 0.05, 0.2, 0.2, 0.0, 0.0, 0.0, 0.04, 0.0, 0.0, 0.0, 0.0, 0.0]
    duck = {"rms": rms, "threshold": 0.03, "duck_db": -12.0, "attack_s": 0.03,
            "release_s": 0.05, "step_s": 0.01,
            "gain_db": [float(v) for v in MU.duck_envelope(rms, threshold=0.03, duck_db=-12.0,
                                                           attack_s=0.03, release_s=0.05)]}
    dump("audio.json", {"placements": cases, "envelope": envelope, "duck": duck})


def extras_fixture() -> None:
    """The linear-light mix table, the parallax overscan fit and the blur plan."""
    from sigma_editor.render.parallax import warp as WARP

    tables = []
    for weight, gain in ((0.0, 1.0), (0.3, 1.0), (0.5, 1.03), (0.77, 1.0), (1.0, 1.0), (0.5, 0.8)):
        table = C._linear_table(weight, gain)
        picks = [(0, 0), (0, 255), (255, 0), (128, 64), (17, 230), (200, 199), (255, 255), (90, 91)]
        tables.append({"weight": weight, "gain": gain,
                       "values": [[a, b, int(table[a * 256 + b])] for a, b in picks]})
    overscan = []
    for preset, (iw, ih), strength, frames, stats in (
        ("pan_left", (1536, 1024), 0.035, 90, (0.45, 0.05, 0.98)),
        ("zoom_in", (2048, 1152), 0.035, 120, (0.5, 0.0, 1.0)),
        ("dolly_in", (1536, 1024), 0.12, 60, (0.4, 0.1, 0.95)),
        ("handheld", (1920, 1080), 0.035, 75, (0.5, 0.2, 0.9)),
        ("hold", (1920, 1080), 0.035, 40, (0.5, 0.0, 1.0)),
        ("pan_up", (1024, 1536), 0.2, 90, (0.3, 0.0, 1.0)),
    ):
        spec = M.preset_spec(preset)
        start = rect_in_pixels(spec.start, iw, ih, 1920, 1080)
        end = rect_in_pixels(spec.end, iw, ih, 1920, 1080)
        region = cover_region(iw, ih, 1920, 1080)
        path = M.MotionPath.for_preset(preset, start, end, seed=2, bounds=region)
        # the working picture of render/parallax/clip.py, at source scale (factor 1)
        rx, ry, rw, rh = region
        x0 = max(int(math.floor(rx - rw * 0.04)), 0)
        y0 = max(int(math.floor(ry - rh * 0.04)), 0)
        x1 = min(int(math.ceil(rx + rw + rw * 0.04)), iw)
        y1 = min(int(math.ceil(ry + rh + rh * 0.04)), ih)
        crop = (float(x0), float(y0), float(x1 - x0), float(y1 - y0))
        work = WARP.WorkingImage(pixels=np.zeros((y1 - y0, x1 - x0, 3), np.uint8), factor=1.0,
                                 origin=(float(x0), float(y0)))
        moved = work.path(path)
        layers = SimpleNamespace(mean_depth=stats[0], depth_range=(stats[1], stats[2]))
        share, soft = WARP.fit_overscan(moved, layers, (1920, 1080), strength=strength,
                                        frames=frames, fps=30, limit=0.03, picture=work.size)
        overscan.append({"preset": preset, "image": [iw, ih], "strength": strength,
                         "frames": frames, "stats": list(stats), "start": list(start),
                         "end": list(end), "crop": list(crop), "share": share, "soft": soft})
    dump("extras.json", {"linear_table": tables, "overscan": overscan})


def layers_fixture() -> None:
    """The depth-layer helpers of render/parallax/warp.py against OpenCV."""
    from sigma_editor.render.parallax import warp as WARP

    rng = np.random.default_rng(7)
    small = rng.random((7, 10)).astype(np.float32)
    medium = rng.random((12, 20)).astype(np.float32)

    def as_list(a: np.ndarray) -> list:
        return [round(float(v), 6) for v in np.asarray(a, dtype=np.float64).ravel()]

    kernel = WARP._rect_kernel(2)
    yy, xx = np.mgrid[0:54, 0:96].astype(np.float32)
    depth = (0.25 + 0.15 * yy / 54).astype(np.float32)
    disc = ((xx - 40) ** 2 + (yy - 30) ** 2) < 10 ** 2
    depth = np.where(disc, 0.85, depth).astype(np.float32)
    analysis = WARP.analyse_depth(depth, M.load_motion_config())
    front = WARP.front_depth(depth, analysis.alpha) if analysis.alpha is not None else None
    dump("layers.json", {
        "small": {"w": 10, "h": 7, "values": as_list(small)},
        "medium": {"w": 20, "h": 12, "values": as_list(medium)},
        "pad": as_list(WARP.odd_reflect_pad(small, 2)),
        "erode": as_list(cv2.erode(small, kernel)),
        "dilate": as_list(cv2.dilate(small, kernel)),
        "gauss": as_list(cv2.GaussianBlur(medium, (0, 0), 1.5, borderType=cv2.BORDER_REFLECT_101)),
        "area_10x6": as_list(cv2.resize(medium, (10, 6), interpolation=cv2.INTER_AREA)),
        "area_7x5": as_list(cv2.resize(medium, (7, 5), interpolation=cv2.INTER_AREA)),
        "linear_23x13": as_list(cv2.resize(small, (23, 13), interpolation=cv2.INTER_LINEAR)),
        "disc": {"w": 96, "h": 54, "values": as_list(depth)},
        "envelope": as_list(analysis.envelope),
        "alpha": as_list(analysis.alpha) if analysis.alpha is not None else None,
        "front": as_list(front) if front is not None else None,
    })


# animatic.json ---------------------------------------------------------------------------------


ANIMATIC_STORYBOARD: dict[str, Any] = {
    "project_id": "fixture-animatic",
    "format": "long",
    "aspect": "16:9",
    "generated_at": datetime(2026, 10, 12, tzinfo=UTC).isoformat(),
    "model": "fixture",
    "motion_style": "ken_burns",
    "scenes": [
        {"index": 0, "sentence_ids": ["s1"], "narration": "The town was quiet that winter.",
         "est_start_s": 0.0, "est_end_s": 2.5, "est_duration_s": 2.5,
         "image_prompt": "a quiet town",
         "popup": {"text": "Fifty  years earlier", "style": "", "position": "top-left"},
         "motion": {"preset": "pan_right"},
         "transition_out": {"type": "film_dissolve", "duration_s": 0.8}},
        {"index": 1, "sentence_ids": ["s2", "s3"],
         "narration": "Nobody noticed the stranger. He paid for everything.",
         "est_start_s": 2.5, "est_end_s": 7.0, "est_duration_s": 4.5,
         "image_prompt": "a stranger", "motion": {"preset": "pan_right"},
         "transition_out": {"type": "whip_right", "duration_s": 0.5}},
        {"index": 2, "sentence_ids": ["s4"], "narration": "Then he was gone.",
         "est_start_s": 7.0, "est_end_s": 9.0, "est_duration_s": 2.0,
         "image_prompt": "an empty street",
         "popup": {"text": "Gone", "style": "pop", "position": "center"},
         "motion": {"preset": "hold"},
         "transition_out": {"type": "zoom_through", "duration_s": 1.0}},
        {"index": 3, "sentence_ids": ["s5"], "narration": "Years later the letter arrived.",
         "est_start_s": 9.0, "est_end_s": 11.5, "est_duration_s": 2.5,
         "image_prompt": "a letter", "motion": {"preset": "zoom_out"},
         "transition_out": {"type": "fade", "duration_s": 2.0}},
    ],
}
ANIMATIC_TIMING: dict[str, Any] = {
    "duration_s": 12.05,
    "sample_rate": 48000,
    "source": "provider_word",
    "sentences": [
        {"id": "s1", "text": "The town was quiet that winter.", "start_s": 0.12, "end_s": 2.4,
         "words": [{"text": "The", "start_s": 0.12, "end_s": 0.3},
                   {"text": "town", "start_s": 0.32, "end_s": 0.7},
                   {"text": "was", "start_s": 0.72, "end_s": 0.9},
                   {"text": "quiet", "start_s": 0.95, "end_s": 1.5},
                   {"text": "that", "start_s": 1.55, "end_s": 1.8},
                   {"text": "winter.", "start_s": 1.85, "end_s": 2.4}]},
        {"id": "s2", "text": "Nobody noticed the stranger.", "start_s": 2.6, "end_s": 5.1,
         "words": []},
        {"id": "s3", "text": "He paid for everything.", "start_s": 5.3, "end_s": 7.95,
         "words": []},
        {"id": "s4", "text": "Then he was gone.", "start_s": 8.4, "end_s": 9.6, "words": []},
        {"id": "s5", "text": "Years later the letter arrived.", "start_s": 9.8, "end_s": 12.05,
         "words": []},
    ],
}
ANIMATIC_SIZES = {0: (2560, 1440), 1: (1920, 1080), 2: (1024, 1024), 3: (1440, 2560)}


def animatic_fixture() -> dict[str, Any]:
    """A storyboard with its timing and picture sizes and the timeline the builder makes."""
    storyboard = StoryboardDoc.model_validate(ANIMATIC_STORYBOARD)
    timing = TB.parse_timing(ANIMATIC_TIMING)
    folder = WORK / "animatic"
    sources = [
        TB.SceneSource(index, f"06_images/scene_{index + 1:02d}.png",
                       folder / f"scene_{index + 1:02d}.png", w, h)
        for index, (w, h) in ANIMATIC_SIZES.items()
    ]
    request = TB.BuildRequest(
        project_id="fixture-animatic", fmt="long", aspect="16:9", language="English",
        folder=folder, storyboard=storyboard, timing=timing, sources=sources, width=1920,
        height=1080, proxy=(854, 480), presets=["1080p"], music_choice="",
        motion_style="ken_burns",
    )
    timeline, warnings = TB.build_timeline(request)
    return {
        "generator": GENERATOR,
        "storyboard": storyboard.model_dump(mode="json"),
        "timing": ANIMATIC_TIMING,
        "pictures": [{"scene": i, "path": f"06_images/scene_{i + 1:02d}.png", "width": w,
                      "height": h} for i, (w, h) in ANIMATIC_SIZES.items()],
        "timeline": timeline.model_dump(mode="json"),
        "warnings": warnings,
    }


# Running -----------------------------------------------------------------------------------------

BUILDERS = {
    "motion": motion_fixture,
    "layout": layout_fixture,
    "bounds": bounds_fixture,
    "transitions": transitions_fixture,
    "noise": noise_fixture,
    "popups": popups_fixture,
    "audio": audio_fixture,
    "extras": extras_fixture,
    "layers": layers_fixture,
}


def build() -> dict[str, str]:
    """``file name -> text`` of the engine families and the animatic fixture."""
    global WORK
    texts: dict[str, str] = {}
    previous = np.seterr(all="ignore")
    threads = cv2.getNumThreads()
    cv2.setNumThreads(2)
    try:
        with tempfile.TemporaryDirectory(prefix="sge-fx-") as folder:
            WORK = Path(folder)
            for name in NAMES:
                _RESULTS.clear()
                BUILDERS[name]()
                texts[f"engine_{name}.json"] = text_of(_RESULTS[name])
            animatic = animatic_fixture()
            texts["animatic.json"] = json.dumps(animatic, indent=1, ensure_ascii=False) + "\n"
    finally:
        WORK = Path(".")
        _RESULTS.clear()
        cv2.setNumThreads(threads)
        np.seterr(**previous)
    return texts


__all__ = ["GENERATOR", "NAMES", "build"]
