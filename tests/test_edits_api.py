"""The file-only routes of /api/edits, the settings route and the app itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sigma_editor.app import create_app
from sigma_editor.edit.timing_doc import estimated_timing, write_timing
from sigma_editor.storage.project_store import ProjectStore


def make_edit(data_dirs: dict[str, Path], name: str = "Autumn walk", **fields: object):
    return ProjectStore(data_dirs["projects"]).create(name, **fields)


def test_system_info_and_no_frontend_page(
    client: TestClient, data_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    info = client.get("/api/system/info").json()
    assert info["version"]
    assert info["projects_dir"] == str(data_dirs["projects"])
    assert info["port"] == 8766
    assert "auth_required" not in info
    # Without a built front end (frontend/dist) the page says how to build it.
    monkeypatch.setenv("SGE_FRONTEND_DIST", str(tmp_path / "no-dist"))
    with TestClient(create_app()) as bare:
        page = bare.get("/")
    assert page.status_code == 200
    assert "9Sigma Editor is running" in page.text


def test_list_get_update_delete(client: TestClient, data_dirs: dict[str, Path]):
    assert client.get("/api/edits").json() == []
    project = make_edit(data_dirs, format="shorts")
    rows = client.get("/api/edits").json()
    assert [row["name"] for row in rows] == ["Autumn walk"]
    assert rows[0]["status"] == "new" and rows[0]["format"] == "shorts"
    got = client.get(f"/api/edits/{project.id}").json()
    assert got["kind"] == "edit" and got["folder"] == project.folder

    updated = client.post(
        f"/api/edits/{project.id}/update",
        json={"name": "Autumn walk, final", "music_licence_note": "  bought on  Artlist "},
    ).json()
    assert updated["name"] == "Autumn walk, final"
    assert updated["music_licence_note"] == "bought on Artlist"
    assert Path(updated["folder"]).name.endswith("_autumn-walk-final")

    assert client.delete(f"/api/edits/{project.id}").status_code == 204
    assert client.get("/api/edits").json() == []
    assert client.get(f"/api/edits/{project.id}").status_code == 404
    assert client.delete(f"/api/edits/{project.id}").status_code == 404


def test_update_validation(client: TestClient, data_dirs: dict[str, Path]):
    project = make_edit(data_dirs)
    response = client.post(f"/api/edits/{project.id}/update", json={"name": ""})
    assert response.status_code == 422
    body = response.json()
    assert body["detail"][0]["loc"] == ["body", "name"]
    assert "input" not in body["detail"][0]


def test_open_folder(client: TestClient, data_dirs: dict[str, Path], quiet_explorer: list[Path]):
    project = make_edit(data_dirs)
    response = client.post(f"/api/edits/{project.id}/open-folder", json={})
    assert response.status_code == 200
    assert quiet_explorer == [Path(project.folder)]
    missing = client.post(f"/api/edits/{project.id}/open-folder", json={"target": "export"})
    assert missing.status_code == 404
    export_dir = data_dirs["exports"] / "2026-10-16_autumn-walk"
    export_dir.mkdir(parents=True)
    (Path(project.folder) / "08_export" / "export.json").write_text(
        json.dumps({"export_folder": str(export_dir)}), encoding="utf-8"
    )
    opened = client.post(f"/api/edits/{project.id}/open-folder", json={"target": "export"})
    assert opened.json()["folder"] == str(export_dir)


def test_timing_get_and_nudge(client: TestClient, data_dirs: dict[str, Path]):
    project = make_edit(data_dirs)
    assert client.get(f"/api/edits/{project.id}/timing").status_code == 404
    doc = estimated_timing("One two. Three four. Five six.", 9.0)
    write_timing(Path(project.folder) / "05_voice" / "timing.json", doc)
    got = client.get(f"/api/edits/{project.id}/timing").json()
    assert [s["id"] for s in got["sentences"]] == ["s-001", "s-002", "s-003"]

    moved = client.post(f"/api/edits/{project.id}/timing", json={"index": 1, "start_s": 2.0})
    assert moved.status_code == 200
    assert moved.json()["sentences"][1]["start_s"] == 2.0
    assert moved.json()["nudged"] is True
    again = client.get(f"/api/edits/{project.id}/timing").json()
    assert again["sentences"][1]["start_s"] == 2.0

    bad = client.post(f"/api/edits/{project.id}/timing", json={"index": 7, "start_s": 1.0})
    assert bad.status_code == 422
    both = client.post(
        f"/api/edits/{project.id}/timing", json={"index": 0, "start_s": 1, "shift_s": 1}
    )
    assert both.status_code == 422


def test_other_site_is_refused(client: TestClient, data_dirs: dict[str, Path]):
    project = make_edit(data_dirs)
    refused = client.post(
        f"/api/edits/{project.id}/update",
        json={"name": "Hacked"},
        headers={"Origin": "http://evil.example", "Host": "127.0.0.1:8766"},
    )
    assert refused.status_code == 403
    own = client.post(
        f"/api/edits/{project.id}/update",
        json={"name": "Mine"},
        headers={"Origin": "http://127.0.0.1:8766", "Host": "127.0.0.1:8766"},
    )
    assert own.status_code == 200
    reading = client.get("/api/edits", headers={"Origin": "http://evil.example"})
    assert reading.status_code == 200


def test_settings_round_trip(client: TestClient, data_dirs: dict[str, Path], tmp_path: Path):
    current = client.get("/api/settings").json()
    assert current["render"]["quality"] == "standard"
    assert current["captions"]["preset"] == "bold-outline"
    assert current["quality_labels"]["best"] == "Best"
    new_projects = tmp_path / "elsewhere"
    saved = client.put(
        "/api/settings",
        json={
            "projects_dir": str(new_projects),
            "render": {
                "quality": "high",
                "default_presets": ["1080p", "720p", "1080p"],
                "enable_4k": True,
            },
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["projects_dir"] == str(new_projects)
    assert body["render"]["quality"] == "high"
    assert body["render"]["default_presets"] == ["1080p", "720p"]
    assert body["captions"]["enabled"] is True  # untouched section kept
    assert new_projects.is_dir()
    on_disk = json.loads((data_dirs["app"] / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["render"]["enable_4k"] is True
    # The edit store follows the new folder.
    assert client.get("/api/edits").json() == []
    ProjectStore(new_projects).create("Here now")
    assert [row["name"] for row in client.get("/api/edits").json()] == ["Here now"]
    # Back to the default folder with null.
    reset = client.put("/api/settings", json={"projects_dir": None}).json()
    assert reset["projects_dir"] == reset["default_projects_dir"]
    bad = client.put("/api/settings", json={"render": {"quality": "ultra"}})
    assert bad.status_code == 422
