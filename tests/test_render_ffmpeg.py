"""The FFmpeg side: the filter graph as text (no FFmpeg needed), then real renders of the
3-scene mock edit at proxy and 720p, a 9:16 variant, the duration check with ffprobe,
cancellation, a second run that keeps what is finished, and the editor's choices. Media
stays tiny."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sigma_editor.edit.choices import EditError
from sigma_editor.edit.renderer import (
    files_fingerprint,
    load_review_payload,
    read_state,
    render_key_for,
    timeline_media,
)
from sigma_editor.models.timeline import Timeline
from sigma_editor.render import ffmpeg as ff
from sigma_editor.render import thumbs
from sigma_editor.render.presets import load_render_config
from sigma_editor.render.scene_clips import zoompan_graph
from test_render_fixtures import (
    SENTENCES,
    apply_edits,
    make_ctx,
    make_render_tmp_fixture,
    requires_ffmpeg,
    run_edit,
    write_project,
)

render_tmp = make_render_tmp_fixture()


# Pure helpers ----------------------------------------------------------------------------------


def test_expression_numbers_and_filter_path_escaping() -> None:
    assert ff.fmt(1.7) == "1.7" and ff.fmt(0.43333) == "0.4333" and ff.fmt(854) == "854"
    assert ff.fmt(-170.8) == "-170.8" and ff.fmt(0.0) == "0" and ff.fmt(-0.00001) == "0"
    escaped = ff.escape_filter_path(Path("F:/cfs_tmp/edit/esc test/captions.ass"))
    assert escaped == "'F\\:/cfs_tmp/edit/esc test/captions.ass'"
    assert ff.escape_filter_path("C:\\Users\\punjab pc\\x.ass") == "'C\\:/Users/punjab pc/x.ass'"
    # Nothing can be escaped inside a quoted graph token: an apostrophe closes the quote,
    # gives the option parser \' and opens the quote again.
    assert ff.escape_filter_path("a'b,c") == "'a'\\\\\\''b\\,c'"


def test_progress_lines_and_benign_log_lines() -> None:
    assert ff.parse_progress_line("out_time_us=1500000\n") == ("out_time_us", "1500000")
    assert ff.parse_progress_line("progress=end") == ("progress", "end")
    assert ff.parse_progress_line("no equals here") is None
    assert ff.is_benign_log_line("[Parsed_subtitles_3 @ 0x1] Error opening memory font 'OFL.txt'")
    assert ff.is_benign_log_line("[aist#5:0/pcm_s16le @ 0x1] Guessed Channel Layout: mono")
    assert not ff.is_benign_log_line("[libx264 @ 0x1] something went wrong")


def test_filter_graph_shape_for_the_mock_project(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    ctx = make_ctx(files)
    config = load_render_config()
    from sigma_editor.render.timeline_builder import (
        BuildRequest,
        build_timeline,
        parse_timing,
        resolve_scene_images,
    )

    request = BuildRequest(
        project_id="p-edit-test", fmt="long", aspect="16:9", language="English",
        folder=files.folder, storyboard=files.storyboard, timing=parse_timing(files.timing),
        sources=resolve_scene_images(files.folder, files.storyboard), width=1920, height=1080,
        proxy=(854, 480), presets=["720p"], music_folder=str(files.music_dir), config=config,
    )
    timeline, _ = build_timeline(request)
    ass = files.edit_dir / "captions.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    popups = [
        ff.PopupAsset(popup, files.edit_dir / f"p{i}.png", 300, 100, 60, 60)
        for i, popup in enumerate(timeline.popups)
    ]
    plan = ff.build_plan(
        timeline, config.presets["720p"], folder=files.folder, config=config, popups=popups,
        ass_path=ass, captions=True, x264_preset=ctx.settings.render.x264_preset,
    )
    assert (plan.width, plan.height) == (1280, 720) and plan.canvas == (2560, 1440)
    assert plan.x264_preset == "ultrafast" and plan.crf == 20
    assert plan.total_s == pytest.approx(timeline.duration_s, abs=1 / 30)
    # Transitions v2: the scenes and cuts are clips joined by the concat demuxer (input 0),
    # then 2 popups + voice + music = 5 inputs; the music is the stem of render/music.py
    # (made just before the assembly pass: looped, faded and dipped already).
    assert len(plan.inputs) == 5
    assert plan.inputs[0] == ["-f", "concat", "-safe", "0", "-i", "clips/720p/concat.txt"]
    assert plan.inputs[1][:2] == ["-loop", "1"]
    stem = files.folder / "07_edit" / "music_ducked.wav"
    assert plan.inputs[4] == ["-i", str(stem)]
    assert plan.audio is not None and plan.audio.stem == stem
    layout = plan.layout
    assert [job.number for job in layout.scenes] == [1, 2, 3]
    assert all(job.renderer == "zoompan" for job in layout.scenes)
    assert all("zoompan=" in zoompan_graph(job) and "format=gbrp" in zoompan_graph(job)
               for job in layout.scenes)
    # The old xfade names are the v2 looks now; 0.4333 s = 13 frames, the end fades to black.
    assert [(c.number, c.label, c.frames) for c in layout.cuts] == [
        (1, "film_dissolve", 13), (2, "luma_wipe_left", 13), (3, "fade to black", 18),
    ]
    assert layout.total_frames == round(timeline.duration_s * 30)
    assert sum(job.frames for job in layout.scenes) + sum(c.frames for c in layout.cuts) == (
        layout.total_frames + sum(hi - lo + 1 for lo, hi in layout.drops)
    )
    graph = plan.filter_graph
    assert "xfade=" not in graph and "zoompan=" not in graph
    assert graph.startswith("[0:v]select='not(((between(n\\,") and "setpts=N/(30*TB)" in graph
    assert graph.count("overlay=") == 2 and "eof_action=pass" in graph
    assert "pow(max(0\\,1-(t-0.3)/0.35)\\,2)" in graph  # slide_left eases in
    assert "subtitles=filename='" in graph and "\\:/" in graph and "fontsdir='" in graph
    # M5.1: no sidechaincompress; the voice is its sentence clips (atrim in samples + concat).
    assert "sidechaincompress" not in graph
    assert "asplit=3" in graph and "concat=n=3:v=0:a=1" in graph
    assert "atrim=start_sample=0:end_sample=76800" in graph  # sentence 1: 0 .. 1.6 s
    assert "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0" in graph
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in graph
    assert "apad=whole_dur=" in graph and graph.rstrip().endswith("[amain]anull[aout]")
    assert "null[vout]" in graph
    # Proxy preset: its own speed and size, captions can be left out, no music -> no ducking.
    timeline.music.path = None
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config,
                         ass_path=ass, captions=False, x264_preset="slow")
    assert plan.x264_preset == "ultrafast" and plan.crf == 30
    assert (plan.width, plan.height) == (854, 480) and len(plan.inputs) == 2
    assert plan.layout.clips_dir == "clips/proxy"
    assert plan.layout.scenes[0].encoding.x264_preset == "ultrafast"
    assert "subtitles=" not in plan.filter_graph and "sidechaincompress" not in plan.filter_graph
    output = plan.output_args()
    assert output[:4] == ["-map", "[vout]", "-map", "[aout]"]
    assert "-movflags" in output and "+faststart" in output and "-t" in output


def test_filter_graph_handles_80_scenes_without_a_long_command_line(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    config = load_render_config()
    scenes = []
    clock = 0.0
    for index in range(80):
        scenes.append({
            "index": index, "image": f"06_images/scene_{index % 3 + 1:02d}.png",
            "start_s": round(clock, 3), "end_s": round(clock + 2.0, 3),
            "motion": {"preset": "zoom_in", "start_rect": [0, 0, 1024, 576],
                       "end_rect": [102.4, 57.6, 819.2, 460.8]},
            "transition_out": {"type": "fade", "duration_s": 0.4},
        })
        clock += 2.0
    timeline = Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 1280, "height": 720,
        "duration_s": clock, "scenes": scenes,
    })
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    layout = plan.layout
    # 80 scene clips, 79 cuts and the fade at the end, all one concat input (+ the voice).
    assert len(layout.scenes) == 80 and len(layout.cuts) == 80 and len(plan.inputs) == 2
    assert len(layout.concat_text().splitlines()) == 1 + 160
    assert plan.filter_graph.count("between(n") == 2 * 79 + 1  # the overlaps the cuts replace
    assert layout.total_frames == 80 * 60 and plan.total_s == pytest.approx(160.0)


# Real renders ------------------------------------------------------------------------------


@requires_ffmpeg
def test_render_proxy_and_720p_with_ffprobe_duration(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    events: list[tuple[str, float | None]] = []
    # The music is the person's choice: the licensed track the fixture put in 01_media.
    ctx = make_ctx(files, progress=lambda message, pct=None: events.append((message, pct)),
                   edits={"music_path": "calm.wav"})
    result = run_edit(ctx)

    edit = files.edit_dir
    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.presets == ["720p"]
    for name in ("timeline.json", "captions.ass", "proxy.mp4", "final_720p.mp4", "render.log",
                 "render_state.json", "filters_proxy.txt", "filters_720p.txt"):
        assert (edit / name).is_file(), name
    assert sorted(p.name for p in (edit / "popups" / "ref").iterdir()) == [
        "popup_00.png", "popup_01.png",
    ]
    assert (edit / "popups" / "720p" / "popup_01.png").is_file()
    # the live preview's sized pictures were made while the videos rendered
    assert thumbs.wait_for_background(30)
    for scene in timeline.scenes:
        source = (files.folder / scene.image).resolve()
        with Image.open(source) as picture:
            heights = thumbs.preview_heights(picture.size, (timeline.width, timeline.height))
        assert heights
        for height in heights:
            assert thumbs.cached_thumb(files.folder, source, height).path.is_file(), height

    for name, size in (("proxy.mp4", (854, 480)), ("final_720p.mp4", (1280, 720))):
        info = ff.probe_media(edit / name)
        assert (info.width, info.height) == size, name
        assert info.has_audio and info.has_video
        assert abs(info.duration_s - timeline.duration_s) < 1.0, name
        assert abs(info.duration_s - timeline.duration_s) < 0.2, name
    assert ff.probe_duration(edit / "proxy.mp4") == pytest.approx(timeline.duration_s, abs=0.2)

    gates = {g["id"]: g for g in result.gate_results}
    assert set(gates) == {"edit.music_license", "edit.duration", "edit.audio_peaks"}
    assert all(g["passed"] for g in gates.values()), gates
    assert "calm.wav" in gates["edit.music_license"]["detail"]
    payload = result.needs_review_payload
    assert payload["stage"] == "edit"
    assert payload["proxy_url"] == "/api/projects/p-edit-test/files/07_edit/proxy.mp4"
    assert payload["proxy_path"] == "07_edit/proxy.mp4"
    renders = {r["preset"]: r for r in payload["renders"]}
    assert renders["proxy"]["status"] == "done" and renders["720p"]["status"] == "done"
    assert renders["720p"]["play_url"].endswith("/files/07_edit/final_720p.mp4")
    assert renders["720p"]["width"] == 1280 and renders["720p"]["size_bytes"] > 1000
    assert renders["1080p"]["status"] == "not_rendered"
    assert payload["summary"]["scene_count"] == 3
    # The fixture's storyboard still says fade / wipeleft / fadeblack: the v2 looks now.
    assert payload["summary"]["transitions"] == ["film_dissolve", "luma_wipe_left", "dip_black"]
    assert renders["720p"]["reuse_note"] == "Reused 0 of 3 scene clips."
    assert payload["render_notes"] == [
        "Proxy: reused 0 of 3 scene clips and 0 of 3 transitions.",
        "720p: reused 0 of 3 scene clips and 0 of 3 transitions (Standard quality).",
    ]
    clips = sorted(p.name.split(".")[0] for p in (edit / "clips" / "720p").glob("*.mp4"))
    assert clips == ["cut_01", "cut_02", "cut_03", "scene_01", "scene_02", "scene_03"]
    assert payload["summary"]["caption_cues"] == 3 and payload["summary"]["popup_count"] == 2
    assert [s["image_url"] for s in payload["scenes"]][0].endswith("06_images/scene_01.png")
    assert payload["scenes"][0]["popup_text"] == "Five every morning"
    assert payload["music_license_ok"] is True and payload["music_path"].endswith("calm.wav")
    assert [t["name"] for t in payload["music_tracks"]] == ["calm"]
    assert payload["captions_enabled"] is True and payload["popups_enabled"] is True
    assert payload["available_presets"] == ["720p", "1080p", "2160p"]
    assert payload["enable_4k"] is False and payload["warnings"] == []
    assert payload["render_log_path"] == "07_edit/render.log"
    assert payload["timing_source"] == "estimated"
    assert payload["quality"] == "standard" and payload["upload_preset"] == "none"
    assert payload["h265_copy"] is False and payload["rendering"] is False
    checks = {c["id"]: c for c in payload["checks"]}
    assert set(checks) == {"edit.pictures_present", "edit.caption_speed"}
    assert checks["edit.pictures_present"]["passed"]
    assert payload["music_folder"] == str(files.music_dir)
    assert result.rendered == ["proxy", "720p"] and result.kept == [] and not result.nothing_new
    assert [p.name for p in result.outputs] == ["proxy.mp4", "final_720p.mp4"]
    assert result.outcome.duration_s == pytest.approx(timeline.duration_s)

    messages = [m for m, _ in events]
    assert messages[0].startswith("Preparing the music") and messages[-1] == "Render finished"
    assert "Rendering the preview 0%" in messages
    assert any(m.startswith("Rendering proxy") for m in messages)
    assert any(m.startswith("Rendering 720p") and m.endswith("100%") for m in messages)
    pcts = [p for m, p in events if m.startswith("Rendering 720p") and p is not None]
    assert pcts == sorted(pcts) and pcts[-1] == 100.0
    log = (edit / "render.log").read_text(encoding="utf-8")
    assert "render proxy -> proxy.mp4" in log and "render 720p -> final_720p.mp4" in log
    assert "exit code 0" in log and "OFL.txt" not in log
    assert "scene 02: drawn in" in log and "cut 02 (luma_wipe_left, 13 frames): drawn in" in log

    # The payload can be rebuilt from the files alone (server restart).
    rebuilt = load_review_payload(ctx.project, ctx.settings)
    assert rebuilt is not None and rebuilt["proxy_url"] == payload["proxy_url"]
    assert {r["preset"]: r["status"] for r in rebuilt["renders"]} == {
        r["preset"]: r["status"] for r in payload["renders"]
    }


@requires_ffmpeg
def test_render_9_16_variant(render_tmp: Path) -> None:
    files = write_project(render_tmp, aspect="9:16", music=False)
    ctx = make_ctx(files, render={"default_presets": ["720p"], "x264_preset": "ultrafast"})
    result = run_edit(ctx)
    edit = files.edit_dir
    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.aspect == "9:16" and (timeline.width, timeline.height) == (720, 1280)
    assert (timeline.proxy.width, timeline.proxy.height) == (480, 854)
    proxy = ff.probe_media(edit / "proxy.mp4")
    final = ff.probe_media(edit / "final_720p.mp4")
    assert (proxy.width, proxy.height) == (480, 854)
    assert (final.width, final.height) == (720, 1280)
    assert abs(final.duration_s - timeline.duration_s) < 1.0
    gates = {g["id"]: g for g in result.gate_results}
    assert gates["edit.duration"]["passed"] and gates["edit.music_license"]["passed"]
    assert "No background music" in gates["edit.music_license"]["detail"]
    payload = result.needs_review_payload
    assert payload["music_path"] is None and payload["music_tracks"] == []
    assert payload["warnings"] == [] and payload["music_license_ok"] is False


@requires_ffmpeg
def test_second_run_reuses_the_timeline_and_edits_rerender(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    ctx = make_ctx(files, edits={"music_path": "calm.wav"})
    first = run_edit(ctx)
    assert first.rendered == ["proxy", "720p"]
    assert first.needs_review_payload["music_path"].endswith("calm.wav")
    edit = files.edit_dir
    before = (edit / "timeline.json").read_text("utf-8")
    proxy_mtime = (edit / "proxy.mp4").stat().st_mtime

    # Nothing changed: the timeline and the renders are kept.
    second = run_edit(make_ctx(files))
    assert second.nothing_new and second.kept == ["proxy", "720p"]
    assert (edit / "timeline.json").read_text("utf-8") == before
    assert (edit / "proxy.mp4").stat().st_mtime == proxy_mtime
    assert second.needs_review_payload["renders"][0]["status"] == "done"

    # The editor's choices: captions off and no music -> the timeline changes and renders again.
    time.sleep(0.05)
    edited = apply_edits(make_ctx(files, edits={"captions_enabled": False,
                                                "music_path": "", "presets": []}))
    assert edited is not None and edited.rendered == ["proxy"]
    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.captions.enabled is False and timeline.music.path is None
    assert timeline.presets == []
    assert (edit / "proxy.mp4").stat().st_mtime > proxy_mtime
    assert "subtitles=" not in (edit / "filters_proxy.txt").read_text("utf-8")
    assert "sidechaincompress" not in (edit / "filters_proxy.txt").read_text("utf-8")
    payload = edited.needs_review_payload
    assert payload["captions_enabled"] is False and payload["music_path"] is None
    assert {r["preset"]: r["status"] for r in payload["renders"]}["720p"] == "done"  # kept
    assert "No background music" in {g["id"]: g for g in edited.gate_results}[
        "edit.music_license"
    ]["detail"]

    # Popups off is remembered in render_state.json; unknown presets are refused plainly.
    off = apply_edits(make_ctx(files, edits={"popups_enabled": False}))
    assert off is not None and off.needs_review_payload["popups_enabled"] is False
    assert read_state(edit)["popups_enabled"] is False
    assert "overlay=" not in (edit / "filters_proxy.txt").read_text("utf-8")
    with pytest.raises(EditError, match="not a render size"):
        apply_edits(make_ctx(files, edits={"presets": ["4k"]}))
    with pytest.raises(EditError, match="was not found"):
        apply_edits(make_ctx(files, edits={"music_path": "nope.mp3"}))
    assert first.gate_results and len(first.needs_review_payload["scenes"]) == 3


def _older_copy_over(target: Path, colour: tuple[int, int, int], spare: Path) -> None:
    """Replace ``target`` the way an Explorer copy or a sync does: the new file keeps the
    copied file's date, an hour older than the picture it replaces."""
    with Image.open(target) as image:
        size = image.size
    Image.new("RGB", size, colour).save(spare)
    past = time.time() - 3600
    os.utime(spare, (past, past))
    shutil.copy2(spare, target)


def test_a_file_replaced_by_an_older_copy_changes_the_fingerprints(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline_like = Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 854, "height": 480,
        "duration_s": 2.0, "scenes": [{
            "index": 0, "image": "06_images/scene_02.png", "start_s": 0.0, "end_s": 2.0,
            "motion": {"preset": "hold", "start_rect": [0, 0, 1024, 576],
                       "end_rect": [0, 0, 1024, 576]}}],
    })
    picture = files.folder / "06_images" / "scene_02.png"
    assert timeline_media(files.folder, timeline_like) == [
        picture, files.folder / "05_voice" / "voice.wav",
    ]
    media = files_fingerprint(files.folder, timeline_media(files.folder, timeline_like))
    assert media == files_fingerprint(files.folder, timeline_media(files.folder, timeline_like))
    key = render_key_for(timeline_like, popups_enabled=True, x264_preset=None, media=media)
    newest = picture.stat().st_mtime

    _older_copy_over(picture, (250, 20, 200), render_tmp / "spare.png")
    assert picture.stat().st_mtime < newest  # a "nothing newer?" check would miss this copy
    media_after = files_fingerprint(files.folder, timeline_media(files.folder, timeline_like))
    assert media_after != media
    assert render_key_for(timeline_like, popups_enabled=True, x264_preset=None,
                          media=media_after) != key
    # Paths inside the edit's folder count from that folder (a moved edit keeps them).
    other = render_tmp / "elsewhere"
    assert files_fingerprint(files.folder, [files.folder / "missing.png"]) == files_fingerprint(
        other, [other / "missing.png"])


def _frame_at(path: Path, number: int, size: tuple[int, int]) -> np.ndarray:
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf",
         f"select=eq(n\\,{number})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-"],
        capture_output=True, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return np.frombuffer(completed.stdout, np.uint8).reshape(size[1], size[0], 3)


@requires_ffmpeg
def test_a_picture_replaced_by_an_older_file_is_rendered_again(render_tmp: Path) -> None:
    """The finished proxy and the timeline are only kept while every file they come from is
    the same (path, size, date of each), not merely while nothing is newer: a picture copied
    in from elsewhere keeps its older date and must still show up."""
    files = write_project(render_tmp, music=False, popups=False)
    first = run_edit(make_ctx(files, edits={"presets": []}))
    assert first.rendered == ["proxy"]
    proxy = files.edit_dir / "proxy.mp4"
    before = (proxy.stat().st_mtime_ns, proxy.stat().st_size)
    key = read_state(files.edit_dir)["renders"]["proxy"]["render_key"]

    _older_copy_over(files.folder / "06_images" / "scene_02.png", (250, 20, 200),
                     render_tmp / "spare.png")
    second = run_edit(make_ctx(files, edits={"presets": []}))
    assert second.rendered == ["proxy"] and not second.nothing_new
    assert (proxy.stat().st_mtime_ns, proxy.stat().st_size) != before
    assert read_state(files.edit_dir)["renders"]["proxy"]["render_key"] != key
    timeline = Timeline.model_validate_json((files.edit_dir / "timeline.json").read_text("utf-8"))
    scene = timeline.scenes[1]
    middle = int(round((scene.start_s + scene.end_s) / 2 * timeline.fps))
    shown = _frame_at(proxy, middle, (854, 480)).reshape(-1, 3).mean(axis=0)
    assert shown[0] > 180 and shown[1] < 90 and shown[2] > 140, shown  # the new magenta

    # Nothing changed since: everything is kept.
    third = run_edit(make_ctx(files, edits={"presets": []}))
    assert third.nothing_new and third.kept == ["proxy"]


@requires_ffmpeg
def test_cancel_flag_stops_ffmpeg(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    config = load_render_config()
    scenes = []
    for index in range(3):
        scenes.append({
            "index": index, "image": f"06_images/scene_{index + 1:02d}.png",
            "start_s": index * 40.0, "end_s": (index + 1) * 40.0,
            "motion": {"preset": "zoom_in", "start_rect": [0, 0, 1024, 576],
                       "end_rect": [102.4, 57.6, 819.2, 460.8]},
            "transition_out": {"type": "fade", "duration_s": 0.5},
        })
    timeline = Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 854, "height": 480,
        "duration_s": 120.0, "scenes": scenes,
    })
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    cancel = threading.Event()
    seen: list[float] = []

    def progress(_message: str, pct: float) -> None:
        seen.append(pct)
        if len(seen) >= 2:
            cancel.set()

    started = time.monotonic()
    with pytest.raises(ff.RenderCancelled):
        ff.run_render(
            plan, files.edit_dir / "long.mp4", work_dir=files.edit_dir,
            log_path=files.edit_dir / "render.log", progress=progress, cancel=cancel,
        )
    assert time.monotonic() - started < 30
    assert not (files.edit_dir / "long.mp4").exists()
    assert seen and max(seen) < 100


@requires_ffmpeg
def test_render_failures_read_plainly(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    config = load_render_config()
    timeline = Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 854, "height": 480,
        "duration_s": 4.0,
        "scenes": [{"index": 0, "image": "06_images/scene_01.png", "start_s": 0, "end_s": 2.0,
                    "motion": {"preset": "hold", "start_rect": [0, 0, 1024, 576],
                               "end_rect": [0, 0, 1024, 576]},
                    "transition_out": {"type": "no-such-transition", "duration_s": 0.3}},
                   {"index": 1, "image": "06_images/scene_02.png", "start_s": 2.0, "end_s": 4.0,
                    "motion": {"preset": "hold", "start_rect": [0, 0, 1024, 576],
                               "end_rect": [0, 0, 1024, 576]}}],
    })
    # An unknown transition is not an error any more: it is drawn as a film dissolve.
    assert timeline.scenes[0].transition_out.type == "film_dissolve"
    # A voice file FFmpeg cannot read makes the assembly pass fail.
    (files.folder / "05_voice" / "voice.wav").write_bytes(b"not a wave file at all" * 40)
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    with pytest.raises(ff.RenderError, match="could not render proxy"):
        ff.run_render(plan, files.edit_dir / "bad.mp4", work_dir=files.edit_dir,
                      log_path=files.edit_dir / "render.log")
    assert "exit code" in (files.edit_dir / "render.log").read_text("utf-8")
    assert not (files.edit_dir / "bad.mp4").exists()
    # The scene and transition clips made before the failure are kept for the next try.
    assert len(list((files.edit_dir / "clips" / "proxy").glob("scene_*.mp4"))) == 2
    missing = timeline.model_copy(deep=True)
    missing.scenes[0].image = "06_images/none.png"
    with pytest.raises(ff.RenderError, match="picture for scene 1 is missing"):
        ff.build_plan(missing, config.proxy, folder=files.folder, config=config)


@requires_ffmpeg
def test_true_peak_of_silence_is_none_and_filters_are_listed() -> None:
    assert ff.missing_filters() == []
    assert ff.measure_true_peak(Path("F:/does/not/exist.wav")) is None
    assert len(SENTENCES) == 3  # the fixture is what the contract asks: 3 scenes


@requires_ffmpeg
def test_captions_render_from_a_folder_with_an_apostrophe_and_a_space(render_tmp: Path) -> None:
    """A project under C:/Users/O'Neil/... (or a fonts folder with an apostrophe) must not
    break the filter graph: FFmpeg's graph parser cannot escape inside a quoted token."""
    from sigma_editor.render.captions import write_ass
    from sigma_editor.render.timeline_builder import (
        BuildRequest,
        build_timeline,
        parse_timing,
        resolve_scene_images,
    )

    root = render_tmp / "o'neil clips"
    root.mkdir()
    files = write_project(root, music=False, popups=False)
    config = load_render_config()
    request = BuildRequest(
        project_id="p-edit-test", fmt="long", aspect="16:9", language="English",
        folder=files.folder, storyboard=files.storyboard, timing=parse_timing(files.timing),
        sources=resolve_scene_images(files.folder, files.storyboard), width=854, height=480,
        proxy=(854, 480), presets=[], music_folder=None, config=config,
    )
    timeline, _ = build_timeline(request)
    assert timeline.captions.cues
    ass = files.edit_dir / "captions.ass"
    write_ass(
        ass, timeline.captions.cues, timeline.captions.style, timeline.aspect,
        margin_v_px=config.captions_margin_v_px, shadow=config.captions_shadow,
    )
    plan = ff.build_plan(
        timeline, config.proxy, folder=files.folder, config=config, ass_path=ass, captions=True,
    )
    assert "subtitles=filename='" in plan.filter_graph
    assert "o'\\\\\\''neil clips" in plan.filter_graph
    result = ff.run_render(
        plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
        log_path=files.edit_dir / "render.log",
    )
    assert result.duration_s == pytest.approx(timeline.duration_s, abs=0.3)
    assert "Error parsing filterchain" not in (files.edit_dir / "render.log").read_text("utf-8")


PROXY_ONLY = {"presets": []}
"""First-run edits that render the proxy alone. Settings > Render cannot ask for no final
(an empty ``default_presets`` falls back to 1080p), so ``render={"default_presets": []}``
would quietly add a 1080p render: on a PC short of memory that render is what failed (x264
"malloc of size ... failed") in an earlier run of these tests."""


def _finals(edit: Path) -> list[str]:
    return sorted(p.name for p in edit.glob("final_*.mp4"))


@requires_ffmpeg
def test_adding_a_preset_keeps_finished_renders(render_tmp: Path) -> None:
    """``{presets: [...]}`` adds a size; the proxy (and any finished final) is not made again
    just because timeline.json was rewritten with the longer preset list."""
    files = write_project(render_tmp, music=False)
    first = run_edit(make_ctx(files, render={"default_presets": ["720p"],
                                             "x264_preset": "ultrafast"}, edits=PROXY_ONLY))
    assert first.rendered == ["proxy"]
    edit = files.edit_dir
    assert (edit / "proxy.mp4").is_file() and _finals(edit) == []
    proxy_mtime = (edit / "proxy.mp4").stat().st_mtime
    time.sleep(0.05)

    result = apply_edits(make_ctx(files, edits={"presets": ["720p"]}))
    assert result is not None
    assert _finals(edit) == ["final_720p.mp4"]
    assert (edit / "proxy.mp4").stat().st_mtime == proxy_mtime  # kept, not rendered again
    assert result.rendered == ["720p"] and result.kept == ["proxy"]
    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.presets == ["720p"]
    state = read_state(edit)
    assert state["renders"]["proxy"]["render_key"] == state["renders"]["720p"]["render_key"]
    # A replaced picture is a different source: the key changes and the renders are redone.
    time.sleep(1.1)
    picture = files.folder / "06_images" / "scene_02.png"
    picture.write_bytes(picture.read_bytes())
    again = run_edit(make_ctx(files, render={"default_presets": ["720p"],
                                             "x264_preset": "ultrafast"}))
    assert again.rendered == ["proxy", "720p"] and again.kept == []


@requires_ffmpeg
def test_intro_and_outro_clips_are_joined(render_tmp: Path) -> None:
    """Intro and outro files come as they are (another size, frame rate, colour range, with
    or without sound): the assembly pass fits them to the frame, retimes them to 30 fps and
    converts them to the clips' limited-range BT.709 before joining. The scene and cut clips
    themselves all share one size, pixel format, frame rate and time base (ffprobe)."""
    import subprocess

    import numpy as np

    from sigma_editor.render.scene_clips import TO_RGB

    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    files = write_project(render_tmp, music=False, popups=False)
    intro = render_tmp / "intro.mp4"  # 16:9, 30 fps, limited range, no sound
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=red:s=320x180:d=1:r=30", "-pix_fmt", "yuv420p", str(intro)],
        check=True, creationflags=no_window,
    )
    outro = render_tmp / "outro.mp4"  # 4:3, 25 fps, full range, with sound
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=0x303030:s=320x240:d=1:r=25", "-f", "lavfi",
         "-i", "sine=frequency=440:sample_rate=44100:duration=1", "-pix_fmt", "yuvj420p",
         "-c:a", "aac", "-shortest", str(outro)],
        check=True, creationflags=no_window,
    )
    run_edit(make_ctx(files, render={"default_presets": ["720p"],
                                     "x264_preset": "ultrafast"}, edits=PROXY_ONLY))
    assert _finals(files.edit_dir) == []
    timeline = Timeline.model_validate_json((files.edit_dir / "timeline.json").read_text("utf-8"))
    timeline.intro.path = str(intro)
    timeline.outro.path = str(outro)
    edits = {"timeline": timeline.model_dump(mode="json")}
    result = apply_edits(make_ctx(files, edits=edits))
    assert result is not None and result.rendered == ["proxy"]
    proxy = files.edit_dir / "proxy.mp4"
    info = ff.probe_media(proxy)
    assert info.duration_s == pytest.approx(timeline.duration_s + 2.0, abs=0.3)
    assert (info.width, info.height) == (854, 480) and info.has_audio
    assert {g["id"]: g["passed"] for g in result.gate_results}["edit.duration"] is True
    state = json.loads((files.edit_dir / "render_state.json").read_text("utf-8"))
    expected = timeline.duration_s + 2.0
    assert state["renders"]["proxy"]["expected_s"] == pytest.approx(expected, abs=0.05)

    def probe(path: Path) -> dict[str, object]:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,pix_fmt,r_frame_rate,time_base,sample_aspect_ratio,"
             "color_range,color_space,nb_frames", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, creationflags=no_window,
        ).stdout
        return json.loads(out)["streams"][0]

    clips = sorted((files.edit_dir / "clips" / "proxy").glob("*.mp4"))
    assert len(clips) == 6  # 3 scenes, 2 cuts and the closing fade
    shared = {key: value for key, value in probe(clips[0]).items() if key != "nb_frames"}
    assert shared["width"] == 854 and shared["height"] == 480 and shared["pix_fmt"] == "yuv420p"
    assert shared["r_frame_rate"] == "30/1" and shared["color_range"] == "tv"
    for clip in clips[1:]:
        assert {k: v for k, v in probe(clip).items() if k != "nb_frames"} == shared, clip.name
    joined = probe(proxy)
    assert joined["r_frame_rate"] == "30/1" and joined["color_range"] == "tv"
    assert abs(int(joined["nb_frames"]) - round(expected * 30)) <= 1

    # The first frame is the red intro; the last is the dark grey outro, pillar-boxed (4:3
    # in 16:9) and still the same grey: its full range was converted, not taken as limited.
    last = int(joined["nb_frames"]) - 1
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(proxy), "-vf",
         f"select='eq(n\\,0)+eq(n\\,{last})',{TO_RGB},format=rgb24", "-fps_mode",
         "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True, check=True, creationflags=no_window,
    ).stdout
    first, final = np.frombuffer(raw, np.uint8).reshape(2, 480, 854, 3).astype(int)
    red = first[240, 427]
    assert red[0] > 200 and red[1] < 40 and red[2] < 40, red
    grey = final[240, 427]
    assert np.abs(grey - 48).max() <= 4, grey
    assert final[240, 20].max() <= 20  # the pillar box is black
