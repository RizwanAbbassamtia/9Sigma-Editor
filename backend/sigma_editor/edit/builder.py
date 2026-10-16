"""New edit from files (docs/CONTRACT-v0.1.md section 3): the person's pictures, clips,
narration, music and caption text become an edit folder, the narration timing, the scene
plan (``07_edit/scenes.json``) and the first timeline (``07_edit/timeline.json``).

Also the later changes to the scenes: add a scene from a file, replace a scene's picture,
reorder scenes (the sentences move with them), remove a scene, and the rebuild of the
timeline after the timing tool moved a sentence. Every rebuild goes through
``render/timeline_builder.py`` (the engine's own builder), then the voice track is laid out
from the timing document (:func:`~.timing_doc.voice_segments_of`).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..audio.errors import AudioError
from ..audio.ffmpeg import convert_to_wav, probe_duration
from ..audio.wav import read_wav_info, write_silence
from ..config_files import transition_catalog
from ..models.edit_input import (
    EditInput,
    EditItem,
    SceneFileInput,
    file_kind,
    natural_key,
)
from ..models.project import Project, aspect_of
from ..models.storyboard import (
    MOTION_PRESETS,
    Scene,
    SceneImage,
    SceneLocks,
    SceneMotion,
    ScenePopup,
    SceneTransition,
    StoryboardDoc,
)
from ..models.timeline import Timeline, VoiceTrack
from ..render import ffmpeg as ff
from ..render.presets import load_render_config
from ..render.timeline_builder import (
    BuildRequest,
    TimelineBuildError,
    _image_size,
    build_timeline,
    parse_timing,
    resolve_scene_images,
)
from ..storage.files import atomic_write_text, safe_file_name, unique_path
from ..storage.project_store import (
    EDIT_DIR,
    IMAGES_DIR,
    MEDIA_DIR,
    VOICE_DIR,
    ProjectStore,
)
from .caption_files import CaptionFileError, read_caption_file
from .renderer import allowed_presets, options_from, read_state, read_timeline, write_timeline
from .timing_doc import (
    TIMING_FILE,
    TimingDoc,
    TimingError,
    estimated_timing,
    read_timing,
    reordered,
    scene_timing,
    timing_from_cues,
    voice_segments_of,
    write_timing,
)

log = logging.getLogger(__name__)

SCENES_FILE = "scenes.json"
VOICE_FILE = "voice.wav"
PICTURE_SECONDS = 6.0
"""How long a picture stays when there is no narration to follow."""
MAX_CLIP_SECONDS = 600.0
POSTER_SUFFIX = ".jpg"
SAMPLE_RATE = 48000


class BuildError(Exception):
    """Plain English, safe to show."""


@dataclass
class IncomingFile:
    """A file the person added: its original name and where it sits now."""

    name: str
    path: Path


@dataclass
class NewEditFiles:
    items: list[IncomingFile] = field(default_factory=list)
    narration: IncomingFile | None = None
    music: IncomingFile | None = None
    captions: IncomingFile | None = None


@dataclass
class SceneSource:
    """One scene's files after the import."""

    index: int
    image_rel: str
    clip_rel: str | None = None
    clip_duration_s: float | None = None
    caption: str = ""
    keep_sound: bool = False


# Files -----------------------------------------------------------------------------------------


def scenes_path(folder: Path | str) -> Path:
    return Path(folder) / EDIT_DIR / SCENES_FILE


def read_scene_plan(folder: Path | str) -> StoryboardDoc | None:
    try:
        return StoryboardDoc.model_validate_json(scenes_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def write_scene_plan(folder: Path | str, plan: StoryboardDoc) -> None:
    plan.revision += 1
    plan.saved_at = datetime.now(UTC)
    atomic_write_text(scenes_path(folder), plan.model_dump_json(indent=2) + "\n")


def _copy(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    shutil.copyfile(source, part)
    part.replace(target)
    return target


def _ffmpeg(settings: Any) -> str:
    try:
        return ff.find_ffmpeg(settings)
    except ff.RenderError as exc:
        raise BuildError(str(exc)) from exc


def poster_frame(clip: Path, target: Path, *, settings: Any, duration_s: float | None) -> Path:
    """The clip's first clear frame as a JPEG (the picture the scene shows in the preview)."""
    at = 0.5 if duration_s is None or duration_s > 1.5 else 0.0
    part = target.with_name(target.name + ".part.jpg")
    command = [
        _ffmpeg(settings), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{at:.2f}", "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(part),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    if completed.returncode != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        detail = (completed.stderr or "").strip().splitlines()
        raise BuildError(
            f"The clip {clip.name} could not be read"
            + (f": {detail[-1]}" if detail else ".")
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    part.replace(target)
    return target


def clip_length(path: Path) -> float | None:
    try:
        return probe_duration(path)
    except AudioError:
        return None


def order_items(files: list[IncomingFile], form: EditInput) -> list[IncomingFile]:
    if form.order == "by_name":
        return sorted(files, key=lambda item: natural_key(item.name))
    if form.items:  # the drag order: the form's items name the files in order
        wanted = [item.file for item in form.items]
        by_name = {item.name: item for item in files}
        ordered = [by_name[name] for name in wanted if name in by_name]
        rest = [item for item in files if item.name not in set(wanted)]
        return ordered + rest
    return list(files)


def import_item(
    folder: Path, index: int, item: IncomingFile, form_item: EditItem | None,
    *, settings: Any, keep_sound_default: bool,
) -> SceneSource:
    """Copy one picture or clip into the edit's folder as scene ``index`` (0-based)."""
    kind = file_kind(item.name)
    suffix = Path(item.name).suffix.lower()
    number = index + 1
    if kind == "picture":
        target = folder / IMAGES_DIR / f"scene_{number:02d}{suffix}"
        _copy(item.path, target)
        return SceneSource(
            index=index, image_rel=target.relative_to(folder).as_posix(),
            caption=form_item.caption if form_item else "",
        )
    if kind == "clip":
        original = unique_path(folder / MEDIA_DIR, safe_file_name(item.name, fallback="clip"))
        _copy(item.path, original)
        duration = clip_length(original)
        poster = folder / IMAGES_DIR / f"scene_{number:02d}{POSTER_SUFFIX}"
        poster_frame(original, poster, settings=settings, duration_s=duration)
        return SceneSource(
            index=index,
            image_rel=poster.relative_to(folder).as_posix(),
            clip_rel=original.relative_to(folder).as_posix(),
            clip_duration_s=min(duration, MAX_CLIP_SECONDS) if duration else None,
            caption=form_item.caption if form_item else "",
            keep_sound=form_item.keep_sound if form_item else keep_sound_default,
        )
    raise BuildError(
        f"{item.name} is not a picture or a clip. Use png, jpg or webp pictures and mp4 or "
        "mov clips."
    )


def import_narration(folder: Path, narration: IncomingFile | None, *, settings: Any,
                     cancel: threading.Event | None = None) -> float | None:
    """The narration as ``05_voice/voice.wav`` (48 kHz mono); returns its length, or
    ``None`` when there is no narration."""
    if narration is None:
        return None
    if file_kind(narration.name) not in ("audio", "clip"):
        raise BuildError(f"{narration.name} is not an audio file (use mp3, wav or m4a).")
    # The original stays next to the converted voice.wav (not in 01_media, where it would be
    # offered as background music).
    original = unique_path(
        folder / VOICE_DIR, safe_file_name(f"original-{narration.name}", fallback="original")
    )
    _copy(narration.path, original)
    target = folder / VOICE_DIR / VOICE_FILE
    try:
        convert_to_wav(original, target, sample_rate=SAMPLE_RATE, channels=1, cancel=cancel)
        info = read_wav_info(target)
    except AudioError as exc:
        raise BuildError(f"The narration could not be read: {exc}") from exc
    seconds = info.frames / info.sample_rate if info.sample_rate else 0.0
    if seconds <= 0.1:
        raise BuildError(f"The narration {narration.name} is empty.")
    return round(seconds, 3)


def write_silent_voice(folder: Path, seconds: float) -> Path:
    target = folder / VOICE_DIR / VOICE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    write_silence(target, max(int(round(seconds * SAMPLE_RATE)), SAMPLE_RATE), SAMPLE_RATE)
    return target


def import_music(folder: Path, music: IncomingFile | None) -> Path | None:
    if music is None:
        return None
    if file_kind(music.name) != "audio":
        raise BuildError(f"{music.name} is not an audio file (use mp3, wav or m4a for music).")
    target = unique_path(folder / MEDIA_DIR, safe_file_name(music.name, fallback="music"))
    return _copy(music.path, target)


# Timing ----------------------------------------------------------------------------------------


def make_timing(
    form: EditInput,
    captions: IncomingFile | None,
    narration_s: float | None,
    sources: list[SceneSource],
    *,
    language: str,
) -> TimingDoc:
    """The sentence clock of the edit: the caption file's times, the typed text's estimate
    over the narration (or at a steady pace), or one sentence per scene."""
    lengths = [
        s.clip_duration_s if s.clip_rel and s.clip_duration_s else PICTURE_SECONDS
        for s in sources
    ]
    fallback_total = sum(lengths)
    try:
        if captions is not None:
            if file_kind(captions.name) != "captions":
                raise BuildError(f"{captions.name} is not an SRT or VTT caption file.")
            cues = read_caption_file(captions.path)
            total = narration_s or max(fallback_total, max(c.end_s for c in cues))
            return timing_from_cues(cues, total, language=language)
        if form.caption_text.strip():
            return estimated_timing(
                form.caption_text, narration_s if narration_s else None, language=language
            )
    except CaptionFileError as exc:
        raise BuildError(str(exc)) from exc
    except TimingError as exc:
        raise BuildError(str(exc)) from exc
    if narration_s:
        share = narration_s / max(len(sources), 1)
        return scene_timing([share] * len(sources), language=language)
    return scene_timing(lengths, language=language)


def assign_sentences(count: int, sentence_ids: list[str]) -> list[list[str]]:
    """Which sentences each of ``count`` scenes covers: consecutive runs; with fewer
    sentences than scenes, neighbouring scenes share one (the builder splits its time)."""
    if count <= 0:
        return []
    if not sentence_ids:
        return [[] for _ in range(count)]
    total = len(sentence_ids)
    groups: list[list[str]] = []
    for scene in range(count):
        first = (scene * total) // count
        last = ((scene + 1) * total) // count
        if last <= first:
            last = first + 1
        groups.append(sentence_ids[first:last] or [sentence_ids[min(first, total - 1)]])
    return groups


def _transition_cycle(pack: str, fmt: str, count: int) -> list[SceneTransition]:
    catalogue = transition_catalog()
    choices = [t.type for t in catalogue.for_pack(pack)] or catalogue.types or ["film_dissolve"]
    out: list[SceneTransition] = []
    previous: str | None = None
    position = 0
    for number in range(count):
        if number == count - 1:
            chosen = catalogue.closer_for(pack)
        else:
            chosen = choices[position % len(choices)]
            position += 1
            if chosen == previous and len(choices) > 1:
                chosen = choices[position % len(choices)]
                position += 1
        previous = chosen
        length = catalogue.duration_for(chosen, fmt, pack)
        out.append(SceneTransition(type=chosen, duration_s=length))
    return out


def make_scene_plan(
    project: Project, sources: list[SceneSource], timing: TimingDoc, settings: Any,
    *, previous: StoryboardDoc | None = None,
) -> StoryboardDoc:
    """The scene plan for ``sources`` over ``timing``: camera moves alternate, transitions
    come from the pack with the no-repeat rule, popups from the caption lines. ``previous``
    keeps the person's own choices (motion, transition, popup) for a scene that is still
    there (matched by its picture)."""
    render = getattr(settings, "render", None)
    pack = str(getattr(render, "transition_pack", "cinematic") or "cinematic")
    motion_style = getattr(render, "motion_style", None)
    ids = [s.id for s in timing.sentences]
    groups = assign_sentences(len(sources), ids)
    transitions = _transition_cycle(pack, project.format, len(sources))
    kept = {s.image.path: s for s in (previous.scenes if previous else []) if s.image.path}
    scenes: list[Scene] = []
    by_id = {s.id: s for s in timing.sentences}
    for position, source in enumerate(sources):
        covered = [by_id[i] for i in groups[position] if i in by_id]
        start = covered[0].start_s if covered else 0.0
        end = covered[-1].end_s if covered else start + PICTURE_SECONDS
        old = kept.get(source.image_rel)
        preset = "hold" if source.clip_rel else MOTION_PRESETS[position % len(MOTION_PRESETS)]
        motion = SceneMotion(preset=preset)
        transition = transitions[position]
        popup = ScenePopup(
            text=source.caption or None,
            key_sentence_id=groups[position][0] if groups[position] else None,
        )
        if old is not None:
            if old.is_edited("motion"):
                motion = old.motion
            if old.is_edited("transition_out") and position < len(sources) - 1:
                transition = old.transition_out
            if old.is_edited("popup"):
                popup = old.popup
        scenes.append(
            Scene(
                index=position,
                sentence_ids=groups[position] or [f"s-{position + 1:03d}"],
                narration=" ".join(s.text for s in covered).strip(),
                est_start_s=round(start, 3),
                est_end_s=round(end, 3),
                est_duration_s=round(end - start, 3),
                popup=popup,
                motion=motion,
                transition_out=transition,
                image=SceneImage(path=source.image_rel, status="approved"),
                clip=source.clip_rel,
                clip_duration_s=source.clip_duration_s,
                keep_sound=source.keep_sound,
                edited=list(old.edited) if old is not None else [],
            )
        )
    return StoryboardDoc(
        project_id=project.id,
        format=project.format,
        aspect=aspect_of(project.format),  # type: ignore[arg-type]
        transition_pack=pack,  # type: ignore[arg-type]
        motion_style=motion_style,
        generated_at=datetime.now(UTC),
        scenes=scenes,
        revision=previous.revision if previous else 0,
    )


# The timeline ----------------------------------------------------------------------------------


def _split_shared_scenes(timeline: Timeline) -> None:
    """Scenes that share the same sentences (more pictures than sentences) split the
    sentences' time evenly instead of the engine's tiny tail scenes."""
    scenes = timeline.scenes
    position = 0
    while position < len(scenes):
        run_end = position
        while (
            run_end + 1 < len(scenes)
            and scenes[run_end + 1].sentence_ids == scenes[position].sentence_ids
            and scenes[position].sentence_ids
        ):
            run_end += 1
        if run_end > position:
            start = scenes[position].start_s
            end = scenes[run_end].end_s
            count = run_end - position + 1
            share = (end - start) / count
            for offset in range(count):
                scene = scenes[position + offset]
                scene.start_s = round(start + share * offset, 3)
                scene.end_s = round(start + share * (offset + 1), 3) if offset < count - 1 else end
        position = run_end + 1
    for index, scene in enumerate(scenes):
        this = scene.end_s - scene.start_s
        if index + 1 < len(scenes):
            following = scenes[index + 1].end_s - scenes[index + 1].start_s
        else:
            following = this
        limit = max(0.0, 0.25 * min(this, following))
        if scene.transition_out.duration_s > limit:
            scene.transition_out.duration_s = round(limit, 3)
    if scenes:
        timeline.duration_s = scenes[-1].end_s


def build_edit_timeline(
    project: Project, settings: Any, plan: StoryboardDoc, timing: TimingDoc,
    *, music_choice: str | None, keep: Timeline | None = None,
) -> tuple[Timeline, list[str]]:
    """The timeline of the edit from its scene plan and timing (the engine's builder), with
    the voice laid out from the timing document and the person's timeline-level choices
    (music, captions switch, sizes) carried over from ``keep``."""
    folder = Path(project.folder)
    config = load_render_config()
    options = options_from(settings)
    warnings: list[str] = []
    presets = allowed_presets(
        (keep.presets if keep is not None and keep.presets else options.presets),
        config, options.enable_4k, warnings,
    )
    first = config.presets.get(presets[0] if presets else "1080p") or config.presets["1080p"]
    aspect = plan.aspect
    width, height = first.size(aspect)
    try:
        sources = resolve_scene_images(folder, plan)
    except TimelineBuildError as exc:
        raise BuildError(str(exc)) from exc
    engine_timing = parse_timing(timing.model_dump(mode="json"))
    if keep is not None and keep.music.path:
        music_choice = keep.music.path
    request = BuildRequest(
        project_id=project.id,
        fmt=project.format,
        aspect=aspect,
        language=project.language,
        folder=folder,
        storyboard=plan,
        timing=engine_timing,
        sources=sources,
        width=width,
        height=height,
        proxy=config.proxy.size(aspect),
        presets=presets,
        fps=config.fps,
        voice_path=f"{VOICE_DIR}/{VOICE_FILE}",
        music_folder=str(folder / MEDIA_DIR),
        music_choice=music_choice if music_choice is not None else "",
        captions_enabled=options.captions_enabled,
        caption_style_name=options.caption_style,
        caption_preset=options.caption_preset,
        popups_enabled=True,
        config=config,
        motion_style=getattr(getattr(settings, "render", None), "motion_style", None),
    )
    try:
        timeline, build_warnings = build_timeline(request)
    except TimelineBuildError as exc:
        raise BuildError(str(exc)) from exc
    warnings.extend(build_warnings)
    _split_shared_scenes(timeline)
    segments = voice_segments_of(timing)
    timeline.voice = VoiceTrack(path=f"{VOICE_DIR}/{VOICE_FILE}", start_s=0.0,
                                segments=segments or None)
    if keep is not None:
        if keep.music.path and timeline.music.path:
            timeline.music.gain_db = keep.music.gain_db
            timeline.music.duck_db = keep.music.duck_db
            timeline.music.start_s = keep.music.start_s
            timeline.music.license_ok = keep.music.license_ok
        timeline.captions.enabled = keep.captions.enabled
        timeline.markers = list(keep.markers)
        timeline.revision = keep.revision + 1
    if timeline.music.path and project.music_licence_note.strip():
        timeline.music.license_ok = True
        warnings[:] = [w for w in warnings if "licence file" not in w]
    return timeline, warnings


def rebuild_timeline(
    project: Project, settings: Any, *, music_choice: str | None = None
) -> tuple[Timeline, list[str]]:
    """Build the timeline again from ``scenes.json`` and ``timing.json`` (after a scene
    change or a timing nudge), keeping the person's timeline-level choices."""
    folder = Path(project.folder)
    plan = read_scene_plan(folder)
    timing = read_timing(folder / VOICE_DIR / TIMING_FILE)
    if plan is None or timing is None:
        raise BuildError("This edit has no scene plan yet. Add pictures or clips first.")
    keep = read_timeline(folder / EDIT_DIR)
    timeline, warnings = build_edit_timeline(
        project, settings, plan, timing, music_choice=music_choice, keep=keep
    )
    write_timeline(folder / EDIT_DIR, timeline)
    return timeline, warnings


# Creating an edit --------------------------------------------------------------------------------


def create_edit(
    store: ProjectStore, settings: Any, form: EditInput, files: NewEditFiles,
    *, cancel: threading.Event | None = None,
) -> tuple[Project, list[str]]:
    """Make the edit's folder from the person's files and build its first timeline.
    Raises :class:`BuildError` (plain English); the folder is kept with status ``failed``."""
    if not files.items:
        raise BuildError("Add at least one picture or clip.")
    for item in files.items:
        if file_kind(item.name) not in ("picture", "clip"):
            raise BuildError(
                f"{item.name} is not a picture or a clip. Use png, jpg or webp pictures and "
                "mp4 or mov clips."
            )
    project = store.create(
        form.name, format=form.format, language=form.language,
        music_licence_note=" ".join(form.music_licence_note.split()),
    )
    folder = Path(project.folder)
    try:
        ordered = order_items(files.items, form)
        by_name = {item.file: item for item in form.items}
        sources = [
            import_item(folder, index, item, by_name.get(item.name), settings=settings,
                        keep_sound_default=form.keep_clip_sound)
            for index, item in enumerate(ordered)
        ]
        narration_s = import_narration(folder, files.narration, settings=settings, cancel=cancel)
        music = import_music(folder, files.music)
        timing = make_timing(form, files.captions, narration_s, sources, language=form.language)
        if narration_s is None:
            write_silent_voice(folder, timing.duration_s)
        write_timing(folder / VOICE_DIR / TIMING_FILE, timing)
        plan = make_scene_plan(project, sources, timing, settings)
        write_scene_plan(folder, plan)
        timeline, warnings = build_edit_timeline(
            project, settings, plan, timing, music_choice=str(music) if music else ""
        )
        write_timeline(folder / EDIT_DIR, timeline)
    except (BuildError, OSError) as exc:
        project.status = "failed"
        project.status_note = str(exc)
        store.save(project)
        raise BuildError(str(exc)) from exc
    project.status = "ready"
    project.status_note = ""
    project.duration_s = round(timeline.duration_s, 3)
    store.save(project)
    return project, warnings


# Scene changes -----------------------------------------------------------------------------------


def _timing_and_plan(project: Project) -> tuple[StoryboardDoc, TimingDoc]:
    folder = Path(project.folder)
    plan = read_scene_plan(folder)
    timing = read_timing(folder / VOICE_DIR / TIMING_FILE)
    if plan is None or timing is None:
        raise BuildError("This edit has no scene plan yet. Add pictures or clips first.")
    return plan, timing


def _sources_of(plan: StoryboardDoc) -> list[SceneSource]:
    return [
        SceneSource(
            index=scene.index, image_rel=scene.image.path or "", clip_rel=scene.clip,
            clip_duration_s=scene.clip_duration_s, caption=scene.popup.text or "",
            keep_sound=scene.keep_sound,
        )
        for scene in plan.scenes
    ]


def _renumber(sources: list[SceneSource]) -> list[SceneSource]:
    return [
        SceneSource(index=i, image_rel=s.image_rel, clip_rel=s.clip_rel,
                    clip_duration_s=s.clip_duration_s, caption=s.caption, keep_sound=s.keep_sound)
        for i, s in enumerate(sources)
    ]


def _finish_scene_change(
    project: Project, store: ProjectStore, settings: Any, plan: StoryboardDoc,
    sources: list[SceneSource], timing: TimingDoc,
) -> tuple[Timeline, list[str]]:
    folder = Path(project.folder)
    new_plan = make_scene_plan(project, sources, timing, settings, previous=plan)
    write_scene_plan(folder, new_plan)
    write_timing(folder / VOICE_DIR / TIMING_FILE, timing)
    keep = read_timeline(folder / EDIT_DIR)
    timeline, warnings = build_edit_timeline(
        project, settings, new_plan, timing, music_choice=None, keep=keep
    )
    write_timeline(folder / EDIT_DIR, timeline)
    project.duration_s = round(timeline.duration_s, 3)
    if project.status in ("new", "failed"):
        project.status = "ready"
        project.status_note = ""
    store.save(project)
    return timeline, warnings


def add_scene(
    project: Project, store: ProjectStore, settings: Any, item: IncomingFile,
    options: SceneFileInput,
) -> tuple[Timeline, list[str]]:
    """A new scene from a picture or clip, after scene ``options.after`` (or at the end).
    Without narration text the clock grows by the new scene's length."""
    plan, timing = _timing_and_plan(project)
    folder = Path(project.folder)
    sources = _sources_of(plan)
    new = import_item(
        folder, len(sources), item, EditItem(file=item.name, caption=options.caption,
                                             keep_sound=options.keep_sound),
        settings=settings, keep_sound_default=options.keep_sound,
    )
    at = len(sources) if options.after is None else max(0, min(options.after + 1, len(sources)))
    sources.insert(at, new)
    sources = _renumber(sources)
    if timing.source == "scenes":
        lengths = [
            s.clip_duration_s if s.clip_rel and s.clip_duration_s else PICTURE_SECONDS
            for s in sources
        ]
        timing = scene_timing(lengths, language=project.language)
        write_silent_voice(folder, timing.duration_s)
    return _finish_scene_change(project, store, settings, plan, sources, timing)


def replace_picture(
    project: Project, store: ProjectStore, settings: Any, index: int, item: IncomingFile,
) -> tuple[Timeline, list[str]]:
    """Give scene ``index`` another picture or clip; everything else stays."""
    plan, timing = _timing_and_plan(project)
    folder = Path(project.folder)
    sources = _sources_of(plan)
    if index < 0 or index >= len(sources):
        raise BuildError("That scene does not exist.")
    old = sources[index]
    new = import_item(
        folder, index, item, EditItem(file=item.name, caption=old.caption,
                                      keep_sound=old.keep_sound),
        settings=settings, keep_sound_default=old.keep_sound,
    )
    if new.image_rel != old.image_rel:
        old_file = folder / old.image_rel
        try:
            if old_file.is_file():
                old_file.unlink()
        except OSError:
            pass
        # The person's choices for this scene follow the new picture.
        for scene in plan.scenes:
            if scene.index == index:
                scene.image.path = new.image_rel
    sources[index] = new
    return _finish_scene_change(project, store, settings, plan, sources, timing)


def reorder_scenes(
    project: Project, store: ProjectStore, settings: Any, order: list[int],
) -> tuple[Timeline, list[str]]:
    """The scenes in a new order (``order`` lists the old 0-based indexes); the sentences,
    and so the narration, move with their scenes."""
    plan, timing = _timing_and_plan(project)
    sources = _sources_of(plan)
    if sorted(order) != list(range(len(sources))):
        raise BuildError("The new order must list every scene exactly once.")
    old_groups = [list(scene.sentence_ids) for scene in plan.scenes]
    sentence_order: list[int] = []
    position = {s.id: i for i, s in enumerate(timing.sentences)}
    seen: set[int] = set()
    for old_index in order:
        for sid in old_groups[old_index]:
            i = position.get(sid)
            if i is not None and i not in seen:
                sentence_order.append(i)
                seen.add(i)
    for i in range(len(timing.sentences)):
        if i not in seen:
            sentence_order.append(i)
    try:
        timing = reordered(timing, sentence_order)
    except TimingError as exc:
        raise BuildError(str(exc)) from exc
    sources = _renumber([sources[i] for i in order])
    plan.scenes = [plan.scenes[i] for i in order]
    for position_now, scene in enumerate(plan.scenes):
        scene.index = position_now
    return _finish_scene_change(project, store, settings, plan, sources, timing)


def remove_scene(
    project: Project, store: ProjectStore, settings: Any, index: int,
) -> tuple[Timeline, list[str]]:
    """Take a scene out; its sentences go to the scene before it (or the one after)."""
    plan, timing = _timing_and_plan(project)
    folder = Path(project.folder)
    sources = _sources_of(plan)
    if index < 0 or index >= len(sources):
        raise BuildError("That scene does not exist.")
    if len(sources) == 1:
        raise BuildError("An edit needs at least one scene.")
    gone = sources.pop(index)
    plan.scenes = [s for s in plan.scenes if s.index != index]
    for position_now, scene in enumerate(plan.scenes):
        scene.index = position_now
    sources = _renumber(sources)
    if timing.source == "scenes":
        lengths = [
            s.clip_duration_s if s.clip_rel and s.clip_duration_s else PICTURE_SECONDS
            for s in sources
        ]
        timing = scene_timing(lengths, language=project.language)
        write_silent_voice(folder, timing.duration_s)
    try:
        if gone.image_rel and (folder / gone.image_rel).is_file():
            (folder / gone.image_rel).unlink()
    except OSError:
        pass
    return _finish_scene_change(project, store, settings, plan, sources, timing)


def _fraction_rect(rect: list[float], size: tuple[int, int] | None) -> list[float]:
    """A timeline rectangle (source-picture pixels) as fractions of the picture."""
    if size is None or size[0] <= 0 or size[1] <= 0:
        return [0.0, 0.0, 1.0, 1.0]
    width, height = size
    x, y, w, h = (float(v) for v in rect[:4])
    return [
        round(min(max(x / width, 0.0), 1.0), 4), round(min(max(y / height, 0.0), 1.0), 4),
        round(min(max(w / width, 0.0), 1.0), 4), round(min(max(h / height, 0.0), 1.0), 4),
    ]


def sync_plan_from_timeline(project: Project, timeline: Timeline) -> None:
    """After the editor saved the timeline: the scene plan is rebuilt from it (the scenes in
    their order, each with its picture, its sentences, its camera move, its transition and
    its popup), so a rebuild from the timing keeps every choice, including a scene that was
    split, merged or re-cut in the editor."""
    folder = Path(project.folder)
    plan = read_scene_plan(folder)
    if plan is None:
        return
    by_image: dict[str, Scene] = {}
    for scene in plan.scenes:
        if scene.image.path and scene.image.path not in by_image:
            by_image[scene.image.path] = scene
    popup_by_scene: dict[int, Any] = {}
    for popup in timeline.popups:
        popup_by_scene.setdefault(popup.scene, popup)
    sizes: dict[str, tuple[int, int] | None] = {}
    scenes: list[Scene] = []
    for position, cut in enumerate(timeline.scenes):
        old = plan.scenes[position] if position < len(plan.scenes) else None
        if old is None or old.image.path != cut.image:
            old = by_image.get(cut.image, old)
        if cut.image not in sizes:
            try:
                sizes[cut.image] = _image_size(folder / cut.image)
            except (OSError, ValueError):
                sizes[cut.image] = None
        size = sizes[cut.image]
        sentence_ids = list(cut.sentence_ids) or (list(old.sentence_ids) if old else [])
        if not sentence_ids and scenes:
            sentence_ids = list(scenes[-1].sentence_ids)
        popup = popup_by_scene.get(cut.index)
        if popup is not None:
            popup_position = popup.position
            key_sentence = popup.anchor.sentence_id if popup.anchor else None
        else:
            popup_position = old.popup.position if old else "bottom-left"
            key_sentence = old.popup.key_sentence_id if old else None
        kept = set(old.edited if old else [])
        scene = Scene(
            index=position,
            sentence_ids=sentence_ids or ["s-001"],
            narration=old.narration if old else "",
            image_prompt=old.image_prompt if old else "",
            popup=ScenePopup(
                text=popup.text if popup is not None and popup.text.strip() else None,
                style=old.popup.style if old else "",
                position=popup_position,  # type: ignore[arg-type]
                key_sentence_id=key_sentence,
            ),
            motion=SceneMotion(
                preset=cut.motion.preset,  # type: ignore[arg-type]
                start_rect=_fraction_rect(list(cut.motion.start_rect), size),
                end_rect=_fraction_rect(list(cut.motion.end_rect), size),
                ease=cut.motion.ease,
                style=cut.motion.style,
                strength=cut.motion.strength,
            ),
            transition_out=SceneTransition(
                type=cut.transition_out.type, duration_s=cut.transition_out.duration_s
            ),
            image=SceneImage(path=cut.image, status="approved"),
            locked=old.locked if old else SceneLocks(),
            notes=list(old.notes) if old else [],
            title=old.title if old else "",
            clip=old.clip if old else None,
            clip_duration_s=old.clip_duration_s if old else None,
            keep_sound=old.keep_sound if old else False,
            edited=sorted(kept | {"motion", "transition_out", "popup", "sentence_ids", "image"}),
        )
        scenes.append(scene)
    plan.scenes = scenes
    write_scene_plan(folder, plan)


def project_state_summary(project: Project) -> dict[str, Any]:
    """What the list and the editor header need beyond ``project.json``."""
    state = read_state(Path(project.folder) / EDIT_DIR)
    renders = state.get("renders") if isinstance(state.get("renders"), dict) else {}
    done = [p for p, r in renders.items() if isinstance(r, dict) and r.get("status") == "done"]
    return {"renders": done}
