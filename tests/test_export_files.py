"""Export to the export folder with checksums (edit/export_files.py)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from sigma_editor.edit.export_files import (
    ExportError,
    ExportFile,
    export_edit,
    export_folder_for,
    format_length,
)


def test_format_length():
    assert format_length(225) == "3:45"
    assert format_length(3723.4) == "1:02:03"
    assert format_length(-1) == "0:00"


def test_export_folder_name_is_dated_and_unique(tmp_path: Path):
    when = datetime(2026, 10, 16, 14, 3)
    first = export_folder_for(tmp_path, "My Video: Part 1", when)
    assert first.name == "2026-10-16_my-video-part-1"
    first.mkdir()
    second = export_folder_for(tmp_path, "My Video: Part 1", when)
    assert second.name == "2026-10-16_my-video-part-1-2"


def test_export_copies_files_and_writes_records(tmp_path: Path):
    render = tmp_path / "final_1080p.mp4"
    render.write_bytes(b"video bytes" * 100)
    poster = tmp_path / "poster.jpg"
    poster.write_bytes(b"jpeg")
    result = export_edit(
        exports_dir=tmp_path / "out",
        name="Autumn walk",
        duration_s=225,
        files=[ExportFile(render, "1080p, High quality", "Autumn walk 1080p.mp4")],
        poster=poster,
        quality="High",
        sizes=["1080p (1920 x 1080)"],
        notes=["Music: licence note present."],
        when=datetime(2026, 10, 16, 14, 3),
    )
    assert result.folder.name == "2026-10-16_autumn-walk"
    names = [item.name for item in result.files]
    assert names == ["Autumn walk 1080p.mp4", "poster.jpg"]
    copied = result.folder / "Autumn walk 1080p.mp4"
    assert copied.read_bytes() == render.read_bytes()
    expected = hashlib.sha256(render.read_bytes()).hexdigest()
    assert result.files[0].sha256 == expected
    text = result.text_file.read_text(encoding="utf-8")
    assert "Name: Autumn walk" in text
    assert "Length: 3:45" in text
    assert "Quality: High" in text
    assert expected in text
    assert "Music: licence note present." in text
    checksums = result.checksums_file.read_text(encoding="utf-8").splitlines()
    assert checksums[0] == f"{expected}  Autumn walk 1080p.mp4"
    assert '"sizes": [' in result.json_file.read_text(encoding="utf-8")


def test_export_refuses_missing_or_no_files(tmp_path: Path):
    with pytest.raises(ExportError, match="render at least one size"):
        export_edit(exports_dir=tmp_path, name="x", duration_s=1, files=[])
    with pytest.raises(ExportError, match="is missing"):
        export_edit(
            exports_dir=tmp_path, name="x", duration_s=1,
            files=[ExportFile(tmp_path / "gone.mp4", "1080p", "gone.mp4")],
        )
    assert not any(tmp_path.iterdir())
