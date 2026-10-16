"""The editing routes of ``/api/edits`` that need the render engine (docs/CONTRACT-v0.1.md
sections 3 to 5): New edit from files, the editor payload, the simple choices, the timeline
autosave, scenes, captions, render, export and the renders list.

Mounted by ``app.py`` next to ``api/edits.py`` (the file-only routes). ``setup_app`` gives the
app its render job runner (``edit/render_jobs.py``).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

from ..edit import builder
from ..edit.builder import BuildError, IncomingFile, NewEditFiles
from ..edit.caption_files import ImportedCue, write_srt
from ..edit.choices import EditError
from ..edit.choices import apply_edits as apply_choices
from ..edit.export_files import ExportError, ExportFile, export_edit
from ..edit.render_jobs import RenderBusy, RenderRequest, RenderRunner, RenderStatus
from ..edit.renderer import (
    PROXY_ID,
    STAGE_DIR_NAME,
    file_url,
    final_file_name,
    h265_file_name,
    load_review_payload,
    read_state,
    read_timeline,
    write_state,
    write_timeline,
)
from ..models.edit_input import EditInput, SceneFileInput, file_kind
from ..models.project import Project, ProjectSummary
from ..models.timeline import CaptionCue, EditChoices, Timeline
from ..render.presets import presets_json, quality_for
from ..storage.files import atomic_write_text, safe_file_name
from ..storage.project_store import EDIT_DIR, EXPORT_DIR, ProjectStoreError
from .deps import JobsDep, SettingsDep, StoreDep
from .edits import load_project, translate

router = APIRouter(prefix="/api", tags=["editing"])

NO_TIMELINE = "This edit has no timeline yet. Add pictures or clips first."
NOT_MEDIA = "Choose a picture (png, jpg, webp) or a clip (mp4, mov)."
TMP_DIR = "tmp"


def setup_app(app: FastAPI) -> None:
    """Give the app its render job runner (one render at a time)."""
    app.state.jobs = RenderRunner(lambda: app.state.store, lambda: app.state.settings)


# Helpers ---------------------------------------------------------------------------------------


class EditedPayload(BaseModel):
    """What the scene and caption routes answer: the fresh editor payload plus warnings."""

    payload: dict[str, Any]
    warnings: list[str] = []


def _payload_or_404(project: Project, settings: Any, jobs: Any) -> dict[str, Any]:
    running = jobs.running_info(project.id) if jobs is not None else None
    payload = load_review_payload(project, settings, running=running)
    if payload is None:
        raise HTTPException(status_code=404, detail=NO_TIMELINE)
    return payload


def _stage_dir(project: Project) -> Path:
    return Path(project.folder) / STAGE_DIR_NAME


def _timeline_or_404(project: Project) -> Timeline:
    timeline = read_timeline(_stage_dir(project))
    if timeline is None:
        raise HTTPException(status_code=404, detail=NO_TIMELINE)
    return timeline


def _refuse_while_rendering(jobs: Any, project_id: str) -> None:
    if jobs is not None and jobs.running_for(project_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="A render is running for this edit. Wait for it to finish or stop it first.",
        )


async def _save_uploads(
    settings: Any, uploads: list[tuple[str, UploadFile | None]]
) -> tuple[Path, dict[str, list[IncomingFile]]]:
    """Save the request's files into a short temp folder; returns it and the files by role."""
    base = Path(settings.app_data_dir) / TMP_DIR / uuid.uuid4().hex[:10]
    base.mkdir(parents=True, exist_ok=True)
    saved: dict[str, list[IncomingFile]] = {}

    def write(role: str, upload: UploadFile, number: int) -> IncomingFile:
        name = Path((upload.filename or "").replace("\\", "/")).name or f"{role}-{number}"
        target = base / f"{number:03d}_{safe_file_name(name, fallback=role)}"
        with target.open("wb") as handle:
            upload.file.seek(0)
            shutil.copyfileobj(upload.file, handle, 1024 * 1024)
        return IncomingFile(name=name, path=target)

    number = 0
    for role, upload in uploads:
        if upload is None or not (upload.filename or "").strip():
            continue
        number += 1
        saved.setdefault(role, []).append(await asyncio.to_thread(write, role, upload, number))
        await upload.close()
    return base, saved


def _discard(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


# New edit from files ----------------------------------------------------------------------------


@router.post("/edits", response_model=Project, status_code=201)
async def create_edit(
    request: Request,
    store: StoreDep,
    settings: SettingsDep,
    form: Annotated[str, Form(description="the EditInput form as JSON")],
    files: Annotated[
        list[UploadFile] | None, File(description="pictures and clips, in order")
    ] = None,
    narration: Annotated[UploadFile | None, File()] = None,
    music: Annotated[UploadFile | None, File()] = None,
    captions: Annotated[UploadFile | None, File()] = None,
) -> Project:
    """One multipart request: the form, the pictures and clips, the narration, the music and
    a caption file. The edit's folder, timing and first timeline are made at once."""
    try:
        parsed = EditInput.model_validate_json(form)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())) or "form"
        raise HTTPException(
            status_code=422, detail=f"The form is not right ({where}): {first.get('msg', '')}"
        ) from exc
    uploads: list[tuple[str, UploadFile | None]] = [("items", f) for f in files or []]
    uploads += [("narration", narration), ("music", music), ("captions", captions)]
    base, saved = await _save_uploads(settings, uploads)
    incoming = NewEditFiles(
        items=saved.get("items", []),
        narration=(saved.get("narration") or [None])[0],
        music=(saved.get("music") or [None])[0],
        captions=(saved.get("captions") or [None])[0],
    )
    try:
        project, warnings = await asyncio.to_thread(
            builder.create_edit, store, settings, parsed, incoming
        )
    except BuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise translate(exc) from exc
    finally:
        await asyncio.to_thread(_discard, base)
    if warnings:
        stage_dir = _stage_dir(project)
        state = read_state(stage_dir)
        state["warnings"] = warnings
        await asyncio.to_thread(write_state, stage_dir, state)
    return project


# The editor payload and the simple choices --------------------------------------------------------


@router.get("/edits/{project_id}/edit")
async def get_edit_payload(
    project_id: str, store: StoreDep, settings: SettingsDep, jobs: JobsDep
) -> dict[str, Any]:
    """Everything the editor screen shows: the timeline, the renders, the checks."""
    project = await load_project(store, project_id)
    return await asyncio.to_thread(_payload_or_404, project, settings, jobs)


@router.post("/edits/{project_id}/edit")
async def apply_edits(
    project_id: str, body: EditChoices, store: StoreDep, settings: SettingsDep, jobs: JobsDep
) -> dict[str, Any]:
    """The simple choices (docs/CONTRACT-v0.1.md section 4): sizes, music, captions, popups,
    or build the timeline again. Answers the fresh editor payload."""
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)

    def work() -> dict[str, Any]:
        try:
            apply_choices(project, settings, body)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _payload_or_404(project, settings, jobs)

    return await asyncio.to_thread(work)


class TimelineSave(BaseModel):
    timeline: Timeline
    revision: int = Field(ge=0, description="the revision the page is editing")


class TimelineSaved(BaseModel):
    revision: int
    timeline: Timeline


@router.put("/edits/{project_id}/timeline", response_model=TimelineSaved)
async def save_timeline(
    project_id: str, body: TimelineSave, store: StoreDep, jobs: JobsDep
) -> TimelineSaved:
    """The autosave: the whole timeline, refused with 409 when the file moved on."""
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)

    def work() -> TimelineSaved:
        stage_dir = _stage_dir(project)
        current = _timeline_or_404(project)
        if body.revision != current.revision:
            raise HTTPException(
                status_code=409,
                detail="This edit changed since the page loaded it. Reload the page to get "
                "the latest version.",
            )
        timeline = body.timeline
        timeline.project_id = project.id
        timeline.revision = current.revision + 1
        write_timeline(stage_dir, timeline)
        builder.sync_plan_from_timeline(project, timeline)
        project.duration_s = round(timeline.duration_s, 3)
        store.save(project)
        return TimelineSaved(revision=timeline.revision, timeline=timeline)

    return await asyncio.to_thread(work)


# Scenes ----------------------------------------------------------------------------------------


def _scene_result(
    project: Project, settings: Any, jobs: Any, warnings: list[str]
) -> EditedPayload:
    return EditedPayload(payload=_payload_or_404(project, settings, jobs), warnings=warnings)


async def _one_file(settings: Any, file: UploadFile) -> tuple[Path, IncomingFile]:
    base, saved = await _save_uploads(settings, [("file", file)])
    items = saved.get("file") or []
    if not items:
        _discard(base)
        raise HTTPException(status_code=422, detail="Choose a file first.")
    return base, items[0]


@router.post("/edits/{project_id}/scenes", response_model=EditedPayload)
async def add_scene(
    project_id: str,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    file: Annotated[UploadFile, File()],
    caption: Annotated[str, Form()] = "",
    keep_sound: Annotated[bool, Form()] = False,
    after: Annotated[int | None, Form()] = None,
) -> EditedPayload:
    """Add a scene from a picture or clip file."""
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)
    if file_kind(file.filename or "") not in ("picture", "clip"):
        raise HTTPException(status_code=415, detail=NOT_MEDIA)
    base, item = await _one_file(settings, file)
    options = SceneFileInput(caption=caption, keep_sound=keep_sound, after=after)
    try:
        _timeline, warnings = await asyncio.to_thread(
            builder.add_scene, project, store, settings, item, options
        )
    except BuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await asyncio.to_thread(_discard, base)
    return _scene_result(project, settings, jobs, warnings)


@router.post("/edits/{project_id}/scenes/{index}/picture", response_model=EditedPayload)
async def replace_scene_picture(
    project_id: str,
    index: int,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    file: Annotated[UploadFile, File()],
) -> EditedPayload:
    """Replace one scene's picture or clip."""
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)
    if file_kind(file.filename or "") not in ("picture", "clip"):
        raise HTTPException(status_code=415, detail=NOT_MEDIA)
    base, item = await _one_file(settings, file)
    try:
        _timeline, warnings = await asyncio.to_thread(
            builder.replace_picture, project, store, settings, index, item
        )
    except BuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await asyncio.to_thread(_discard, base)
    return _scene_result(project, settings, jobs, warnings)


class ReorderBody(BaseModel):
    order: list[int] = Field(min_length=1, description="the old 0-based indexes in the new order")


@router.post("/edits/{project_id}/scenes/reorder", response_model=EditedPayload)
async def reorder_scenes(
    project_id: str, body: ReorderBody, store: StoreDep, settings: SettingsDep, jobs: JobsDep
) -> EditedPayload:
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)
    try:
        _timeline, warnings = await asyncio.to_thread(
            builder.reorder_scenes, project, store, settings, body.order
        )
    except BuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _scene_result(project, settings, jobs, warnings)


@router.delete("/edits/{project_id}/scenes/{index}", response_model=EditedPayload)
async def remove_scene(
    project_id: str, index: int, store: StoreDep, settings: SettingsDep, jobs: JobsDep
) -> EditedPayload:
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)
    try:
        _timeline, warnings = await asyncio.to_thread(
            builder.remove_scene, project, store, settings, index
        )
    except BuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _scene_result(project, settings, jobs, warnings)


# Captions --------------------------------------------------------------------------------------


class CueInput(BaseModel):
    id: str = ""
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    text: str = Field(max_length=500)


class CaptionsBody(BaseModel):
    cues: list[CueInput]


class CaptionsView(BaseModel):
    enabled: bool
    cues: list[CaptionCue]
    revision: int


@router.get("/edits/{project_id}/captions", response_model=CaptionsView)
async def get_captions(project_id: str, store: StoreDep) -> CaptionsView:
    project = await load_project(store, project_id)
    timeline = await asyncio.to_thread(_timeline_or_404, project)
    return CaptionsView(enabled=timeline.captions.enabled, cues=timeline.captions.cues,
                        revision=timeline.revision)


@router.put("/edits/{project_id}/captions", response_model=CaptionsView)
async def put_captions(
    project_id: str, body: CaptionsBody, store: StoreDep, jobs: JobsDep
) -> CaptionsView:
    """The caption table: edit cue text and times (the ASS file is written again at the
    next render)."""
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)

    def work() -> CaptionsView:
        timeline = _timeline_or_404(project)
        cues: list[CaptionCue] = []
        for row in body.cues:
            text = row.text.replace("\r", "").strip()
            if not text or row.end_s <= row.start_s:
                continue
            cues.append(CaptionCue(start_s=round(row.start_s, 3), end_s=round(row.end_s, 3),
                                   text=text.replace("\n", "\\N"), id=row.id, edited=True))
        cues.sort(key=lambda c: (c.start_s, c.end_s))
        timeline.captions.cues = cues
        timeline.revision += 1
        write_timeline(_stage_dir(project), timeline)
        return CaptionsView(enabled=timeline.captions.enabled, cues=timeline.captions.cues,
                            revision=timeline.revision)

    return await asyncio.to_thread(work)


@router.get("/edits/{project_id}/captions.srt", response_class=PlainTextResponse)
async def download_captions(project_id: str, store: StoreDep) -> PlainTextResponse:
    """The captions as an SRT file (Save captions as SRT)."""
    project = await load_project(store, project_id)
    timeline = await asyncio.to_thread(_timeline_or_404, project)
    cues = [ImportedCue(c.start_s, c.end_s, c.text.replace("\\N", "\n"))
            for c in timeline.captions.cues]
    name = safe_file_name(f"{project.name}.srt", fallback="captions.srt")
    return PlainTextResponse(
        write_srt(cues), media_type="application/x-subrip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# Render ----------------------------------------------------------------------------------------


class RenderStarted(BaseModel):
    job_id: str
    message: str


@router.get("/render-presets")
async def render_presets() -> dict[str, Any]:
    """The quality and upload choices for the editor's menus."""
    return presets_json()


@router.post("/edits/{project_id}/render", response_model=RenderStarted, status_code=202)
async def start_render(
    project_id: str, body: RenderRequest, store: StoreDep, jobs: JobsDep
) -> RenderStarted:
    project = await load_project(store, project_id)
    await asyncio.to_thread(_timeline_or_404, project)
    if body.quality is not None and quality_for(body.quality).id != body.quality.strip().lower():
        raise HTTPException(status_code=422, detail="Quality is Standard, High or Best.")
    try:
        job = jobs.start(project, body)
    except RenderBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if body.presets is None:
        names = "the ticked sizes"
    else:
        names = ", ".join(body.presets) if body.presets else "the preview only"
    what = "the range" if body.range else names
    return RenderStarted(job_id=job.id, message=f"Rendering {what}.")


@router.get("/edits/{project_id}/render/status", response_model=RenderStatus)
async def render_status(project_id: str, store: StoreDep, jobs: JobsDep) -> RenderStatus:
    await load_project(store, project_id)
    return await asyncio.to_thread(jobs.status_of, project_id)


@router.post("/edits/{project_id}/render/cancel", status_code=204, response_class=Response)
async def cancel_render(project_id: str, store: StoreDep, jobs: JobsDep) -> Response:
    await load_project(store, project_id)
    jobs.cancel(project_id)
    return Response(status_code=204)


# Export ----------------------------------------------------------------------------------------


class ExportBody(BaseModel):
    presets: list[str] = Field(default=[], description="the sizes to export; empty = every "
                               "rendered size")


class ExportedFileView(BaseModel):
    name: str
    label: str
    size_bytes: int
    sha256: str


class ExportView(BaseModel):
    folder: str
    files: list[ExportedFileView]


def _export(project: Project, settings: Any, presets: list[str]) -> ExportView:
    folder = Path(project.folder)
    stage_dir = folder / STAGE_DIR_NAME
    state = read_state(stage_dir)
    renders = state.get("renders") if isinstance(state.get("renders"), dict) else {}
    timeline = _timeline_or_404(project)
    wanted = presets or [p for p in renders if p != PROXY_ID]
    files: list[ExportFile] = []
    sizes: list[str] = []
    for preset_id in wanted:
        entry = renders.get(preset_id) if isinstance(renders.get(preset_id), dict) else None
        if preset_id == PROXY_ID or entry is None or entry.get("status") != "done":
            raise HTTPException(
                status_code=422, detail=f"{preset_id} is not rendered yet. Render it first."
            )
        quality = quality_for(entry.get("quality")).label
        label = f"{preset_id}, {quality} quality"
        default_path = f"{STAGE_DIR_NAME}/{final_file_name(preset_id)}"
        source = folder / str(entry.get("path") or default_path)
        files.append(ExportFile(source, label, f"{project.name} {preset_id}.mp4"))
        width, height = entry.get("width"), entry.get("height")
        sizes.append(f"{preset_id} ({width} x {height})" if width and height else preset_id)
        if entry.get("h265_path"):
            h265 = folder / str(entry["h265_path"])
            if h265.is_file():
                files.append(ExportFile(
                    h265, f"{preset_id}, H.265 copy", f"{project.name} {preset_id} H265.mp4"
                ))
    if not files:
        raise HTTPException(status_code=422, detail="Nothing to export: render a size first.")
    first = timeline.scenes[0].image if timeline.scenes else None
    poster = folder / first if first else None
    notes: list[str] = []
    if timeline.music.path:
        found = "licence file found" if timeline.music.license_ok else "no note and no licence file"
        notes.append("Music licence: " + (project.music_licence_note or found))
    try:
        result = export_edit(
            exports_dir=settings.exports_dir, name=project.name, duration_s=timeline.duration_s,
            files=files, poster=poster, quality=quality_for(state.get("quality")).label,
            sizes=sizes, notes=notes,
        )
    except ExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record = {
        "export_folder": str(result.folder),
        "exported_at": result.folder.name,
        "files": [f.to_json() for f in result.files],
    }
    atomic_write_text(folder / EXPORT_DIR / "export.json", json.dumps(record, indent=2) + "\n")
    return ExportView(
        folder=str(result.folder),
        files=[ExportedFileView(**f.to_json()) for f in result.files],
    )


@router.post("/edits/{project_id}/export", response_model=ExportView)
async def export_edit_route(
    project_id: str, body: ExportBody, store: StoreDep, settings: SettingsDep, jobs: JobsDep
) -> ExportView:
    """Copy the chosen renders, a poster frame and export.txt (with checksums) to the export
    folder (docs/CONTRACT-v0.1.md section 5)."""
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)
    view = await asyncio.to_thread(_export, project, settings, body.presets)
    project.status = "exported"
    project.status_note = f"Exported to {view.folder}"
    await asyncio.to_thread(store.save, project)
    return view


# Every render ------------------------------------------------------------------------------------


class RenderListRow(BaseModel):
    project_id: str
    name: str
    format: str
    preset: str
    quality: str
    path: str
    url: str
    duration_s: float | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    rendered_at: str | None = None
    exported: bool = False


def _render_rows(summary: ProjectSummary, folder: Path) -> list[RenderListRow]:
    state = read_state(folder / STAGE_DIR_NAME)
    renders = state.get("renders") if isinstance(state.get("renders"), dict) else {}
    rows: list[RenderListRow] = []
    for preset_id, entry in renders.items():
        if preset_id == PROXY_ID or not isinstance(entry, dict) or entry.get("status") != "done":
            continue
        path = str(entry.get("path") or f"{STAGE_DIR_NAME}/{final_file_name(preset_id)}")
        if not (folder / path).is_file():
            continue
        rows.append(
            RenderListRow(
                project_id=summary.id, name=summary.name, format=summary.format,
                preset=preset_id, quality=quality_for(entry.get("quality")).label, path=path,
                url=file_url(summary.id, path), duration_s=entry.get("duration_s"),
                size_bytes=entry.get("size_bytes"), width=entry.get("width"),
                height=entry.get("height"), rendered_at=entry.get("rendered_at"),
                exported=summary.status == "exported",
            )
        )
        if entry.get("h265_path") and (folder / str(entry["h265_path"])).is_file():
            rows.append(
                RenderListRow(
                    project_id=summary.id, name=summary.name, format=summary.format,
                    preset=f"{preset_id} H.265", quality=quality_for(entry.get("quality")).label,
                    path=str(entry["h265_path"]), url=file_url(summary.id, str(entry["h265_path"])),
                    duration_s=entry.get("duration_s"), rendered_at=entry.get("rendered_at"),
                    exported=summary.status == "exported",
                )
            )
    return rows


@router.get("/renders", response_model=list[RenderListRow])
async def list_renders(store: StoreDep) -> list[RenderListRow]:
    """Every finished render of every edit, newest first."""

    def work() -> list[RenderListRow]:
        rows: list[RenderListRow] = []
        for summary in store.list():
            try:
                folder = Path(store.get(summary.id).folder)
            except (ProjectStoreError, OSError):
                continue
            rows.extend(_render_rows(summary, folder))
        rows.sort(key=lambda r: r.rendered_at or "", reverse=True)
        return rows

    return await asyncio.to_thread(work)


__all__ = ["router", "setup_app", "h265_file_name", "EDIT_DIR"]
