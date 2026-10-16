"""Health checks (doctor.py) and the sge command line's report."""

from __future__ import annotations

from pathlib import Path

import pytest

from sigma_editor import doctor
from sigma_editor.cli import format_report
from sigma_editor.config import Settings


def test_python_check_ok():
    assert doctor.check_python_version().status == "ok"


def test_folder_checks(tmp_path: Path):
    settings = Settings(
        app_data_dir=tmp_path / "app", projects_dir=tmp_path / "p", exports_dir=tmp_path / "e"
    )
    assert doctor.check_projects_dir(settings).status == "ok"
    assert doctor.check_exports_dir(settings).status == "ok"
    assert (tmp_path / "p").is_dir()


def test_ffmpeg_missing_is_a_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    settings = Settings(app_data_dir=tmp_path, projects_dir=tmp_path, exports_dir=tmp_path)
    result = doctor.check_ffmpeg(settings)
    assert result.status == "fail"
    assert "winget install Gyan.FFmpeg" in result.fix_hint
    assert doctor.check_ffmpeg_filters(settings).status == "fail"


def test_run_all_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "check_disk_space", boom)
    settings = Settings(app_data_dir=tmp_path, projects_dir=tmp_path, exports_dir=tmp_path)
    report = doctor.run_all(settings)
    ids = [check.id for check in report.checks]
    assert ids == [
        "python_version", "ffmpeg", "ffprobe", "ffmpeg_filters", "fonts", "webview2",
        "projects_dir", "exports_dir", "parallax", "disk_space",
    ]
    disk = report.checks[-1]
    assert disk.status == "fail" and "kaboom" in disk.detail
    text = format_report(report)
    assert "Some checks failed." in text
    assert "Disk space" in text


def test_doctor_route(client):
    report = client.get("/api/doctor").json()
    assert {"ok", "checks"} <= set(report)
    assert any(check["id"] == "ffmpeg" for check in report["checks"])
