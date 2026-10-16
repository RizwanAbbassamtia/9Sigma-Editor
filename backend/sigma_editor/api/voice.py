"""The AI voice routes (docs/CONTRACT-v0.2.md section 5): the API key kept on this laptop
(``GET /api/voice/status``, ``PUT /api/voice/key``), the account's voices
(``GET /api/voice/voices``) and a voice made from text for an edit
(``POST /api/edits/{id}/voice``): the sound file lands in ``01_media/voice/`` with the
provider's character times next to it (``<name>.mp3.alignment.json``); as the narration it
replaces the recording and the edit is rebuilt with the timing taken from those times.

Mounted by ``app.py`` with the other routers that need the render engine. Tests put an
``httpx.MockTransport`` in :data:`TRANSPORT` (or replace :func:`make_client`), so no test
ever reaches the internet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from ..audio.errors import AudioError
from ..audio.ffmpeg import probe_duration
from ..edit import builder
from ..edit import media as media_lib
from ..edit.builder import BuildError, IncomingFile
from ..edit.renderer import STAGE_DIR_NAME, load_review_payload, read_timeline
from ..edit.timing_doc import TimingError
from ..models.project import Project
from ..models.voice import VoiceInfo, VoiceKeyUpdate, VoiceRequest, VoiceResult, VoiceStatus
from ..storage.files import atomic_write_bytes, atomic_write_text, safe_file_name, unique_path
from ..storage.project_store import ProjectStore
from ..voice.alignment import timing_from_alignment
from ..voice.elevenlabs import ElevenLabsClient, ProviderError, SpeechResult
from ..voice.secrets import key_hint, read_api_key, write_api_key
from .deps import JobsDep, SettingsDep, StoreDep
from .edits import load_project, translate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["voice"])

NO_KEY = "Add your ElevenLabs API key in Settings first."
RENDER_RUNNING = "A render is running for this edit. Wait for it to finish or stop it first."
SPEAK_TIMEOUT_S = 120.0
"""A long script takes up to a minute (docs/CONTRACT-v0.2.md section 5); twice that before
giving up on an answer."""
NAME_CHARS = 40
"""A file named from its text: the first characters of the text, slugified."""
TRANSPORT: httpx.BaseTransport | None = None
"""Tests put an ``httpx.MockTransport`` here; the app leaves it ``None`` (the internet)."""
VOICE_NAMES: dict[str, str] = {}
"""Voice ids to names from the last voices list, for the library's label of a voice."""


def make_client(api_key: str) -> ElevenLabsClient:
    """The provider's client for ``api_key`` (tests replace this or :data:`TRANSPORT`)."""
    return ElevenLabsClient(api_key, transport=TRANSPORT, timeout=SPEAK_TIMEOUT_S)


# The key ---------------------------------------------------------------------------------------


def _status(settings: Any) -> VoiceStatus:
    key = read_api_key(settings.app_data_dir)
    return VoiceStatus(key_set=bool(key), key_hint=key_hint(key))


def _key_or_422(settings: Any) -> str:
    key = read_api_key(settings.app_data_dir)
    if not key:
        raise HTTPException(status_code=422, detail=NO_KEY)
    return key


@router.get("/voice/status", response_model=VoiceStatus)
async def voice_status(settings: SettingsDep) -> VoiceStatus:
    """Whether an API key is saved on this laptop, and its last characters."""
    return await asyncio.to_thread(_status, settings)


@router.put("/voice/key", response_model=VoiceStatus)
async def put_voice_key(update: VoiceKeyUpdate, settings: SettingsDep) -> VoiceStatus:
    """Save the API key in ``<app_data_dir>/secrets.json``; an empty key removes it."""

    def work() -> VoiceStatus:
        write_api_key(settings.app_data_dir, update.api_key)
        return _status(settings)

    try:
        return await asyncio.to_thread(work)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The API key could not be saved: {exc}"
        ) from exc


# The voices ------------------------------------------------------------------------------------


@router.get("/voice/voices", response_model=list[VoiceInfo])
async def list_voices(settings: SettingsDep) -> list[VoiceInfo]:
    """The voices of the person's ElevenLabs account."""
    api_key = await asyncio.to_thread(_key_or_422, settings)
    try:
        voices = await asyncio.to_thread(make_client(api_key).voices)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    VOICE_NAMES.update({voice.id: voice.name for voice in voices})
    return voices


# A voice for an edit ---------------------------------------------------------------------------


def _refuse_while_rendering(jobs: Any, project_id: str) -> None:
    if jobs is not None and jobs.running_for(project_id) is not None:
        raise HTTPException(status_code=409, detail=RENDER_RUNNING)


def _slug(text: str) -> str:
    return "-".join(re.sub(r"[^\w]+", " ", text.lower()).split()).strip("-_")


def file_name_for(request: VoiceRequest) -> str:
    """``<name>.mp3``: the given name, else the first words of the text, else ``voice``."""
    base = request.name.strip() or _slug(request.text[:NAME_CHARS]) or "voice"
    if base.lower().endswith(".mp3"):
        base = base[:-4]
    return safe_file_name(base, fallback="voice") + ".mp3"


def _write_sidecar(
    target: Path, request: VoiceRequest, speech: SpeechResult, voice_name: str
) -> Path:
    """``<file>.alignment.json`` next to the voice: the text, the voice and the character
    times, so the library can label the file and the timing can be made again."""
    data = {
        "text": request.text,
        "voice": request.voice_id,
        "voice_name": voice_name,
        "model": speech.model_id,
        "speed": request.speed,
        "characters": speech.characters,
        "starts": speech.starts,
        "ends": speech.ends,
        "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    sidecar = target.with_name(target.name + media_lib.ALIGNMENT_SUFFIX)
    return atomic_write_text(sidecar, json.dumps(data, ensure_ascii=False) + "\n")


def make_voice(
    project: Project, request: VoiceRequest, api_key: str, store: ProjectStore, settings: Any,
    jobs: Any,
) -> VoiceResult:
    """The whole job, off the event loop: ask the provider once, save the file and its
    character times in ``01_media/voice/``, and, as the narration, rebuild the edit over it
    with the timing from those times."""
    speech = make_client(api_key).speak(request.text, request.voice_id, speed=request.speed)
    folder = Path(project.folder)
    voice_dir = media_lib.library_dir(folder) / media_lib.VOICE_SUBDIR
    target = unique_path(voice_dir, file_name_for(request))
    atomic_write_bytes(target, speech.audio)
    voice_name = (
        request.voice_name.strip() or VOICE_NAMES.get(request.voice_id) or request.voice_id
    )
    _write_sidecar(target, request, speech, voice_name)
    try:
        duration_s = round(probe_duration(target), 3)
    except AudioError as exc:
        raise BuildError(
            f"The voice was saved as {target.name} but its length could not be read: {exc}"
        ) from exc
    relative = target.relative_to(folder).as_posix()
    warnings: list[str] = []
    payload: dict[str, Any] | None = None
    if request.use == "narration":
        try:
            timing = timing_from_alignment(
                request.text, speech.characters, speech.starts, speech.ends,
                duration_s=duration_s, language=project.language,
                voice_id=request.voice_id, model_id=speech.model_id,
            )
            warnings.extend(timing.warnings)
            _timeline, build_warnings = builder.set_narration(
                project, store, settings, IncomingFile(name=target.name, path=target),
                timing=timing,
            )
        except (TimingError, BuildError) as exc:
            raise BuildError(f"{exc} The voice is in the library as {target.name}.") from exc
        warnings.extend(build_warnings)
        running = jobs.running_info(project.id) if jobs is not None else None
        payload = load_review_payload(project, settings, running=running)
    item = media_lib.item_for(
        project, settings, relative, read_timeline(folder / STAGE_DIR_NAME)
    )
    return VoiceResult(item=item, duration_s=duration_s, payload=payload, warnings=warnings)


@router.post("/edits/{project_id}/voice", response_model=VoiceResult)
async def make_voice_route(
    project_id: str, body: VoiceRequest, store: StoreDep, settings: SettingsDep, jobs: JobsDep,
) -> VoiceResult:
    """Make a voice from text and put it in the edit's library; as the narration, rebuild
    the edit over it with the timing from the provider's character times."""
    project = await load_project(store, project_id)
    _refuse_while_rendering(jobs, project.id)
    api_key = await asyncio.to_thread(_key_or_422, settings)
    try:
        return await asyncio.to_thread(
            make_voice, project, body, api_key, store, settings, jobs
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except media_lib.MediaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise translate(exc) from exc
