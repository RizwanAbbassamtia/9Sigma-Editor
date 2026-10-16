"""``/api/edits``: the edits on this laptop (docs/CONTRACT-v0.1.md sections 2 to 5).

This module holds the routes that only touch the edit's files: the list, one edit, rename,
delete to the Recycle Bin, open the folder, the timing document. The routes that need the
render engine (New edit from files, the editor payload, the timeline autosave, scenes,
render, export) are added by ``api/editing.py`` once the engine is in place; both share the
helpers here.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException, Response
from pydantic import BaseModel, Field

from ..edit.timing_doc import TIMING_FILE, TimingError, nudged, read_timing, write_timing
from ..models.project import Project, ProjectSummary
from ..models.timing import TimingDoc
from ..storage.project_store import (
    EXPORT_DIR,
    VOICE_DIR,
    ProjectFolderBusy,
    ProjectNotFound,
    ProjectStoreError,
)
from .deps import SettingsDep, StoreDep

router = APIRouter(prefix="/api/edits", tags=["edits"])


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    language: str | None = Field(default=None, min_length=1, max_length=40)
    music_licence_note: str | None = Field(default=None, max_length=500)


class OpenFolderRequest(BaseModel):
    """``target: "export"`` opens the folder the last export went to."""

    target: Literal["project", "export"] = "project"


class OpenFolderResponse(BaseModel):
    ok: bool
    folder: str


class TimingNudge(BaseModel):
    index: int = Field(ge=0, description="0-based sentence")
    start_s: float | None = Field(default=None, ge=0, description="drag: the new start")
    shift_s: float | None = Field(default=None, description="shift this sentence and everything "
                                  "after it by this many seconds")


def translate(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProjectFolderBusy):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ProjectStoreError):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, OSError):
        return HTTPException(
            status_code=500,
            detail=f"The edit's files could not be written: {exc}. Check that the edits "
            "folder is available and not read-only.",
        )
    return HTTPException(status_code=500, detail=str(exc))


async def load_project(store: StoreDep, project_id: str) -> Project:
    try:
        return await asyncio.to_thread(store.get, project_id)
    except (ProjectStoreError, OSError) as exc:
        raise translate(exc) from exc


def export_folder_of(project: Project) -> Path | None:
    """The folder the last export wrote to (``08_export/export.json``), or ``None``."""
    stage_dir = Path(project.folder) / EXPORT_DIR
    try:
        data = json.loads((stage_dir / "export.json").read_text(encoding="utf-8"))
        recorded = Path(str(data.get("export_folder") or "")) if isinstance(data, dict) else None
    except (OSError, ValueError):
        recorded = None
    if recorded is not None and str(recorded) and recorded.is_dir():
        return recorded
    return None


def open_folder(folder: Path) -> None:
    """Show a folder in the system file manager (Explorer on Windows). Tests replace this."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(folder)])  # noqa: S603 - fixed program, our own path
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


# Routes --------------------------------------------------------------------------------------


@router.get("", response_model=list[ProjectSummary])
async def list_edits(store: StoreDep) -> list[ProjectSummary]:
    try:
        return await asyncio.to_thread(store.list)
    except (ProjectStoreError, OSError) as exc:
        raise translate(exc) from exc


@router.get("/{project_id}", response_model=Project)
async def get_edit(project_id: str, store: StoreDep) -> Project:
    return await load_project(store, project_id)


@router.post("/{project_id}/update", response_model=Project)
async def update_edit(project_id: str, body: ProjectPatch, store: StoreDep) -> Project:
    project = await load_project(store, project_id)
    try:
        if body.name is not None and body.name.strip() != project.name:
            project = await asyncio.to_thread(store.rename, project, body.name)
        if body.language is not None:
            project.language = body.language.strip() or project.language
        if body.music_licence_note is not None:
            project.music_licence_note = " ".join(body.music_licence_note.split())
        return await asyncio.to_thread(store.save, project)
    except (ProjectStoreError, OSError) as exc:
        raise translate(exc) from exc


@router.delete("/{project_id}", status_code=204, response_class=Response)
async def delete_edit(project_id: str, store: StoreDep) -> Response:
    try:
        await asyncio.to_thread(store.delete, project_id)
    except (ProjectStoreError, OSError) as exc:
        raise translate(exc) from exc
    return Response(status_code=204)


@router.post("/{project_id}/open-folder", response_model=OpenFolderResponse)
async def open_edit_folder(
    project_id: str,
    store: StoreDep,
    body: Annotated[OpenFolderRequest | None, Body()] = None,
) -> OpenFolderResponse:
    project = await load_project(store, project_id)
    folder = Path(project.folder)
    if not folder.is_dir():
        raise HTTPException(
            status_code=404, detail=f"The edit's folder {folder} is missing on this PC."
        )
    if body is not None and body.target == "export":
        exported = export_folder_of(project)
        if exported is None:
            raise HTTPException(
                status_code=404,
                detail="Nothing has been exported for this edit yet, so there is no export "
                "folder to open.",
            )
        folder = exported
    try:
        open_folder(folder)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The folder could not be opened: {exc}"
        ) from exc
    return OpenFolderResponse(ok=True, folder=str(folder))


# The timing tool ------------------------------------------------------------------------------


def timing_path(project: Project) -> Path:
    return Path(project.folder) / VOICE_DIR / TIMING_FILE


NO_TIMING = "This edit has no narration timing yet. Add caption text or a caption file first."


@router.get("/{project_id}/timing", response_model=TimingDoc)
async def get_timing(project_id: str, store: StoreDep) -> TimingDoc:
    project = await load_project(store, project_id)
    doc = await asyncio.to_thread(read_timing, timing_path(project))
    if doc is None:
        raise HTTPException(status_code=404, detail=NO_TIMING)
    return doc


@router.post("/{project_id}/timing", response_model=TimingDoc)
async def nudge_timing(
    project_id: str, body: TimingNudge, store: StoreDep, settings: SettingsDep
) -> TimingDoc:
    """One move of the timing tool. The scene times and the caption cues follow the new
    sentence times: the timeline is built again from the scene plan."""
    project = await load_project(store, project_id)
    path = timing_path(project)
    doc = await asyncio.to_thread(read_timing, path)
    if doc is None:
        raise HTTPException(status_code=404, detail=NO_TIMING)
    try:
        updated = nudged(doc, index=body.index, start_s=body.start_s, shift_s=body.shift_s)
    except TimingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def work() -> None:
        write_timing(path, updated)
        from ..edit.builder import BuildError, rebuild_timeline

        try:
            timeline, _warnings = rebuild_timeline(project, settings)
        except BuildError:
            return  # no scene plan yet: the timing alone changed
        project.duration_s = round(timeline.duration_s, 3)
        store.save(project)

    try:
        await asyncio.to_thread(work)
    except (ProjectStoreError, OSError) as exc:
        raise translate(exc) from exc
    return updated
