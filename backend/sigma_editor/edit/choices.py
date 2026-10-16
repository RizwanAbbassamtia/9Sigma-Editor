"""The simple choices of the editor (docs/CONTRACT-v0.1.md section 4): which sizes to render,
the music track and its levels, captions on or off, popups on or off, and "build the
timeline again". Used by ``POST /api/edits/{id}/edit`` and by the tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models.project import Project
from ..models.timeline import EditChoices, Timeline
from ..render.music import has_licence, resolve_track
from ..render.presets import load_render_config
from .builder import BuildError, rebuild_timeline
from .renderer import (
    STAGE_DIR_NAME,
    music_folder_of,
    read_state,
    read_timeline,
    write_state,
    write_timeline,
)


class EditError(ValueError):
    """A choice that cannot be applied, in plain English (the API answers 422)."""


def apply_edits(project: Project, settings: Any, edits: EditChoices) -> Timeline:
    """Apply ``edits`` to the edit's timeline (and ``render_state.json`` for the popups
    switch) and return the timeline. Raises :class:`EditError`."""
    folder = Path(project.folder)
    stage_dir = folder / STAGE_DIR_NAME
    if edits.rebuild:
        try:
            rebuild_timeline(project, settings)
        except BuildError as exc:
            raise EditError(str(exc)) from exc
    timeline = read_timeline(stage_dir)
    if timeline is None:
        raise EditError("This edit has no timeline yet. Add pictures or clips first.")
    config = load_render_config()
    state = read_state(stage_dir)
    if edits.presets is not None:
        unknown = [p for p in edits.presets if p not in config.presets]
        if unknown:
            choices = ", ".join(config.presets)
            raise EditError(
                f"'{unknown[0]}' is not a render size this app knows. Choose one of: {choices}."
            )
        timeline.presets = list(dict.fromkeys(edits.presets))
    if edits.captions_enabled is not None:
        timeline.captions.enabled = bool(edits.captions_enabled)
    if edits.popups_enabled is not None:
        state["popups_enabled"] = bool(edits.popups_enabled)
    if edits.changes_music:
        chosen = (edits.music_path or "").strip()
        if not chosen:
            timeline.music.path = None
            timeline.music.license_ok = False
            timeline.music.stem = None
            timeline.music.envelope = None
        else:
            track = resolve_track(music_folder_of(folder), chosen)
            if track is None:
                raise EditError(
                    f"The music file '{chosen}' was not found. Add it to the edit first."
                )
            timeline.music.path = str(track)
            timeline.music.license_ok = bool(
                has_licence(track) or project.music_licence_note.strip()
            )
    if edits.music_gain_db is not None:
        timeline.music.gain_db = float(edits.music_gain_db)
    if edits.music_duck_db is not None:
        timeline.music.duck_db = float(edits.music_duck_db)
    timeline.revision += 1
    write_timeline(stage_dir, timeline)
    write_state(stage_dir, state)
    return timeline


__all__ = ["EditError", "apply_edits"]
