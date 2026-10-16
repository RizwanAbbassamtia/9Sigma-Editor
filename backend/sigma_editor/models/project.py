"""An edit (one video the person is making) and its list row.

Stored as ``project.json`` in the edit's folder (docs/CONTRACT-v0.1.md section 3)::

    <projects_dir>/<YYYY-MM-DD>_<name-slug>/
        project.json      this model (``kind: "edit"``)
        01_media/         the person's own pictures and clips as they were added, and the music
        05_voice/         voice.wav (the narration, converted), its original, timing.json
        06_images/        scene_01.png ... the pictures and clip poster frames
        07_edit/          timeline.json, render_state.json, captions.ass, the renders
        08_export/        what Export wrote, and export.json (where it went)

The numbered folders match the render engine's own paths (``05_voice/voice.wav``,
``07_edit/timeline.json``), so the engine copied from CashCow Studio reads an edit as it read a
pipeline project. The front end mirrors these shapes in ``frontend/src/types/project.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectFormat = Literal["long", "shorts"]
"""``long`` is a 16:9 video, ``shorts`` a 9:16 Short."""

ProjectStatus = Literal["new", "ready", "rendering", "rendered", "exported", "failed"]
"""Where an edit stands, for the list: ``new`` (files added, no timeline yet), ``ready``
(timeline built, nothing rendered), ``rendering`` (a render is running), ``rendered`` (at least
one final size exists), ``exported`` (Export wrote the files out), ``failed`` (the last render
stopped with an error; ``status_note`` says why)."""

STATUS_LABELS: dict[str, str] = {
    "new": "New",
    "ready": "Ready to render",
    "rendering": "Rendering",
    "rendered": "Rendered",
    "exported": "Exported",
    "failed": "Render failed",
}


class Project(BaseModel):
    """``project.json``: the edit itself. The timeline lives in ``07_edit/timeline.json``."""

    id: str = Field(min_length=1)
    kind: Literal["edit"] = "edit"
    name: str = Field(min_length=1, max_length=200)
    format: ProjectFormat = "long"
    language: str = Field(default="English", min_length=1, max_length=40)
    """The language of the narration and captions (picks the caption font and rules)."""
    folder: str
    created_at: datetime
    updated_at: datetime
    status: ProjectStatus = "new"
    status_note: str = ""
    """Plain English next to the status: the last render's error, or where it was exported."""
    duration_s: float = Field(default=0.0, ge=0)
    """Length of the video (the end of the last scene), kept here for the list."""
    music_licence_note: str = ""
    """What the person wrote about the music's licence (the licence check looks at it)."""
    schema_version: int = 1


class ProjectSummary(BaseModel):
    """One row of the "My edits" list."""

    id: str
    name: str
    format: ProjectFormat
    language: str
    status: ProjectStatus
    status_note: str = ""
    duration_s: float = 0.0
    created_at: datetime
    updated_at: datetime


def summary_of(project: Project) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        name=project.name,
        format=project.format,
        language=project.language,
        status=project.status,
        status_note=project.status_note,
        duration_s=project.duration_s,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def aspect_of(fmt: str) -> str:
    """The frame shape of a format: ``9:16`` for a Short, ``16:9`` otherwise."""
    return "9:16" if fmt == "shorts" else "16:9"
