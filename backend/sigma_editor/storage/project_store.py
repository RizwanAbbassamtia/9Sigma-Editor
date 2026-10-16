"""The edits on this laptop: one folder each under ``<projects_dir>``, ``project.json`` inside.

The files are the truth (docs/CONTRACT-v0.1.md section 3): ``project.json`` is written
atomically on every change, the list is read from the folders every time (a few hundred edits
read in well under a second), and a small in-memory map of id -> folder saves the scan on a
``get``. A folder moved or copied by hand is picked up on the next list: the folder it sits
in is the truth, not the path recorded inside. Every check-then-write runs under
:data:`LOCK` because the API serves requests from several threads.
"""

from __future__ import annotations

import errno
import os
import shutil
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from slugify import slugify

from ..models.project import Project, ProjectSummary, summary_of
from .files import atomic_write_text
from .recycle import recycle

PROJECT_FILE = "project.json"
MEDIA_DIR = "01_media"
VOICE_DIR = "05_voice"
IMAGES_DIR = "06_images"
EDIT_DIR = "07_edit"
EXPORT_DIR = "08_export"
SUBFOLDERS = (MEDIA_DIR, VOICE_DIR, IMAGES_DIR, EDIT_DIR, EXPORT_DIR)
# Keeps <projects_dir>/<date>_<slug>/07_edit/clips/<preset>/<file> well under the 260-character
# Windows path limit for the default projects folder.
MAX_NAME_SLUG_LENGTH = 48

LOCK = threading.RLock()


class ProjectStoreError(Exception):
    """Base class; the message is plain English and safe to show to the user."""


class ProjectNotFound(ProjectStoreError):
    pass


class ProjectFolderBusy(ProjectStoreError):
    """The folder cannot be moved because a file inside it is still open (a render is still
    running, or a file is open in another program)."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def name_slug(text: str | None, fallback: str = "edit") -> str:
    """Folder-safe form of an edit's name, at most 48 characters."""
    slug = slugify(text or "", max_length=MAX_NAME_SLUG_LENGTH, word_boundary=True)
    return slug or fallback


def folder_name(name: str, created: datetime | None = None) -> str:
    stamp = (created or utc_now()).strftime("%Y-%m-%d")
    return f"{stamp}_{name_slug(name)}"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class ProjectStore:
    """Reads and writes ``project.json`` files under the projects folder."""

    def __init__(self, projects_dir: Path | str) -> None:
        self.projects_dir = Path(projects_dir)
        self._folders: dict[str, Path] = {}

    # Paths ----------------------------------------------------------------------------

    def new_folder(self, name: str, created: datetime | None = None) -> Path:
        """A folder path that does not exist yet: ``<date>_<slug>[-2, -3...]``."""
        base = self.projects_dir / folder_name(name, created)
        candidate = base
        counter = 2
        while candidate.exists():
            candidate = base.with_name(f"{base.name}-{counter}")
            counter += 1
        return candidate

    @staticmethod
    def project_file(folder: Path | str) -> Path:
        return Path(folder) / PROJECT_FILE

    # CRUD -----------------------------------------------------------------------------

    def create(self, name: str, **fields: object) -> Project:
        """Make the folder with its subfolders and write ``project.json``."""
        now = utc_now()
        with LOCK:
            folder = self.new_folder(name, now)
            project = Project(
                id=new_id(), name=name, folder=str(folder), created_at=now, updated_at=now,
                **fields,  # type: ignore[arg-type]
            )
            folder.mkdir(parents=True, exist_ok=False)
            for sub in SUBFOLDERS:
                (folder / sub).mkdir(exist_ok=True)
            self._write(project)
        return project

    def save(self, project: Project) -> Project:
        """Write ``project.json`` atomically. Sets ``updated_at``."""
        project.updated_at = utc_now()
        with LOCK:
            self._write(project)
        return project

    def get(self, project_id: str) -> Project:
        folder = self._folders.get(project_id)
        if folder is not None:
            project = self._read(folder)
            if project is not None and project.id == project_id:
                return project
        for project in self._scan():
            if project.id == project_id:
                return project
        self._folders.pop(project_id, None)
        raise ProjectNotFound("That edit no longer exists. It may have been deleted.")

    def list(self) -> list[ProjectSummary]:
        """Every edit, most recently changed first."""
        rows: list[ProjectSummary] = []
        seen: set[str] = set()
        for project in self._scan():
            if project.id in seen:
                continue  # a copied folder: the first one wins
            seen.add(project.id)
            rows.append(summary_of(project))
        rows.sort(key=lambda row: row.updated_at, reverse=True)
        return rows

    def rename(self, project: Project, name: str) -> Project:
        """Give the edit a new name and its folder the matching name when that is possible
        (nothing may be writing into the folder at that moment)."""
        name = " ".join(name.split())
        if not name:
            raise ProjectStoreError("An edit needs a name.")
        with LOCK:
            project.name = name
            old = Path(project.folder)
            target = self.new_folder(name, project.created_at)
            if old.is_dir() and target.name != old.name:
                try:
                    old.rename(target)
                    project.folder = str(target)
                    self._folders[project.id] = target
                except OSError:
                    pass  # keep the old folder name; the edit's name still changes
            self._write(project)
        return project

    def delete(self, project_id: str) -> Path | None:
        """Move the edit's folder to the Recycle Bin (or ``_deleted/`` when the bin cannot
        take it). Returns where it went, or ``None`` for the bin. Never removes outright."""
        project = self.get(project_id)
        folder = Path(project.folder)
        with LOCK:
            try:
                destination = recycle(folder, fallback_root=self.projects_dir)
            except FileNotFoundError as exc:
                raise ProjectNotFound(str(exc)) from exc
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    destination = self.projects_dir / "_deleted" / folder.name
                    shutil.move(str(folder), str(destination))
                else:
                    raise ProjectFolderBusy(
                        f"The folder {folder.name} is still in use (a render may be running, or "
                        "a file in it is open in another program). Close that and try again."
                    ) from exc
            self._folders.pop(project_id, None)
        return destination

    # Internals ------------------------------------------------------------------------

    def _write(self, project: Project) -> None:
        atomic_write_text(self.project_file(project.folder),
                          project.model_dump_json(indent=2) + "\n")
        self._folders[project.id] = Path(project.folder)

    def _read(self, folder: Path) -> Project | None:
        path = self.project_file(folder)
        try:
            project = Project.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            return None
        if Path(project.folder) != folder:
            project.folder = str(folder)  # moved or copied by hand: this folder is the truth
        return project

    def _scan(self) -> Iterator[Project]:
        """Every readable ``project.json`` directly under the projects folder."""
        if not self.projects_dir.is_dir():
            return
        try:
            entries = sorted(os.scandir(self.projects_dir), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            project = self._read(Path(entry.path))
            if project is None:
                continue
            self._folders[project.id] = Path(entry.path)
            yield project
