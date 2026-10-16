"""Edits on disk (storage/project_store.py) and the edit-only project model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sigma_editor.models.edit_input import EditInput, file_kind, natural_key
from sigma_editor.models.project import Project, aspect_of, summary_of
from sigma_editor.storage import project_store as ps
from sigma_editor.storage.files import atomic_write_text, safe_file_name, unique_path


def test_create_makes_folders_and_project_json(tmp_path: Path):
    store = ps.ProjectStore(tmp_path / "projects")
    project = store.create("Autumn walk: part 1", format="shorts", language="Spanish")
    folder = Path(project.folder)
    assert folder.parent == tmp_path / "projects"
    assert folder.name.endswith("_autumn-walk-part-1")
    for sub in ps.SUBFOLDERS:
        assert (folder / sub).is_dir()
    data = json.loads((folder / ps.PROJECT_FILE).read_text(encoding="utf-8"))
    assert data["kind"] == "edit"
    assert data["format"] == "shorts"
    assert data["status"] == "new"
    assert project.language == "Spanish"
    assert aspect_of(project.format) == "9:16"


def test_second_edit_with_same_name_gets_its_own_folder(tmp_path: Path):
    store = ps.ProjectStore(tmp_path)
    first = store.create("Same name")
    second = store.create("Same name")
    assert Path(second.folder).name == Path(first.folder).name + "-2"
    assert first.id != second.id


def test_list_get_save_and_rename(tmp_path: Path):
    store = ps.ProjectStore(tmp_path)
    a = store.create("First")
    b = store.create("Second")
    b.status = "ready"
    b.duration_s = 12.5
    store.save(b)
    rows = store.list()
    assert [row.name for row in rows] == ["Second", "First"]
    assert rows[0].status == "ready" and rows[0].duration_s == 12.5
    assert store.get(a.id).name == "First"
    renamed = store.rename(a, "First, renamed")
    assert Path(renamed.folder).name.endswith("_first-renamed")
    assert store.get(a.id).name == "First, renamed"
    with pytest.raises(ps.ProjectNotFound):
        store.get("nope")


def test_moved_folder_is_found_again(tmp_path: Path):
    store = ps.ProjectStore(tmp_path)
    project = store.create("Moved")
    old = Path(project.folder)
    new = old.with_name("2020-01-01_moved-by-hand")
    old.rename(new)
    fresh = ps.ProjectStore(tmp_path)
    found = fresh.get(project.id)
    assert Path(found.folder) == new
    assert [row.id for row in fresh.list()] == [project.id]


def test_copied_folder_counted_once_and_underscore_folders_skipped(tmp_path: Path):
    store = ps.ProjectStore(tmp_path)
    project = store.create("Copied")
    import shutil

    shutil.copytree(project.folder, tmp_path / "copy-of-it")
    shutil.copytree(project.folder, tmp_path / "_deleted")
    assert len(store.list()) == 1


def test_delete_moves_to_bin_or_deleted_folder(tmp_path: Path):
    store = ps.ProjectStore(tmp_path)
    project = store.create("Gone")
    folder = Path(project.folder)
    destination = store.delete(project.id)
    assert not folder.exists()
    if destination is not None:  # the Recycle Bin was not available (a temp drive, CI)
        assert destination.parent == tmp_path / "_deleted"
    assert store.list() == []
    with pytest.raises(ps.ProjectNotFound):
        store.get(project.id)


def test_summary_and_models():
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    project = Project(id="abc", name="N", folder="x", created_at=now, updated_at=now)
    row = summary_of(project)
    assert (row.id, row.name, row.status) == ("abc", "N", "new")
    form = EditInput(name="  Two   words ", caption_text="Hi.")
    assert form.name == "Two words"
    assert form.format == "long" and form.order == "as_given"


def test_file_kind_and_natural_order():
    assert file_kind("a.PNG") == "picture"
    assert file_kind("clip.mov") == "clip"
    assert file_kind("voice.m4a") == "audio"
    assert file_kind("subs.srt") == "captions"
    assert file_kind("notes") == "other"
    names = ["scene_10.png", "scene_2.png", "Scene_1.png"]
    assert sorted(names, key=natural_key) == ["Scene_1.png", "scene_2.png", "scene_10.png"]


def test_file_helpers(tmp_path: Path):
    target = tmp_path / "sub" / "a.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    assert not list((tmp_path / "sub").glob("*.part"))
    assert safe_file_name('my: "video"?.mp4') == "my_ _video__.mp4"
    assert safe_file_name("con.txt") == "con_.txt"
    assert safe_file_name("..") == "file"
    assert unique_path(tmp_path / "sub", "a.txt").name == "a-2.txt"
