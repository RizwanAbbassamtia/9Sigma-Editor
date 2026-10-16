"""The editor's routes (``api/editing.py``) end to end: New edit from files, the payload, the
simple choices, the timeline autosave, scenes, captions, a real render (FFmpeg, tiny media),
export and the renders list (docs/CONTRACT-v0.1.md sections 3 to 5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sigma_editor.edit.builder import read_scene_plan
from sigma_editor.edit.renderer import read_state
from sigma_editor.render import ffmpeg as ff
from test_render_fixtures import (
    make_render_tmp_fixture,
    requires_ffmpeg,
    write_music_track,
    write_picture,
    write_silence_wav,
)

pytestmark = requires_ffmpeg
render_tmp = make_render_tmp_fixture()

CAPTIONS = (
    "The first morning was quiet. We walked down to the harbour before anyone else was up. "
    "By noon the whole town had woken. The last evening ended with a storm over the bay."
)


def media(render_tmp: Path, pictures: int = 4, aspect: str = "16:9") -> dict[str, Any]:
    folder = render_tmp / "in"
    folder.mkdir(exist_ok=True)
    return {
        "pictures": [
            write_picture(folder / f"holiday_{n}.png", aspect, n) for n in range(1, pictures + 1)
        ],
        "narration": write_silence_wav(folder / "narration.wav", 14.0),
        "music": write_music_track(folder, "calm.wav", licence=False),
    }


def create(client: TestClient, files: dict[str, Any], *, name: str = "Holiday", fmt: str = "long",
           caption_text: str = CAPTIONS, items: list[dict[str, Any]] | None = None,
           narration: bool = True, music: bool = True) -> dict[str, Any]:
    form = {
        "name": name, "format": fmt, "language": "English", "order": "as_given",
        "items": items if items is not None else [
            {"file": p.name, "caption": "Day one" if i == 0 else "", "keep_sound": False}
            for i, p in enumerate(files["pictures"])
        ],
        "caption_text": caption_text, "music_licence_note": "Own recording",
        "keep_clip_sound": False,
    }
    parts = [("files", (p.name, p.read_bytes(), "image/png")) for p in files["pictures"]]
    if narration:
        parts.append(("narration", ("narration.wav", files["narration"].read_bytes(), "audio/wav")))
    if music:
        parts.append(("music", ("calm.wav", files["music"].read_bytes(), "audio/wav")))
    response = client.post("/api/edits", data={"form": json.dumps(form)}, files=parts)
    assert response.status_code == 201, response.text
    return response.json()


def payload_of(client: TestClient, project_id: str) -> dict[str, Any]:
    response = client.get(f"/api/edits/{project_id}/edit")
    assert response.status_code == 200, response.text
    return response.json()


# New edit from files ----------------------------------------------------------------------------


def test_new_edit_from_files_builds_the_first_timeline(
    client: TestClient, render_tmp: Path
) -> None:
    project = create(client, media(render_tmp))
    assert project["status"] == "ready" and project["kind"] == "edit"
    assert 14.0 <= project["duration_s"] <= 15.5  # the narration plus the engine's short tail
    folder = Path(project["folder"])
    assert (folder / "01_media" / "calm.wav").is_file()
    assert (folder / "05_voice" / "voice.wav").is_file()
    assert (folder / "05_voice" / "original-narration.wav").is_file()
    assert (folder / "05_voice" / "timing.json").is_file()
    assert sorted(p.name for p in (folder / "06_images").iterdir()) == [
        "scene_01.png", "scene_02.png", "scene_03.png", "scene_04.png",
    ]
    assert (folder / "07_edit" / "scenes.json").is_file()
    assert (folder / "07_edit" / "timeline.json").is_file()
    # the narration's original is not offered as music
    assert not list((folder / "01_media").glob("*narration*"))

    payload = payload_of(client, project["id"])
    assert payload["summary"]["scene_count"] == 4
    assert payload["summary"]["caption_cues"] == 4 and payload["summary"]["popup_count"] == 1
    assert payload["music_path"].endswith("calm.wav") and payload["music_license_ok"] is True
    assert [t["name"] for t in payload["music_tracks"]] == ["calm"]
    assert payload["presets"] == ["1080p"]
    assert payload["available_presets"] == ["720p", "1080p", "2160p"]
    assert all(check["passed"] for check in payload["checks"]), payload["checks"]
    assert payload["timing_source"] == "estimated" and payload["rendering"] is False
    assert [s["popup_text"] for s in payload["scenes"]][0] == "Day one"
    timing = client.get(f"/api/edits/{project['id']}/timing").json()
    assert len(timing["sentences"]) == 4 and timing["nudged"] is False
    assert timing["sentences"][0]["start_frame"] == 0
    rows = client.get("/api/edits").json()
    assert [row["name"] for row in rows] == ["Holiday"] and rows[0]["status"] == "ready"


def test_without_narration_each_picture_gets_six_seconds(
    client: TestClient, render_tmp: Path
) -> None:
    project = create(
        client, media(render_tmp, pictures=2), caption_text="", narration=False, music=False
    )
    payload = payload_of(client, project["id"])
    assert payload["summary"]["scene_count"] == 2
    assert 12.0 <= payload["summary"]["duration_s"] <= 13.0  # 6 s each, plus the tail
    assert payload["music_path"] is None and payload["summary"]["caption_cues"] == 0


def test_bad_forms_are_refused_in_plain_english(client: TestClient, render_tmp: Path) -> None:
    files = media(render_tmp, pictures=1)
    form = {"name": "Nothing", "format": "long", "language": "English", "order": "as_given",
            "items": [], "caption_text": "", "music_licence_note": "", "keep_clip_sound": False}
    none = client.post("/api/edits", data={"form": json.dumps(form)})
    assert none.status_code == 422 and "at least one picture" in none.json()["detail"]
    notes = client.post(
        "/api/edits", data={"form": json.dumps({**form, "items": [{"file": "notes.txt"}]})},
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert notes.status_code == 422 and "not a picture or a clip" in notes.json()["detail"]
    broken = client.post("/api/edits", data={"form": "{not json"},
                         files=[("files", (files["pictures"][0].name, b"x", "image/png"))])
    assert broken.status_code == 422 and "form is not right" in broken.json()["detail"]
    assert client.get("/api/edits").json() == []


# The simple choices and the autosave ----------------------------------------------------------


def test_choices_and_the_timeline_autosave(client: TestClient, render_tmp: Path) -> None:
    project = create(client, media(render_tmp))
    base = f"/api/edits/{project['id']}"
    changed = client.post(f"{base}/edit", json={
        "presets": ["720p"], "captions_enabled": False, "popups_enabled": False,
        "music_gain_db": -12, "music_duck_db": -20,
    })
    assert changed.status_code == 200, changed.text
    payload = changed.json()
    assert payload["presets"] == ["720p"] and payload["captions_enabled"] is False
    assert payload["popups_enabled"] is False
    assert payload["timeline"]["music"]["gain_db"] == -12
    assert payload["timeline"]["music"]["duck_db"] == -20
    assert read_state(Path(project["folder"]) / "07_edit")["popups_enabled"] is False

    unknown = client.post(f"{base}/edit", json={"presets": ["8k"]})
    assert unknown.status_code == 422 and "not a render size" in unknown.json()["detail"]
    missing = client.post(f"{base}/edit", json={"music_path": "nope.mp3"})
    assert missing.status_code == 422 and "was not found" in missing.json()["detail"]
    silent = client.post(f"{base}/edit", json={"music_path": ""}).json()
    assert silent["music_path"] is None and silent["music_license_ok"] is False
    back = client.post(f"{base}/edit", json={"music_path": "calm"}).json()
    assert back["music_path"].endswith("calm.wav") and back["music_license_ok"] is True

    timeline = back["timeline"]
    stale = client.put(
        f"{base}/timeline", json={"timeline": timeline, "revision": timeline["revision"] - 1}
    )
    assert stale.status_code == 409 and "Reload" in stale.json()["detail"]
    timeline["scenes"][0]["transition_out"] = {"type": "push_left", "duration_s": 0.4}
    saved = client.put(
        f"{base}/timeline", json={"timeline": timeline, "revision": timeline["revision"]}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == timeline["revision"] + 1
    fresh = payload_of(client, project["id"])
    assert fresh["scenes"][0]["transition"] == "push_left"
    assert fresh["timeline"]["revision"] == timeline["revision"] + 1

    rebuilt = client.post(f"{base}/edit", json={"rebuild": True}).json()
    assert rebuilt["summary"]["scene_count"] == 4
    assert rebuilt["scenes"][0]["transition"] == "push_left"  # the person's cut is kept

    # A scene split in the editor (two scenes, one picture) reaches the scene plan too, so a
    # rebuild keeps the split.
    doc = rebuilt["timeline"]
    first = doc["scenes"][0]
    middle = round((first["start_s"] + first["end_s"]) / 2, 3)
    left = {**first, "end_s": middle, "sentence_ids": first["sentence_ids"][:1]}
    right = {**first, "start_s": middle, "sentence_ids": first["sentence_ids"][1:]}
    doc["scenes"] = [left, right, *[{**s, "index": s["index"] + 1} for s in doc["scenes"][1:]]]
    right["index"] = 1
    split = client.put(f"{base}/timeline", json={"timeline": doc, "revision": doc["revision"]})
    assert split.status_code == 200, split.text
    plan = read_scene_plan(Path(project["folder"]))
    assert plan is not None and len(plan.scenes) == 5
    assert plan.scenes[0].image.path == plan.scenes[1].image.path
    again = client.post(f"{base}/edit", json={"rebuild": True}).json()
    assert again["summary"]["scene_count"] == 5


# Scenes ----------------------------------------------------------------------------------------


def test_scenes_are_added_replaced_reordered_and_removed(
    client: TestClient, render_tmp: Path
) -> None:
    files = media(render_tmp, pictures=3)
    project = create(client, files)
    base = f"/api/edits/{project['id']}"
    extra = write_picture(render_tmp / "extra.png", "16:9", 9)

    wrong = client.post(f"{base}/scenes", files={"file": ("notes.txt", b"x", "text/plain")})
    assert wrong.status_code == 415

    added = client.post(
        f"{base}/scenes", files={"file": ("extra.png", extra.read_bytes(), "image/png")},
        data={"caption": "Extra"},
    )
    assert added.status_code == 200, added.text
    assert added.json()["payload"]["summary"]["scene_count"] == 4
    assert isinstance(added.json()["warnings"], list)
    images = Path(project["folder"]) / "06_images"
    assert (images / "scene_04.png").is_file()

    before = (images / "scene_01.png").read_bytes()
    replaced = client.post(f"{base}/scenes/0/picture",
                           files={"file": ("new.png", extra.read_bytes(), "image/png")})
    assert replaced.status_code == 200, replaced.text
    assert (images / "scene_01.png").read_bytes() != before

    reordered = client.post(f"{base}/scenes/reorder", json={"order": [3, 0, 1, 2]})
    assert reordered.status_code == 200, reordered.text
    scenes = reordered.json()["payload"]["scenes"]
    assert [s["index"] for s in scenes] == [0, 1, 2, 3]
    assert scenes[0]["popup_text"] == "Extra"

    removed = client.delete(f"{base}/scenes/0")
    assert removed.status_code == 200, removed.text
    assert removed.json()["payload"]["summary"]["scene_count"] == 3
    assert removed.json()["payload"]["scenes"][0]["popup_text"] != "Extra"
    timing = client.get(f"{base}/timing").json()
    assert len(timing["sentences"]) == 4  # the narration keeps its sentences


# Captions --------------------------------------------------------------------------------------


def test_the_caption_table_and_the_srt_file(client: TestClient, render_tmp: Path) -> None:
    project = create(client, media(render_tmp))
    base = f"/api/edits/{project['id']}"
    captions = client.get(f"{base}/captions").json()
    assert captions["enabled"] is True and len(captions["cues"]) == 4
    cues = captions["cues"]
    cues[0]["text"] = "The first morning was very quiet."
    cues.append({"id": "", "start_s": 5.0, "end_s": 4.0, "text": "ends before it starts"})
    cues.append({"id": "", "start_s": 13.0, "end_s": 14.0, "text": "   "})
    saved = client.put(f"{base}/captions", json={"cues": cues})
    assert saved.status_code == 200, saved.text
    assert [c["text"] for c in saved.json()["cues"]][0] == "The first morning was very quiet."
    assert len(saved.json()["cues"]) == 4 and saved.json()["revision"] == captions["revision"] + 1
    assert saved.json()["cues"][0]["edited"] is True
    srt = client.get(f"{base}/captions.srt")
    assert srt.status_code == 200
    assert srt.headers["content-disposition"].startswith("attachment;")
    assert srt.text.startswith("1\n00:00:00,000 --> ") and "very quiet" in srt.text
    assert payload_of(client, project["id"])["summary"]["caption_cues"] == 4


# Render, export and the renders list -----------------------------------------------------------


def test_render_export_and_the_renders_list(client: TestClient, render_tmp: Path,
                                            data_dirs: dict[str, Path]) -> None:
    assert client.put("/api/settings", json={"render": {
        "default_presets": ["720p"], "quality": "standard", "x264_preset": "ultrafast",
        "two_pass_best": False, "h265_copy": False, "enable_4k": False,
        "transition_pack": "cinematic", "motion_style": "ken_burns",
    }}).status_code == 200
    project = create(client, media(render_tmp, pictures=3))
    base = f"/api/edits/{project['id']}"
    jobs = client.app.state.jobs
    assert client.get("/api/renders").json() == []
    assert client.post(f"{base}/export", json={"presets": []}).status_code == 422

    started = client.post(f"{base}/render", json={"presets": []})
    assert started.status_code == 202, started.text
    assert "preview only" in started.json()["message"]
    assert client.get(f"{base}/render/status").json()["running"] is True
    busy = client.post(f"{base}/edit", json={"captions_enabled": False})
    assert busy.status_code == 409
    assert jobs.wait(project["id"], timeout=300) is not None
    status = client.get(f"{base}/render/status").json()
    assert status["running"] is False and status["error"] is None, status
    assert status["rendered"] == ["proxy"]
    payload = payload_of(client, project["id"])
    assert payload["proxy_url"].endswith("/files/07_edit/proxy.mp4")
    assert {r["preset"]: r["status"] for r in payload["renders"]}["720p"] == "not_rendered"
    assert client.get(f"/api/edits/{project['id']}").json()["status"] == "ready"
    nothing = client.post(f"{base}/export", json={"presets": []})
    assert nothing.status_code == 422 and "render a size first" in nothing.json()["detail"]

    started = client.post(f"{base}/render", json={"presets": ["720p"], "quality": "standard"})
    assert started.status_code == 202 and "720p" in started.json()["message"]
    assert jobs.wait(project["id"], timeout=300) is not None
    status = client.get(f"{base}/render/status").json()
    assert status["rendered"] == ["720p"] and status["kept"] == ["proxy"], status
    final = Path(project["folder"]) / "07_edit" / "final_720p.mp4"
    assert final.is_file()
    info = ff.probe_media(final)
    assert (info.width, info.height) == (1280, 720)
    assert abs(info.duration_s - payload["summary"]["duration_s"]) < 0.5
    assert client.get(f"/api/edits/{project['id']}").json()["status"] == "rendered"
    payload = payload_of(client, project["id"])
    renders = {r["preset"]: r for r in payload["renders"]}
    assert renders["720p"]["status"] == "done" and renders["720p"]["width"] == 1280
    assert payload["quality"] == "standard" and payload["upload_preset"] == "none"

    part = client.post(
        f"{base}/render", json={"presets": ["720p"], "range": {"start_s": 1.0, "end_s": 3.0}}
    )
    assert part.status_code == 202 and "range" in part.json()["message"]
    assert jobs.wait(project["id"], timeout=300) is not None
    piece = Path(project["folder"]) / "07_edit" / "range" / "final_720p.mp4"
    assert piece.is_file() and abs(ff.probe_duration(piece) - 2.0) < 0.3
    assert final.is_file()  # the whole render is untouched

    exported = client.post(f"{base}/export", json={"presets": []})
    assert exported.status_code == 200, exported.text
    folder = Path(exported.json()["folder"])
    assert folder.parent == data_dirs["exports"]
    labels = [f["label"] for f in exported.json()["files"]]
    assert labels == ["720p, Standard quality", "Poster frame"]
    assert (folder / "export.txt").is_file()
    assert (folder / exported.json()["files"][0]["name"]).is_file()
    assert client.get(f"/api/edits/{project['id']}").json()["status"] == "exported"
    record = json.loads((Path(project["folder"]) / "08_export" / "export.json").read_text("utf-8"))
    assert record["export_folder"] == str(folder)

    rows = client.get("/api/renders").json()
    assert len(rows) == 1 and rows[0]["preset"] == "720p" and rows[0]["exported"] is True
    assert rows[0]["url"].endswith("/files/07_edit/final_720p.mp4")
    assert rows[0]["quality"] == "Standard"
    assert client.post(f"{base}/render/cancel").status_code == 204
