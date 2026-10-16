"""The edit-only app around the renderer: plain errors when the files of an edit are
missing, choices refused before any work, the render sizes and options, the edit gates, the
render and caption settings on /api/settings, and the doctor checks for the fonts and the
FFmpeg filters. No FFmpeg needed except where marked."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sigma_editor import doctor
from sigma_editor.config import Settings, load_settings
from sigma_editor.edit.builder import BuildError, rebuild_timeline
from sigma_editor.edit.checks import editor_checks
from sigma_editor.edit.choices import EditError
from sigma_editor.edit.renderer import (
    RenderFailed,
    allowed_presets,
    final_file_name,
    options_from,
)
from sigma_editor.models.timeline import EditChoices
from sigma_editor.policy import gates
from sigma_editor.render import ffmpeg as ff
from sigma_editor.render.presets import load_render_config
from test_render_fixtures import (
    apply_edits,
    ffmpeg_available,
    make_ctx,
    make_render_tmp_fixture,
    write_project,
)

render_tmp = make_render_tmp_fixture()


# Missing inputs -------------------------------------------------------------------------------


def test_missing_files_give_plain_english_errors(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    ctx = make_ctx(files)
    timeline, warnings = rebuild_timeline(ctx.project, ctx.settings)
    assert warnings == [] and len(timeline.scenes) == 3
    # A picture gone from the edit's folder: the editor's own check names the scene, and the
    # timeline cannot be built again until it is replaced.
    (files.folder / "06_images" / "scene_02.png").unlink()
    pictures = {c["id"]: c for c in editor_checks(timeline, files.folder)}["edit.pictures_present"]
    assert not pictures["passed"] and "scene(s) 2 is missing" in pictures["detail"]
    with pytest.raises(BuildError, match=r"No picture for scene\(s\) 2"):
        rebuild_timeline(ctx.project, ctx.settings)

    # The narration file is checked when the render plan is made.
    files2 = write_project(render_tmp / "two", music=False)
    ctx2 = make_ctx(files2)
    timeline2, _ = rebuild_timeline(ctx2.project, ctx2.settings)
    (files2.folder / "05_voice" / "voice.wav").unlink()
    config = load_render_config()
    with pytest.raises(ff.RenderError, match="voice file is missing"):
        ff.build_plan(timeline2, config.proxy, folder=files2.folder, config=config)
    (files2.folder / "05_voice" / "timing.json").unlink()
    with pytest.raises(BuildError, match="Add pictures or clips first"):
        rebuild_timeline(ctx2.project, ctx2.settings)
    files3 = write_project(render_tmp / "three", music=False)
    ctx3 = make_ctx(files3)
    (files3.folder / "07_edit" / "scenes.json").unlink()
    with pytest.raises(BuildError, match="no scene plan yet"):
        rebuild_timeline(ctx3.project, ctx3.settings)
    assert final_file_name("proxy") == "proxy.mp4" and final_file_name("1080p") == "final_1080p.mp4"


def test_invalid_choices_are_refused_before_any_work(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    with pytest.raises(ValidationError, match="captions_enabled"):
        EditChoices.model_validate({"captions_enabled": "maybe"})
    with pytest.raises(ValidationError):
        apply_edits(make_ctx(files, edits={"timeline": {"nonsense": 1}}))
    assert apply_edits(make_ctx(files)) is None  # no edits: nothing to do
    # The choices need a timeline; an unknown size or a missing track is refused plainly.
    with pytest.raises(EditError, match="no timeline yet"):
        apply_edits(make_ctx(files, edits={"presets": ["720p"]}))
    ctx = make_ctx(files)
    rebuild_timeline(ctx.project, ctx.settings)
    with pytest.raises(EditError, match="not a render size"):
        apply_edits(make_ctx(files, edits={"presets": ["4k"]}))
    with pytest.raises(EditError, match="was not found"):
        apply_edits(make_ctx(files, edits={"music_path": "nope.mp3"}))
    assert not (files.edit_dir / "proxy.mp4").exists()


def test_presets_follow_the_settings_and_the_4k_switch() -> None:
    config = load_render_config()
    warnings: list[str] = []
    assert allowed_presets(["1080p", "720p", "1080p"], config, False, warnings) == ["1080p", "720p"]
    assert allowed_presets(["2160p", "720p"], config, False, warnings) == ["720p"]
    assert warnings and "4K is switched off" in warnings[0]
    assert allowed_presets(["2160p"], config, True, []) == ["2160p"]
    assert allowed_presets(["proxy", ""], config, True, []) == []
    with pytest.raises(RenderFailed, match="not a render size"):
        allowed_presets(["8k"], config, True, [])
    options = options_from(Settings(render={"default_presets": ["720p"], "x264_preset": "fast",
                                            "enable_4k": True, "quality": "high"},
                                    captions={"enabled": False, "style": "yellow"}))
    assert options.presets == ["720p"] and options.x264_preset == "fast" and options.x264 == "fast"
    assert options.enable_4k and not options.captions_enabled and options.caption_style == "yellow"
    assert options.quality.id == "high" and options.upload is None and not options.h265_copy
    defaults = options_from(object())
    assert defaults.presets == ["1080p"] and defaults.quality.id == "standard"
    assert defaults.x264_preset is None and defaults.x264 == "medium"  # the quality's own speed
    # A render's own choices win over the settings.
    chosen = options_from(Settings(), presets=["720p", "1080p"], quality="best",
                          upload_preset="youtube_1080p", h265_copy=True)
    assert chosen.presets == ["720p", "1080p"] and chosen.quality.id == "best"
    assert chosen.upload is not None and chosen.upload.id == "youtube_1080p"
    assert chosen.h265_copy and chosen.x264 == "veryslow"


# Gates -----------------------------------------------------------------------------------------


def test_edit_gates() -> None:
    results = {r.id: r for r in gates.evaluate("edit", {
        "music_path": None, "music_license_ok": False,
        "renders": [{"preset": "proxy", "expected_s": 10.0, "actual_s": 10.4}],
        "true_peak_dbtp": None,
    })}
    assert results["edit.music_license"].passed and "No background music" in results[
        "edit.music_license"].detail
    assert results["edit.duration"].passed and results["edit.audio_peaks"].passed
    assert results["edit.music_license"].severity == "warn"
    assert results["edit.duration"].severity == "block"

    results = {r.id: r for r in gates.evaluate("edit", {
        "music_path": "D:/music/calm.mp3", "music_license_ok": False,
        "renders": [{"preset": "proxy", "expected_s": 10.0, "actual_s": 10.4},
                    {"preset": "1080p", "expected_s": 10.0, "actual_s": 12.0},
                    {"preset": "720p", "expected_s": 10.0, "actual_s": None}],
        "true_peak_dbtp": -0.4,
    })}
    assert not results["edit.music_license"].passed
    assert "calm.mp3.license.txt" in results["edit.music_license"].detail
    assert not results["edit.duration"].passed
    assert "1080p (12.0 s instead of 10.0 s)" in results["edit.duration"].detail
    assert "720p (length unknown)" in results["edit.duration"].detail
    assert not results["edit.audio_peaks"].passed and "-0.4" in results["edit.audio_peaks"].detail
    assert gates.blocking_reasons(list(results.values())) == [
        "Rendered length matches the timeline: " + results["edit.duration"].detail
    ]
    empty = {r.id: r for r in gates.evaluate("edit", {"renders": []})}
    assert not empty["edit.duration"].passed and "No video was rendered" in empty[
        "edit.duration"].detail


# Settings ------------------------------------------------------------------------------------


def test_render_and_caption_settings_round_trip(
    client: TestClient, data_dirs: dict[str, Path]
) -> None:
    body = client.get("/api/settings").json()
    assert body["render"] == {
        "default_presets": ["1080p"], "quality": "standard", "x264_preset": None,
        "two_pass_best": False, "h265_copy": False, "enable_4k": False,
        "transition_pack": "cinematic", "motion_style": "ken_burns",
    }
    assert body["captions"] == {"enabled": True, "style": "bold-white", "preset": "bold-outline"}
    assert body["quality_labels"] == {"standard": "Standard", "high": "High", "best": "Best"}

    saved = client.put("/api/settings", json={
        "render": {"default_presets": ["720p", "1080p", "720p"], "x264_preset": "fast",
                   "quality": "high", "enable_4k": True, "transition_pack": "dynamic",
                   "motion_style": "parallax"},
        "captions": {"enabled": False, "style": "yellow", "preset": "active-word"},
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["render"] == {
        "default_presets": ["720p", "1080p"], "quality": "high", "x264_preset": "fast",
        "two_pass_best": False, "h265_copy": False, "enable_4k": True,
        "transition_pack": "dynamic", "motion_style": "parallax",
    }
    assert saved.json()["captions"] == {"enabled": False, "style": "yellow",
                                        "preset": "active-word"}
    settings_file = data_dirs["app"] / "settings.json"
    on_disk = json.loads(settings_file.read_text(encoding="utf-8"))
    assert on_disk["render"]["x264_preset"] == "fast" and on_disk["captions"]["style"] == "yellow"
    assert on_disk["projects_dir"] == str(data_dirs["projects"])  # the folders stay
    # Saving a folder alone keeps these.
    moved = client.put("/api/settings", json={"exports_dir": str(data_dirs["exports"] / "out")})
    assert moved.status_code == 200, moved.text
    again = client.get("/api/settings").json()
    assert again["render"]["enable_4k"] is True and again["captions"]["enabled"] is False
    assert again["exports_dir"] == str(data_dirs["exports"] / "out")
    reloaded = load_settings()
    assert reloaded.render.default_presets == ["720p", "1080p"]
    assert reloaded.render.transition_pack == "dynamic"
    assert reloaded.render.motion_style == "parallax"
    assert reloaded.captions.style == "yellow"
    # The running app uses the saved settings at once.
    assert client.app.state.settings.render.x264_preset == "fast"
    assert client.app.state.settings.captions.preset == "active-word"

    bad = client.put("/api/settings", json={"render": {"x264_preset": "warp-speed"}})
    assert bad.status_code == 422 and "x264_preset" in json.dumps(bad.json()["detail"])
    bad = client.put("/api/settings", json={"render": {"default_presets": ["4k"]}})
    assert bad.status_code == 422 and "default_presets" in json.dumps(bad.json()["detail"])
    bad = client.put("/api/settings", json={"render": {"transition_pack": "flashy"}})
    assert bad.status_code == 422 and "transition_pack" in json.dumps(bad.json()["detail"])
    bad = client.put("/api/settings", json={"render": {"quality": "ultra"}})
    assert bad.status_code == 422 and "quality" in json.dumps(bad.json()["detail"])
    bad = client.put("/api/settings", json={"captions": {"style": ""}})
    assert bad.status_code == 422 and "style" in json.dumps(bad.json()["detail"])
    assert client.get("/api/settings").json()["render"]["x264_preset"] == "fast"  # untouched


def test_a_broken_render_section_falls_back_to_defaults(data_dirs: dict[str, Path]) -> None:
    (data_dirs["app"] / "settings.json").write_text(
        json.dumps({
            "projects_dir": str(data_dirs["projects"]), "exports_dir": str(data_dirs["exports"]),
            "render": {"x264_preset": 42}, "captions": {"style": "clean"},
        }),
        "utf-8",
    )
    settings = load_settings()
    assert settings.render.x264_preset is None and settings.render.default_presets == ["1080p"]
    assert settings.captions.style == "clean"
    assert settings.projects_dir == data_dirs["projects"]  # the folders are read as before


# Doctor ----------------------------------------------------------------------------------------


def test_doctor_fonts_check(monkeypatch: pytest.MonkeyPatch, render_tmp: Path) -> None:
    result = doctor.check_fonts()
    assert result.status == "ok" and "6 Noto fonts" in result.detail

    monkeypatch.setenv("SGE_FONTS_DIR", str(render_tmp / "missing"))
    result = doctor.check_fonts()
    assert result.status == "fail" and result.fix_hint

    partial = render_tmp / "partial"
    partial.mkdir()
    monkeypatch.setenv("SGE_FONTS_DIR", str(partial))
    assert doctor.check_fonts().status == "fail"
    source = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSans[wdth,wght].ttf"
    shutil.copy(source, partial / source.name)
    result = doctor.check_fonts()
    assert result.status == "warn" and "Noto Sans JP" in result.detail and result.fix_hint


def test_doctor_ffmpeg_filters_check(
    data_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = load_settings()
    if ffmpeg_available():
        result = doctor.check_ffmpeg_filters(settings)
        assert result.status == "ok" and "libass" in result.detail
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    result = doctor.check_ffmpeg_filters(load_settings())
    assert result.status == "fail" and "FFmpeg was not found" in result.detail
    assert result.fix_hint


def test_doctor_report_lists_the_render_checks(client: TestClient) -> None:
    ids = [check["id"] for check in client.get("/api/doctor").json()["checks"]]
    assert ids.index("ffmpeg_filters") == ids.index("ffprobe") + 1
    assert ids.index("fonts") == ids.index("ffmpeg_filters") + 1
    assert "parallax" in ids and ids.index("parallax") > ids.index("fonts")
