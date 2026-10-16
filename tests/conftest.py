"""Shared fixtures: every test gets a fresh app with its own temporary data folders.

The ``SGE_*`` environment variables are set before ``create_app()`` runs, which is how the
backend learns where to keep its settings and the edits. There is no sign-in in this app.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sigma_editor.app import create_app
from sigma_editor.config import load_settings


@pytest.fixture
def data_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Short folder names keep the temp paths well under the Windows 260-character limit."""
    folders = {
        "app": tmp_path / "app",
        "projects": tmp_path / "prj",
        "exports": tmp_path / "exp",
    }
    monkeypatch.setenv("SGE_APP_DATA_DIR", str(folders["app"]))
    # The folders come from settings.json, as on a real laptop, so the Settings screen's
    # changes are not shadowed by an environment variable (which always wins).
    monkeypatch.delenv("SGE_PROJECTS_DIR", raising=False)
    monkeypatch.delenv("SGE_EXPORTS_DIR", raising=False)
    monkeypatch.delenv("SGE_PORT", raising=False)
    monkeypatch.delenv("SGE_FRONTEND_DIST", raising=False)
    folders["app"].mkdir(parents=True, exist_ok=True)
    (folders["app"] / "settings.json").write_text(
        json.dumps(
            {"projects_dir": str(folders["projects"]), "exports_dir": str(folders["exports"])}
        ),
        encoding="utf-8",
    )
    return folders


@pytest.fixture
def settings(data_dirs: dict[str, Path]):
    return load_settings()


@pytest.fixture
def app(data_dirs: dict[str, Path]):
    return create_app()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
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
