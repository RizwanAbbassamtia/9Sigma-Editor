"""Narration timing: ``05_voice/timing.json`` (:class:`TimingDoc`).

``timing.json`` is the clock for everything in the edit: scenes, popups, captions and the
music dip all read their start and end times from it. In 9Sigma Editor the times are
estimated from the narration's length and the caption text (``edit/sentences.py``) and then
nudged by the person in the timing tool, or they come from a caption file (SRT/VTT); a
speech-to-text tool may fill them exactly in a later release. The shape is the render
engine's own (copied from CashCow Studio's voice stage), so the engine reads it unchanged.

The front end mirrors these in ``frontend/src/types/timing.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

TimingSource = Literal["provider_word", "provider_sentence", "estimated", "aligned", "captions"]
"""Where the times come from: ``estimated`` (shared out by word count over the narration),
``captions`` (an SRT/VTT file's own times), ``aligned`` (a speech-to-text tool, later),
``provider_word`` / ``provider_sentence`` (kept for timing files made by CashCow Studio)."""
WordSource = Literal["provider", "estimated", "aligner"]
TimingConfidence = Literal["high", "medium", "low"]

DEFAULT_SAMPLE_RATE = 48000


class TimedWord(BaseModel):
    text: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> TimedWord:
        if self.end_s < self.start_s:
            raise ValueError(
                f"the word {self.text!r} ends ({self.end_s}) before it starts ({self.start_s})"
            )
        return self

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)


class TimedSentence(BaseModel):
    id: str
    text: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    words: list[TimedWord] = []
    words_source: WordSource = "estimated"
    start_frame: int | None = Field(default=None, description="sample offset in voice.wav")
    end_frame: int | None = None
    audio_path: str = Field(default="", description="05_voice/sentences/<id>.wav")
    paragraph: int = Field(default=0, ge=0, description="which paragraph of the caption "
                           "text the sentence belongs to (0-based)")

    @model_validator(mode="after")
    def _ordered(self) -> TimedSentence:
        if self.end_s < self.start_s:
            raise ValueError(
                f"sentence {self.id} ends ({self.end_s}) before it starts ({self.start_s})"
            )
        return self

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)


class TimingDoc(BaseModel):
    """``05_voice/timing.json``."""

    sample_rate: int = Field(default=DEFAULT_SAMPLE_RATE, ge=8000)
    duration_s: float = Field(ge=0)
    source: TimingSource
    sentences: list[TimedSentence]
    timing_confidence: TimingConfidence = "medium"
    provider: str = ""
    model: str = ""
    voice_id: str = ""
    language: str = ""
    aligner: str = Field(default="none", description="aligner used for an own recording")
    own_recording: bool = True
    nudged: bool = Field(default=False, description="a person moved sentence starts in the "
                         "timing tool")
    generated_at: datetime | None = None
    warnings: list[str] = []
    schema_version: int = 1

    @property
    def words(self) -> list[TimedWord]:
        return [w for s in self.sentences for w in s.words]

    def sentence(self, sentence_id: str) -> TimedSentence | None:
        for item in self.sentences:
            if item.id == sentence_id:
                return item
        return None
