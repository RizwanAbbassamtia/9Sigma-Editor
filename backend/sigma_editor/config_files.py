"""The YAML files in ``config/`` as Python objects: ``render.yaml``, ``captions.yaml``,
``transitions.yaml`` and ``voice.yaml`` (the speaking rates for the timing estimate).

The ``config`` folder sits next to ``backend`` in the repository (and in the installed app);
``SGE_CONFIG_DIR`` overrides it (``config.config_dir``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import config_dir

# Re-exported: the render engine imports these from here.
from .models.transitions import Transition as Transition
from .models.transitions import TransitionCatalog as TransitionCatalog

VOICE_FILE = "voice.yaml"
TRANSITIONS_FILE = "transitions.yaml"
DEFAULT_WPM = 150


class ConfigError(Exception):
    """A YAML file under ``config/`` is missing or malformed (plain-English message)."""


def load_yaml(name: str) -> dict[str, Any]:
    """Parse ``config/<name>``; a missing file is an empty mapping, a broken one an error."""
    path: Path = config_dir() / name
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"The settings file {path} could not be read: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"The settings file {path} must contain a mapping at the top level.")
    return data


# Speaking rate -----------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _voice_config() -> dict[str, Any]:
    return load_yaml(VOICE_FILE)


def speaking_rate_wpm(language: str | None, override: int | None = None) -> int:
    """Words per minute for a language from ``config/voice.yaml`` (default 150)."""
    if override:
        try:
            return max(1, int(override))
        except (TypeError, ValueError):
            pass
    data = _voice_config().get("speaking_rate") or {}
    by_language = data.get("by_language") or {}
    default = int(data.get("default_wpm") or DEFAULT_WPM)
    if language and language in by_language:
        try:
            return int(by_language[language])
        except (TypeError, ValueError):
            return default
    return default


# Transitions -------------------------------------------------------------------------------
# config/transitions.yaml v2 (packs, energy, easing, engine params, the legacy xfade names);
# the shapes and the lookups live in models/transitions.py, shared with the preview tool.


@lru_cache(maxsize=1)
def transition_catalog() -> TransitionCatalog:
    """``config/transitions.yaml`` as a :class:`TransitionCatalog` (cached; see
    :func:`clear_caches`). A missing or half-broken file still gives a working catalogue."""
    return TransitionCatalog.from_mapping(load_yaml(TRANSITIONS_FILE))


def clear_caches() -> None:
    """Forget cached YAML (tests that point ``SGE_CONFIG_DIR`` somewhere else call this)."""
    _voice_config.cache_clear()
    transition_catalog.cache_clear()
