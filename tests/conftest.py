"""Shared fixtures: every test gets a fresh app with its own temporary data folders.

The ``SGE_*`` environment variables are set before ``create_app()`` runs, which is how the
backend learns where to keep its settings and the edits. Two accounts exist in every test
(``user1`` and ``user2``, password :data:`TEST_PASSWORD`); the shared ``client`` is signed in
as ``user1``, ``anon_client`` is not signed in.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sigma_editor.app import create_app
from sigma_editor.auth.passwords import hash_password
from sigma_editor.auth.users import User, write_users_file
from sigma_editor.config import load_settings

TEST_PASSWORD = "test-password-1"
TEST_HASH = hash_password(TEST_PASSWORD)
TEST_USERS = ("user1", "user2")


@pytest.fixture
def data_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Short folder names keep the temp paths well under the Windows 260-character limit."""
    folders = {
        "app": tmp_path / "app",
        "projects": tmp_path / "prj",
        "exports": tmp_path / "exp",
        "config": tmp_path / "cfg",
    }
    monkeypatch.setenv("SGE_APP_DATA_DIR", str(folders["app"]))
    monkeypatch.setenv("SGE_CONFIG_DIR", str(folders["config"]))
    # The folders come from settings.json, as on a real laptop, so the Settings screen's
    # changes are not shadowed by an environment variable (which always wins).
    monkeypatch.delenv("SGE_PROJECTS_DIR", raising=False)
    monkeypatch.delenv("SGE_EXPORTS_DIR", raising=False)
    monkeypatch.delenv("SGE_PORT", raising=False)
    monkeypatch.delenv("SGE_FRONTEND_DIST", raising=False)
    folders["app"].mkdir(parents=True, exist_ok=True)
    folders["config"].mkdir(parents=True, exist_ok=True)
    (folders["app"] / "settings.json").write_text(
        json.dumps(
            {"projects_dir": str(folders["projects"]), "exports_dir": str(folders["exports"])}
        ),
        encoding="utf-8",
    )
    accounts = [
        User(username=name, display_name=name.title(), password_hash=TEST_HASH)
        for name in TEST_USERS
    ]
    write_users_file(folders["app"] / "users.json", accounts)
    return folders


@pytest.fixture
def settings(data_dirs: dict[str, Path]):
    return load_settings()


@pytest.fixture
def app(data_dirs: dict[str, Path]):
    return create_app()


def sign_in(client: TestClient, username: str = "user1", password: str = TEST_PASSWORD) -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password, "remember": False}
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    """A client signed in as user1."""
    with TestClient(app) as test_client:
        sign_in(test_client)
        yield test_client


@pytest.fixture
def anon_client(app) -> Iterator[TestClient]:
    """A client that is not signed in."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def quiet_explorer(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Records the folders "Open folder" would show instead of starting Explorer."""
    opened: list[Path] = []
    from sigma_editor.api import edits

    monkeypatch.setattr(edits, "open_folder", lambda folder: opened.append(Path(folder)))
    return opened


@pytest.fixture(autouse=True)
def _no_cwd_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own FFMPEG_PATH must not leak into a test that checks the defaults."""
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.delenv("FFPROBE_PATH", raising=False)
    os.environ.pop("SGE_FONTS_DIR", None)
