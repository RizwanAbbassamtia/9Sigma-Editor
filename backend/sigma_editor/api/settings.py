"""``GET`` / ``PUT /api/settings``: the folders and the render and caption defaults
(docs/CONTRACT-v0.1.md section 6).

For the two folders, a missing field means "leave as is" and ``null`` or ``""`` means "back
to the default". The nested ``render`` and ``captions`` objects are saved whole under the
same keys in ``settings.json``; a missing object means "leave as is". A saved change takes
effect at once: the app's settings object is replaced and the edit store follows the new
projects folder.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from ..config import (
    PATH_KEYS,
    QUALITY_LABELS,
    CaptionsSettings,
    RenderSettings,
    Settings,
    default_exports_dir,
    default_projects_dir,
    load_settings,
)
from ..storage.project_store import ProjectStore
from ..storage.settings_store import SettingsStore
from .deps import SettingsDep

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsView(BaseModel):
    projects_dir: str
    exports_dir: str
    default_projects_dir: str
    default_exports_dir: str
    app_data_dir: str
    ffmpeg_path: str
    ffprobe_path: str
    render: RenderSettings
    captions: CaptionsSettings
    quality_labels: dict[str, str] = QUALITY_LABELS


class SettingsUpdate(BaseModel):
    projects_dir: str | None = None
    exports_dir: str | None = None
    render: RenderSettings | None = None
    captions: CaptionsSettings | None = None


def view_of(settings: Settings) -> SettingsView:
    return SettingsView(
        projects_dir=str(settings.projects_dir),
        exports_dir=str(settings.exports_dir),
        default_projects_dir=str(default_projects_dir()),
        default_exports_dir=str(default_exports_dir()),
        app_data_dir=str(settings.app_data_dir),
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
        render=settings.render,
        captions=settings.captions,
    )


def folder_value(value: str | None) -> str | None:
    """``None`` or blank -> back to the default; otherwise the trimmed path."""
    if value is None:
        return None
    text = value.strip()
    return text or None


@router.get("", response_model=SettingsView)
def get_settings(settings: SettingsDep) -> SettingsView:
    return view_of(settings)


@router.put("", response_model=SettingsView)
def update_settings(body: SettingsUpdate, request: Request, settings: SettingsDep) -> SettingsView:
    store = SettingsStore(settings.app_data_dir)
    changes = body.model_dump(exclude_unset=True)
    paths = store.read_paths()
    for key in PATH_KEYS:
        if key in changes:
            paths[key] = folder_value(changes[key])
    sections: dict[str, dict[str, Any]] = {}
    for key in ("render", "captions"):
        if key in changes and changes[key] is not None:
            sections[key] = getattr(body, key).model_dump(mode="json")
    try:
        if any(key in changes for key in PATH_KEYS):
            store.write_paths(paths)
        if sections:
            store.write_sections(sections)
        fresh = load_settings()
    except (OSError, ValidationError) as exc:
        raise HTTPException(
            status_code=500, detail=f"The settings could not be saved: {exc}"
        ) from exc
    problems = fresh.ensure_dirs()
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))
    request.app.state.settings = fresh
    request.app.state.store = ProjectStore(fresh.projects_dir)
    return view_of(fresh)
