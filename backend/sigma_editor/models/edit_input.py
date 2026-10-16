"""What "New edit from files" takes (docs/CONTRACT-v0.1.md section 3).

The form is sent as one multipart request (``POST /api/edits``): the fields below as form
values and the files as uploads. The server copies every file into the edit's folder and
builds the first timeline. Later additions ("Add scene", "Replace picture") reuse
:class:`SceneFileInput`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .project import ProjectFormat

PICTURE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif",
                                               ".tiff"})
CLIP_SUFFIXES: frozenset[str] = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"})
AUDIO_SUFFIXES: frozenset[str] = frozenset({".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
                                             ".opus", ".wma", ".aif", ".aiff"})
CAPTION_SUFFIXES: frozenset[str] = frozenset({".srt", ".vtt"})

ItemOrder = Literal["as_given", "by_name"]
"""``as_given``: the order the files were added (drag order); ``by_name``: sorted by file name,
with the numbers in names compared as numbers (``scene_2`` before ``scene_10``)."""


class EditItem(BaseModel):
    """One picture or clip of the new edit, in order."""

    file: str = Field(min_length=1, max_length=400)
    """The uploaded file's name (``files`` of the request) or, for a folder import, its path."""
    caption: str = Field(default="", max_length=300)
    """An optional short line shown as a popup while this scene plays."""
    keep_sound: bool = False
    """Clips only: keep the clip's own sound (clips are muted unless ticked)."""

    @field_validator("caption", mode="before")
    @classmethod
    def _one_line(cls, value: object) -> object:
        return " ".join(str(value).split()) if isinstance(value, str) else value


class EditInput(BaseModel):
    """The form of "New edit from files", without the files themselves."""

    name: str = Field(min_length=1, max_length=200)
    format: ProjectFormat = "long"
    language: str = Field(default="English", min_length=1, max_length=40)
    order: ItemOrder = "as_given"
    items: list[EditItem] = Field(default=[], max_length=500)
    """The pictures and clips; empty when the files are given without captions (then every
    picture and clip of the request is a scene, in ``order``)."""
    caption_text: str = Field(default="", max_length=60_000)
    """Typed caption text, one paragraph per blank line; sentences become the caption cues and
    set the scene lengths when there is a narration. Ignored when a caption file is given."""
    music_licence_note: str = Field(default="", max_length=500)
    """What the person knows about the music's licence (the check warns while it is empty)."""
    keep_clip_sound: bool = False
    """The default for clips given without ``items``."""

    @field_validator("name", mode="before")
    @classmethod
    def _tidy_name(cls, value: object) -> object:
        return " ".join(str(value).split()) if isinstance(value, str) else value


class SceneFileInput(BaseModel):
    """``POST /api/edits/{id}/scenes`` (Add scene) and ``.../scenes/{n}/picture`` (Replace)."""

    caption: str = Field(default="", max_length=300)
    keep_sound: bool = False
    after: int | None = Field(default=None, ge=-1, description="insert after this scene "
                              "(0-based); -1 = at the start; null = at the end")


def file_kind(name: str) -> Literal["picture", "clip", "audio", "captions", "other"]:
    """What a file is for, from its extension."""
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    dotted = f".{suffix}"
    if dotted in PICTURE_SUFFIXES:
        return "picture"
    if dotted in CLIP_SUFFIXES:
        return "clip"
    if dotted in AUDIO_SUFFIXES:
        return "audio"
    if dotted in CAPTION_SUFFIXES:
        return "captions"
    return "other"


def natural_key(name: str) -> list[object]:
    """Sort key that compares the numbers inside a name as numbers."""
    import re

    parts = re.split(r"(\d+)", name.lower())
    return [int(part) if part.isdigit() else part for part in parts]
