"""Helpers shared by the ``test_render_*`` modules: a complete 3-scene mock edit on disk
(scene plan, voice timing, silent voice.wav, placeholder pictures, a licensed music track in
``01_media``), the project and settings for it, and the two things the old edit stage did:
``run_edit`` (build the timeline when there is none, apply the edits, render) and
``apply_edits`` (the simple choices, then a render). Media is tiny (1K pictures, a few
seconds) so the real FFmpeg renders stay quick on a laptop. One sanity test keeps the
fixture honest."""

from __future__ import annotations

import colorsys
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

from sigma_editor.audio.wav import write_silence
from sigma_editor.config import Settings
from sigma_editor.edit import choices
from sigma_editor.edit.builder import rebuild_timeline, sync_plan_from_timeline, write_scene_plan
from sigma_editor.edit.renderer import (
    STAGE_DIR_NAME,
    RenderOutcome,
    load_review_payload,
    read_timeline,
    render_edit,
    write_timeline,
)
from sigma_editor.models.project import Project
from sigma_editor.models.storyboard import (
    Scene,
    SceneImage,
    SceneMotion,
    ScenePopup,
    SceneTransition,
    StoryboardDoc,
)
from sigma_editor.models.timeline import EditChoices, Timeline
from sigma_editor.render.motion import PRESETS
from sigma_editor.storage.project_store import MEDIA_DIR, PROJECT_FILE

TMP_ROOT = Path("F:/sge_tmp/edit")
PROJECT_ID = "p-edit-test"
SENTENCES: list[tuple[str, str]] = [
    ("s-01-01-01", "The diner opened at five every morning without fail, and the coffee was hot."),
    ("s-01-01-02", "Nobody noticed the stranger in the corner booth that day."),
    ("s-01-01-03", "When the bill came he left a note folded under the plate."),
]
SENTENCE_S = 1.6
GAP_S = 0.1
POPUPS: list[str | None] = ["Five every morning", None, "A folded note"]
POSITIONS = ["top-left", "bottom-left", "bottom-right"]
MOTIONS = ["zoom_in", "pan_right", "zoom_out"]
TRANSITIONS: list[tuple[str, float]] = [("fade", 0.6), ("wipeleft", 0.5), ("fadeblack", 0.8)]
MOTION_RECTS: dict[str, tuple[list[float], list[float]]] = {
    name: (list(spec.start), list(spec.end)) for name, spec in PRESETS.items()
}
"""Each camera move's start and end rectangle (fractions of the frame), from the engine."""


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg is not installed")


def make_render_tmp_fixture():
    """The ``render_tmp`` fixture: a short temporary folder (Windows path limit), removed
    afterwards. Each test module binds it with ``render_tmp = make_render_tmp_fixture()``."""

    @pytest.fixture(name="render_tmp")
    def render_tmp(tmp_path: Path) -> Iterator[Path]:
        base = TMP_ROOT
        try:
            base.mkdir(parents=True, exist_ok=True)
            folder = base / f"t-{os.getpid()}-{uuid.uuid4().hex[:6]}"
            folder.mkdir()
        except OSError:
            folder = tmp_path / "render"
            folder.mkdir()
        try:
            yield folder
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    return render_tmp


render_tmp = make_render_tmp_fixture()


# Media ------------------------------------------------------------------------------------------


def write_silence_wav(path: Path, seconds: float, sample_rate: int = 48000) -> Path:
    """A silent 16-bit mono WAV of ``seconds``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_silence(path, max(1, round(seconds * sample_rate)), sample_rate)
    return path


def write_picture(path: Path, aspect: str = "16:9", number: int = 1,
                  size: tuple[int, int] | None = None) -> Path:
    """A placeholder picture: its own colour, a gradient and its number, so frames differ."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = size or ((1024, 576) if aspect == "16:9" else (576, 1024))
    red, green, blue = colorsys.hsv_to_rgb((number * 0.37) % 1.0, 0.55, 0.9)
    base = (int(red * 255), int(green * 255), int(blue * 255))
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 8):  # a gradient to the right: darker
        shade = int(120 * x / width)
        draw.rectangle([x, 0, x + 8, height],
                       fill=(max(0, base[0] - shade), max(0, base[1] - shade),
                             max(0, base[2] - shade)))
    for y in range(0, height, height // 6):  # a few lines, so motion shows
        draw.line([0, y, width, y], fill=(255, 255, 255), width=3)
    font = ImageFont.load_default(size=height // 3)
    draw.text((width // 2, height // 2), str(number), fill=(20, 20, 20), font=font,
              anchor="mm")
    image.save(path, "PNG")
    return path


def write_music_track(music_dir: Path, name: str = "calm.wav", licence: bool = True) -> Path:
    """A two-second sine tone (needs FFmpeg); the licence sidecar is optional."""
    music_dir.mkdir(parents=True, exist_ok=True)
    track = music_dir / name
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-ac", "2", str(track),
        ],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if licence:
        (music_dir / f"{name}.license.txt").write_text("CC0 1.0", encoding="utf-8")
    return track


# Timing and scene plan ----------------------------------------------------------------------------


def make_timing(
    sentences: list[tuple[str, str]] | None = None,
    *,
    sentence_s: float = SENTENCE_S,
    gap_s: float = GAP_S,
) -> dict[str, Any]:
    """A ``timing.json`` document with evenly spaced words (the mock voice's shape)."""
    rows: list[dict[str, Any]] = []
    clock = 0.0
    for sid, text in sentences or SENTENCES:
        words = text.split()
        slot = sentence_s / len(words)
        rows.append(
            {
                "id": sid,
                "text": text,
                "start_s": round(clock, 3),
                "end_s": round(clock + sentence_s, 3),
                "words": [
                    {
                        "text": word,
                        "start_s": round(clock + i * slot, 3),
                        "end_s": round(clock + (i + 1) * slot, 3),
                        "confidence": 0.5,
                    }
                    for i, word in enumerate(words)
                ],
            }
        )
        clock += sentence_s + gap_s
    return {
        "sample_rate": 48000,
        "duration_s": round(clock - gap_s, 3),
        "source": "estimated",
        "sentences": rows,
    }


def make_storyboard(aspect: str = "16:9", popups: bool = True) -> StoryboardDoc:
    """The scene plan (``07_edit/scenes.json``) of the mock edit: 3 scenes, one sentence each."""
    scenes: list[Scene] = []
    for index, (sid, text) in enumerate(SENTENCES):
        start, end = MOTION_RECTS[MOTIONS[index]]
        scenes.append(
            Scene(
                index=index,
                sentence_ids=[sid],
                narration=text,
                image_prompt=f"Picture {index + 1}",
                popup=ScenePopup(
                    text=POPUPS[index] if popups else None,
                    style="Rounded box, brand colour, slide-in from left",
                    position=POSITIONS[index],  # type: ignore[arg-type]
                ),
                motion=SceneMotion(
                    preset=MOTIONS[index],  # type: ignore[arg-type]
                    start_rect=list(start),
                    end_rect=list(end),
                ),
                transition_out=SceneTransition(
                    type=TRANSITIONS[index][0], duration_s=TRANSITIONS[index][1]
                ),
                image=SceneImage(path=f"06_images/scene_{index + 1:02d}.png", status="generated"),
            )
        )
    return StoryboardDoc(
        project_id=PROJECT_ID,
        format="long" if aspect == "16:9" else "shorts",
        aspect=aspect,  # type: ignore[arg-type]
        style_guide="Cinematic photo-realism",
        generated_at=datetime.now(UTC),
        model="mock",
        scenes=scenes,
    )


make_scene_plan = make_storyboard


# The edit on disk ---------------------------------------------------------------------------


@dataclass
class ProjectFiles:
    root: Path
    folder: Path
    app_dir: Path
    music_dir: Path | None
    storyboard: StoryboardDoc
    timing: dict[str, Any]

    @property
    def edit_dir(self) -> Path:
        return self.folder / STAGE_DIR_NAME

    @property
    def scene_plan(self) -> StoryboardDoc:
        return self.storyboard


def write_project(
    root: Path,
    *,
    aspect: str = "16:9",
    music: bool = True,
    licence: bool = True,
    popups: bool = True,
    images: bool = True,
    name: str = "The Diner Debt",
) -> ProjectFiles:
    """The files of an edit as New edit leaves them: ``project.json``, the scene plan, the
    timing, a silent narration, the pictures and (with ``music``) a track in ``01_media``."""
    folder = root / "proj"
    for sub in ("01_media", "05_voice", "06_images", "07_edit", "08_export"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    app_dir = root / "app"
    app_dir.mkdir(exist_ok=True)
    storyboard = make_storyboard(aspect, popups=popups)
    write_scene_plan(folder, storyboard)
    timing = make_timing()
    (folder / "05_voice" / "timing.json").write_text(json.dumps(timing, indent=2), "utf-8")
    write_silence_wav(folder / "05_voice" / "voice.wav", timing["duration_s"], 48000)
    if images:
        for index in range(len(SENTENCES)):
            write_picture(folder / "06_images" / f"scene_{index + 1:02d}.png", aspect, index + 1)
    music_dir: Path | None = None
    if music:
        music_dir = folder / MEDIA_DIR
        if ffmpeg_available():
            write_music_track(music_dir, licence=licence)
        else:
            write_silence_wav(music_dir / "calm.wav", 2.0, 48000)
            if licence:
                (music_dir / "calm.wav.license.txt").write_text("CC0", encoding="utf-8")
    now = datetime.now(UTC)
    project = Project(
        id=PROJECT_ID, name=name, format=storyboard.format, language="English",
        folder=str(folder), created_at=now, updated_at=now, status="ready",
    )
    (folder / PROJECT_FILE).write_text(project.model_dump_json(indent=2), encoding="utf-8")
    return ProjectFiles(root, folder, app_dir, music_dir, storyboard, timing)


def make_settings(
    files: ProjectFiles,
    *,
    render: dict[str, Any] | None = None,
    captions: dict[str, Any] | None = None,
) -> Settings:
    return Settings(
        app_data_dir=files.app_dir,
        projects_dir=files.root,
        exports_dir=files.root / "exports",
        render=render or {"default_presets": ["720p"], "x264_preset": "ultrafast"},
        captions=captions or {"enabled": True, "style": "bold-white"},
    )


def make_project(files: ProjectFiles, *, language: str = "English") -> Project:
    project = Project.model_validate_json((files.folder / PROJECT_FILE).read_text("utf-8"))
    project.language = language
    return project


@dataclass
class Ctx:
    """What the old edit stage got as its context: the edit, the settings and the edits."""

    project: Project
    settings: Settings
    folder: Path
    progress: Callable[[str, float | None], None]
    edits: dict[str, Any] = field(default_factory=dict)


def make_ctx(
    files: ProjectFiles,
    *,
    render: dict[str, Any] | None = None,
    captions: dict[str, Any] | None = None,
    language: str = "English",
    progress: Callable[[str, float | None], None] | None = None,
    edits: dict[str, Any] | None = None,
) -> Ctx:
    return Ctx(
        project=make_project(files, language=language),
        settings=make_settings(files, render=render, captions=captions),
        folder=files.folder,
        progress=progress or (lambda message, pct=None: None),
        edits=dict(edits or {}),
    )


# Running the edit step ------------------------------------------------------------------------


@dataclass
class EditResult:
    """What ``run_edit`` and ``apply_edits`` answer: the render's outcome and the editor
    payload rebuilt from the files, as the API answers it."""

    outcome: RenderOutcome
    payload: dict[str, Any]

    @property
    def needs_review_payload(self) -> dict[str, Any]:
        return self.payload

    @property
    def gate_results(self) -> list[dict[str, Any]]:
        return self.outcome.gate_results

    @property
    def rendered(self) -> list[str]:
        return self.outcome.rendered

    @property
    def kept(self) -> list[str]:
        return self.outcome.kept

    @property
    def outputs(self) -> list[Path]:
        return self.outcome.outputs

    @property
    def warnings(self) -> list[str]:
        return self.outcome.warnings

    @property
    def nothing_new(self) -> bool:
        """Every requested file was up to date and kept."""
        return not self.outcome.rendered


def _apply(ctx: Ctx, edits: dict[str, Any]) -> None:
    edits = dict(edits)
    full = edits.pop("timeline", None)
    if full is not None:
        timeline = Timeline.model_validate(full)
        current = read_timeline(ctx.folder / STAGE_DIR_NAME)
        timeline.revision = (current.revision if current else 0) + 1
        write_timeline(ctx.folder / STAGE_DIR_NAME, timeline)
        sync_plan_from_timeline(ctx.project, timeline)
    if edits:
        choices.apply_edits(ctx.project, ctx.settings, EditChoices.model_validate(edits))


def run_edit(ctx: Ctx) -> EditResult:
    """The old edit stage's ``run``: build the timeline when there is none, apply the
    edits (``presets`` picks the sizes of this render; ``[]`` = the preview only), render."""
    stage_dir = ctx.folder / STAGE_DIR_NAME
    if read_timeline(stage_dir) is None:
        rebuild_timeline(ctx.project, ctx.settings)
    edits = dict(ctx.edits)
    presets = edits.pop("presets", None)
    _apply(ctx, edits)
    outcome = render_edit(ctx.project, ctx.settings, presets=presets, report=ctx.progress)
    payload = load_review_payload(ctx.project, ctx.settings)
    assert payload is not None
    return EditResult(outcome, payload)


def apply_edits(ctx: Ctx) -> EditResult | None:
    """The old edit stage's ``apply_edits``: the simple choices (or a whole timeline), then
    a render of the preview and the ticked sizes. ``None`` when there was nothing to do."""
    if not ctx.edits:
        return None
    _apply(ctx, ctx.edits)
    outcome = render_edit(ctx.project, ctx.settings, report=ctx.progress)
    payload = load_review_payload(ctx.project, ctx.settings)
    assert payload is not None
    return EditResult(outcome, payload)


def test_fixture_builds_a_complete_mock_edit(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    assert (files.folder / "07_edit" / "scenes.json").is_file()
    assert (files.folder / "05_voice" / "timing.json").is_file()
    assert (files.folder / "05_voice" / "voice.wav").stat().st_size > 1000
    assert sorted(p.name for p in (files.folder / "06_images").iterdir()) == [
        "scene_01.png", "scene_02.png", "scene_03.png",
    ]
    with Image.open(files.folder / "06_images" / "scene_02.png") as picture:
        assert picture.size == (1024, 576)
    assert files.timing["duration_s"] == pytest.approx(3 * SENTENCE_S + 2 * GAP_S)
    assert files.music_dir is not None and (files.music_dir / "calm.wav").is_file()
    assert files.music_dir == files.folder / "01_media"
    ctx = make_ctx(files)
    assert ctx.settings.render.default_presets == ["720p"]
    assert ctx.project.id == PROJECT_ID and ctx.project.folder == str(files.folder)
    assert MOTION_RECTS["hold"] == ([0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0])
