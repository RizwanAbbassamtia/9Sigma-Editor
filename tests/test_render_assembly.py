"""Transitions v2 render pipeline (docs/TRANSITIONS-V2-CONTRACT.md section 3): the frame
layout of scene clips and cuts, the clip hashes and their reuse, a real render of a tiny
project at 480p with zoom_through, light_leak_warm and whip_left (frame count and length
from ffprobe, frames at every cut midpoint looked at), cancellation that keeps finished
clips, and the frame-provider hook the motion module plugs into. Media stays tiny."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sigma_editor.models.timeline import Timeline
from sigma_editor.render import assembly
from sigma_editor.render import ffmpeg as ff
from sigma_editor.render import scene_clips as sc
from sigma_editor.render.presets import load_render_config
from test_render_fixtures import (
    TMP_ROOT,
    make_render_tmp_fixture,
    requires_ffmpeg,
    write_project,
)

render_tmp = make_render_tmp_fixture()
FPS = 30
ZOOM_IN = {"preset": "zoom_in", "start_rect": [0, 0, 1024, 576],
           "end_rect": [102.4, 57.6, 819.2, 460.8]}
PAN_RIGHT = {"preset": "pan_right", "start_rect": [0, 0, 870.4, 489.6],
             "end_rect": [153.6, 0, 870.4, 489.6]}
HOLD = {"preset": "hold", "start_rect": [0, 0, 1024, 576], "end_rect": [0, 0, 1024, 576]}


def make_timeline(
    lengths: list[float],
    transitions: list[tuple[str, float]],
    *,
    images: list[str] | None = None,
    motions: list[dict[str, Any]] | None = None,
    gap_after: dict[int, float] | None = None,
) -> Timeline:
    scenes = []
    clock = 0.0
    for index, length in enumerate(lengths):
        start = clock
        end = clock + length
        image = images[index] if images else f"06_images/scene_{index % 3 + 1:02d}.png"
        motion = motions[index] if motions else (ZOOM_IN if index % 2 == 0 else PAN_RIGHT)
        kind, seconds = transitions[index]
        scenes.append({
            "index": index,
            "image": image,
            "start_s": round(start, 3),
            "end_s": round(end, 3),
            "motion": motion,
            "transition_out": {"type": kind, "duration_s": seconds},
        })
        clock = end + (gap_after or {}).get(index, 0.0)
    return Timeline.model_validate({
        "project_id": "p-assembly", "format": "long", "aspect": "16:9", "width": 854,
        "height": 480, "fps": FPS, "duration_s": round(scenes[-1]["end_s"], 3), "scenes": scenes,
    })


def layout_for(files: Any, timeline: Timeline) -> assembly.Layout:
    config = load_render_config()
    return assembly.plan_layout(timeline, config.proxy, folder=files.folder, config=config)


# Frame layout (no FFmpeg) ----------------------------------------------------------------------


def test_cuts_are_centred_on_the_scene_boundaries(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline(
        [2.0, 1.5, 2.5], [("zoom_through", 0.4), ("whip_left", 0.3), ("dip_black", 0.5)]
    )
    layout = layout_for(files, timeline)
    a, b, c = layout.scenes
    # 60 / 45 / 75 frames; cut 1 asks for 12 frames but a cut takes at most a quarter of the
    # shorter scene (45 / 4 -> 11): 5 before the boundary, 6 after; cut 2 = 9 (4 before, 5
    # after), the end fade = 15 frames inside scene 3.
    assert [cut.frames for cut in layout.cuts] == [11, 9, 15]
    assert [cut.label for cut in layout.cuts] == ["zoom_through", "whip_left", "fade to black"]
    assert (a.request.lead_in, a.request.tail_out, a.frames) == (0, 6, 66)
    assert (b.request.lead_in, b.request.tail_out, b.frames) == (5, 5, 55)
    assert (c.request.lead_in, c.request.tail_out, c.frames) == (4, 0, 79)
    # Keyframes where each body starts and where the outgoing overlap starts.
    assert a.keyframes == (55,) and b.keyframes == (11, 46) and c.keyframes == (9, 64)
    # Bodies: 60 - 5 = 55, 45 - 6 - 4 = 35, 75 - 5 - 15 = 55; the cuts fill the rest.
    assert layout.scene_frames == {1: (0, 55), 2: (66, 35), 3: (110, 55)}
    assert layout.total_frames == 180 == round(timeline.duration_s * FPS)
    # Joined: clip 1 = 0-65, cut 1 = 66-76, clip 2 = 77-131, cut 2 = 132-140, clip 3 =
    # 141-219, fade = 220-234; the overlaps the cuts replace are dropped.
    assert layout.drops == [(55, 65), (77, 87), (123, 131), (141, 149), (205, 219)]
    # The drop test is a balanced sum (FFmpeg refuses a flat sum of 100 terms or more).
    assert layout.select_filter() == (
        "select='not(((between(n\\,55\\,65)+between(n\\,77\\,87))+(between(n\\,123\\,131)+"
        "(between(n\\,141\\,149)+between(n\\,205\\,219)))))',setpts=N/(30*TB)"
    )
    assert layout.concat_text().splitlines() == [
        "ffconcat version 1.0",
        f"file '{a.file_name}'", f"file '{layout.cuts[0].file_name}'",
        f"file '{b.file_name}'", f"file '{layout.cuts[1].file_name}'",
        f"file '{c.file_name}'", f"file '{layout.cuts[2].file_name}'",
    ]
    # Frames in = frames out + the overlaps the cuts replace.
    joined = sum(job.frames for job in layout.scenes) + sum(cut.frames for cut in layout.cuts)
    assert joined - sum(hi - lo + 1 for lo, hi in layout.drops) == layout.total_frames


def test_short_and_missing_transitions_become_hard_cuts(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    # 0.03 s is one frame (below transitions.min_frames = 2); 0 s is a plain cut; the long
    # one is capped at a quarter of the shorter scene (12 frames -> 3); the last scene's whip
    # becomes the end fade.
    timeline = make_timeline(
        [1.0, 1.0, 0.4, 1.0],
        [("film_dissolve", 0.03), ("film_dissolve", 0.0), ("crosswarp", 2.0), ("whip_left", 0.2)],
    )
    layout = layout_for(files, timeline)
    assert [(cut.number, cut.label, cut.frames) for cut in layout.cuts] == [
        (3, "crosswarp", 3), (4, "fade to black", 6),
    ]
    assert layout.total_frames == 102
    timeline = make_timeline([1.0, 1.0], [("film_dissolve", 0.5), ("dip_white", 0.4)])
    assert layout_for(files, timeline).cuts[-1].label == "fade to white"
    assert layout_for(files, timeline).cuts[-1].fade_colour == (1.0, 1.0, 1.0)


def test_gaps_belong_to_the_scene_before_and_tiny_scenes_drop_out(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 1.0, 1.0], [("film_dissolve", 0.2)] * 3,
                             gap_after={0: 0.5})
    layout = layout_for(files, timeline)
    assert layout.scene_frames[1][1] + layout.cuts[0].frames // 2 == 45  # 1.5 s before scene 2
    assert layout.total_frames == 105
    tiny = make_timeline([1.0, 0.01, 1.0], [("film_dissolve", 0.2)] * 3)
    layout = layout_for(files, tiny)
    assert [job.number for job in layout.scenes] == [1, 3]
    assert layout.total_frames == 60


def test_missing_pictures_are_named_plainly(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 1.0], [("film_dissolve", 0.2)] * 2)
    timeline.scenes[1].image = "06_images/nope.png"
    with pytest.raises(ff.RenderError, match="picture for scene 2 is missing"):
        layout_for(files, timeline)


def test_clip_hashes_follow_exactly_what_each_clip_is_made_of(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    base = make_timeline([1.5, 1.5, 1.5], [("zoom_through", 0.4), ("whip_left", 0.3),
                                           ("dip_black", 0.5)])
    first = layout_for(files, base)
    assert [job.key for job in layout_for(files, base).scenes] == [j.key for j in first.scenes]
    assert len({job.key for job in first.scenes}) == 3 and len(first.scenes[0].key) == 16
    assert first.scenes[0].file_name == f"scene_01.{first.scenes[0].key}.mp4"
    assert first.cuts[0].file_name == f"cut_01.{first.cuts[0].key}.mp4"

    # Another transition type: the scenes stay, only that cut changes.
    other = base.model_copy(deep=True)
    other.scenes[0].transition_out.type = "light_leak_warm"
    changed = layout_for(files, other)
    assert [j.key for j in changed.scenes] == [j.key for j in first.scenes]
    assert [c.key == d.key for c, d in zip(changed.cuts, first.cuts, strict=True)] == [
        False, True, True]

    # Another move for scene 3: its clip, the cut into it and the end fade change.
    moved = base.model_copy(deep=True)
    moved.scenes[2].motion = moved.scenes[2].motion.model_validate(HOLD)
    changed = layout_for(files, moved)
    assert [c.key == d.key for c, d in zip(changed.scenes, first.scenes, strict=True)] == [
        True, True, False]
    assert [c.key == d.key for c, d in zip(changed.cuts, first.cuts, strict=True)] == [
        True, False, False]

    # A shorter cut 1 changes both clips around it (their overlaps shrink). A longer one asks
    # in vain: cut 1 is already at its cap, a quarter of the shorter scene.
    shorter = base.model_copy(deep=True)
    shorter.scenes[0].transition_out.duration_s = 0.3
    changed = layout_for(files, shorter)
    assert [c.key == d.key for c, d in zip(changed.scenes, first.scenes, strict=True)] == [
        False, False, True]
    longer = base.model_copy(deep=True)
    longer.scenes[0].transition_out.duration_s = 0.6
    assert [j.key for j in layout_for(files, longer).scenes] == [j.key for j in first.scenes]

    # A scene that moves in time (a new scene inserted before it) keeps its clip as long as
    # its picture, move, length and transitions are the same.
    shifted = make_timeline([1.0, 1.5, 1.5, 1.5], [("film_dissolve", 0.2), ("zoom_through", 0.4),
                                                    ("whip_left", 0.3), ("dip_black", 0.5)],
                            images=["06_images/scene_03.png", "06_images/scene_01.png",
                                    "06_images/scene_02.png", "06_images/scene_03.png"],
                            motions=[HOLD, ZOOM_IN, PAN_RIGHT, ZOOM_IN])
    moved_on = layout_for(files, shifted)
    assert moved_on.scenes[2].key == first.scenes[1].key
    # So do the cuts between unchanged clips: the whip (now cut 3, was cut 2) and the end
    # fade. Only the glitch, the light leak and the film burn depend on the cut's number.
    assert moved_on.cuts[2].key == first.cuts[1].key
    assert moved_on.cuts[3].key == first.cuts[2].key
    glitchy = base.model_copy(deep=True)
    glitchy.scenes[1].transition_out.type = "glitch_subtle"
    glitchy_shifted = shifted.model_copy(deep=True)
    glitchy_shifted.scenes[2].transition_out.type = "glitch_subtle"
    assert layout_for(files, glitchy_shifted).cuts[2].key != layout_for(files, glitchy).cuts[1].key

    # A replaced picture (new modification time) is a new clip; so is another preset.
    picture = files.folder / "06_images" / "scene_02.png"
    stat = picture.stat()
    os.utime(picture, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert layout_for(files, base).scenes[1].key != first.scenes[1].key
    config = load_render_config()
    big = assembly.plan_layout(base, config.presets["720p"], folder=files.folder, config=config)
    assert big.scenes[0].key != first.scenes[0].key and big.clips_dir == "clips/720p"


def test_clip_encoding_never_slower_than_the_final(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_render_config()
    assert sc.clip_encoding_for(config.presets["1080p"]) == sc.ClipEncoding("fast", 14)
    assert sc.clip_encoding_for(config.presets["1080p"], "slow").x264_preset == "fast"
    assert sc.clip_encoding_for(config.presets["1080p"], "ultrafast").x264_preset == "ultrafast"
    assert sc.clip_encoding_for(config.proxy, "slow") == sc.ClipEncoding("ultrafast", 18)
    args = sc.ClipEncoding().args()
    for flag in ("-crf", "-pix_fmt", "-video_track_timescale", "-colorspace"):
        assert flag in args
    assert sc.keyframe_args([0, 12, 47, 12]) == ["-force_key_frames", "expr:eq(n,12)+eq(n,47)"]
    # Memory: each clip encoder gets its share of the CPUs (x264 alone takes 1.5 threads per
    # CPU, each holding frames) and looks 10 frames ahead instead of 30: half the memory of
    # a 1080p clip encoder. The pictures, and so the clips' hash, stay the same.
    fast = sc.clip_encoding_for(config.presets["1080p"])
    assert fast.threads == sc.encoder_threads(sc.clip_workers((1920, 1080)))
    assert 2 <= fast.threads <= sc.MAX_ENCODER_THREADS
    assert fast.token() == sc.ClipEncoding("fast", 14).token()
    args = fast.args()
    assert args[args.index("-threads") + 1] == str(fast.threads)
    assert args[args.index("-rc-lookahead") + 1] == "10"
    assert "-rc-lookahead" not in sc.ClipEncoding("ultrafast", 18).args()  # never raised
    assert "-threads" not in sc.ClipEncoding("fast", 14).args()  # 0: x264 decides
    # Two clips at a time, one at a time above 1080p (a 4K encoder alone takes a gigabyte).
    assert sc.clip_workers((1920, 1080)) == min(2, os.cpu_count() or 2)
    assert sc.clip_workers((3840, 2160)) == sc.clip_workers((2160, 3840)) == 1
    four_k = sc.clip_encoding_for(config.presets["2160p"])
    assert four_k.threads == sc.encoder_threads(1) >= fast.threads
    monkeypatch.setattr(sc, "_clip_settings", lambda: {"x264_preset": "veryfast", "crf": 16,
                                                       "workers": 1})
    assert sc.clip_encoding_for(config.presets["720p"]) == sc.ClipEncoding("veryfast", 16)
    assert sc.clip_workers() == 1
    monkeypatch.setattr(sc, "_clip_settings", lambda: {"workers": 3, "workers_large": 2})
    assert sc.clip_workers((1280, 720)) == min(3, os.cpu_count() or 2)
    assert sc.clip_workers((3840, 2160)) == min(2, os.cpu_count() or 2)


# A real render -------------------------------------------------------------------------------


def _frames_at(path: Path, numbers: list[int], size: tuple[int, int]) -> np.ndarray:
    """RGB frames ``numbers`` (0-based) of a video, in order."""
    expr = "+".join(f"eq(n\\,{n})" for n in numbers)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
         f"select='{expr}',{sc.TO_RGB},format=rgb24", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, size[1], size[0], 3)


def _video_stream(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=nb_frames,duration,width,height,color_space", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return json.loads(out)["streams"][0]


@pytest.fixture(scope="module")
def rendered() -> Iterator[dict[str, Any]]:
    """Four pictures, three real cuts (zoom_through, light_leak_warm, whip_left) and the
    closing dip to black, rendered once at 480p for the tests below."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("FFmpeg is not installed")
    root = TMP_ROOT / f"asm-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        files = write_project(root, music=False, popups=False)
        timeline = make_timeline(
            [1.6, 1.4, 1.4, 1.6],
            [("zoom_through", 0.4), ("light_leak_warm", 0.4), ("whip_left", 0.3),
             ("dip_black", 0.5)],
            images=["06_images/scene_01.png", "06_images/scene_02.png",
                    "06_images/scene_03.png", "06_images/scene_01.png"],
            motions=[ZOOM_IN, PAN_RIGHT, ZOOM_IN, HOLD],
        )
        config = load_render_config()
        plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
        events: list[tuple[str, float]] = []
        result = ff.run_render(
            plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
            log_path=files.edit_dir / "render.log",
            progress=lambda message, pct: events.append((message, pct)),
        )
        yield {"files": files, "timeline": timeline, "plan": plan, "result": result,
               "events": events, "config": config}
    finally:
        shutil.rmtree(root, ignore_errors=True)


@requires_ffmpeg
def test_render_length_matches_the_timeline_to_the_frame(rendered: dict[str, Any]) -> None:
    result, plan, timeline = rendered["result"], rendered["plan"], rendered["timeline"]
    video = _video_stream(result.path)
    assert int(video["nb_frames"]) == plan.layout.total_frames == round(timeline.duration_s * 30)
    assert abs(float(video["duration"]) - timeline.duration_s) <= 1 / 30
    assert abs(result.duration_s - timeline.duration_s) <= 1 / 30 + 0.03  # audio rounding
    assert (int(video["width"]), int(video["height"])) == (854, 480)
    assert video.get("color_space") == "bt709"
    assert (result.scene_clips_total, result.scene_clips_reused) == (4, 0)
    assert (result.cuts_total, result.cuts_reused) == (4, 0)
    assert set(result.phase_seconds) == {"scenes", "cuts", "assembly"}


@requires_ffmpeg
def test_every_cut_midpoint_shows_its_transition(rendered: dict[str, Any]) -> None:
    layout: assembly.Layout = rendered["plan"].layout
    path = rendered["result"].path
    cuts = layout.cuts[:3]
    numbers: list[int] = []
    for cut in cuts:
        body_start, body_len = layout.scene_frames[cut.a.number]
        start = body_start + body_len  # the cut's first frame
        numbers += [start - 1, start + cut.frames // 2, start + cut.frames]
    frames = _frames_at(path, numbers, (854, 480)).astype(np.float32)
    assert len(frames) == 9
    for i, cut in enumerate(cuts):
        before, middle, after = frames[3 * i: 3 * i + 3]
        assert middle.mean() > 25 and middle.std() > 8, cut.label  # not black, not flat
        assert np.abs(middle - before).mean() > 4, cut.label  # the change is under way ...
        assert np.abs(middle - after).mean() > 4, cut.label  # ... and not finished
    zoom, leak, whip = (frames[1], frames[4], frames[7])
    # light leak: brighter and warmer than both neighbours.
    neighbours = (frames[3] + frames[5]) / 2
    assert leak.mean() > neighbours.mean() + 5
    assert (leak[..., 0] - leak[..., 2]).mean() > (neighbours[..., 0] - neighbours[..., 2]).mean()
    # whip: smeared sideways, not up and down. Strong steps (over 24 levels) are the
    # picture's own edges; the faint banding the proxy encoder leaves on the fixture's
    # gradients does not count.
    def edges(img: np.ndarray, axis: int) -> float:
        return float((np.abs(np.diff(img.mean(axis=2), axis=axis)) > 24).mean())
    assert edges(whip, 1) < edges(frames[6], 1) / 2  # the sideways edges are gone ...
    assert edges(whip, 0) > edges(frames[6], 0) / 2  # ... the horizontal lines stay sharp

    def energy(img: np.ndarray, axis: int) -> float:
        return float(np.abs(np.diff(img.mean(axis=2), axis=axis)).mean())
    # zoom_through: softer than the picture before it (radial blur and crossfade).
    assert energy(zoom, 1) + energy(zoom, 0) < energy(frames[0], 1) + energy(frames[0], 0)


@requires_ffmpeg
def test_the_render_ends_on_black_and_logs_every_clip(rendered: dict[str, Any]) -> None:
    layout: assembly.Layout = rendered["plan"].layout
    last = _frames_at(rendered["result"].path, [layout.total_frames - 1], (854, 480))
    assert last.mean() < 3
    log = (rendered["files"].edit_dir / "render.log").read_text(encoding="utf-8")
    for number in (1, 2, 3, 4):
        assert f"scene {number:02d}: drawn in" in log
    # 1.6 / 1.4 / 1.4 / 1.6 s: the 0.4 s cuts and the 0.5 s fade are capped at a quarter of
    # the shorter scene (0.35 s -> 11 frames, 0.4 s -> 12 frames); the 0.3 s whip fits (9).
    assert "cut 01 (zoom_through, 11 frames): drawn in" in log
    assert "cut 02 (light_leak_warm, 11 frames): drawn in" in log
    assert "cut 03 (whip_left, 9 frames): drawn in" in log
    assert "cut 04 (fade to black, 12 frames): drawn in" in log
    assert "phases: scene clips" in log and "exit code 0" in log
    clips = rendered["files"].edit_dir / "clips" / "proxy"
    assert not list(clips.glob("*.part.mp4"))
    assert (clips / "concat.txt").read_text(encoding="utf-8").startswith("ffconcat version 1.0")


@requires_ffmpeg
def test_progress_runs_through_three_phases_to_100(rendered: dict[str, Any]) -> None:
    events = rendered["events"]
    assert all(message.startswith("Rendering proxy ") for message, _ in events)
    values = [pct for _, pct in events]
    assert values == sorted(values) and values[0] == 0.0 and values[-1] == 100.0
    assert any(0 < v < 55 for v in values) and 55.0 in values and 70.0 in values
    assert any(70 < v < 100 for v in values)


@requires_ffmpeg
def test_a_second_render_reuses_the_clips_and_an_edit_redraws_only_its_part(
    rendered: dict[str, Any],
) -> None:
    files, timeline, config = rendered["files"], rendered["timeline"], rendered["config"]
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    again = ff.run_render(plan, files.edit_dir / "proxy_again.mp4", work_dir=files.edit_dir,
                          log_path=files.edit_dir / "render.log")
    assert (again.scene_clips_reused, again.cuts_reused) == (4, 4)
    assert again.duration_s == pytest.approx(rendered["result"].duration_s, abs=0.01)

    edited = timeline.model_copy(deep=True)
    edited.scenes[3].motion = edited.scenes[3].motion.model_validate(ZOOM_IN)  # scene 4 only
    edited.scenes[0].transition_out.type = "film_dissolve"  # and cut 1's look
    plan = ff.build_plan(edited, config.proxy, folder=files.folder, config=config)
    third = ff.run_render(plan, files.edit_dir / "proxy_edit.mp4", work_dir=files.edit_dir,
                          log_path=files.edit_dir / "render.log")
    assert (third.scene_clips_total, third.scene_clips_reused) == (4, 3)
    # cut 1 (new look), cut 3 (into the new scene 4) and the end fade are drawn again.
    assert (third.cuts_total, third.cuts_reused) == (4, 1)
    clips = files.edit_dir / "clips" / "proxy"
    assert len(list(clips.glob("scene_*.mp4"))) == 4  # the old scene 4 clip was tidied away
    assert int(_video_stream(third.path)["nb_frames"]) == plan.layout.total_frames


@requires_ffmpeg
def test_cancelling_keeps_the_finished_clips(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 12.0, 12.0], [("film_dissolve", 0.3)] * 3)
    config = load_render_config()
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    clips = files.edit_dir / "clips" / "proxy"
    cancel = threading.Event()

    def finished(pattern: str) -> list[Path]:
        return [p for p in clips.glob(pattern) if not p.name.endswith(".part.mp4")]

    def progress(_message: str, _pct: float) -> None:
        if finished("scene_01.*.mp4"):
            cancel.set()

    with pytest.raises(ff.RenderCancelled, match="proxy render was stopped"):
        ff.run_render(plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
                      log_path=files.edit_dir / "render.log", progress=progress, cancel=cancel)
    assert not (files.edit_dir / "proxy.mp4").exists()
    assert len(finished("scene_01.*.mp4")) == 1  # finished: kept for next time
    assert not list(clips.glob("*.part.mp4"))  # unfinished: removed
    assert "finished clips are kept" in (files.edit_dir / "render.log").read_text("utf-8")


def _box(kind: bytes, payload: bytes = b"", *, large: bool = False) -> bytes:
    if large:
        return (1).to_bytes(4, "big") + kind + (16 + len(payload)).to_bytes(8, "big") + payload
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def test_only_whole_mp4_clips_count_as_cached(render_tmp: Path) -> None:
    whole = _box(b"ftyp", b"isom\0\0\2\0") + _box(b"moov", b"x" * 40) + _box(b"mdat", b"y" * 99)
    cases = {
        "whole.mp4": (whole, True),
        "large.mp4": (_box(b"ftyp", b"isom") + _box(b"moov", b"x" * 9)
                      + _box(b"mdat", b"y" * 50, large=True), True),
        "to_end.mp4": (_box(b"ftyp", b"isom") + _box(b"moov", b"x") + (0).to_bytes(4, "big")
                       + b"mdat" + b"y" * 70, True),
        "cut_short.mp4": (whole[:-30], False),
        "zeros.mp4": (b"\0" * len(whole), False),
        "no_moov.mp4": (_box(b"ftyp", b"isom") + _box(b"mdat", b"y" * 20), False),
        "not_mp4.mp4": (b"not a video at all, just some text", False),
        "empty.mp4": (b"", False),
    }
    for name, (data, expected) in cases.items():
        path = render_tmp / name
        path.write_bytes(data)
        assert sc.mp4_is_whole(path) is expected, name
    assert sc.mp4_is_whole(render_tmp / "missing.mp4") is False


def test_a_clip_that_cannot_be_read_is_removed_and_named(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    layout = layout_for(files, make_timeline([1.0, 1.0], [("film_dissolve", 0.2)] * 2))
    clips = render_tmp / "clips"
    clips.mkdir()
    cut = layout.cuts[0]
    bad = clips / cut.b.file_name
    bad.write_bytes(b"\0" * 64)
    error = assembly._damaged_clip(cut, clips, sc.ClipReadError(bad, "Invalid data"))
    assert "scene 2 could not be read (Invalid data), so it was removed" in str(error)
    assert not bad.exists()


@requires_ffmpeg
def test_damaged_cached_clips_are_drawn_again(render_tmp: Path) -> None:
    """A clip cut short (a crash) or full of zeros (a power cut just after it was saved) is
    not reused: it is removed and drawn again, and the render log says so."""
    files = write_project(render_tmp, music=False, popups=False)
    # 3 s: FFmpeg's loudnorm turns less than 3 s of the fixture's silent voice into NaN.
    timeline = make_timeline([1.5, 1.5], [("film_dissolve", 0.2)] * 2)
    config = load_render_config()
    clips = files.edit_dir / "clips" / "proxy"

    def render(name: str) -> ff.RenderResult:
        plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
        return ff.run_render(plan, files.edit_dir / name, work_dir=files.edit_dir,
                             log_path=files.edit_dir / "render.log")

    first = render("proxy.mp4")
    assert first.scene_clips_reused == 0
    scene_1, scene_2 = sorted(clips.glob("scene_*.mp4"))
    assert sc.mp4_is_whole(scene_1) and sc.mp4_is_whole(scene_2)
    scene_2.write_bytes(scene_2.read_bytes()[: scene_2.stat().st_size // 2])  # cut short
    scene_1.write_bytes(b"\0" * scene_1.stat().st_size)  # zeroed
    again = render("proxy_again.mp4")
    assert (again.scene_clips_total, again.scene_clips_reused) == (2, 0)
    assert again.cuts_reused == again.cuts_total == 2  # the cuts' own clips were fine
    assert sc.mp4_is_whole(scene_1) and sc.mp4_is_whole(scene_2)  # drawn again, same names
    assert again.duration_s == pytest.approx(first.duration_s, abs=0.01)
    log = (files.edit_dir / "render.log").read_text(encoding="utf-8")
    assert f"{scene_2.name} was damaged and has been removed" in log


class SolidProvider:
    """A frame provider that paints scene 2 (and only scene 2) one flat colour."""

    name = "solid-test"
    colour = (40, 160, 90)

    def __init__(self) -> None:
        self.requests: list[sc.SceneFrameRequest] = []

    def cache_token(self, request: sc.SceneFrameRequest) -> str | None:
        return "v1" if request.index == 1 else None

    def frames(self, request: sc.SceneFrameRequest) -> Iterator[np.ndarray]:
        self.requests.append(request)
        frame = np.empty((request.size[1], request.size[0], 3), np.uint8)
        frame[:] = self.colour
        for _ in range(request.frames - 3):  # a little short: the last frame is held
            yield frame


@requires_ffmpeg
def test_a_frame_provider_draws_the_scenes_it_takes(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 1.0, 1.0], [("film_dissolve", 0.2)] * 3)
    provider = SolidProvider()
    sc.register_frame_provider(provider)
    try:
        assert provider in sc.frame_providers()
        config = load_render_config()
        plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
        jobs = plan.layout.scenes
        assert [job.renderer for job in jobs] == ["zoompan", "solid-test:v1", "zoompan"]
        result = ff.run_render(plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
                               log_path=files.edit_dir / "render.log")
    finally:
        sc.unregister_frame_provider(provider)
    assert sc.frame_providers() == [p for p in sc.frame_providers() if p is not provider]
    request = provider.requests[0]
    assert request.index == 1 and request.size == (854, 480) and request.preset_id == "proxy"
    assert request.frames == jobs[1].frames and request.lead_in == 3 and request.tail_out == 3
    start, length = plan.layout.scene_frames[2]
    middle = _frames_at(result.path, [start + length // 2], (854, 480))[0].astype(int)
    assert np.abs(middle - SolidProvider.colour).max() <= 4
    # Without the provider the scene is drawn by zoompan again (a different clip).
    plan = ff.build_plan(timeline, load_render_config().proxy, folder=files.folder,
                         config=load_render_config())
    assert plan.layout.scenes[1].renderer == "zoompan"
    assert plan.layout.scenes[1].key != jobs[1].key


class BrokenProvider(SolidProvider):
    """Takes scene 2, draws two frames, then fails (a bug, or numpy out of memory)."""

    name = "broken-test"

    def frames(self, request: sc.SceneFrameRequest) -> Iterator[np.ndarray]:
        frame = np.zeros((request.size[1], request.size[0], 3), np.uint8)
        yield frame
        yield frame
        raise RuntimeError("boom")


@requires_ffmpeg
def test_a_failing_provider_falls_back_without_poisoning_the_cache(render_tmp: Path) -> None:
    """A scene whose provider fails is drawn by zoompan and saved under zoompan's own hash
    (and so are the cuts next to it): the provider's hash never names a zoompan clip, so the
    provider is asked again next time and no cut shows frames its scene no longer has."""
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 1.0, 1.0], [("film_dissolve", 0.2)] * 3)
    config = load_render_config()
    plain = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config).layout

    def render(provider: SolidProvider, name: str) -> tuple[assembly.Layout, ff.RenderResult]:
        sc.register_frame_provider(provider, first=True)
        try:
            plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
            assert plan.layout.scenes[1].renderer == f"{provider.name}:v1"
            result = ff.run_render(plan, files.edit_dir / name, work_dir=files.edit_dir,
                                   log_path=files.edit_dir / "render.log")
        finally:
            sc.unregister_frame_provider(provider)
        return plan.layout, result

    layout, first = render(BrokenProvider(), "proxy.mp4")
    assert [job.renderer for job in layout.scenes] == ["zoompan"] * 3
    assert [job.key for job in layout.scenes] == [job.key for job in plain.scenes]
    assert [cut.key for cut in layout.cuts] == [cut.key for cut in plain.cuts]
    assert first.scene_clips_total == 3 and first.duration_s == pytest.approx(3.0, abs=0.05)
    log = (files.edit_dir / "render.log").read_text(encoding="utf-8")
    assert "(zoompan, broken-test:v1 failed: RuntimeError: boom, " in log
    clips = files.edit_dir / "clips" / "proxy"
    assert sorted(p.name for p in clips.glob("scene_02.*.mp4")) == [plain.scenes[1].file_name]

    # Next time the provider works: scene 2 and the two cuts around it are drawn again, the
    # rest is reused, and the middle of scene 2 is the provider's colour.
    layout, second = render(SolidProvider(), "proxy_again.mp4")
    assert (second.scene_clips_reused, second.cuts_reused) == (2, 1)
    start, length = layout.scene_frames[2]
    middle = _frames_at(second.path, [start + length // 2], (854, 480))[0].astype(int)
    assert np.abs(middle - SolidProvider.colour).max() <= 4


# Running short of memory -----------------------------------------------------------------------


def test_memory_failures_are_recognised() -> None:
    assert ff.looks_out_of_memory("x264 [error]: malloc of size 6200704 failed")
    assert ff.looks_out_of_memory("[h264 @ 0x1] Error while decoding: Cannot allocate memory")
    assert ff.looks_out_of_memory("[WinError 1455] The paging file is too small for this "
                                  "operation to complete")
    assert not ff.looks_out_of_memory("Invalid data found when processing input")
    assert not ff.looks_out_of_memory("Error submitting video frame to the encoder")
    assert not ff.looks_out_of_memory(None)
    paging = OSError(22, "The paging file is too small for this operation to complete", None,
                     1455)
    assert ff.oserror_is_memory(paging) and ff.oserror_is_memory(MemoryError())
    assert not ff.oserror_is_memory(FileNotFoundError(2, "No such file or directory"))
    assert ff.out_of_memory_message("1080p") == (
        "The computer ran out of memory while rendering 1080p. Close some programs and try "
        "again."
    )


def test_a_child_that_cannot_allocate_reads_as_out_of_memory() -> None:
    import sys

    say = "import sys; sys.stderr.write('x264 [error]: malloc of size 6200704 failed\\n'); " \
          "sys.exit(1)"
    child = sc._Watched([sys.executable, "-c", say])
    assert child.wait() == 1
    error = child.failure("FFmpeg could not draw scene 2 (exit code 1)")
    assert isinstance(error, ff.RenderOutOfMemory) and "malloc of size 6200704" in str(error)
    plain = sc._Watched([sys.executable, "-c", "import sys; sys.stderr.write('Invalid data'); "
                                               "sys.exit(1)"])
    assert plain.wait() == 1
    error = plain.failure("FFmpeg could not draw scene 2 (exit code 1)")
    assert type(error) is ff.RenderError and str(error).endswith(": Invalid data")


class _FailedDecoder:
    """Stands in for an FFmpeg decoder that has already failed, ``said`` on its stderr."""

    said: tuple[str, ...] = ()

    def __init__(self, command: list[str], **_kwargs: Any) -> None:
        import io
        from collections import deque
        from types import SimpleNamespace

        self.command = command
        self.lines = deque(self.said)
        self.killed = threading.Event()
        self.process = SimpleNamespace(stdout=io.BytesIO(b""))

    def kill(self) -> None:
        pass

    def wait(self) -> int:
        return 1

    tail = sc._Watched.tail
    out_of_memory = sc._Watched.out_of_memory
    failure = sc._Watched.failure


def test_a_decoder_out_of_memory_keeps_the_clip(render_tmp: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading a clip back fails with "Cannot allocate memory": the clip is fine, so this is
    not a damaged clip (which would be removed); anything else still is."""
    clip = render_tmp / "scene_01.0123456789abcdef.mp4"
    clip.write_bytes(b"a clip")
    decoder = type("Decoder", (_FailedDecoder,),
                   {"said": ("[h264 @ 0x1] get_buffer() failed: Cannot allocate memory",)})
    monkeypatch.setattr(sc, "_Watched", decoder)
    with pytest.raises(ff.RenderOutOfMemory):
        list(sc.decode_frames(clip, (16, 16), ffmpeg="ffmpeg", fps=30))
    decoder = type("Decoder", (_FailedDecoder,), {"said": ("moov atom not found",)})
    monkeypatch.setattr(sc, "_Watched", decoder)
    with pytest.raises(sc.ClipReadError, match="moov atom not found"):
        list(sc.decode_frames(clip, (16, 16), ffmpeg="ffmpeg", fps=30))
    assert clip.is_file()


@requires_ffmpeg
def test_running_out_of_memory_reads_plainly_and_keeps_the_clips(
    render_tmp: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.5, 1.5], [("film_dissolve", 0.2)] * 2)
    config = load_render_config()
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)

    def no_memory(*_args: Any, **_kwargs: Any) -> None:
        raise ff.RenderOutOfMemory("FFmpeg could not write cut_01 (exit code 1): x264 [error]: "
                                   "malloc of size 6200704 failed")

    monkeypatch.setattr(assembly, "render_cut", no_memory)
    with pytest.raises(ff.RenderOutOfMemory) as caught:
        ff.run_render(plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
                      log_path=files.edit_dir / "render.log")
    assert str(caught.value) == ff.out_of_memory_message("proxy")
    clips = files.edit_dir / "clips" / "proxy"
    assert len(list(clips.glob("scene_*.mp4"))) == 2  # the finished clips are kept
    assert not list(clips.glob("*.part.mp4"))
    assert not (files.edit_dir / "proxy.mp4").exists()
    log = (files.edit_dir / "render.log").read_text(encoding="utf-8")
    assert "failed: out of memory (RenderOutOfMemory: " in log and "malloc of size" in log


# The edit stage around it ---------------------------------------------------------------------


@requires_ffmpeg
def test_the_editor_payload_says_what_was_reused_and_when_parallax_fell_back(
    render_tmp: Path,
) -> None:
    from sigma_editor.edit.renderer import prepare_depth_maps, read_state
    from test_render_fixtures import apply_edits, make_ctx, run_edit

    files = write_project(render_tmp, music=False, popups=False)
    first = run_edit(make_ctx(files, edits={"presets": []}))  # proxy only
    assert first.rendered == ["proxy"]
    assert first.needs_review_payload["render_notes"] == [
        "Proxy: reused 0 of 3 scene clips and 0 of 3 transitions."
    ]
    timeline = Timeline.model_validate_json(
        (files.edit_dir / "timeline.json").read_text(encoding="utf-8")
    )
    assert prepare_depth_maps(files.folder, timeline, None) == (None, "")

    # The editor turns scene 3 into a 2.5D parallax move; this test app has no depth model.
    edited = timeline.model_copy(deep=True)
    edited.scenes[2].motion = edited.scenes[2].motion.model_validate(
        {**HOLD, "style": "parallax"}
    )
    result = apply_edits(make_ctx(files, edits={"timeline": edited.model_dump(mode="json")}))
    assert result is not None and result.rendered == ["proxy"]
    payload = result.needs_review_payload
    assert payload["render_notes"][0] == (
        "Proxy: reused 2 of 3 scene clips and 1 of 3 transitions."
    )
    assert payload["render_notes"][1] == (
        "2.5D parallax was not available for 1 scene, so they use the Ken Burns move instead."
    )
    proxy = next(row for row in payload["renders"] if row["preset"] == "proxy")
    assert proxy["reuse_note"] == "Reused 2 of 3 scene clips." and proxy["status"] == "done"
    state = read_state(files.edit_dir)["renders"]["proxy"]
    assert state["parallax_fallbacks"] == 1 and set(state["phase_seconds"]) == {
        "scenes", "cuts", "assembly"}
    assert any("Ken Burns" in warning or "depth" in warning.lower()
               for warning in payload["warnings"]), payload["warnings"]


def test_the_timeline_takes_the_motion_style_of_the_scene_plan_then_the_settings(
    render_tmp: Path,
) -> None:
    """Motion v2: a scene plan's own motion style wins, then Settings > Render, then
    config/render.yaml. Pure: the timeline is built, nothing is rendered."""
    from types import SimpleNamespace

    from sigma_editor.edit.builder import rebuild_timeline, write_scene_plan
    from sigma_editor.render.motion import load_motion_config
    from test_render_fixtures import make_ctx

    files = write_project(render_tmp, music=False, popups=False)

    def settings_with(style: str) -> Any:
        return make_ctx(files, render={"default_presets": ["720p"], "motion_style": style}).settings

    def styles(settings: Any, plan_style: str | None) -> set[str]:
        plan = files.storyboard.model_copy(update={"motion_style": plan_style})
        write_scene_plan(files.folder, plan)
        timeline, _warnings = rebuild_timeline(make_ctx(files).project, settings)
        return {scene.motion.style for scene in timeline.scenes}

    assert styles(settings_with("parallax"), None) == {"parallax"}
    assert styles(settings_with("parallax"), "ken_burns") == {"ken_burns"}
    assert styles(settings_with("ken_burns"), "parallax") == {"parallax"}
    assert styles(settings_with("ken_burns"), None) == {"ken_burns"}
    # Without a setting at all, config/render.yaml decides.
    bare = SimpleNamespace(render=SimpleNamespace(default_presets=["720p"]))
    assert styles(bare, None) == {load_motion_config().style}
    assert styles(bare, "parallax") == {"parallax"}


# Review fixes: long videos, cut length and memory, one render at a time ----------------------


def _nesting(expression: str) -> int:
    depth = deepest = 0
    for char in expression:
        if char == "(":
            depth += 1
            deepest = max(deepest, depth)
        elif char == ")":
            depth -= 1
    return deepest


def test_drop_ranges_are_joined_and_summed_as_a_balanced_tree() -> None:
    assert assembly.merge_ranges([(10, 14), (0, 3), (15, 20), (4, 4), (30, 31), (9, 8)]) == [
        (0, 4), (10, 20), (30, 31)]
    assert assembly.frames_in_expression([]) == "0"
    assert assembly.frames_in_expression([(5, 9)]) == "between(n\\,5\\,9)"
    ranges = [(i * 10, i * 10 + 2) for i in range(400)]
    expression = assembly.frames_in_expression(ranges)
    assert expression.count("between(") == 400 and expression.count("+") == 399
    # A flat sum would nest 400 deep for FFmpeg; the tree is log2(400) + between() deep.
    assert _nesting(expression) <= 11


def _tiny_long_timeline(folder: Path, scenes: int) -> Timeline:
    from PIL import Image

    (folder / "06_images").mkdir(parents=True, exist_ok=True)
    for i in range(4):
        Image.new("RGB", (64, 36), (40 + 50 * i, 90, 160 - 30 * i)).save(
            folder / "06_images" / f"s{i}.png")
    return make_timeline([0.3] * scenes, [("film_dissolve", 0.1)] * scenes,
                         images=[f"06_images/s{i % 4}.png" for i in range(scenes)],
                         motions=[HOLD] * scenes)


@requires_ffmpeg
def test_the_drop_test_of_an_80_scene_video_runs_in_ffmpeg(render_tmp: Path) -> None:
    """80 scenes with a transition on every cut need 159 drop ranges. FFmpeg 9 refuses a flat
    sum of 100 terms or more ("Cannot allocate memory"); the balanced sum must parse and keep
    exactly the frames outside the ranges. Numbered frames (16 x 16) through the real select."""
    config = load_render_config()
    timeline = _tiny_long_timeline(render_tmp, 80)
    layout = assembly.plan_layout(timeline, config.proxy, folder=render_tmp, config=config)
    assert len(layout.drops) == 159 and len(layout.cuts) == 80
    joined = sum(item.frames for item in layout.order)
    select = layout.select_filter().split(",setpts=")[0]
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info", "-f", "lavfi", "-i",
         f"color=c=black:s=16x16:r=30,trim=end_frame={joined}",
         "-vf", f"{select},showinfo", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    assert completed.returncode == 0, completed.stderr[-600:]
    kept = [int(m) for m in re.findall(r"\bn:\s*\d+\s+pts:\s*(\d+)", completed.stderr)]
    dropped = {n for lo, hi in layout.drops for n in range(lo, hi + 1)}
    assert kept == [n for n in range(joined) if n not in dropped]
    assert len(kept) == layout.total_frames == round(timeline.duration_s * FPS)


def test_a_refused_filter_expression_is_not_reported_as_out_of_memory() -> None:
    refused = (
        "[Parsed_select_0 @ 0x1] Error while parsing expression 'not(between(n,0,2)+...'\n"
        "[AVFilterGraph @ 0x2] Error initializing filters\nError : Cannot allocate memory\n"
    )
    assert not ff.looks_out_of_memory(refused)
    assert ff.looks_out_of_memory("[h264 @ 0x1] get_buffer() failed: Cannot allocate memory")
    assert ff.looks_out_of_memory(refused + "x264 [error]: malloc of size 6200704 failed")


def test_cut_lengths_are_capped_on_the_server_too(render_tmp: Path) -> None:
    """A replaced or hand-edited timeline skips the builder's clipping: the layout applies the
    same quarter-of-the-shorter-scene rule and never makes a cut longer than 3 s."""
    from sigma_editor.models.storyboard import SceneTransition
    from sigma_editor.models.timeline import TimelineTransition
    from sigma_editor.models.transitions import MAX_TRANSITION_S

    assert TimelineTransition(type="film_dissolve", duration_s=30).duration_s == MAX_TRANSITION_S
    assert SceneTransition(type="film_dissolve", duration_s=15.0).duration_s == MAX_TRANSITION_S
    assert SceneTransition(type="film_dissolve", duration_s=0.7).duration_s == 0.7
    files = write_project(render_tmp, music=False, popups=False)
    long = make_timeline([30.0, 30.0], [("film_dissolve", 30.0), ("dip_black", 30.0)])
    assert long.scenes[0].transition_out.duration_s == MAX_TRANSITION_S  # the model's cap
    # Past the model (assignment is not validated): the layout still caps it at 3 s.
    long.scenes[0].transition_out.duration_s = 15.0
    long.scenes[1].transition_out.duration_s = 15.0
    assert [cut.frames for cut in layout_for(files, long).cuts] == [90, 90]
    short = make_timeline([2.0, 1.0], [("film_dissolve", 0.9), ("dip_black", 0.9)])
    # A quarter of the shorter scene: 1.0 s -> 0.25 s = 7.5 frames (8 with the builder's
    # rounding leeway) for the cut, the same for the end fade of the 1 s scene.
    assert [cut.frames for cut in layout_for(files, short).cuts] == [8, 8]


class _FakeDecoders:
    produced: dict[str, int] = {"a": 0, "b": 0}
    calls: list[dict[str, Any]] = []

    @classmethod
    def decode(cls, path: Path, size: tuple[int, int], *, ffmpeg: str, fps: int,
               start_frame: int = 0, count: int | None = None, stop: Any = None
               ) -> Iterator[np.ndarray]:
        which = "a" if start_frame > 0 else "b"
        cls.calls.append({"which": which, "start_frame": start_frame, "count": count})
        for _ in range(int(count or 0)):
            cls.produced[which] += 1
            yield np.full((size[1], size[0], 3), 90 if which == "a" else 160, np.uint8)


class _CountingWriter:
    """Stands in for the transition clip's encoder: notes how far each decoder had got."""

    last: _CountingWriter | None = None

    def __init__(self, path: Path, size: tuple[int, int], fps: int, encoding: Any, *,
                 ffmpeg: str, stop: Any = None, keyframes: Any = ()) -> None:
        self.path = path
        self.ahead: list[tuple[int, int]] = []
        _CountingWriter.last = self

    def write(self, frame: np.ndarray) -> None:
        self.ahead.append((_FakeDecoders.produced["a"], _FakeDecoders.produced["b"]))

    def close(self) -> None:
        pass

    def abort(self) -> None:
        pass


def test_a_cut_holds_one_frame_of_each_clip_at_a_time(render_tmp: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """The outgoing overlap is read frame by frame, side by side with the incoming one, so a
    long cut never holds its whole overlap in memory (it used to: 90 frames at 1080p was half
    a gigabyte per cut)."""
    files = write_project(render_tmp, music=False, popups=False)
    layout = layout_for(files, make_timeline([4.0, 4.0], [("film_dissolve", 1.0),
                                                           ("dip_black", 0.5)]))
    cut = layout.cuts[0]
    assert cut.frames == 30
    _FakeDecoders.produced = {"a": 0, "b": 0}
    _FakeDecoders.calls = []
    monkeypatch.setattr(assembly, "decode_frames", _FakeDecoders.decode)
    monkeypatch.setattr(assembly, "FrameWriter", _CountingWriter)
    assembly.render_cut(cut, render_tmp, ffmpeg="ffmpeg", fps=FPS, size=(32, 18),
                        stop=lambda: False, on_frame=lambda: None)
    assert _CountingWriter.last is not None
    ahead = _CountingWriter.last.ahead
    assert len(ahead) == 30
    assert all(a == k + 1 and b == k + 1 for k, (a, b) in enumerate(ahead)), ahead[:3]
    assert {"which": "a", "start_frame": cut.a.frames - 30, "count": 30} in _FakeDecoders.calls


@requires_ffmpeg
def test_reading_an_overlap_starts_on_its_first_frame_wherever_the_seek_lands(
    render_tmp: Path,
) -> None:
    """Frames numbered by their grey level, keyframes at 20 and 50: from 50 the seek lands on
    its keyframe, from 45 on the one at 20; either way the first frame is the one asked for."""
    clip = render_tmp / "numbered.mp4"
    writer = sc.FrameWriter(clip, (64, 64), FPS, sc.ClipEncoding(), ffmpeg="ffmpeg",
                            keyframes=(20, 50))
    for k in range(60):
        writer.write(np.full((64, 64, 3), k * 4, np.uint8))
    writer.close()

    def numbers(start: int, count: int) -> list[int]:
        frames = sc.decode_frames(clip, (64, 64), ffmpeg="ffmpeg", fps=FPS, start_frame=start,
                                  count=count)
        return [int(round(float(frame.mean()) / 4)) for frame in frames]

    assert numbers(50, 10) == list(range(50, 60))
    assert numbers(45, 15) == list(range(45, 60))
    assert numbers(0, 5) == [0, 1, 2, 3, 4]


def test_a_read_cut_off_by_a_cancel_is_not_a_damaged_clip(render_tmp: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """When a cancel (or another clip's failure) kills the decoder in the middle of a frame,
    the clip is fine: the read says "stopped", and the clip is not removed as damaged."""
    import io

    clip = render_tmp / "scene_01.0123456789abcdef.mp4"
    clip.write_bytes(b"a clip")

    class Killed(_FailedDecoder):
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            super().__init__(command, **kwargs)
            self.process.stdout = io.BytesIO(b"\x00" * 100)  # less than one 16 x 16 frame
            self.killed.set()

    monkeypatch.setattr(sc, "_Watched", Killed)
    with pytest.raises(ff.RenderCancelled):
        list(sc.decode_frames(clip, (16, 16), ffmpeg="ffmpeg", fps=30))
    assert clip.is_file()


def test_one_render_at_a_time_in_the_whole_app(monkeypatch: pytest.MonkeyPatch,
                                               render_tmp: Path) -> None:
    """Two projects approved at the same moment: the second render waits (and says so) until
    the first is done, so the per-render memory budget holds for the PC."""
    from types import SimpleNamespace

    running: list[str] = []
    overlaps: list[tuple[str, str]] = []
    started = threading.Event()
    release = threading.Event()

    def fake_run(plan: Any, layout: Any, output: Path, **kwargs: Any) -> str:
        if running:
            overlaps.append((running[0], kwargs["name"]))
        running.append(kwargs["name"])
        started.set()
        release.wait(5)
        running.remove(kwargs["name"])
        return kwargs["name"]

    monkeypatch.setattr(assembly, "_run_pipeline", fake_run)
    plan = SimpleNamespace(layout=object(), preset=SimpleNamespace(id="proxy"))
    messages: list[str] = []
    results: dict[str, Any] = {}

    def render(name: str, progress: Any = None) -> None:
        results[name] = assembly.run_pipeline(
            plan, render_tmp / f"{name}.mp4", work_dir=render_tmp,
            log_path=render_tmp / "r.log", ffmpeg="ffmpeg", label=name, progress=progress)

    first = threading.Thread(target=render, args=("first",))
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=render,
                              args=("second", lambda message, pct: messages.append(message)))
    second.start()
    deadline = time.monotonic() + 5
    while not messages and time.monotonic() < deadline:
        time.sleep(0.02)
    assert messages == ["Waiting for another render to finish before rendering second"]
    assert running == ["first"]
    release.set()
    first.join(5)
    second.join(5)
    assert results == {"first": "first", "second": "second"} and overlaps == []

    # A render waiting for its turn can be cancelled.
    release.clear()
    started.clear()
    third = threading.Thread(target=render, args=("third",))
    third.start()
    assert started.wait(5)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ff.RenderCancelled, match="fourth"):
        assembly.run_pipeline(plan, render_tmp / "fourth.mp4", work_dir=render_tmp,
                              log_path=render_tmp / "r.log", ffmpeg="ffmpeg", label="fourth",
                              cancel=cancel)
    release.set()
    third.join(5)
    assert assembly.RENDER_GATE.acquire(blocking=False)  # nothing kept the gate
    assembly.RENDER_GATE.release()
