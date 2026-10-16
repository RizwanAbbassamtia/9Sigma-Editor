"""The CapCut-style start (docs/CONTRACT-v0.2.md sections 1 and 2): an empty project, the
media library (import, list, remove), scenes added from the library (a clip is used in
place and keeps playing after a rebuild), the narration set later, and the scene lengths of
an edit without narration surviving Add."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from test_render_fixtures import (
    make_render_tmp_fixture,
    requires_ffmpeg,
    write_picture,
    write_silence_wav,
)

pytestmark = requires_ffmpeg
render_tmp = make_render_tmp_fixture()


def write_clip(path: Path, seconds: float = 2.0, size: str = "320x180", sound: bool = True) -> Path:
    """A tiny test clip (a moving test pattern, with a tone when ``sound``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [shutil.which("ffmpeg") or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=30"]
    if sound:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-shortest",
                str(path)]
    subprocess.run(command, check=True, capture_output=True)
    return path


def blank(client: TestClient, name: str = "Fresh start") -> dict[str, Any]:
    response = client.post(
        "/api/edits/blank", json={"name": name, "format": "long", "language": "English"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def payload_of(client: TestClient, project_id: str) -> dict[str, Any]:
    response = client.get(f"/api/edits/{project_id}/edit")
    assert response.status_code == 200, response.text
    return response.json()


def upload(client: TestClient, project_id: str, *files: Path) -> dict[str, Any]:
    parts = [("files", (f.name, f.read_bytes(), "application/octet-stream")) for f in files]
    response = client.post(f"/api/edits/{project_id}/media", files=parts)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_empty_project_then_media_from_the_library(client: TestClient, render_tmp: Path) -> None:
    project = blank(client)
    assert project["status"] == "new" and project["duration_s"] == 0
    folder = Path(project["folder"])
    assert (folder / "07_edit" / "timeline.json").is_file()
    assert (folder / "05_voice" / "voice.wav").is_file()
    payload = payload_of(client, project["id"])
    assert payload["summary"]["scene_count"] == 0 and payload["timeline"]["scenes"] == []
    assert payload["timeline"]["width"] == 1920 and payload["presets"] == ["1080p"]
    assert client.get(f"/api/edits/{project['id']}/media").json()["items"] == []
    bundle = client.get(f"/api/projects/{project['id']}/timeline")
    assert bundle.status_code == 200 and bundle.json()["timeline"]["scenes"] == []

    picture = write_picture(render_tmp / "in" / "beach.png", "16:9", 1)
    clip = write_clip(render_tmp / "in" / "walk.mp4")
    sound = write_silence_wav(render_tmp / "in" / "voice.wav", 3.0)
    library = upload(client, project["id"], picture, clip, sound)
    items = {item["name"]: item for item in library["items"]}
    assert sorted(items) == ["beach.png", "voice.wav", "walk.mp4"]
    assert items["beach.png"]["kind"] == "picture" and items["beach.png"]["width"] == 1024
    assert items["beach.png"]["thumb_url"].startswith("/api/projects/")
    assert items["walk.mp4"]["kind"] == "clip" and 1.9 <= items["walk.mp4"]["duration_s"] <= 2.2
    assert items["walk.mp4"]["width"] == 320 and items["walk.mp4"]["thumb_url"].endswith(".jpg")
    assert (folder / "07_edit" / "preview" / "posters").is_dir()
    assert items["voice.wav"]["kind"] == "audio" and abs(items["voice.wav"]["duration_s"] - 3) < 0.1
    assert all(item["in_use"] == [] for item in library["items"])
    assert (folder / "01_media" / ".library.json").is_file()
    # the poster frame is served through the files route
    poster = client.get(items["walk.mp4"]["thumb_url"])
    assert poster.status_code == 200 and poster.headers["content-type"].startswith("image/jpeg")

    rejected = client.post(
        f"/api/edits/{project['id']}/media",
        files=[("files", ("notes.docx", b"not media", "application/octet-stream"))],
    )
    assert rejected.status_code == 422 and "notes.docx" in rejected.json()["detail"]

    # Add to video from the library: a picture (copied into 06_images) and a clip (used in place)
    added = client.post(f"/api/edits/{project['id']}/scenes", data={"media": "01_media/beach.png"})
    assert added.status_code == 200, added.text
    assert added.json()["payload"]["summary"]["scene_count"] == 1
    assert 6.0 <= added.json()["payload"]["summary"]["duration_s"] <= 7.0
    added = client.post(
        f"/api/edits/{project['id']}/scenes",
        data={"media": "01_media/walk.mp4", "keep_sound": "true"},
    )
    assert added.status_code == 200, added.text
    timeline = added.json()["payload"]["timeline"]
    assert len(timeline["scenes"]) == 2
    clip_scene = timeline["scenes"][1]
    assert clip_scene["clip"]["path"] == "01_media/walk.mp4"
    assert clip_scene["clip"]["keep_sound"] is True and clip_scene["clip"]["speed"] == 1.0
    assert 1.9 <= clip_scene["clip"]["duration_s"] <= 2.2
    # the clip's own length (plus the engine's tail)
    assert 1.9 <= clip_scene["end_s"] - clip_scene["start_s"] <= 3.1
    assert timeline["scenes"][0]["clip"] is None
    assert len(list((folder / "01_media").glob("walk*.mp4"))) == 1  # not copied again
    library = client.get(f"/api/edits/{project['id']}/media").json()
    items = {item["name"]: item for item in library["items"]}
    assert items["walk.mp4"]["in_use"] == ["scene 2"] and items["beach.png"]["in_use"] == []

    # a file the timeline uses cannot be removed; a copied picture can
    busy = client.delete(f"/api/edits/{project['id']}/media", params={"path": "01_media/walk.mp4"})
    assert busy.status_code == 409 and "scene 2" in busy.json()["detail"]
    gone = client.delete(f"/api/edits/{project['id']}/media", params={"path": "01_media/beach.png"})
    assert gone.status_code == 200
    assert "beach.png" not in {item["name"] for item in gone.json()["items"]}
    missing = client.delete(
        f"/api/edits/{project['id']}/media", params={"path": "01_media/beach.png"}
    )
    assert missing.status_code == 404
    outside = client.post(f"/api/edits/{project['id']}/scenes", data={"media": "../project.json"})
    assert outside.status_code == 404
    nothing = client.post(f"/api/edits/{project['id']}/scenes", data={})
    assert nothing.status_code == 422

    # the narration, set later, with its caption text: the timing is estimated over it
    narrated = client.post(
        f"/api/edits/{project['id']}/narration",
        data={
            "media": "01_media/voice.wav",
            "caption_text": "One sentence here. And a second one.",
        },
    )
    assert narrated.status_code == 200, narrated.text
    payload = narrated.json()["payload"]
    assert payload["timing_source"] == "estimated" and payload["summary"]["scene_count"] == 2
    timing = client.get(f"/api/edits/{project['id']}/timing").json()
    assert [s["text"] for s in timing["sentences"]] == ["One sentence here.", "And a second one."]
    assert abs(timing["duration_s"] - 3.0) < 0.1
    assert (folder / "05_voice" / "original-voice.wav").is_file()
    # the clip scene keeps its clip through the rebuild
    assert payload["timeline"]["scenes"][1]["clip"]["path"] == "01_media/walk.mp4"
    assert payload["timeline"]["scenes"][1]["clip"]["keep_sound"] is True
    rows = client.get("/api/edits").json()
    assert rows[0]["status"] == "ready"


def test_scene_lengths_survive_add_without_narration(client: TestClient, render_tmp: Path) -> None:
    project = blank(client, "Pictures only")
    pictures = [write_picture(render_tmp / "in" / f"p{n}.png", "16:9", n) for n in (1, 2, 3)]
    upload(client, project["id"], *pictures)
    for name in ("p1.png", "p2.png"):
        added = client.post(
            f"/api/edits/{project['id']}/scenes", data={"media": f"01_media/{name}"}
        )
        assert added.status_code == 200, added.text
    payload = payload_of(client, project["id"])
    timeline = payload["timeline"]
    assert payload["timing_source"] == "scenes"
    # the person makes the first picture 4 s long (a ripple edit saved whole by the autosave)
    scenes = timeline["scenes"]
    shift = scenes[0]["end_s"] - 4.0
    scenes[0]["end_s"] = 4.0
    scenes[1]["start_s"] = round(scenes[1]["start_s"] - shift, 3)
    scenes[1]["end_s"] = round(scenes[1]["end_s"] - shift, 3)
    timeline["duration_s"] = scenes[1]["end_s"]
    saved = client.put(
        f"/api/edits/{project['id']}/timeline",
        json={"timeline": timeline, "revision": timeline["revision"]},
    )
    assert saved.status_code == 200, saved.text
    # a third picture is added: the first keeps its 4 s, the new one gets the usual 6 s
    added = client.post(f"/api/edits/{project['id']}/scenes", data={"media": "01_media/p3.png"})
    assert added.status_code == 200, added.text
    scenes = added.json()["payload"]["timeline"]["scenes"]
    assert len(scenes) == 3
    assert abs((scenes[0]["end_s"] - scenes[0]["start_s"]) - 4.0) < 0.05
    assert abs((scenes[2]["end_s"] - scenes[2]["start_s"]) - 6.0) < 1.0  # 6 s plus the tail
