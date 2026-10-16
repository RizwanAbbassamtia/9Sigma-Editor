"""The AI voice routes (docs/CONTRACT-v0.2.md section 5): the key, the voices, a request.

The front end mirrors these in ``frontend/src/types/voice.ts``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .media import MediaItem

PROVIDER_ID = "elevenlabs"
PROVIDER_LABEL = "ElevenLabs"
MAX_TEXT_CHARS = 5000
MIN_PROVIDER_SPEED = 0.7
MAX_PROVIDER_SPEED = 1.2


class VoiceStatus(BaseModel):
    """``GET /api/voice/status``."""

    provider: str = PROVIDER_ID
    provider_label: str = PROVIDER_LABEL
    key_set: bool = False
    key_hint: str = Field(default="", description='the key\'s last characters ("...a1b2")')


class VoiceKeyUpdate(BaseModel):
    """``PUT /api/voice/key``: an empty key removes the saved one."""

    api_key: str = Field(default="", max_length=400)

    @field_validator("api_key", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class VoiceInfo(BaseModel):
    """One voice of the person's account."""

    id: str
    name: str
    description: str = ""
    preview_url: str | None = None
    labels: dict[str, str] = {}


class VoiceRequest(BaseModel):
    """``POST /api/edits/{id}/voice``: make a voice from ``text`` and add it to the edit."""

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    voice_id: str = Field(min_length=1, max_length=120)
    voice_name: str = Field(default="", max_length=120, description="the voice's name for "
                            "the library's label; the voices list fills it when empty")
    speed: float = Field(default=1.0, ge=MIN_PROVIDER_SPEED, le=MAX_PROVIDER_SPEED)
    name: str = Field(default="", max_length=120, description="the file's name; made from "
                      "the text when empty")
    use: Literal["sound", "narration"] = Field(
        default="sound",
        description="sound: a clip on a sound track at the playhead; narration: replaces the "
        "narration, with the timing from the provider's word times",
    )
    at_s: float = Field(default=0.0, ge=0, description="sound: where the clip starts")

    @field_validator("text", "name", "voice_name", mode="before")
    @classmethod
    def _tidy(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class VoiceResult(BaseModel):
    """What ``POST /api/edits/{id}/voice`` answers."""

    item: MediaItem
    duration_s: float = 0.0
    payload: dict[str, Any] | None = Field(
        default=None, description="the fresh editor payload when the voice became the narration"
    )
    warnings: list[str] = []
