"""Hardening: a 422 never repeats what was typed, writes from other websites are refused (the
app's own page and the dev server pass), every error is JSON with a plain-English detail, the
app's page survives a bad address, and file names are made safe for Windows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_PASSWORD, sign_in
from sigma_editor.app import create_app
from sigma_editor.storage import project_store
from sigma_editor.storage.files import safe_file_name
from sigma_editor.storage.project_store import ProjectStore

SECRET = "ZQX-secret-value-that-must-not-come-back-ABCDEFGHIJKLMNOP"


# What was typed never comes back inside an error -------------------------------------------


def test_422_never_echoes_submitted_values(client: TestClient, edit: tuple[str, Path]) -> None:
    project_id, _folder = edit
    url = f"/api/edits/{project_id}/update"
    # A name over the 200-character limit: the answer says so without repeating it.
    response = client.post(url, json={"name": SECRET + "x" * 200})
    assert response.status_code == 422
    assert "ZQX" not in response.text
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail
    for item in detail:
        assert set(item) == {"type", "loc", "msg"}
    assert detail[0]["loc"] == ["body", "name"]
    assert "200" in detail[0]["msg"]

    # Wrong types, an over-long note and a body that is not an object: type/loc/msg only.
    for body in (
        {"name": 12345678},
        {"language": [SECRET]},
        {"music_licence_note": SECRET + "y" * 600},
        [SECRET],
    ):
        response = client.post(url, json=body)
        assert response.status_code == 422, body
        assert "12345678" not in response.text and "ZQX" not in response.text
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail
        for item in detail:
            assert set(item) == {"type", "loc", "msg"}

    # The Settings screen's values are not repeated either.
    response = client.put("/api/settings", json={"render": {"quality": SECRET}})
    assert response.status_code == 422
    assert "ZQX" not in response.text
    # Nothing was saved.
    assert client.get(f"/api/edits/{project_id}").json()["name"] == "Kind Ledger"
    assert client.get("/api/settings").json()["render"]["quality"] == "standard"


# Writes must come from the app's own page ----------------------------------------------------


def test_writes_from_another_website_are_refused(
    client: TestClient, edit: tuple[str, Path], data_dirs: dict[str, Path]
) -> None:
    project_id, folder = edit
    evil = {"Origin": "https://evil.example", "Referer": "https://evil.example/"}

    # A multipart upload needs no preflight, so CORS alone would let it through.
    upload = client.post(
        f"/api/projects/{project_id}/upload/media",
        files={"file": ("payload.mov", b"x", "application/octet-stream")},
        headers=evil,
    )
    assert upload.status_code == 403
    assert "another website" in upload.json()["detail"]
    assert "access-control-allow-origin" not in upload.headers
    assert not (folder / "01_media" / "uploads").exists()

    settings = client.put("/api/settings", json={"render": {"quality": "best"}}, headers=evil)
    assert settings.status_code == 403
    on_disk = json.loads((data_dirs["app"] / "settings.json").read_text(encoding="utf-8"))
    assert "render" not in on_disk

    renamed = client.post(f"/api/edits/{project_id}/update", json={"name": "Hacked"},
                          headers=evil)
    assert renamed.status_code == 403
    assert client.delete(f"/api/edits/{project_id}", headers=evil).status_code == 403
    assert client.post(f"/api/projects/{project_id}/popups/preview", json={"text": "Hi"},
                       headers=evil).status_code == 403
    assert client.get(f"/api/edits/{project_id}").json()["name"] == "Kind Ledger"

    # "Origin: null" (sandboxed frames, file:// pages) is another site too.
    nulled = client.put(
        "/api/settings", json={"render": {"quality": "best"}}, headers={"Origin": "null"}
    )
    assert nulled.status_code == 403

    # Reads are not blocked here; CORS decides whether the page may see them.
    assert client.get("/api/edits", headers=evil).status_code == 200
    assert client.get(f"/api/projects/{project_id}/files/project.json",
                      headers=evil).status_code == 200


def test_writes_from_the_apps_own_page_and_the_dev_server_pass(app) -> None:
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        own_page = {"Origin": "http://127.0.0.1:8766"}
        signed_in = client.post(
            "/api/auth/login",
            json={"username": "user1", "password": TEST_PASSWORD, "remember": False},
            headers=own_page,
        )
        assert signed_in.status_code == 200, signed_in.text
        own = client.put("/api/settings", json={"render": {"quality": "high"}}, headers=own_page)
        assert own.status_code == 200, own.text
        assert own.json()["render"]["quality"] == "high"

        dev = client.put(
            "/api/settings",
            json={"render": {"quality": "best"}},
            headers={"Origin": "http://localhost:5173"},
        )
        assert dev.status_code == 200
        assert dev.headers.get("access-control-allow-origin") == "http://localhost:5173"

        # A name that merely resolves to this PC (DNS rebinding) is not this server.
        rebound = client.put(
            "/api/settings",
            json={"render": {"quality": "standard"}},
            headers={"Origin": "http://evil.example:8766", "Host": "evil.example:8766"},
        )
        assert rebound.status_code == 403
        assert client.get("/api/settings").json()["render"]["quality"] == "best"


# Every error is JSON with a plain-English detail ---------------------------------------------


def test_edit_save_failure_is_a_json_500_with_a_reason(
    client: TestClient, edit: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, _folder = edit

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(project_store, "atomic_write_text", refuse)
    response = client.post(
        f"/api/edits/{project_id}/update", json={"music_licence_note": "bought on Artlist"}
    )
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert "could not be written" in response.json()["detail"]


def test_unexpected_errors_are_json_not_plain_text(app, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(self: ProjectStore) -> list[object]:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ProjectStore, "list", explode)
    with TestClient(app, raise_server_exceptions=False) as client:
        sign_in(client)
        response = client.get("/api/edits")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"].startswith("Something went wrong")
    assert "disk on fire" not in response.text


def test_spa_route_survives_a_bad_address(
    data_dirs: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    index = "<!doctype html><title>9Sigma Editor</title>"
    (dist / "index.html").write_text(index, encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("SGE_FRONTEND_DIST", str(dist))
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.get("/app.js").text == "console.log(1)"
        # The page itself needs no sign-in (it shows the sign-in screen).
        for path in ("/", "/login", "/%00", "/edits/%00x", "/" + "a" * 300,
                     "/%2e%2e/%2e%2e/pyproject.toml"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "9Sigma Editor" in response.text
            assert "[project]" not in response.text  # nothing outside the built page
        # API addresses are never swallowed by the page.
        assert client.get("/api/nothing-here").status_code == 401
        sign_in(client)
        assert client.get("/api/nothing-here").status_code == 404


# File names ------------------------------------------------------------------------------


def test_safe_file_name_handles_windows_device_names_and_long_names() -> None:
    assert safe_file_name("CON") == "CON_"
    assert safe_file_name("nul.txt") == "nul_.txt"
    assert safe_file_name("LPT1") == "LPT1_"
    assert safe_file_name("console.txt") == "console.txt"  # only the exact device names
    assert safe_file_name("a" * 200 + ".pdf") == "a" * 110 + ".pdf"
    assert safe_file_name("notes." + "x" * 40) == "notes." + "x" * 40
    assert safe_file_name("Title Writing Prompt.txt") == "Title Writing Prompt.txt"
    # The base name only, with the characters Windows refuses replaced.
    assert safe_file_name("C:\\Users\\me\\My Clip?.mp4") == "My Clip_.mp4"
    assert safe_file_name("a/b/c.mp4") == "c.mp4"
    assert safe_file_name("take\x01<1>.wav") == "take__1_.wav"
    assert safe_file_name(" .hidden. ") == "hidden"
    assert safe_file_name("") == "file"
    assert safe_file_name("..", fallback="clip") == "clip"
