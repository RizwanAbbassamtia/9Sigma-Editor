"""Rendering an edit: ``07_edit/timeline.json`` becomes ``proxy.mp4`` and
``final_<preset>.mp4`` per requested size (docs/CONTRACT-v0.1.md section 5).

Taken from CashCow Studio's edit stage and cut free of the pipeline: there is no stage
context, no channel and no review; the API's render job calls :func:`render_edit` in a
thread with a cancel flag and a progress callback.

Files first: the timeline, ``captions.ass``, the popup PNGs and ``render.log`` are written
before any video. A finished render is kept while what it was made from is unchanged (the
timeline without its preset list, the quality, the upload preset, the popup switch and the
media files it uses, hashed into ``render_key`` in ``render_state.json``), so adding a size
or changing the quality re-encodes only what is missing or stale; every scene and every
transition is a cached clip under ``07_edit/clips/<preset>/``, so a quality change never
redraws the scenes (only the final assembly pass runs again).

Before rendering the music stem and its envelope are made (``render/music.py``) and the
preview gain is measured, so the live preview sounds like the finished file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..models.project import Project
from ..models.timeline import (
    DEFAULT_ACTIVE_COLOUR,
    DEFAULT_CAPTION_PRESET,
    MIN_CLIP_S,
    TIMELINE_FILE,
    EditReviewPayload,
    EditSceneView,
    RenderOutput,
    Timeline,
    TimelineSummary,
    VoiceSegment,
    VoiceTrack,
)
from ..policy import gates
from ..render import ffmpeg as ff
from ..render.captions import ASS_FILE, rules_for, write_ass
from ..render.music import (
    STEM_VERSION,
    ensure_ducked_stem,
    ensure_preview_gain,
    has_licence,
    music_file,
    relative_stem_paths,
    stem_is_current,
    track_infos,
)
from ..render.popups import brand_colour, popup_position, popup_scale, render_popup_png
from ..render.presets import (
    PROXY_ID,
    Quality,
    RenderConfig,
    RenderPreset,
    UploadPreset,
    load_render_config,
    quality_for,
    upload_preset_for,
)
from ..render.thumbs import prepare_in_background
from ..storage.files import atomic_write_text
from ..storage.project_store import EDIT_DIR, MEDIA_DIR, VOICE_DIR
from .checks import editor_checks
from .timing_doc import TIMING_FILE, read_timing

log = logging.getLogger(__name__)

STAGE_DIR_NAME = EDIT_DIR
STATE_FILE = "render_state.json"
RENDER_LOG = "render.log"
POPUPS_DIR = "popups"
PROXY_FILE = "proxy.mp4"
DEFAULT_PRESET = "1080p"
RANGE_DIR = "range"
H265_SUFFIX = "_h265"
MIN_RANGE_S = 0.5

ProgressFn = Callable[[str, float | None], None]


class RenderFailed(Exception):
    """A render could not be made; the message is plain English for the person."""


def final_file_name(preset_id: str) -> str:
    return PROXY_FILE if preset_id == PROXY_ID else f"final_{preset_id}.mp4"


def h265_file_name(preset_id: str) -> str:
    return f"final_{preset_id}{H265_SUFFIX}.mp4"


def file_url(project_id: str, relative: str) -> str:
    return f"/api/projects/{project_id}/files/{relative.replace(chr(92), '/')}"


def utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Fingerprints ------------------------------------------------------------------------------------


def files_fingerprint(folder: Path, paths: Iterable[Path | str]) -> str:
    """A short hash of each file's path (relative to the edit's folder when inside it), size
    and modification time in nanoseconds; a missing file counts as missing. Any replaced file
    changes it, also one replaced by a file with an older date."""
    folder = Path(folder)
    rows: list[list[Any]] = []
    for path in sorted({Path(p) for p in paths}, key=lambda p: str(p).lower()):
        try:
            name = path.relative_to(folder).as_posix()
        except ValueError:
            name = path.as_posix()
        try:
            stat = path.stat()
            rows.append([name, int(stat.st_size), int(stat.st_mtime_ns)])
        except OSError:
            rows.append([name, -1, -1])
    blob = json.dumps(rows, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def timeline_media(folder: Path, timeline: Timeline) -> list[Path]:
    """Every file the render of ``timeline`` reads: the pictures, the voice, the music, the
    intro and outro clips and (v0.2) every clip, layer and sound track source."""
    paths = [ff.resolve_path(folder, scene.image) for scene in timeline.scenes if scene.image]
    paths.append(ff.resolve_path(folder, timeline.voice.path))
    for value in (timeline.music.path, timeline.intro.path, timeline.outro.path):
        if value:
            paths.append(ff.resolve_path(folder, value))
    for value in timeline.source_files:
        paths.append(ff.resolve_path(folder, value))
    return paths


# State on disk -----------------------------------------------------------------------------


def read_state(stage_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((stage_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(stage_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_stamp()
    atomic_write_text(stage_dir / STATE_FILE, json.dumps(state, indent=2, default=str) + "\n")


def read_timeline(stage_dir: Path) -> Timeline | None:
    path = stage_dir / TIMELINE_FILE
    if not path.is_file():
        return None
    try:
        return Timeline.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        log.warning("timeline.json could not be read: %s", exc)
        return None


def write_timeline(stage_dir: Path, timeline: Timeline) -> tuple[Path, bool]:
    """Write ``timeline.json`` only when its content changed. Returns ``(path, changed)``."""
    path = stage_dir / TIMELINE_FILE
    text = timeline.model_dump_json(indent=2) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return path, False
    except OSError:
        pass
    atomic_write_text(path, text)
    return path, True


def render_content(timeline: Timeline) -> dict[str, Any]:
    """The timeline as far as the finished video depends on it: without the preset list and
    what the renderer ignores (``revision``, ``markers``, ``render``, ids and names,
    ``edited`` and ``locked`` flags, popup anchors, a scene's ``sentence_ids``, the stem and
    envelope paths), and without the fields that still hold their defaults (a scene's
    ``clip``, ``frame`` and ``adjust`` when null, ``layers`` and ``audio_tracks`` when
    empty, so a v0.1 timeline keeps its key)."""
    content = timeline.model_dump(
        mode="json", exclude={"presets", "revision", "markers", "render"}
    )
    music = content.get("music") or {}
    music.pop("stem", None)
    music.pop("envelope", None)
    voice = content.get("voice") or {}
    if voice.get("segments") is None:
        voice.pop("segments", None)
    for scene in content.get("scenes") or []:
        scene.pop("edited", None)
        scene.pop("sentence_ids", None)
        motion = scene.get("motion") or {}
        if motion.get("ease") is None:
            motion.pop("ease", None)
        for key in ("clip", "frame", "adjust"):
            if scene.get(key) is None:
                scene.pop(key, None)
    for key in ("layers", "audio_tracks"):
        if not content.get(key):
            content.pop(key, None)
    for layer in content.get("layers") or []:
        for key in ("id", "name", "locked"):
            layer.pop(key, None)
        for clip in layer.get("clips") or []:
            clip.pop("id", None)
            clip.pop("name", None)
    for track in content.get("audio_tracks") or []:
        track.pop("id", None)
        track.pop("name", None)
        for clip in track.get("clips") or []:
            for key in ("id", "name", "text", "voice"):
                clip.pop(key, None)
    for popup in content.get("popups") or []:
        for key in ("id", "anchor", "edited", "locked"):
            popup.pop(key, None)
    captions = content.get("captions") or {}
    for cue in captions.get("cues") or []:
        cue.pop("id", None)
        cue.pop("edited", None)
    style = captions.get("style") or {}
    if style.get("preset") == DEFAULT_CAPTION_PRESET:
        style.pop("preset", None)
        if style.get("layout") == "phrase":
            style.pop("layout", None)
        if style.get("active_colour") == DEFAULT_ACTIVE_COLOUR:
            style.pop("active_colour", None)
    return content


def render_key_for(
    timeline: Timeline,
    *,
    popups_enabled: bool,
    x264_preset: str | None,
    media: str,
    depth: str = "",
    quality: str = "standard",
    upload_preset: str = "none",
) -> str:
    """A short hash of everything a finished render depends on."""
    from ..render.scene_clips import CLIP_ENGINE_VERSION, PARALLAX_DRAW_VERSION
    from ..render.transitions import ENGINE_VERSION

    payload: dict[str, Any] = {
        "timeline": render_content(timeline),
        "popups": bool(popups_enabled),
        "x264": x264_preset or "",
        "media": media,
        "engine": f"{ENGINE_VERSION}/{CLIP_ENGINE_VERSION}/{PARALLAX_DRAW_VERSION}",
        "depth": depth,
        "quality": quality,
        "upload": upload_preset,
    }
    if timeline.music.path:
        payload["music_mix"] = STEM_VERSION
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# The sound of the live preview ------------------------------------------------------------------


@dataclass
class PreviewAudio:
    stem_built: bool = False
    stem_seconds: float = 0.0
    stem_key: str | None = None
    preview_gain_db: float | None = None


def prepare_preview_audio(
    folder: Path,
    timeline: Timeline,
    config: RenderConfig,
    *,
    ffmpeg: str | None = None,
    cancel: Any = None,
) -> PreviewAudio:
    """Make the music stem and its envelope when they are not current and measure
    ``preview_gain_db``; write both into ``timeline``."""
    result = PreviewAudio()
    stem = None
    if music_file(timeline, folder) is not None:
        stem = ensure_ducked_stem(timeline, folder, config=config, ffmpeg=ffmpeg, cancel=cancel)
    if stem is not None:
        timeline.music.stem, timeline.music.envelope = relative_stem_paths()
        result.stem_built, result.stem_seconds = stem.built, stem.seconds
        result.stem_key = stem.key
    else:
        timeline.music.stem = timeline.music.envelope = None
    try:
        result.preview_gain_db = ensure_preview_gain(
            timeline, folder, config=config, stem=stem, ffmpeg=ffmpeg, cancel=cancel
        )
    except ff.RenderCancelled:
        raise
    except ff.RenderError as exc:  # the preview plays at its own level; the render goes on
        log.warning("The loudness for the preview could not be measured: %s", exc)
        result.preview_gain_db = None
    timeline.render.preview_gain_db = result.preview_gain_db
    return result


# Settings ----------------------------------------------------------------------------------------


@dataclass
class RenderOptions:
    presets: list[str] = field(default_factory=lambda: [DEFAULT_PRESET])
    quality: Quality = field(default_factory=lambda: quality_for("standard"))
    upload: UploadPreset | None = None
    x264_preset: str | None = None
    """An encoder speed that overrides the quality's own."""
    enable_4k: bool = False
    h265_copy: bool = False
    captions_enabled: bool = True
    caption_style: str = "bold-white"
    caption_preset: str | None = None

    @property
    def x264(self) -> str:
        return self.x264_preset or self.quality.x264_preset


def options_from(
    settings: Any,
    *,
    presets: list[str] | None = None,
    quality: str | None = None,
    upload_preset: str | None = None,
    h265_copy: bool | None = None,
) -> RenderOptions:
    render = getattr(settings, "render", None)
    captions = getattr(settings, "captions", None)
    options = RenderOptions()
    defaults = getattr(render, "default_presets", None)
    if isinstance(defaults, list | tuple) and defaults:
        options.presets = [str(p) for p in defaults]
    if presets:
        options.presets = [str(p) for p in presets]
    options.quality = quality_for(quality or getattr(render, "quality", None))
    options.upload = upload_preset_for(upload_preset)
    x264 = getattr(render, "x264_preset", None)
    options.x264_preset = str(x264) if isinstance(x264, str) and x264 else None
    options.enable_4k = bool(getattr(render, "enable_4k", False))
    if h265_copy is None:
        h265_copy = getattr(render, "h265_copy", False)
    options.h265_copy = bool(h265_copy)
    enabled = getattr(captions, "enabled", None)
    if isinstance(enabled, bool):
        options.captions_enabled = enabled
    style = getattr(captions, "style", None)
    if isinstance(style, str) and style:
        options.caption_style = style
    preset = getattr(captions, "preset", None)
    if isinstance(preset, str) and preset:
        options.caption_preset = preset
    return options


def allowed_presets(
    wanted: list[str], config: RenderConfig, enable_4k: bool, warnings: list[str]
) -> list[str]:
    """The presets of ``wanted`` this app can render; unknown ones raise, 4K without the
    setting is dropped with a warning."""
    result: list[str] = []
    for preset_id in wanted:
        name = str(preset_id).strip()
        if not name or name in result:
            continue
        if name == PROXY_ID:
            continue
        if name not in config.presets:
            choices = ", ".join(config.presets)
            raise RenderFailed(
                f"'{name}' is not a render size this app knows. Choose one of: {choices}."
            )
        if config.presets[name].requires_enable_4k and not enable_4k:
            warnings.append(f"{name} was not rendered: 4K is switched off in Settings > Render.")
            continue
        result.append(name)
    return result


# 2.5D parallax ----------------------------------------------------------------------------------


def _wants_parallax(scene: Any) -> bool:
    return getattr(scene.motion, "style", None) == "parallax"


def prepare_depth_maps(
    folder: Path, timeline: Timeline, settings: Any, cancel: Any = None
) -> tuple[str | None, str]:
    """Depth maps for the pictures of scenes set to 2.5D parallax (made only when the depth
    model is downloaded). Returns a note when some scenes must use the Ken Burns move and a
    fingerprint of the maps for the render key."""
    images: list[Path] = []
    for scene in timeline.scenes:
        if _wants_parallax(scene) and scene.image:
            image = ff.resolve_path(folder, scene.image)
            if image not in images:
                images.append(image)
    if not images:
        return None, ""
    try:
        from ..render.parallax import depth_fingerprint, ensure_depth_maps
    except ImportError:
        return (
            "2.5D parallax is not installed on this computer, so those scenes use the Ken "
            "Burns move instead.",
            "",
        )
    note: str | None = None
    try:
        batch = ensure_depth_maps(images, settings, cancel=cancel)
        note = batch.fallback_reason
    except Exception as exc:  # noqa: BLE001 - the render goes on with the Ken Burns move
        log.warning("Depth maps could not be made: %s", exc)
        note = "Depth maps could not be made, so the 2.5D parallax scenes use the Ken Burns move."
    token = "|".join(f"{image.name}:{depth_fingerprint(image)}" for image in images)
    return note, token


# Popups ------------------------------------------------------------------------------------------


def popup_assets(
    timeline: Timeline,
    preset: RenderPreset,
    *,
    stage_dir: Path,
    config: RenderConfig,
    box_colour: str,
    font_family: str,
    captions_on: bool,
    enabled: bool,
) -> list[ff.PopupAsset]:
    """The popup PNGs for one preset, positioned in its frame (above the caption area)."""
    if not enabled or not timeline.popups:
        return []
    width, height = preset.size(timeline.aspect)
    scale = popup_scale(width, height)
    look = config.popups
    reserved = 0
    if captions_on and timeline.captions.cues:
        style = timeline.captions.style
        reserved = round((style.size * style.max_lines * 1.4 + config.captions_margin_v_px) * scale)
    out_dir = stage_dir / POPUPS_DIR / preset.id
    assets: list[ff.PopupAsset] = []
    for number, popup in enumerate(timeline.popups):
        png = out_dir / f"popup_{number:02d}.png"
        popup_w, popup_h = render_popup_png(
            popup.text, png, scale=scale, look=look, box_colour=box_colour,
            font_family=font_family, frame_width=width,
        )
        x, y = popup_position(
            popup.position, (popup_w, popup_h), (width, height),
            margin_px=round(look.margin_px * scale), bottom_reserved_px=reserved,
        )
        assets.append(ff.PopupAsset(popup, png, popup_w, popup_h, x, y))
    return assets


# The editor payload ----------------------------------------------------------------------------


def current_render_key(
    folder: Path, timeline: Timeline, state: dict[str, Any], options: RenderOptions,
    *, quality: str | None = None, upload_preset: str | None = None,
) -> str:
    """The render key the next render of ``timeline`` would get (for the "out of date" state;
    the depth token is left out, as a render without the parallax add-on has it empty)."""
    media = files_fingerprint(folder, timeline_media(folder, timeline))
    return render_key_for(
        timeline, popups_enabled=bool(state.get("popups_enabled", True)),
        x264_preset=options.x264, media=media, depth="",
        quality=quality or options.quality.id,
        upload_preset=upload_preset or (options.upload.id if options.upload else "none"),
    )


def _render_rows(
    project_id: str, timeline: Timeline, state: dict[str, Any], config: RenderConfig,
    enable_4k: bool, *, folder: Path | None = None, options: RenderOptions | None = None,
) -> list[RenderOutput]:
    renders = state.get("renders") if isinstance(state.get("renders"), dict) else {}
    rows: list[RenderOutput] = []
    keys: dict[tuple[str, str], str] = {}

    def key_for(raw: dict[str, Any]) -> str | None:
        if folder is None or options is None:
            return None
        quality = str(raw.get("quality") or options.quality.id)
        upload = str(raw.get("upload_preset") or "none")
        found = keys.get((quality, upload))
        if found is None:
            try:
                found = current_render_key(
                    folder, timeline, state, options, quality=quality, upload_preset=upload
                )
            except (OSError, ValueError):
                return None
            keys[(quality, upload)] = found
        return found

    for preset_id in [PROXY_ID, *config.available_presets(enable_4k=True)]:
        raw = renders.get(preset_id) if isinstance(renders.get(preset_id), dict) else {}
        path = raw.get("path") if isinstance(raw.get("path"), str) else None
        status = str(raw.get("status") or "not_rendered")
        if status == "done" and not path:
            status = "not_rendered"
        if preset_id not in timeline.presets and preset_id != PROXY_ID and status == "not_rendered":
            if config.presets[preset_id].requires_enable_4k and not enable_4k:
                continue
        total = raw.get("scene_clips_total")
        reused = raw.get("scene_clips_reused")
        counted = isinstance(total, int) and isinstance(reused, int) and total > 0
        stale = False
        changes: int | None = None
        if status == "done" and isinstance(raw.get("render_key"), str):
            now_key = key_for(raw)
            stale = now_key is not None and now_key != raw["render_key"]
            rendered_at = raw.get("revision")
            if stale and isinstance(rendered_at, int):
                changes = max(1, timeline.revision - rendered_at)
        rows.append(
            RenderOutput(
                preset=preset_id,
                status=status,  # type: ignore[arg-type]
                path=path,
                play_url=file_url(project_id, path) if path and status == "done" else None,
                progress=100.0 if status == "done" else None,
                error=raw.get("error") if isinstance(raw.get("error"), str) else None,
                duration_s=raw.get("duration_s"),
                size_bytes=raw.get("size_bytes"),
                width=raw.get("width"),
                height=raw.get("height"),
                rendered_at=raw.get("rendered_at"),
                scene_clips_total=total if counted else None,
                scene_clips_reused=reused if counted else None,
                reuse_note=reuse_note(reused, total) if counted else None,
                stale=stale,
                changes=changes,
            )
        )
    return rows


def reuse_note(reused: int, total: int) -> str:
    noun = "scene clip" if total == 1 else "scene clips"
    return f"Reused {reused} of {total} {noun}."


def render_notes(state: dict[str, Any], presets: list[str]) -> list[str]:
    """One plain line per finished render about the clips it reused, plus a note when
    scenes set to 2.5D parallax had to use the Ken Burns move."""
    renders = state.get("renders") if isinstance(state.get("renders"), dict) else {}
    notes: list[str] = []
    fallbacks = 0
    for preset_id in [PROXY_ID, *presets]:
        raw = renders.get(preset_id)
        if not isinstance(raw, dict) or raw.get("status") != "done":
            continue
        total, reused = raw.get("scene_clips_total"), raw.get("scene_clips_reused")
        if isinstance(total, int) and isinstance(reused, int) and total > 0:
            name = "Proxy" if preset_id == PROXY_ID else preset_id
            noun = "scene clip" if total == 1 else "scene clips"
            line = f"{name}: reused {reused} of {total} {noun}"
            cuts_total, cuts_reused = raw.get("cuts_total"), raw.get("cuts_reused")
            if isinstance(cuts_total, int) and isinstance(cuts_reused, int) and cuts_total:
                word = "transition" if cuts_total == 1 else "transitions"
                line += f" and {cuts_reused} of {cuts_total} {word}"
            quality = raw.get("quality")
            if isinstance(quality, str) and quality and preset_id != PROXY_ID:
                line += f" ({quality_for(quality).label} quality)"
            notes.append(line + ".")
        if isinstance(raw.get("parallax_fallbacks"), int):
            fallbacks = max(fallbacks, int(raw["parallax_fallbacks"]))
    if fallbacks:
        scenes = "scene" if fallbacks == 1 else "scenes"
        notes.append(
            f"2.5D parallax was not available for {fallbacks} {scenes}, so they use the "
            "Ken Burns move instead."
        )
    return notes


def music_folder_of(folder: Path | str) -> Path:
    """Where the edit's music files are: ``01_media`` inside the edit's folder."""
    return Path(folder) / MEDIA_DIR


def _versioned(project_id: str, relative: str | None, key: str | None) -> str | None:
    if not relative:
        return None
    url = file_url(project_id, relative)
    return f"{url}?v={key}" if key else url


def build_payload(
    project: Project,
    timeline: Timeline,
    state: dict[str, Any],
    *,
    config: RenderConfig,
    settings: Any,
    stem_key: str | None = None,
    running: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The editor payload (``GET /api/edits/{id}/edit``)."""
    folder = Path(project.folder)
    options = options_from(settings)
    enable_4k = options.enable_4k
    renders = _render_rows(
        project.id, timeline, state, config, enable_4k, folder=folder, options=options
    )
    proxy = next((r for r in renders if r.preset == PROXY_ID and r.status == "done"), None)
    popup_by_scene: dict[int, str] = {}
    for popup in timeline.popups:
        popup_by_scene.setdefault(popup.scene, popup.text)
    scenes = [
        EditSceneView(
            index=scene.index,
            start_s=scene.start_s,
            end_s=scene.end_s,
            duration_s=round(scene.end_s - scene.start_s, 3),
            image_path=scene.image,
            image_url=file_url(project.id, scene.image) if scene.image else None,
            transition=scene.transition_out.type,
            transition_s=scene.transition_out.duration_s,
            motion=scene.motion.preset,
            popup_text=popup_by_scene.get(scene.index),
            locked=scene.locked,
        )
        for scene in timeline.scenes
    ]
    timing = read_timing(folder / VOICE_DIR / TIMING_FILE)
    warnings = [str(w) for w in (state.get("warnings") or [])]
    gate_results = [g for g in (state.get("gate_results") or []) if isinstance(g, dict)]
    checks = editor_checks(timeline, folder)
    music_folder = music_folder_of(folder)
    running = running or {}
    last_error = state.get("last_error")
    payload = EditReviewPayload(
        proxy_path=proxy.path if proxy else None,
        proxy_url=proxy.play_url if proxy else None,
        timeline=timeline,
        summary=TimelineSummary(
            scene_count=len(timeline.scenes),
            duration_s=timeline.duration_s,
            transitions=timeline.transition_types,
            popup_count=len(timeline.popups),
            caption_cues=len(timeline.captions.cues),
            fps=timeline.fps,
            width=timeline.width,
            height=timeline.height,
            aspect=timeline.aspect,
        ),
        scenes=scenes,
        renders=renders,
        presets=list(timeline.presets),
        available_presets=config.available_presets(enable_4k=True),
        enable_4k=enable_4k,
        music_tracks=track_infos(music_folder if music_folder.is_dir() else None),
        music_path=timeline.music.path,
        music_license_ok=timeline.music.license_ok,
        captions_enabled=timeline.captions.enabled,
        popups_enabled=bool(state.get("popups_enabled", True)),
        warnings=warnings,
        gate_results=gate_results,
        render_log_path=f"{STAGE_DIR_NAME}/{RENDER_LOG}",
        timing_source=timing.source if timing is not None else None,
        render_notes=render_notes(state, list(timeline.presets)),
        stem_url=_versioned(project.id, timeline.music.stem, stem_key),
        envelope_url=_versioned(project.id, timeline.music.envelope, stem_key),
        preview_gain_db=timeline.render.preview_gain_db,
        quality=str(state.get("quality") or options.quality.id),
        upload_preset=str(state.get("upload_preset") or "none"),
        h265_copy=bool(state.get("h265_copy", options.h265_copy)),
        music_licence_note=project.music_licence_note,
        music_folder=str(music_folder),
        checks=checks,
        rendering=bool(running.get("running")),
        render_message=str(running.get("message") or ""),
        render_pct=running.get("pct"),
        last_render_error=last_error if isinstance(last_error, str) else None,
    )
    return payload.model_dump(mode="json")


def load_review_payload(
    project: Project, settings: Any, *, running: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """The editor payload rebuilt from the files, or ``None`` when there is no timeline."""
    folder = Path(project.folder)
    stage_dir = folder / STAGE_DIR_NAME
    timeline = read_timeline(stage_dir)
    if timeline is None:
        return None
    state = read_state(stage_dir)
    config = load_render_config()
    return build_payload(
        project, timeline, state, config=config, settings=settings,
        stem_key=stem_is_current(timeline, folder, config) if timeline.music.stem else None,
        running=running,
    )


# A range of the video ----------------------------------------------------------------------------


def _clip_segments(segments: list[VoiceSegment], start: float, end: float) -> list[VoiceSegment]:
    out: list[VoiceSegment] = []
    for segment in segments:
        seg_start, seg_end = segment.at_s, segment.end_s
        a, b = max(seg_start, start), min(seg_end, end)
        if b - a <= 0.01:
            continue
        src_start = segment.src_start_s + (a - seg_start)
        out.append(
            VoiceSegment(
                sentence_id=segment.sentence_id, src_start_s=round(src_start, 6),
                src_end_s=round(src_start + (b - a), 6), at_s=round(a - start, 6),
            )
        )
    return out


def _range_clips(clips: list[Any], start: float, end: float) -> list[dict[str, Any]]:
    """The layer or sound clips of a lane inside ``start`` .. ``end``, moved so the range
    starts at 0: a clip outside is dropped, one crossing an edge is cut there (a clip cut at
    its start plays its file from ``in_s`` plus the cut seconds at its speed); ids stay."""
    rows: list[dict[str, Any]] = []
    for clip in clips:
        first, last = max(float(clip.start_s), start), min(float(clip.end_s), end)
        if last - first < MIN_CLIP_S:
            continue
        row = clip.model_dump(mode="json")
        cut = first - float(clip.start_s)
        if cut > 0:
            row["in_s"] = round(float(clip.in_s) + cut * float(clip.speed), 6)
        row["start_s"] = round(first - start, 3)
        row["end_s"] = round(last - start, 3)
        rows.append(row)
    return rows


def timeline_range(timeline: Timeline, start_s: float, end_s: float) -> Timeline:
    """The part of ``timeline`` between ``start_s`` and ``end_s`` as a timeline of its own
    (starting at 0), for a range render. The layer and sound clips inside the range come
    along (cut at its edges); a layer or sound track with none left stays, empty."""
    start = max(0.0, float(start_s))
    end = min(float(timeline.duration_s), float(end_s))
    if end - start < MIN_RANGE_S:
        raise RenderFailed("Choose a range of at least half a second to render.")
    data = timeline.model_dump(mode="json")
    scenes: list[dict[str, Any]] = []
    new_index: dict[int, int] = {}
    for scene in timeline.scenes:
        if scene.end_s <= start or scene.start_s >= end:
            continue
        row = scene.model_dump(mode="json")
        row["index"] = len(scenes)
        row["start_s"] = round(max(scene.start_s, start) - start, 3)
        row["end_s"] = round(min(scene.end_s, end) - start, 3)
        row["sentence_ids"] = []
        if scene.clip is not None and scene.start_s < start:  # a clip scene cut at its start
            cut = start - scene.start_s
            row["clip"]["in_s"] = round(scene.clip.in_s + cut * scene.clip.speed, 6)
        new_index[scene.index] = len(scenes)
        scenes.append(row)
    if not scenes:
        raise RenderFailed("No scene lies inside that range.")
    popups: list[dict[str, Any]] = []
    for popup in timeline.popups:
        if popup.scene not in new_index or popup.end_s <= start or popup.start_s >= end:
            continue
        row = popup.model_dump(mode="json")
        row["scene"] = new_index[popup.scene]
        row["start_s"] = round(max(popup.start_s, start) - start, 3)
        row["end_s"] = round(min(popup.end_s, end) - start, 3)
        row["anchor"] = None
        popups.append(row)
    cues: list[dict[str, Any]] = []
    for cue in timeline.captions.cues:
        if cue.end_s <= start or cue.start_s >= end:
            continue
        row = cue.model_dump(mode="json")
        row["start_s"] = round(max(cue.start_s, start) - start, 3)
        row["end_s"] = round(min(cue.end_s, end) - start, 3)
        cues.append(row)
    if timeline.voice.segments:
        segments = _clip_segments(timeline.voice.segments, start, end)
    else:
        segments = [VoiceSegment(sentence_id="range", src_start_s=round(start, 6),
                                 src_end_s=round(end, 6), at_s=0.0)]
    data["scenes"] = scenes
    data["popups"] = popups
    data["captions"]["cues"] = cues
    data["voice"] = VoiceTrack(
        path=timeline.voice.path, start_s=0.0, segments=segments or None
    ).model_dump(mode="json")
    data["music"]["start_s"] = round(timeline.music.start_s + start, 3)
    data["music"]["stem"] = None
    data["music"]["envelope"] = None
    data["markers"] = [
        {**m.model_dump(mode="json"), "t": round(m.t - start, 3)}
        for m in timeline.markers if start <= m.t <= end
    ]
    data["duration_s"] = round(scenes[-1]["end_s"], 3)
    data["intro"] = {"path": None}
    data["outro"] = {"path": None}
    data["layers"] = [
        {**layer.model_dump(mode="json"), "clips": _range_clips(layer.clips, start, end)}
        for layer in timeline.layers
    ]
    data["audio_tracks"] = [
        {**track.model_dump(mode="json"), "clips": _range_clips(track.clips, start, end)}
        for track in timeline.audio_tracks
    ]
    return Timeline.model_validate(data)


# The render ----------------------------------------------------------------------------------


@dataclass
class RenderOutcome:
    rendered: list[str] = field(default_factory=list)
    """Preset ids rendered in this run (the proxy as ``proxy``)."""
    kept: list[str] = field(default_factory=list)
    """Preset ids whose finished file was up to date and kept."""
    outputs: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    gate_results: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    duration_s: float = 0.0


RENDER_LOCK = threading.Lock()
"""One render at a time on this laptop (docs/PLAN.md section 4)."""


def _apply_quality(plan: ff.RenderPlan, preset: RenderPreset, options: RenderOptions) -> None:
    """The quality's CRF and x264 speed, and the upload preset's ceilings, for a final file
    (the proxy keeps its own quick settings)."""
    if preset.id == PROXY_ID:
        return
    plan.crf = int(options.quality.crf)
    plan.x264_preset = options.x264
    upload = options.upload
    if upload is not None and (upload.preset_id is None or upload.preset_id == preset.id):
        plan.maxrate_kbps = upload.maxrate_kbps
        plan.audio_bitrate_kbps = upload.audio_bitrate_kbps


def _h265_copy(source: Path, target: Path, *, ffmpeg: str, cancel: threading.Event | None,
               log_path: Path) -> None:
    """An H.265 (HEVC) copy of a finished file: smaller, slower, for archives."""
    part = target.with_name(target.name + ".part.mp4")
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source),
        "-c:v", "libx265", "-preset", "medium", "-crf", "24", "-tag:v", "hvc1",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(part),
    ]
    with log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"\n===== H.265 copy of {source.name} =====\n")
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=handle,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        while True:
            try:
                code = process.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    process.kill()
                    process.wait()
                    part.unlink(missing_ok=True)
                    raise ff.RenderCancelled("The H.265 copy was stopped.") from None
    if code != 0:
        part.unlink(missing_ok=True)
        raise ff.RenderError(
            f"The H.265 copy of {source.name} failed (FFmpeg exit code {code}). See render.log."
        )
    part.replace(target)


def render_edit(
    project: Project,
    settings: Any,
    *,
    presets: list[str] | None = None,
    quality: str | None = None,
    upload_preset: str | None = None,
    h265_copy: bool | None = None,
    range_s: tuple[float, float] | None = None,
    cancel: threading.Event | None = None,
    report: ProgressFn | None = None,
) -> RenderOutcome:
    """Render the edit's timeline: the proxy and every requested size (or, with ``range_s``,
    only that part of the video at the requested sizes, into ``07_edit/range/``).
    ``presets`` ``None`` means the edit's ticked sizes; ``[]`` means the preview only.

    Raises :class:`RenderFailed` (plain English) or ``ff.RenderCancelled``."""
    cancel = cancel or threading.Event()
    report = report or (lambda message, pct=None: None)
    started = time.monotonic()
    folder = Path(project.folder)
    stage_dir = folder / STAGE_DIR_NAME
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = load_render_config()
    options = options_from(
        settings, presets=presets, quality=quality, upload_preset=upload_preset,
        h265_copy=h265_copy,
    )
    outcome = RenderOutcome()
    warnings = outcome.warnings
    notes = outcome.notes

    timeline = read_timeline(stage_dir)
    if timeline is None:
        raise RenderFailed("This edit has no timeline yet. Add pictures or clips first.")
    if not timeline.scenes:
        raise RenderFailed("The timeline has no scenes.")
    wanted = list(presets) if presets is not None else list(timeline.presets)
    chosen = allowed_presets(wanted, config, options.enable_4k, warnings)
    if range_s is None:
        timeline.presets = chosen
    language = project.language
    rules = rules_for(language)
    if timeline.music.path:
        track = music_file(timeline, folder)
        timeline.music.license_ok = bool(
            (track is not None and has_licence(track)) or project.music_licence_note.strip()
        )

    state = read_state(stage_dir)
    popups_enabled = bool(state.get("popups_enabled", True))
    state["quality"] = options.quality.id
    state["upload_preset"] = options.upload.id if options.upload else "none"
    state["h265_copy"] = options.h265_copy
    state.pop("last_error", None)

    report("Preparing the music and the sound level of the preview", 2)
    ffmpeg_path = ff.find_ffmpeg(settings)
    ffprobe_path = ff.find_ffprobe(settings)
    try:
        sound = prepare_preview_audio(folder, timeline, config, ffmpeg=ffmpeg_path, cancel=cancel)
    except ff.RenderCancelled:
        raise
    except ff.RenderError as exc:
        raise RenderFailed(str(exc)) from exc
    if sound.stem_built:
        notes.append(f"Music stem made in {sound.stem_seconds:.1f} s.")
    if range_s is None:
        write_timeline(stage_dir, timeline)
    work = timeline if range_s is None else timeline_range(timeline, *range_s)
    work_dir = stage_dir if range_s is None else stage_dir / RANGE_DIR
    work_dir.mkdir(parents=True, exist_ok=True)

    report("Writing the captions and popups", 5)
    ass_path = work_dir / ASS_FILE
    timing = read_timing(folder / VOICE_DIR / TIMING_FILE)
    if work.captions.cues:
        write_ass(
            ass_path, work.captions.cues, work.captions.style, work.aspect,
            margin_v_px=config.captions_margin_v_px, shadow=config.captions_shadow,
            sentences=timing.sentences if timing is not None and range_s is None else None,
            space_dependent=rules.space_dependent,
        )
    elif ass_path.exists():
        ass_path.unlink()
    box_colour = brand_colour([])
    font_family = rules.popup_font
    reference = RenderPreset("ref", (1920, 1080), (1080, 1920))
    popup_assets(
        work, reference, stage_dir=work_dir, config=config, box_colour=box_colour,
        font_family=font_family, captions_on=work.captions.enabled, enabled=True,
    )
    if cancel.is_set():
        raise ff.RenderCancelled("The render was stopped before it started.")
    if any(_wants_parallax(scene) for scene in work.scenes):
        report("Preparing depth maps for 2.5D parallax", 8)
    depth_note, depth_token = prepare_depth_maps(folder, work, settings, cancel)
    if depth_note and depth_note not in warnings:
        warnings.append(depth_note)

    targets = ([config.proxy] if range_s is None else []) + [config.presets[p] for p in chosen]
    media = files_fingerprint(folder, timeline_media(folder, work))
    render_key = render_key_for(
        work, popups_enabled=popups_enabled, x264_preset=options.x264, media=media,
        depth=depth_token, quality=options.quality.id,
        upload_preset=options.upload.id if options.upload else "none",
    )
    renders_state: dict[str, Any] = (
        state.get("renders") if isinstance(state.get("renders"), dict) else {}
    )
    if range_s is None:
        state["renders"] = renders_state
    else:
        renders_state = {}
    to_render: list[RenderPreset] = []
    for preset in targets:
        output = work_dir / final_file_name(preset.id)
        known = renders_state.get(preset.id)
        entry = known if isinstance(known, dict) else {}
        fresh = (
            output.is_file() and entry.get("status") == "done"
            and entry.get("render_key") == render_key
        )
        if fresh:
            notes.append(f"{output.name} is up to date and was kept.")
            outcome.kept.append(preset.id)
            outcome.outputs.append(output)
        else:
            to_render.append(preset)

    if range_s is None:
        prepare_in_background(folder, [s.image for s in work.scenes if s.image],
                              (int(work.width), int(work.height)))

    log_path = work_dir / RENDER_LOG
    peaks: list[float] = []
    with RENDER_LOCK:
        for preset in to_render:
            if cancel.is_set():
                raise ff.RenderCancelled("The render was stopped.")
            output = work_dir / final_file_name(preset.id)
            if range_s is None:
                renders_state[preset.id] = {
                    "preset": preset.id, "status": "rendering", "path": None, "error": None,
                }
                write_state(stage_dir, state)
            try:
                assets = popup_assets(
                    work, preset, stage_dir=work_dir, config=config, box_colour=box_colour,
                    font_family=font_family, captions_on=work.captions.enabled,
                    enabled=popups_enabled,
                )
                plan = ff.build_plan(
                    work, preset, folder=folder, config=config, popups=assets,
                    ass_path=ass_path if ass_path.is_file() else None,
                    captions=work.captions.enabled, x264_preset=options.x264,
                )
                _apply_quality(plan, preset, options)
                label = "the preview" if preset.id == PROXY_ID else preset.id
                report(f"Rendering {label} 0%", 0.0)
                result = ff.run_render(
                    plan, output, work_dir=work_dir, log_path=log_path, progress=report,
                    cancel=cancel, ffmpeg=ffmpeg_path, ffprobe=ffprobe_path,
                )
            except ff.RenderCancelled:
                if range_s is None:
                    renders_state[preset.id] = {
                        "preset": preset.id, "status": "failed", "path": None,
                        "error": "The render was stopped.",
                    }
                    write_state(stage_dir, state)
                raise
            except ff.RenderError as exc:
                if range_s is None:
                    renders_state[preset.id] = {
                        "preset": preset.id, "status": "failed", "path": None, "error": str(exc),
                    }
                    state["last_error"] = str(exc)
                    write_state(stage_dir, state)
                raise RenderFailed(str(exc)) from exc
            relative = output.relative_to(folder).as_posix()
            entry: dict[str, Any] = {
                "preset": preset.id,
                "status": "done",
                "path": relative,
                "error": None,
                "duration_s": result.duration_s,
                "expected_s": plan.total_s,
                "size_bytes": result.size_bytes,
                "width": result.width,
                "height": result.height,
                "seconds_taken": result.seconds_taken,
                "popups_enabled": popups_enabled,
                "render_key": render_key,
                "revision": timeline.revision,
                "rendered_at": utc_stamp(),
                "quality": options.quality.id if preset.id != PROXY_ID else None,
                "upload_preset": state["upload_preset"] if preset.id != PROXY_ID else None,
                "scene_clips_total": result.scene_clips_total,
                "scene_clips_reused": result.scene_clips_reused,
                "cuts_total": result.cuts_total,
                "cuts_reused": result.cuts_reused,
                "phase_seconds": dict(result.phase_seconds),
                "parallax_fallbacks": result.parallax_fallbacks,
            }
            if options.h265_copy and preset.id != PROXY_ID:
                report(f"Making the H.265 copy of {preset.id}", None)
                h265 = work_dir / h265_file_name(preset.id)
                try:
                    _h265_copy(output, h265, ffmpeg=ffmpeg_path, cancel=cancel, log_path=log_path)
                    entry["h265_path"] = h265.relative_to(folder).as_posix()
                    outcome.outputs.append(h265)
                except ff.RenderCancelled:
                    raise
                except ff.RenderError as exc:
                    warnings.append(str(exc))
            if range_s is None:
                renders_state[preset.id] = entry
                write_state(stage_dir, state)
            outcome.rendered.append(preset.id)
            outcome.outputs.append(output)
            peak = ff.measure_true_peak(output, ffmpeg_path, cancel=cancel)
            if peak is not None:
                peaks.append(peak)

    if range_s is None:
        if peaks:
            state["true_peak_dbtp"] = max(peaks)
        elif outcome.rendered:
            state["true_peak_dbtp"] = None
        rendered = [
            {"preset": pid, "expected_s": entry.get("expected_s"),
             "actual_s": entry.get("duration_s")}
            for pid, entry in renders_state.items()
            if isinstance(entry, dict) and entry.get("status") == "done"
            and (pid == PROXY_ID or pid in timeline.presets)
        ]
        gate_context = {
            "music_path": timeline.music.path,
            "music_license_ok": timeline.music.license_ok,
            "renders": rendered,
            "true_peak_dbtp": state.get("true_peak_dbtp"),
        }
        outcome.gate_results = gates.to_dicts(gates.evaluate("edit", gate_context))
        outcome.checks = editor_checks(timeline, folder)
        state["gate_results"] = outcome.gate_results
        state["warnings"] = warnings
        state["last_render"] = {
            "finished_at": utc_stamp(), "presets": chosen, "quality": options.quality.id,
            "upload_preset": state["upload_preset"], "rendered": outcome.rendered,
        }
        write_state(stage_dir, state)
    outcome.seconds = time.monotonic() - started
    outcome.duration_s = timeline.duration_s
    report("Render finished", 100)
    return outcome
