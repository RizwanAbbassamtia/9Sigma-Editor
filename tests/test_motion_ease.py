"""``motion.ease`` (docs/M5-CONTRACT.md section 3.3): a scene's own speed curve overrides
its preset's, in the Python path (``render/motion.py``), in the scene clips' path
(``render/scene_clips.py``, both zoompan and the Python frame providers) and in the FFmpeg
``zoompan`` expression, which FFmpeg itself evaluates to the Python windows within 0.5 px.
A scene without an override keeps its cached clip. The live preview's JSON fixtures
(``tools/make_preview_fixtures.py``) match this code."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sigma_editor.models.timeline import (
    MOTION_EASES,
    Timeline,
    TimelineMotion,
    normalize_motion_ease,
)
from sigma_editor.render import assembly
from sigma_editor.render.geometry import rect_in_pixels
from sigma_editor.render.motion import (
    MotionPath,
    ease,
    preset_rects,
    scene_easing,
    scene_path,
    zoompan_expressions,
)
from sigma_editor.render.presets import load_render_config
from test_motion import ffmpeg_eval, standalone
from test_render_fixtures import make_render_tmp_fixture, requires_ffmpeg, write_project

render_tmp = make_render_tmp_fixture()


def motion(preset: str, ease_name: str | None = None) -> TimelineMotion:
    start, end = preset_rects(preset) or ((0, 0, 1, 1), (0, 0, 1, 1))
    return TimelineMotion(
        preset=preset,
        start_rect=rect_in_pixels(start, 1024, 576, 1920, 1080),
        end_rect=rect_in_pixels(end, 1024, 576, 1920, 1080),
        ease=ease_name,
    )


def test_ease_names_are_normalised_and_unknown_ones_dropped() -> None:
    assert MOTION_EASES == ("ease_in_out_cubic", "ease_out_quart", "ease_in_out_sine", "snap",
                            "linear")
    assert normalize_motion_ease("Ease-Out Quart") == "ease_out_quart"
    assert normalize_motion_ease("SNAP") == "snap"
    assert normalize_motion_ease("bounce") is None and normalize_motion_ease("") is None
    assert normalize_motion_ease(None) is None
    old = TimelineMotion.model_validate(
        {"preset": "zoom_in", "start_rect": [0, 0, 10, 10], "end_rect": [1, 1, 8, 8]})
    assert old.ease is None  # timelines from before M5 read as before
    assert TimelineMotion.model_validate(
        {"preset": "zoom_in", "start_rect": [0, 0, 10, 10], "end_rect": [1, 1, 8, 8],
         "ease": "linear"}).ease == "linear"


def test_the_override_wins_over_the_preset_curve() -> None:
    assert scene_easing(motion("drift")) == "ease_in_out_sine"
    assert scene_easing(motion("zoom_in")) == "ease_in_out_cubic"
    assert scene_easing(motion("drift", "snap")) == "snap"
    assert scene_easing(motion("zoom_in", "linear")) == "linear"
    unknown = motion("zoom_in").model_copy(update={"preset": "mystery"})
    assert scene_easing(unknown) == "ease_in_out_cubic"
    # MotionPath.for_preset ignores a name it does not know (the preset's curve stays).
    path = MotionPath.for_preset("drift", (0, 0, 100, 50), (10, 0, 100, 50), easing="bounce")
    assert path.easing == "ease_in_out_sine"


@pytest.mark.parametrize("name", MOTION_EASES)
def test_scene_path_follows_the_override(name: str) -> None:
    scene = motion("pan_right", name)
    path = scene_path(scene, (1024, 576), (1920, 1080), seed=2)
    assert path.easing == name
    plain = scene_path(motion("pan_right"), (1024, 576), (1920, 1080), seed=2)
    x0, x1 = path.start[0], path.end[0]
    for t in (0.1, 0.25, 0.5, 0.9):
        expected = x0 + (x1 - x0) * ease(name, t)
        assert path.rect(t)[0] == pytest.approx(expected, abs=1e-9)
    if name != "ease_in_out_cubic":
        assert path.rect(0.25)[0] != pytest.approx(plain.rect(0.25)[0], abs=1e-6)


def _timeline(motions: list[TimelineMotion]) -> Timeline:
    return Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 854, "height": 480,
        "duration_s": 4.5,
        "scenes": [
            {"index": i, "image": f"06_images/scene_{i + 1:02d}.png", "start_s": i * 1.5,
             "end_s": (i + 1) * 1.5, "motion": m.model_dump(),
             "transition_out": {"type": "fade", "duration_s": 0.3}}
            for i, m in enumerate(motions)
        ],
    })


def test_scene_clips_draw_the_override_and_keep_old_clips(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    config = load_render_config()
    plain = _timeline([motion("zoom_in"), motion("pan_right"), motion("drift")])
    eased = _timeline([motion("zoom_in"), motion("pan_right", "linear"), motion("drift")])
    first = assembly.plan_layout(plain, config.proxy, folder=files.folder, config=config)
    second = assembly.plan_layout(eased, config.proxy, folder=files.folder, config=config)
    # Only the scene with the override is a new clip (and the cuts next to it).
    assert [a.key == b.key for a, b in zip(first.scenes, second.scenes, strict=True)] == [
        True, False, True]
    assert second.scenes[1].request.path().easing == "linear"
    assert first.scenes[1].request.path().easing == "ease_in_out_cubic"
    assert first.scenes[2].request.path().easing == "ease_in_out_sine"
    # A motion block without "ease" hashes exactly like one with ease = null.
    payload = first.scenes[0].hash_payload(None)
    assert "ease" not in payload["motion"]
    assert "ease" in second.scenes[1].hash_payload(None)["motion"]
    # The zoompan filter of that scene uses the linear curve: no cubic "if(lt(" term.
    from sigma_editor.render.scene_clips import zoompan_part

    assert "if(lt(" not in zoompan_part(second.scenes[1])
    assert "if(lt(" in zoompan_part(first.scenes[1])


@requires_ffmpeg
@pytest.mark.parametrize("name", ["snap", "linear", "ease_out_quart", "ease_in_out_sine"])
def test_zoompan_with_the_override_follows_the_python_path(name: str) -> None:
    canvas = (2880, 1620)
    region = (0.0, 0.0, float(canvas[0]), float(canvas[1]))
    start_frac, end_frac = preset_rects("dolly_in")
    start = rect_in_pixels(start_frac, canvas[0], canvas[1], 1920, 1080)
    end = rect_in_pixels(end_frac, canvas[0], canvas[1], 1920, 1080)
    path = MotionPath.for_preset("dolly_in", start, end, seed=3, bounds=region, easing=name)
    assert path.easing == name
    frames = 40
    exprs = zoompan_expressions(path, frames=frames, canvas_w=canvas[0], fps=30)
    z = ffmpeg_eval(re.sub(r"\bon\b", "n", exprs.z), frames)
    x = ffmpeg_eval(standalone(exprs.x, exprs.z, canvas), frames)
    y = ffmpeg_eval(standalone(exprs.y, exprs.z, canvas), frames)
    for k in range(frames):
        px, py, pw, _ph = path.frame_rect(k, frames, 30)
        assert canvas[0] / z[k] == pytest.approx(pw, abs=0.5)
        assert abs(x[k] - px) < 0.5 and abs(y[k] - py) < 0.5


def test_the_preview_fixtures_are_current() -> None:
    """The live preview's maths (ease curves, camera windows, scene bounds, cuts, popups,
    the music dip, caption words) is tested against JSON that tools/make_preview_fixtures.py
    makes from this code; after a change to it, run the tool again."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "make_preview_fixtures.py"
    spec = importlib.util.spec_from_file_location("make_preview_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, "run tools/make_preview_fixtures.py"


@requires_ffmpeg
@pytest.mark.parametrize("preset", ["dolly_in", "pan_right", "zoom_out"])
def test_zoompan_frames_sit_on_the_python_windows(render_tmp: Path, preset: str) -> None:
    """zoompan crops whole pixels; the renderer's filter centres that crop on the sub-pixel
    window the live preview draws (M5.1 parity: unrounded, the crop sat 0.75 canvas px up and
    left on average, about 0.65 output px at 1080p). Measured on rendered frames against the
    Python reference by phase correlation: no offset left beyond a quarter of a pixel."""
    import subprocess

    import cv2
    import numpy as np
    from PIL import Image

    from sigma_editor.render.motion import zoompan_filter
    from sigma_editor.render.parallax import warp

    canvas = (960, 540)
    size = (640, 360)
    rng = np.random.default_rng(5)
    noise = cv2.GaussianBlur(rng.uniform(0, 255, (canvas[1], canvas[0], 3)), (0, 0), 1.2)
    yy, xx = np.mgrid[0:canvas[1], 0:canvas[0]].astype(np.float32)
    ramp = np.stack([60 * np.sin(xx / 37.0), 60 * np.cos(yy / 29.0), 40 * np.sin((xx - yy) / 53.0)],
                    axis=-1)
    picture = np.clip(noise * 0.6 + 50 + ramp, 0, 255).astype(np.uint8)
    source = render_tmp / f"canvas_{preset}.png"
    Image.fromarray(picture).save(source)
    start_frac, end_frac = preset_rects(preset)
    start = rect_in_pixels(start_frac, canvas[0], canvas[1], size[0], size[1])
    end = rect_in_pixels(end_frac, canvas[0], canvas[1], size[0], size[1])
    path = MotionPath.for_preset(preset, start, end, bounds=(0, 0, canvas[0], canvas[1]))
    frames = 24
    script = render_tmp / f"zoom_{preset}.txt"
    graph = zoompan_filter(path, frames=frames, canvas=canvas, size=size, fps=30)
    script.write_text(f"[0:v]format=gbrp,{graph},format=rgb24[out]\n", encoding="utf-8")
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source),
         "-/filter_complex", str(script), "-map", "[out]", "-f", "rawvideo", "-pix_fmt",
         "rgb24", "-"],
        capture_output=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    decoded = np.frombuffer(completed.stdout, np.uint8).reshape(-1, size[1], size[0], 3)
    window = cv2.createHanningWindow(size, cv2.CV_64F)
    shifts = []
    for k in range(2, frames - 2, 3):
        expected = warp.kenburns_frame(picture, path.frame_rect(k, frames, 30), size, "cubic")
        a = cv2.cvtColor(expected, cv2.COLOR_RGB2GRAY).astype(np.float64)
        b = cv2.cvtColor(decoded[k], cv2.COLOR_RGB2GRAY).astype(np.float64)
        (dx, dy), _response = cv2.phaseCorrelate(a, b, window)
        shifts.append((dx, dy))
    mean_dx, mean_dy = np.mean(shifts, axis=0)
    assert abs(mean_dx) < 0.25 and abs(mean_dy) < 0.25, shifts
    assert max(abs(v) for pair in shifts for v in pair) < 0.75, shifts
