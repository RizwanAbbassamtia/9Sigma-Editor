"""The live preview's routes (from CashCow Studio's M5.1 preview engine; design 10.6).

* ``GET  /api/projects/{id}/timeline``: everything the preview needs in one call
  (:class:`~..models.timeline.TimelineBundle`): ``07_edit/timeline.json``, the sentences and
  words of ``05_voice/timing.json``, the pictures with their sizes and thumbnail URLs, the
  media URLs (voice, music stem and envelope while they are current for this timeline, the
  fonts), ``preview_gain_db`` and, once measured for this sound, ``preview_gain_curve``.
  ``GET /timeline`` also queues the preview's sized pictures for the background maker.
* ``GET  /api/projects/{id}/media/thumb?path=...&h=128``: a JPEG of a picture of the edit.
* ``GET  /api/projects/{id}/media/music``: the timeline's own music track (the raw file the
  preview plays while the music stem is being made); only an audio file inside the edit's
  folder is served.
* ``GET  /api/projects/{id}/media/peaks/{voice|music}.json``: ``{step_s: 0.01, min, max}``
  of the voice file or the music stem.
* ``GET  /api/projects/{id}/media/voice/segments/{sid}.wav``: one sentence's audio, cut once
  from the voice file at the sentence's span in ``timing.json`` and cached.
* ``POST /api/projects/{id}/popups/preview``: the popup PNG the renderer draws.
* ``POST /api/projects/{id}/music/stem``: makes ``07_edit/music_ducked.wav`` and its
  envelope when they are not current, and measures the preview gain.
* ``GET  /api/fonts/{file}``: the Noto fonts of ``assets/fonts`` (only those files).

The routes keep their CashCow paths (``/api/projects/...``) so the copied preview engine
works unchanged; the edits themselves live under ``/api/edits``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
import wave
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image

from ..edit.renderer import STAGE_DIR_NAME, file_url, music_folder_of, read_timeline
from ..models.project import Project
from ..models.timeline import (
    BundleFont,
    BundleMedia,
    BundlePicture,
    BundleSentence,
    BundleWord,
    MusicStemResult,
    PeaksDoc,
    PopupPreviewRequest,
    PopupPreviewResult,
    PreviewGainCurve,
    Timeline,
    TimelineBundle,
)
from ..render import music as audio
from ..render import thumbs
from ..render.captions import cue_word_times, rules_for
from ..render.ffmpeg import RenderError, find_ffmpeg
from ..render.fonts import FONT_FILES, fonts_dir
from ..render.popups import brand_colour, popup_position, popup_scale, render_popup_png
from ..render.presets import load_render_config
from ..render.timeline_builder import Timing, parse_timing
from ..storage.project_store import ProjectNotFound, ProjectStoreError
from .deps import StoreDep
from .files import resolve_project_file

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["preview"])

PREVIEW_DIR = f"{STAGE_DIR_NAME}/preview"
THUMB_HEIGHT = thumbs.THUMB_HEIGHT
THUMB_MAX_HEIGHT = thumbs.THUMB_MAX_HEIGHT
PEAKS_VERSION = "peaks.1"
POPUP_VERSION = "popup.1"
VOICE_CUT_VERSION = "voice.1"
POPUP_CACHE_KEEP = 200
NO_CACHE = "no-cache"
FONT_CACHE = "public, max-age=31536000, immutable"
PICTURE_SUFFIXES = thumbs.PICTURE_SUFFIXES
MUSIC_TYPES = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wma": "audio/x-ms-wma",
    ".aif": "audio/aiff", ".aiff": "audio/aiff",
}
SENTENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
TIMING_FILE = "05_voice/timing.json"
DEFAULT_VOICE = "05_voice/voice.wav"
SENTENCES_DIR = "05_voice/sentences"

NO_TIMELINE = "This edit has no timeline yet, so there is nothing to play."
NOT_A_PICTURE = "That file is not a picture."
NO_STEM = "The music for the preview is not ready yet."
NO_MUSIC = "This video has no background music."
NO_TRACK = "The music track is not on this PC."


# Helpers ------------------------------------------------------------------------------------


async def _project(store: StoreDep, project_id: str) -> Project:
    try:
        return await asyncio.to_thread(store.get, project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _key(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _stat_key(path: Path, *extra: Any) -> str:
    stat = path.stat()
    return _key(str(path), int(stat.st_size), int(stat.st_mtime_ns), *extra)


def _not_modified(request: Request, etag: str) -> bool:
    wanted = request.headers.get("if-none-match", "")
    return any(tag.strip() in (etag, f"W/{etag}") for tag in wanted.split(",") if tag.strip())


def _cached_response(request: Request, data: bytes, media_type: str, key: str) -> Response:
    etag = f'"{key}"'
    headers = {"ETag": etag, "Cache-Control": NO_CACHE}
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=media_type, headers=headers)


def inside_project(folder: Path, value: str | None) -> str | None:
    """``value`` (relative or absolute) as a path relative to the edit's folder when it
    names a file inside it; ``None`` otherwise."""
    if not value:
        return None
    try:
        root = Path(folder).resolve()
        path = Path(value)
        target = (path if path.is_absolute() else root / path).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if root not in target.parents or not target.is_file():
        return None
    return target.relative_to(root).as_posix()


def _read_timing(folder: Path) -> Timing | None:
    path = Path(folder) / TIMING_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    timing = parse_timing(data)
    return timing if timing.sentences else None


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)


def _prune(folder: Path, pattern: str, keep: int) -> None:
    """Keep the ``keep`` newest files matching ``pattern`` in ``folder``."""
    try:
        files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            continue


def _remove_others(folder: Path, pattern: str, keep: Path) -> None:
    try:
        for path in folder.glob(pattern):
            if path != keep:
                path.unlink()
    except OSError:
        pass


# The timeline bundle ------------------------------------------------------------------------


def bundle_sentences(timing: Timing | None) -> list[BundleSentence]:
    if timing is None:
        return []
    return [
        BundleSentence(
            id=sentence.id,
            text=sentence.text,
            start_s=sentence.start_s,
            end_s=sentence.end_s,
            words=[BundleWord(w=w.text, start_s=w.start_s, end_s=w.end_s)
                   for w in sentence.words],
        )
        for sentence in timing.sentences
    ]


def thumb_url(project_id: str, relative: str, height: int = THUMB_HEIGHT) -> str:
    return (
        f"/api/projects/{project_id}/media/thumb?path={quote(relative, safe='/')}&h={height}"
    )


def bundle_pictures(project_id: str, folder: Path, timeline: Timeline) -> list[BundlePicture]:
    pictures: list[BundlePicture] = []
    for scene in timeline.scenes:
        relative = inside_project(folder, scene.image)
        width = height = 0
        if relative is not None:
            try:  # one picture Pillow cannot open (or too big to) only loses its own scene
                width, height = thumbs.picture_size(Path(folder) / relative)
            except thumbs.NotAPicture:
                relative = None
        pictures.append(
            BundlePicture(
                scene=scene.index, path=scene.image, width=width, height=height,
                thumb_url=thumb_url(project_id, relative) if relative else None,
            )
        )
    return pictures


def bundle_fonts() -> list[BundleFont]:
    folder = fonts_dir()
    return [
        BundleFont(family=family, url=f"/api/fonts/{quote(name, safe='')}")
        for family, name in FONT_FILES.items()
        if (folder / name).is_file()
    ]


def music_track(folder: Path, timeline: Timeline | None, music_folder: str | None) -> Path | None:
    """The timeline's own music track when the preview may play it: an audio file inside the
    edit's folder (nothing else is ever served)."""
    if timeline is None or not timeline.music.path:
        return None
    track = audio.music_file(timeline, folder)
    if track is None or track.suffix.lower() not in MUSIC_TYPES:
        return None
    try:
        resolved = track.resolve()
    except (OSError, RuntimeError):
        return None
    for root in (Path(folder), Path(music_folder) if music_folder else None):
        if root is None:
            continue
        try:
            if root.resolve() in resolved.parents:
                return resolved
        except (OSError, RuntimeError):
            continue
    return None


def music_url(project_id: str, track: Path) -> str:
    """The raw track's URL, its version in ``?v=`` (path, size and date)."""
    return f"/api/projects/{project_id}/media/music?v={_stat_key(track, 'music')}"


def bundle_media(project_id: str, folder: Path, timeline: Timeline,
                 music_folder: str | None = None) -> BundleMedia:
    voice = inside_project(folder, timeline.voice.path)
    media = BundleMedia(
        voice_url=file_url(project_id, voice) if voice else None, fonts=bundle_fonts()
    )
    key = audio.stem_is_current(timeline, folder, load_render_config())
    if key is not None:
        stem, envelope = audio.relative_stem_paths()
        media.stem_url = f"{file_url(project_id, stem)}?v={key}"
        media.envelope_url = f"{file_url(project_id, envelope)}?v={key}"
    track = music_track(folder, timeline, music_folder)
    if track is not None:
        try:
            media.music_url = music_url(project_id, track)
        except OSError:
            media.music_url = None
    return media


def build_bundle(project: Project, folder: Path, music_folder: str | None = None) -> TimelineBundle:
    folder = Path(folder)
    timing = _read_timing(folder)
    timeline = read_timeline(folder / STAGE_DIR_NAME)
    if timeline is None:
        raise HTTPException(status_code=404, detail=NO_TIMELINE)
    # the preview's sized pictures, made in the background before the preview asks for them
    thumbs.prepare_in_background(folder, [scene.image for scene in timeline.scenes],
                                 (int(timeline.width), int(timeline.height)))
    loudness = audio.cached_preview_loudness(timeline, folder, load_render_config())
    gain = loudness.gain_db if loudness is not None else timeline.render.preview_gain_db
    curve = None
    if loudness is not None and loudness.curve_db:
        curve = PreviewGainCurve(step_s=loudness.curve_step_s, gain_db=loudness.curve_db)
    space_dependent = rules_for(str(project.language)).space_dependent
    words = cue_word_times(timeline.captions.cues, timing.sentences if timing else None,
                           space_dependent)
    return TimelineBundle(
        timeline=timeline,
        revision=timeline.revision,
        sentences=bundle_sentences(timing),
        sections=[],
        pictures=bundle_pictures(project.id, folder, timeline),
        media=bundle_media(project.id, folder, timeline, music_folder),
        preview_gain_db=gain,
        virtual=False,
        timing_estimated=timing is None,
        cue_words={
            cue.id: [BundleWord(w=w.text, start_s=w.start_s, end_s=w.end_s) for w in cue_words]
            for cue, cue_words in zip(timeline.captions.cues, words, strict=True)
        },
        preview_gain_curve=curve,
    )


def _music_folder(project: Project) -> str:
    return str(music_folder_of(project.folder))


@router.get("/projects/{project_id}/timeline", response_model=TimelineBundle)
async def get_timeline_bundle(project_id: str, store: StoreDep) -> TimelineBundle:
    """The timeline and everything the live preview needs to play it."""
    project = await _project(store, project_id)
    return await asyncio.to_thread(
        build_bundle, project, Path(project.folder), _music_folder(project)
    )


# Thumbnails -----------------------------------------------------------------------------------


make_thumbnail = thumbs.make_thumbnail


@router.get("/projects/{project_id}/media/thumb")
async def get_thumbnail(
    project_id: str,
    request: Request,
    store: StoreDep,
    path: str = Query(min_length=1, max_length=400),
    h: int = Query(default=THUMB_HEIGHT, ge=16, le=THUMB_MAX_HEIGHT),
) -> Response:
    """A thumbnail of a picture inside the edit's folder (cached; ``render/thumbs.py``)."""
    project = await _project(store, project_id)
    folder = Path(project.folder)
    source = resolve_project_file(folder, path)
    if source.suffix.lower() not in PICTURE_SUFFIXES:
        raise HTTPException(status_code=404, detail=NOT_A_PICTURE)
    try:
        data, key = await asyncio.to_thread(thumbs.thumbnail, folder, source, h)
    except (OSError, thumbs.NotAPicture) as exc:
        raise HTTPException(status_code=404, detail=NOT_A_PICTURE) from exc
    return _cached_response(request, data, "image/jpeg", key)


# The raw music track ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/media/music")
async def get_music_track(project_id: str, request: Request, store: StoreDep) -> Response:
    """The timeline's music track as it is (the preview plays it while the stem is made)."""
    project = await _project(store, project_id)
    folder = Path(project.folder)
    timeline = await asyncio.to_thread(read_timeline, folder / STAGE_DIR_NAME)
    if timeline is None or not timeline.music.path:
        raise HTTPException(status_code=404, detail=NO_MUSIC)
    track = await asyncio.to_thread(music_track, folder, timeline, _music_folder(project))
    if track is None:
        raise HTTPException(status_code=404, detail=NO_TRACK)
    try:
        etag = f'"{_stat_key(track, "music")}"'
    except OSError as exc:
        raise HTTPException(status_code=404, detail=NO_TRACK) from exc
    headers = {"Cache-Control": NO_CACHE, "ETag": etag}
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    return FileResponse(track, media_type=MUSIC_TYPES[track.suffix.lower()],
                        headers={**headers, "Accept-Ranges": "bytes"})


# Peaks ---------------------------------------------------------------------------------------


def _peaks_source(folder: Path, kind: str) -> tuple[Path, str]:
    """The file the peaks of ``kind`` come from and its name for ``source``."""
    timeline = read_timeline(folder / STAGE_DIR_NAME)
    if kind == "voice":
        value = timeline.voice.path if timeline is not None else DEFAULT_VOICE
        relative = inside_project(folder, value)
        if relative is None:
            raise HTTPException(status_code=404, detail="The narration is not ready yet.")
        return folder / relative, "voice"
    if timeline is None or not timeline.music.path:
        raise HTTPException(status_code=404, detail=NO_MUSIC)
    stem, _envelope = audio.stem_paths(folder)
    if audio.stem_is_current(timeline, folder, load_render_config()) is not None:
        return stem, "stem"
    track = audio.music_file(timeline, folder)
    if track is None:
        raise HTTPException(status_code=404, detail=NO_STEM)
    return track, "track"


@router.get("/projects/{project_id}/media/peaks/{kind}.json", response_model=PeaksDoc)
async def get_peaks(project_id: str, kind: str, request: Request, store: StoreDep) -> Response:
    """Lowest and highest sample per 10 ms of the voice file or the music stem, cached."""
    if kind not in ("voice", "music"):
        raise HTTPException(status_code=404, detail="Peaks are for the voice or the music.")
    project = await _project(store, project_id)
    folder = Path(project.folder)
    source, name = await asyncio.to_thread(_peaks_source, folder, kind)
    key = _stat_key(source, kind, PEAKS_VERSION)
    cache_dir = folder / PREVIEW_DIR / "peaks"
    cache = cache_dir / f"{kind}-{key}.json"

    def load() -> bytes:
        try:
            return cache.read_bytes()
        except OSError:
            pass
        lows, highs, duration = audio.audio_peaks(source)
        doc = PeaksDoc(step_s=audio.ENVELOPE_STEP_S, min=lows, max=highs, duration_s=duration,
                       source=name)
        data = doc.model_dump_json().encode("utf-8")
        try:
            _write_bytes(cache, data)
            _remove_others(cache_dir, f"{kind}-*.json", cache)
        except OSError as exc:
            log.info("Could not keep the peaks %s: %s", cache.name, exc)
        return data

    try:
        data = await asyncio.to_thread(load)
    except RenderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _cached_response(request, data, "application/json", key)


# One sentence of the voice ---------------------------------------------------------------------


def _wav_frames(path: Path) -> tuple[int, int] | None:
    """``(frames, sample rate)`` of a PCM WAV, ``None`` when it is not one."""
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes(), handle.getframerate()
    except (wave.Error, EOFError, OSError, ValueError):
        return None


def sentence_audio(folder: Path, sentence_id: str) -> Path:
    """The audio of one sentence: its own file when it matches the timing, else cut from the
    voice file at the sentence's span (cached in ``07_edit/preview/voice``)."""
    from ..audio.errors import AudioError
    from ..audio.wav import slice_wav

    timing = _read_timing(folder)
    sentence = timing.sentence(sentence_id) if timing is not None else None
    if sentence is None:
        raise HTTPException(status_code=404, detail="That sentence is not in the voice timing.")
    timeline = read_timeline(folder / STAGE_DIR_NAME)
    relative = inside_project(folder, timeline.voice.path if timeline else DEFAULT_VOICE)
    voice = folder / relative if relative else None
    voice_info = _wav_frames(voice) if voice is not None else None
    rate = voice_info[1] if voice_info else (timing.sample_rate if timing else 48000)
    start = sentence.start_frame if sentence.start_frame is not None else round(
        sentence.start_s * rate)
    end = sentence.end_frame if sentence.end_frame is not None else round(sentence.end_s * rate)
    clips = [
        clip for clip in ((timeline.voice.segments or []) if timeline is not None else [])
        if clip.sentence_id == sentence_id
    ]
    if clips:
        start = min(start, *(round(clip.src_start_s * rate) for clip in clips))
        end = max(end, *(round(clip.src_end_s * rate) for clip in clips))
        if voice_info is not None:
            end = min(end, max(voice_info[0], start))
    own = folder / SENTENCES_DIR / f"{sentence_id}.wav"
    own_info = _wav_frames(own)
    if own_info is not None and own_info[1] == rate and abs(own_info[0] - (end - start)) <= 2:
        return own
    if voice is None or voice_info is None:
        if own_info is not None:
            return own
        raise HTTPException(status_code=404, detail="The narration is not ready yet.")
    key = _stat_key(voice, start, end, VOICE_CUT_VERSION)[:10]
    cache_dir = folder / PREVIEW_DIR / "voice"
    cache = cache_dir / f"{sentence_id}-{key}.wav"
    if cache.is_file():
        return cache
    cache_dir.mkdir(parents=True, exist_ok=True)
    part = cache.with_name(f"{cache.stem}.{uuid.uuid4().hex[:8]}.part.wav")
    try:
        slice_wav(voice, part, start, end)
        os.replace(part, cache)
    except (AudioError, OSError) as exc:
        try:
            part.unlink()
        except OSError:
            pass
        if cache.is_file():  # the other request's copy is in place
            return cache
        raise HTTPException(status_code=404, detail="The narration could not be read.") from exc
    _remove_others(cache_dir, f"{sentence_id}-*.wav", cache)
    return cache


@router.get("/projects/{project_id}/media/voice/segments/{sid}.wav")
async def get_voice_segment(
    project_id: str, sid: str, request: Request, store: StoreDep
) -> Response:
    """The audio of sentence ``sid`` (a WAV; ``304`` when the browser's copy is current)."""
    if not SENTENCE_ID.match(sid or ""):
        raise HTTPException(status_code=404, detail="That sentence is not in the voice timing.")
    project = await _project(store, project_id)
    path = await asyncio.to_thread(sentence_audio, Path(project.folder), sid)
    etag = f'"{_stat_key(path, VOICE_CUT_VERSION)}"'
    headers = {"Cache-Control": NO_CACHE, "ETag": etag}
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="audio/wav",
                        headers={**headers, "Accept-Ranges": "bytes"})


# Popup preview ---------------------------------------------------------------------------------


def _even(value: float) -> int:
    rounded = int(round(value))
    return max(rounded - rounded % 2, 2)


def preview_frame(aspect: str, height: int) -> tuple[int, int]:
    """The preview's frame for ``aspect`` at ``height`` pixels tall."""
    width = height * 9 / 16 if aspect == "9:16" else height * 16 / 9
    return _even(width), int(height)


def render_popup_preview(
    project: Project, folder: Path, body: PopupPreviewRequest, colours: list[str]
) -> PopupPreviewResult:
    """The popup PNG for a preview frame (the same drawing and placing as the render)."""
    config = load_render_config()
    look = config.popups
    width, height = preview_frame(body.aspect, body.height)
    scale = popup_scale(width, height)
    font = rules_for(str(project.language)).popup_font
    box = brand_colour(colours)
    reserved = 0
    timeline = read_timeline(folder / STAGE_DIR_NAME)
    if timeline is not None and timeline.captions.enabled and timeline.captions.cues:
        style = timeline.captions.style
        reserved = round(
            (style.size * style.max_lines * 1.4 + config.captions_margin_v_px) * scale
        )
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(status_code=422, detail="The popup needs some text.")
    key = _key(POPUP_VERSION, text, width, height, box, font, repr(look))
    cache_dir = folder / PREVIEW_DIR / "popups"
    png = cache_dir / f"{key}.png"
    if png.is_file():
        with Image.open(png) as image:
            size = (int(image.width), int(image.height))
        try:  # in use again: the pruning below keeps the newest
            os.utime(png)
        except OSError:
            pass
    else:
        part = cache_dir / f"{key}.{uuid.uuid4().hex[:8]}.part.png"
        size = render_popup_png(text, part, scale=scale, look=look, box_colour=box,
                                font_family=font, frame_width=width)
        try:
            os.replace(part, png)
        except OSError:  # the same popup drawn by two requests at once: either file will do
            try:
                part.unlink()
            except OSError:
                pass
        _prune(cache_dir, "*.png", POPUP_CACHE_KEEP)
    x, y = popup_position(
        body.position, size, (width, height), margin_px=round(look.margin_px * scale),
        bottom_reserved_px=reserved,
    )
    relative = f"{PREVIEW_DIR}/popups/{png.name}"
    return PopupPreviewResult(png_url=file_url(project.id, relative), x=x, y=y, w=size[0],
                              h=size[1])


@router.post("/projects/{project_id}/popups/preview", response_model=PopupPreviewResult)
async def preview_popup(
    project_id: str, body: PopupPreviewRequest, store: StoreDep
) -> PopupPreviewResult:
    """The popup as the render will draw it, for a preview ``height`` pixels tall."""
    project = await _project(store, project_id)
    return await asyncio.to_thread(render_popup_preview, project, Path(project.folder), body, [])


# Music stem ------------------------------------------------------------------------------------


@router.post("/projects/{project_id}/music/stem", response_model=MusicStemResult)
async def rebuild_music_stem(project_id: str, request: Request, store: StoreDep) -> MusicStemResult:
    """Make the music stem and its envelope for the timeline's music block (when they are not
    current) and measure the preview gain."""
    project = await _project(store, project_id)
    folder = Path(project.folder)
    timeline = await asyncio.to_thread(read_timeline, folder / STAGE_DIR_NAME)
    if timeline is None:
        raise HTTPException(status_code=404, detail=NO_TIMELINE)
    settings = request.app.state.settings

    def work() -> MusicStemResult:
        config = load_render_config()
        ffmpeg = find_ffmpeg(settings)
        stem = audio.ensure_ducked_stem(timeline, folder, config=config, ffmpeg=ffmpeg)
        try:
            gain = audio.ensure_preview_gain(timeline, folder, config=config, stem=stem,
                                             ffmpeg=ffmpeg)
        except RenderError as exc:  # the preview plays at its own level
            log.warning("The loudness for the preview could not be measured: %s", exc)
            gain = None
        result = MusicStemResult(preview_gain_db=gain)
        if stem is not None:
            stem_path, envelope_path = audio.relative_stem_paths()
            result.stem_url = f"{file_url(project.id, stem_path)}?v={stem.key}"
            result.envelope_url = f"{file_url(project.id, envelope_path)}?v={stem.key}"
        return result

    try:
        return await asyncio.to_thread(work)
    except RenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# Fonts -----------------------------------------------------------------------------------------


@router.get("/fonts/{file}")
async def get_font(file: str) -> FileResponse:
    """One of the Noto fonts the captions and popups use (nothing else is served)."""
    if file not in set(FONT_FILES.values()):
        raise HTTPException(status_code=404, detail="That font is not one of the app's fonts.")
    path = fonts_dir() / file
    if not path.is_file():
        raise HTTPException(status_code=404, detail="That font is missing on this PC.")
    return FileResponse(path, media_type="font/ttf", headers={"Cache-Control": FONT_CACHE})


__all__ = ["router", "build_bundle", "inside_project", "preview_frame"]
