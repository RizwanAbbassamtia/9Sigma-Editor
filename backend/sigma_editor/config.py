"""Runtime settings: where the app keeps its data, which port and tools it uses, and the
render defaults (docs/CONTRACT-v0.1.md section 6).

Values come from, in order of priority:

1. environment variables ``SGE_APP_DATA_DIR``, ``SGE_PROJECTS_DIR``, ``SGE_EXPORTS_DIR``,
   ``SGE_PORT`` (prefix ``SGE_``) and the plain ``FFMPEG_PATH`` / ``FFPROBE_PATH``;
2. ``<app_data_dir>/settings.json``, written by the Settings screen;
3. the defaults below.

``<app_data_dir>/.env`` may hold ``FFMPEG_PATH`` / ``FFPROBE_PATH`` lines; it is loaded into
the process environment before the settings are read. There are no API keys, no sign-in and
no shared folder in this app: settings are per laptop.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models.transitions import (
    DEFAULT_MOTION_STYLE,
    DEFAULT_TRANSITION_PACK,
    MotionStyle,
    TransitionPack,
)
from .storage.settings_store import SettingsStore

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
ENV_FILE = ".env"
PATH_KEYS = ("projects_dir", "exports_dir")
NESTED_KEYS = ("render", "captions")
"""The nested objects of settings.json (Settings > Render and Settings > Captions)."""
HOST = "127.0.0.1"
DEFAULT_PORT = 8766
APP_FOLDER = "9SigmaEditor"

RenderPresetName = Literal["720p", "1080p", "2160p"]
X264Preset = Literal[
    "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"
]
QualityName = Literal["standard", "high", "best"]
"""Standard (x264 crf 20, medium), High (crf 17, slow), Best (crf 15, veryslow)."""
UploadPresetName = Literal["none", "youtube_1080p", "youtube_4k"]

QUALITY_LABELS: dict[str, str] = {"standard": "Standard", "high": "High", "best": "Best"}


class RenderSettings(BaseModel):
    """Settings > Render: the defaults a new edit starts with."""

    default_presets: list[RenderPresetName] = ["1080p"]
    """Which final sizes a new edit renders; the editor can tick more."""
    quality: QualityName = "standard"
    """Standard, High or Best (docs/CONTRACT-v0.1.md section 5)."""
    x264_preset: X264Preset | None = None
    """An encoder speed that overrides the quality's own (``None`` = the quality decides)."""
    two_pass_best: bool = False
    """Best quality in two passes (slower, a touch better bitrate use)."""
    h265_copy: bool = False
    """Also write an H.265 copy of every final size (smaller file, slower, for archives)."""
    enable_4k: bool = False
    """Allow 2160p renders (slow on a laptop)."""
    transition_pack: TransitionPack = DEFAULT_TRANSITION_PACK
    """cinematic, clean or dynamic: the transitions a new edit picks from."""
    motion_style: MotionStyle = DEFAULT_MOTION_STYLE
    """ken_burns (pan and zoom) or parallax (2.5D depth, needs the depth model)."""

    @field_validator("default_presets")
    @classmethod
    def _unique_presets(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for preset in value:
            if preset not in seen:
                seen.append(preset)
        return seen or ["1080p"]


class CaptionsSettings(BaseModel):
    """Settings > Captions."""

    enabled: bool = True
    """Burn the captions into new edits (the editor can switch them off per edit)."""
    style: str = Field(default="bold-white", min_length=1, max_length=40)
    """A named look from config/captions.yaml (bold-white, yellow, clean): the colours."""
    preset: str = Field(default="bold-outline", min_length=1, max_length=40)
    """The look preset: bold-outline, active-word, single-word-pop or boxed."""


NESTED_MODELS: dict[str, type[BaseModel]] = {
    "render": RenderSettings,
    "captions": CaptionsSettings,
}


def _home() -> Path:
    profile = os.environ.get("USERPROFILE") if sys.platform == "win32" else None
    return Path(profile) if profile else Path.home()


def default_app_data_dir() -> Path:
    """``%LOCALAPPDATA%/9SigmaEditor`` on Windows, ``~/.local/share/9SigmaEditor`` elsewhere."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else _home() / "AppData" / "Local"
        return base / APP_FOLDER
    return Path.home() / ".local" / "share" / APP_FOLDER


def default_videos_dir() -> Path:
    """``%USERPROFILE%/Videos/9SigmaEditor`` on Windows, ``~/Videos/9SigmaEditor`` elsewhere."""
    return _home() / "Videos" / APP_FOLDER


def default_projects_dir() -> Path:
    return default_videos_dir() / "projects"


def default_exports_dir() -> Path:
    return default_videos_dir() / "exports"


def _expand(value: Any) -> Any:
    """Expand ``~`` and ``%VAR%`` / ``$VAR`` in a path string; blank strings become ``None``."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return Path(os.path.expandvars(os.path.expanduser(text)))
    return value


class Settings(BaseSettings):
    """Effective configuration for one running app."""

    model_config = SettingsConfigDict(
        env_prefix="SGE_",
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,
        validate_assignment=True,
    )

    app_data_dir: Path = Field(default_factory=default_app_data_dir)
    projects_dir: Path = Field(default_factory=default_projects_dir)
    exports_dir: Path = Field(default_factory=default_exports_dir)
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    ffmpeg_path: str = Field(default="", validation_alias="FFMPEG_PATH")
    ffprobe_path: str = Field(default="", validation_alias="FFPROBE_PATH")

    render: RenderSettings = RenderSettings()
    captions: CaptionsSettings = CaptionsSettings()

    @field_validator("app_data_dir", mode="before")
    @classmethod
    def _app_data_dir_default(cls, value: Any) -> Any:
        expanded = _expand(value)
        return default_app_data_dir() if expanded is None else expanded

    @field_validator("projects_dir", mode="before")
    @classmethod
    def _projects_dir_default(cls, value: Any) -> Any:
        expanded = _expand(value)
        return default_projects_dir() if expanded is None else expanded

    @field_validator("exports_dir", mode="before")
    @classmethod
    def _exports_dir_default(cls, value: Any) -> Any:
        expanded = _expand(value)
        return default_exports_dir() if expanded is None else expanded

    @field_validator("ffmpeg_path", "ffprobe_path", mode="before")
    @classmethod
    def _tool_path_strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    # Derived locations -------------------------------------------------------------------

    @property
    def settings_file(self) -> Path:
        return self.app_data_dir / SETTINGS_FILE

    @property
    def env_file(self) -> Path:
        return self.app_data_dir / ENV_FILE

    @property
    def logs_dir(self) -> Path:
        return self.app_data_dir / "logs"

    def paths_as_dict(self) -> dict[str, str | None]:
        """The person's folder choices in the form saved to settings.json."""
        return {"projects_dir": str(self.projects_dir), "exports_dir": str(self.exports_dir)}

    def nested_as_dict(self) -> dict[str, dict[str, Any]]:
        """The ``render`` and ``captions`` objects of settings.json."""
        return {key: getattr(self, key).model_dump(mode="json") for key in NESTED_KEYS}

    def ensure_dirs(self) -> list[str]:
        """Create the folders the app needs. Never raises; returns plain-English problems."""
        problems: list[str] = []
        for folder in (self.app_data_dir, self.logs_dir, self.projects_dir, self.exports_dir):
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                problems.append(f"Could not create the folder {folder}: {exc}")
        return problems


def nested_overrides(saved: dict[str, Any]) -> dict[str, BaseModel]:
    """Validate the nested objects of settings.json one by one.

    A section that does not validate (a hand-edited file, an older app version) is logged and
    replaced by its defaults; it never stops the app from starting.
    """
    overrides: dict[str, BaseModel] = {}
    for key, model in NESTED_MODELS.items():
        value = saved.get(key)
        if not isinstance(value, dict):
            continue
        try:
            overrides[key] = model.model_validate(value)
        except ValidationError as exc:
            log.warning(
                "Ignoring the '%s' section of settings.json (using defaults): %s", key, exc
            )
    return overrides


def load_settings() -> Settings:
    """Build the effective settings: env vars win, then settings.json, then defaults."""
    bootstrap = Settings()
    store = SettingsStore(bootstrap.app_data_dir)
    store.load_into_environ()  # tool paths saved in .env
    saved = store.read_paths()
    overrides: dict[str, Any] = {}
    for key in PATH_KEYS:
        if os.environ.get(f"SGE_{key.upper()}"):
            continue  # an environment variable always wins over settings.json
        if key in saved:
            overrides[key] = saved[key]
    overrides.update(nested_overrides(store.read_sections()))
    return Settings(**overrides)
