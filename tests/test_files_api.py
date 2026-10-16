"""``GET /api/projects/{id}/files/{path}`` (content types, Range requests, traversal refused)
and ``POST /api/projects/{id}/upload/{stage}`` (safe names, stage folders, bad types)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sigma_editor.api.files import resolve_project_file, safe_upload_name


def write(folder: Path, relative: str, data: bytes) -> Path:
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# Serving --------------------------------------------------------------------------------------


def test_serves_files_with_content_types(client: TestClient, edit: tuple[str, Path]) -> None:
    project_id, folder = edit
    write(folder, "05_voice/voice.wav", b"RIFF" + bytes(96))
    write(folder, "07_edit/proxy.mp4", b"\x00\x00\x00\x18ftypmp42" + bytes(50))
    write(folder, "06_images/scene_01.png", b"\x89PNG\r\n\x1a\n" + bytes(20))
    write(folder, "01_media/cover.jpg", b"\xff\xd8\xff" + bytes(20))
    write(folder, "05_voice/timing.json", b'{"sample_rate": 48000}')
    write(folder, "07_edit/render.log", b"all good")
    write(folder, "08_export/weird.bin", b"xyz")

    cases = {
        "05_voice/voice.wav": "audio/wav",
        "07_edit/proxy.mp4": "video/mp4",
        "06_images/scene_01.png": "image/png",
        "01_media/cover.jpg": "image/jpeg",
        "05_voice/timing.json": "application/json",
        "07_edit/render.log": "text/plain; charset=utf-8",
        "08_export/weird.bin": "application/octet-stream",
    }
    for relative, content_type in cases.items():
        response = client.get(f"/api/projects/{project_id}/files/{relative}")
        assert response.status_code == 200, relative
        assert response.headers["content-type"] == content_type, relative
        assert response.headers["accept-ranges"] == "bytes"
        assert response.content == (folder / relative).read_bytes()
        assert int(response.headers["content-length"]) == len(response.content)
    assert client.get(f"/api/projects/{project_id}/files/05_voice/timing.json").json() == {
        "sample_rate": 48000
    }
    head = client.head(f"/api/projects/{project_id}/files/05_voice/voice.wav")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == "100"


def test_range_requests(client: TestClient, edit: tuple[str, Path]) -> None:
    project_id, folder = edit
    data = bytes(range(256)) * 4  # 1024 bytes
    write(folder, "05_voice/voice.wav", data)
    url = f"/api/projects/{project_id}/files/05_voice/voice.wav"

    first = client.get(url, headers={"Range": "bytes=0-99"})
    assert first.status_code == 206
    assert first.content == data[:100]
    assert first.headers["content-range"] == "bytes 0-99/1024"
    assert first.headers["content-length"] == "100"
    assert first.headers["content-type"] == "audio/wav"

    middle = client.get(url, headers={"Range": "bytes=500-"})
    assert middle.status_code == 206 and middle.content == data[500:]
    assert middle.headers["content-range"] == "bytes 500-1023/1024"

    tail = client.get(url, headers={"Range": "bytes=-24"})
    assert tail.status_code == 206 and tail.content == data[-24:]

    beyond = client.get(url, headers={"Range": "bytes=5000-6000"})
    assert beyond.status_code == 416
    assert beyond.headers["content-range"] == "bytes */1024"

    video = write(folder, "07_edit/proxy.mp4", data)
    assert video.is_file()
    clip = client.get(f"/api/projects/{project_id}/files/07_edit/proxy.mp4",
                      headers={"Range": "bytes=10-19"})
    assert clip.status_code == 206 and clip.content == data[10:20]
    assert clip.headers["content-type"] == "video/mp4"


def test_refuses_paths_outside_the_edit(client: TestClient, edit: tuple[str, Path]) -> None:
    project_id, folder = edit
    secret = folder.parent / "secret.txt"
    secret.write_text("not yours", encoding="utf-8")
    write(folder, "05_voice/voice.wav", b"RIFF")
    attempts = [
        "../secret.txt",
        "05_voice/../../secret.txt",
        "..%2Fsecret.txt",
        "%2e%2e/secret.txt",
        "05_voice/..\\..\\secret.txt",
        "C:/Windows/win.ini",
        "C:\\Windows\\win.ini",
        "//server/share/file",
        "05_voice/voice.wav:stream",
    ]
    for attempt in attempts:
        response = client.get(f"/api/projects/{project_id}/files/{attempt}")
        assert response.status_code == 404, attempt
        assert "not yours" not in response.text
    # Folders and missing files are 404 too, and the folder's own project.json is served.
    assert client.get(f"/api/projects/{project_id}/files/05_voice").status_code == 404
    assert client.get(f"/api/projects/{project_id}/files/05_voice/nope.wav").status_code == 404
    assert client.get(f"/api/projects/{project_id}/files/").status_code == 404
    assert client.get(f"/api/projects/{project_id}/files/project.json").status_code == 200
    assert client.get("/api/projects/no-such-edit/files/project.json").status_code == 404


def test_resolve_project_file_unit(tmp_path: Path) -> None:
    folder = tmp_path / "proj"
    write(folder, "a/b.txt", b"x")
    (tmp_path / "outside.txt").write_text("o", encoding="utf-8")
    assert resolve_project_file(folder, "a/b.txt") == (folder / "a" / "b.txt").resolve()
    assert resolve_project_file(folder, "a\\b.txt") == (folder / "a" / "b.txt").resolve()
    assert resolve_project_file(folder, "./a/./b.txt") == (folder / "a" / "b.txt").resolve()
    for bad in ("../outside.txt", "/outside.txt", "a/../../outside.txt", "", "a",
                str(tmp_path / "outside.txt"), "a/b.txt\x00", "a/b.txt:stream"):
        with pytest.raises(HTTPException) as excinfo:
            resolve_project_file(folder, bad)
        assert excinfo.value.status_code == 404, bad


# Upload -----------------------------------------------------------------------------------------


def upload(client: TestClient, project_id: str, stage: str, name: str, data: bytes,
           scene: int | None = None, content_type: str = "application/octet-stream") -> object:
    form = {"scene": str(scene)} if scene is not None else {}
    return client.post(
        f"/api/projects/{project_id}/upload/{stage}",
        files={"file": (name, io.BytesIO(data), content_type)},
        data=form,
    )


def test_upload_saves_into_the_stage_folder(client: TestClient, edit: tuple[str, Path]) -> None:
    project_id, folder = edit
    response = upload(client, project_id, "voice", "My Take (final).WAV", b"RIFF" + bytes(40))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == "05_voice/uploads/my-take-final.wav"
    assert body["stage"] == "voice" and body["size_bytes"] == 44 and body["scene"] is None
    assert body["name"] == "my-take-final.wav"
    saved = folder / "05_voice" / "uploads" / "my-take-final.wav"
    assert saved.read_bytes() == b"RIFF" + bytes(40)
    # The uploaded file is served back by the files endpoint.
    served = client.get(f"/api/projects/{project_id}/files/{body['path']}")
    assert served.status_code == 200 and served.headers["content-type"] == "audio/wav"
    # A second upload with the same name does not overwrite the first.
    again = upload(client, project_id, "voice", "My Take (final).WAV", b"RIFF2").json()
    assert again["path"] == "05_voice/uploads/my-take-final-2.wav"
    assert saved.read_bytes() == b"RIFF" + bytes(40)
    # Images carry the scene number in the name.
    image = upload(client, project_id, "images", "../../evil/photo.PNG", b"\x89PNG", scene=3).json()
    assert image["path"] == "06_images/uploads/scene_03_photo.png" and image["scene"] == 3
    assert (folder / "06_images" / "uploads" / "scene_03_photo.png").is_file()
    assert not (folder.parent / "evil").exists()
    nameless = upload(client, project_id, "export", ".mp4", b"\x00" * 8).json()
    assert nameless["path"] == "08_export/uploads/upload.mp4"
    # The person's own clips go to the media folder.
    clip = upload(client, project_id, "media", "B-roll Clip.MOV", b"\x00\x00\x00\x14ftypqt").json()
    assert clip["path"] == "01_media/uploads/b-roll-clip.mov" and clip["stage"] == "media"
    served_clip = client.get(f"/api/projects/{project_id}/files/{clip['path']}")
    assert served_clip.status_code == 200
    assert served_clip.headers["content-type"] == "video/quicktime"
    assert not list((folder / "05_voice" / "uploads").glob("*.part"))
    assert not list((folder / "01_media" / "uploads").glob("*.part"))


def test_upload_rejections(client: TestClient, edit: tuple[str, Path]) -> None:
    project_id, _folder = edit
    wrong_type = upload(client, project_id, "voice", "notes.txt", b"hello")
    assert wrong_type.status_code == 415 and "wav" in wrong_type.json()["detail"]
    wrong_image = upload(client, project_id, "images", "clip.mp4", b"x")
    assert wrong_image.status_code == 415
    wrong_media = upload(client, project_id, "media", "notes.docx", b"x")
    assert wrong_media.status_code == 415
    empty = upload(client, project_id, "voice", "silent.wav", b"")
    assert empty.status_code == 422 and "empty" in empty.json()["detail"]
    stage = upload(client, project_id, "title", "x.wav", b"RIFF")
    assert stage.status_code == 404
    assert stage.json()["detail"] == (
        "Uploads go to the voice, images, edit, export or media folder."
    )
    negative = upload(client, project_id, "images", "a.png", b"\x89PNG", scene=-1)
    assert negative.status_code == 422
    missing = upload(client, "no-such-edit", "voice", "a.wav", b"RIFF")
    assert missing.status_code == 404
    no_file = client.post(f"/api/projects/{project_id}/upload/voice", data={"scene": "1"})
    assert no_file.status_code == 422


def test_safe_upload_name_unit() -> None:
    assert safe_upload_name("Take 1.MP3", "voice", None) == "take-1.mp3"
    assert safe_upload_name("C:\\Users\\me\\scene.jpg", "images", 7) == "scene_07_scene.jpg"
    assert safe_upload_name("Première prise.wav", "voice", None) == "premiere-prise.wav"
    assert safe_upload_name(".wav", "voice", None) == "upload.wav"  # a type and no name
    assert safe_upload_name("Clip (1).webm", "media", None) == "clip-1.webm"
    long_name = safe_upload_name("x" * 200 + ".wav", "voice", None)
    assert long_name.endswith(".wav") and len(long_name) <= 64
    with pytest.raises(HTTPException) as excinfo:
        safe_upload_name("danger.exe", "voice", None)
    assert excinfo.value.status_code == 415
    assert "voice step" in excinfo.value.detail and "wav" in excinfo.value.detail
    with pytest.raises(HTTPException) as no_name:
        safe_upload_name(None, "images", None)
    assert no_name.value.status_code == 415
