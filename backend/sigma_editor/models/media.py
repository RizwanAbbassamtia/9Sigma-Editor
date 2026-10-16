"""The media library of an edit (docs/CONTRACT-v0.2.md section 2): what sits in ``01_media/``.

The front end mirrors these in ``frontend/src/types/media.ts``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MediaKind = Literal["picture", "clip", "audio", "captions"]

KIND_LABELS: dict[str, str] = {
    "picture": "Picture",
    "clip": "Clip",
    "audio": "Sound",
    "captions": "Captions",
}


class MediaItem(BaseModel):
    """One file of the library."""

    path: str = Field(description="relative to the edit's folder, e.g. 01_media/beach.jpg")
    name: str
    kind: MediaKind
    size_bytes: int = 0
    duration_s: float | None = Field(default=None, description="clips and sound")
    width: int = Field(default=0, description="pictures and clips; 0 when unknown")
    height: int = 0
    thumb_url: str | None = Field(
        default=None, description="a small picture of it (pictures and clips); null for sound"
    )
    url: str = Field(description="the file itself through the files route")
    in_use: list[str] = Field(
        default=[], description='where it is used, in plain words ("scene 3", "Layer 1")'
    )
    added_at: str = Field(default="", description="when the file was added (ISO 8601)")
    ai_voice: bool = Field(default=False, description="made by the AI voice")
    text: str = Field(default="", description="an AI voice: the text it says")
    voice: str = Field(default="", description="an AI voice: the voice's name")


class MediaLibrary(BaseModel):
    """``GET /api/edits/{id}/media``."""

    folder: str = Field(description="the library folder on this PC")
    items: list[MediaItem] = []


class BlankEditInput(BaseModel):
    """``POST /api/edits/blank``: an empty project, like CapCut's Create project."""

    name: str = Field(min_length=1, max_length=200)
    format: Literal["long", "shorts"] = "long"
    language: str = Field(default="English", min_length=1, max_length=40)
