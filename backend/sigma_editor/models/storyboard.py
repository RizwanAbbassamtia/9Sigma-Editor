"""The scene plan: which picture or clip each scene shows, which sentences it covers, its
camera move, its transition and its popup. Kept as ``07_edit/scenes.json``.

The shape is the storyboard document of CashCow Studio's render engine
(``render/timeline_builder.py`` builds the timeline from it), trimmed to what an edit from
the person's own files needs (docs/CONTRACT-v0.1.md section 7.4). It is never shown as a
storyboard: there is no storyboard tab in this app.

Rectangles are fractions of the frame ``[x, y, w, h]`` in 0-1 so they work for any resolution
and aspect; the timeline builder multiplies by the output size. Camera moves (Motion v2): the
presets, their rectangles and labels are defined once, in ``render/motion.py`` (``PRESETS``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..render.motion import PRESETS as _MOTION_SPECS
from .timeline import MotionEase
from .transitions import (
    DEFAULT_TRANSITION_PACK,
    MotionStyle,
    TransitionPack,
    clamp_transition_s,
)

Aspect = Literal["16:9", "9:16"]
PopupPosition = Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"]
MotionPreset = Literal[
    "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "hold", "slow_push",
    "drift", "tilt_up", "tilt_down", "dolly_in", "dolly_out", "handheld",
]
ImageStatus = Literal["pending", "generated", "approved", "rejected"]

POPUP_POSITIONS: tuple[PopupPosition, ...] = (
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "center",
)
MOTION_PRESETS: tuple[MotionPreset, ...] = (
    "zoom_in",
    "pan_right",
    "zoom_out",
    "pan_left",
    "slow_push",
    "pan_up",
    "pan_down",
    "hold",
    "drift",
    "tilt_up",
    "dolly_in",
    "tilt_down",
    "dolly_out",
    "handheld",
)
"""Every preset, in the order the app cycles through them when it picks one."""
MOTION_PRESET_LABELS: dict[str, str] = {
    preset: _MOTION_SPECS[preset].label for preset in MOTION_PRESETS
}
"""Plain-English names (``drift`` -> "Drift"), from ``render/motion.py``."""
MOTION_PRESET_ENERGY: dict[str, str] = {
    preset: _MOTION_SPECS[preset].energy for preset in MOTION_PRESETS
}
MAX_POPUP_WORDS = 6
MOTION_EASES: tuple[str, ...] = (
    "ease_in_out_cubic", "ease_out_quart", "ease_in_out_sine", "snap", "linear",
)
EDIT_FIELDS: tuple[str, ...] = (
    "popup", "motion", "transition_out", "notes", "sentence_ids", "order", "image",
)
_MOTION_STYLE_ALIASES: dict[str, MotionStyle] = {
    "ken_burns": "ken_burns", "kenburns": "ken_burns", "kb": "ken_burns",
    "pan_and_zoom": "ken_burns", "flat": "ken_burns",
    "parallax": "parallax", "2_5d": "parallax", "2_5d_parallax": "parallax",
    "depth": "parallax",
}


def _key(value: Any) -> str:
    text = str(value or "").strip().lower()
    for char in "-'. /’":
        text = text.replace(char, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


class ScenePopup(BaseModel):
    text: str | None = Field(default=None, description="at most 6 words; null = no popup")
    style: str = ""
    position: PopupPosition = "bottom-left"
    in_offset_s: float = 0.4
    out_offset_s: float = 3.5
    key_sentence_id: str | None = Field(
        default=None,
        description="the sentence the popup appears after (in_offset_s after it starts); "
        "null = counted from the start of the scene",
    )


class SceneMotion(BaseModel):
    preset: MotionPreset = "zoom_in"
    start_rect: list[float] = Field(default=[0.0, 0.0, 1.0, 1.0], min_length=4, max_length=4)
    end_rect: list[float] = Field(default=[0.1, 0.1, 0.8, 0.8], min_length=4, max_length=4)
    ease: MotionEase | None = Field(
        default=None, description="the move's speed curve; null = the preset's own"
    )
    style: MotionStyle | None = Field(
        default=None, description="ken_burns or parallax for this scene; null = the video's"
    )
    strength: float | None = Field(
        default=None, description="parallax depth strength 0 to 0.2; null = the default"
    )

    @field_validator("ease", mode="before")
    @classmethod
    def _ease(cls, value: Any) -> str | None:
        key = _key(value)
        return key if key in MOTION_EASES else None

    @field_validator("style", mode="before")
    @classmethod
    def _style(cls, value: Any) -> str | None:
        return _MOTION_STYLE_ALIASES.get(_key(value))

    @field_validator("strength", mode="before")
    @classmethod
    def _strength(cls, value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return round(min(0.2, max(0.0, float(value))), 4)
        except (TypeError, ValueError):
            return None


class SceneTransition(BaseModel):
    type: str = Field(
        description="a transition id from config/transitions.yaml (old xfade names still map)"
    )
    duration_s: float = Field(
        default=0.6, description="seconds; anything above 3 s is brought down to 3 s"
    )
    beat: str | None = Field(default=None, description="the story moment at this cut")

    @field_validator("duration_s")
    @classmethod
    def _at_most_max(cls, value: float) -> float:
        return clamp_transition_s(value)


class SceneImage(BaseModel):
    path: str | None = None
    status: ImageStatus = "pending"
    qa: dict[str, Any] | None = None


class SceneLocks(BaseModel):
    popup: bool = False
    motion: bool = False
    transition_out: bool = False
    narration: bool = False
    image: bool = False

    @property
    def any(self) -> bool:
        return any((self.popup, self.motion, self.transition_out, self.narration, self.image))


class Scene(BaseModel):
    index: int
    sentence_ids: list[str]
    narration: str
    est_start_s: float = 0.0
    est_end_s: float = 0.0
    est_duration_s: float = 0.0
    image_prompt: str = ""
    negative_prompt: str = ""
    popup: ScenePopup = ScenePopup()
    motion: SceneMotion = SceneMotion()
    transition_out: SceneTransition = SceneTransition(type="film_dissolve")
    on_screen_text: str | None = None
    image: SceneImage = SceneImage()
    locked: SceneLocks = SceneLocks()
    notes: list[str] = []
    title: str = Field(default="", description="2 to 3 words naming the shot")
    # 9Sigma Editor: a scene made from a short video clip instead of a picture.
    clip: str | None = Field(
        default=None,
        description="the clip file (relative to the edit's folder) this scene plays; the "
        "picture is its poster frame; null = a still picture",
    )
    clip_duration_s: float | None = Field(default=None, description="the clip's own length")
    keep_sound: bool = Field(default=False, description="clips only: keep the clip's sound")
    edited: list[str] = Field(
        default=[], description="fields a person changed by hand; kept across a rebuild"
    )

    @field_validator("sentence_ids")
    @classmethod
    def _at_least_one_sentence(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a scene must cover at least one sentence")
        return value

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: Any) -> str:
        return _clean_text(value)[:40]

    @field_validator("edited", mode="before")
    @classmethod
    def _edited(cls, value: Any) -> list[str]:
        wanted = {str(v) for v in (value or [])}
        return [f for f in EDIT_FIELDS if f in wanted]

    def is_edited(self, field: str) -> bool:
        return field in self.edited

    def mark_edited(self, *fields: str) -> None:
        wanted = set(self.edited) | {f for f in fields if f in EDIT_FIELDS}
        self.edited = [f for f in EDIT_FIELDS if f in wanted]

    @property
    def has_text(self) -> bool:
        return bool((self.popup.text or "").strip() or (self.on_screen_text or "").strip())


class StoryboardVariety(BaseModel):
    scenes: int = 0
    avg_scene_s: float = 0.0
    popup_share: float = 0.0
    distinct_transitions: int = 0
    distinct_motions: int = 0
    warnings: list[str] = []
    transition_histogram: dict[str, int] = Field(default={})


class StoryboardDoc(BaseModel):
    """``07_edit/scenes.json``: the scene plan of an edit."""

    project_id: str
    format: Literal["long", "shorts"]
    aspect: Aspect
    style_guide: str = ""
    negative_rules: str = ""
    popup_style: str = ""
    transition_pack: TransitionPack = Field(default=DEFAULT_TRANSITION_PACK)
    motion_style: MotionStyle | None = Field(
        default=None, description="this video's camera motion; null = the app's default"
    )
    generated_at: datetime
    model: str = "9sigma-editor"
    speaking_rate_wpm: int = 150
    scenes: list[Scene]
    variety: StoryboardVariety = StoryboardVariety()
    gate_results: list[dict[str, Any]] = []
    notes: list[str] = []
    schema_version: int = 1
    revision: int = Field(default=0, description="goes up by one with every save")
    saved_at: datetime | None = None

    @property
    def total_s(self) -> float:
        return round(self.scenes[-1].est_end_s, 2) if self.scenes else 0.0
