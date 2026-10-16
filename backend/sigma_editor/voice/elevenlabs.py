"""ElevenLabs, the text-to-speech provider (docs/CONTRACT-v0.2.md section 5): the account's
voices, and a voice made from text with the time of every character, so the narration
timing can follow the voice exactly.

Every error is a :class:`ProviderError` in plain English. Nothing here retries: a request
that makes a voice costs the person's characters, so it runs once and reports.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from ..models.voice import VoiceInfo

BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_TIMEOUT_S = 60.0
CONNECT_TIMEOUT_S = 15.0
STABILITY = 0.5
SIMILARITY_BOOST = 0.75
DETAIL_CHARS = 200

BAD_KEY = "ElevenLabs did not accept the API key. Check it in Settings."
NO_CHARACTERS = "Your ElevenLabs plan has no characters left this month."
BUSY = "ElevenLabs is busy; try again in a moment."
TIMED_OUT = "ElevenLabs did not answer in time. Check the internet connection and try again."
UNREACHABLE = "ElevenLabs could not be reached. Check the internet connection."
UNREADABLE = "ElevenLabs sent an answer the app could not read. Try again."
NO_SOUND = "ElevenLabs sent no sound for this text. Try again."


class ProviderError(Exception):
    """Plain English, safe to show."""


@dataclass
class SpeechResult:
    """A voice made from text: the sound file's bytes and, when the provider sent them, the
    start and end of every character of the text (empty lists otherwise)."""

    audio: bytes
    characters: list[str] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)
    model_id: str = DEFAULT_MODEL
    voice_id: str = ""


class ElevenLabsClient:
    """One account's view of ElevenLabs. ``transport`` lets tests answer the requests
    themselves (``httpx.MockTransport``); the app leaves it ``None``."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.timeout = httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT_S, timeout))

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"xi-api-key": self.api_key, "accept": "application/json"}
        try:
            with httpx.Client(
                base_url=self.base_url, timeout=self.timeout, transport=self.transport,
                headers=headers,
            ) as client:
                response = client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderError(TIMED_OUT) from exc
        except httpx.HTTPError as exc:  # no connection, no route, a broken answer
            raise ProviderError(UNREACHABLE) from exc
        if response.status_code >= 400:
            raise error_for(response)
        return response

    def voices(self) -> list[VoiceInfo]:
        """The voices the account can use, as ElevenLabs lists them."""
        data = _json_of(self._request("GET", "/v1/voices"))
        rows = data.get("voices") if isinstance(data, dict) else None
        voices: list[VoiceInfo] = []
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, dict):
                continue
            voice_id = str(raw.get("voice_id") or "").strip()
            if not voice_id:
                continue
            labels = _labels_of(raw.get("labels"))
            description = str(raw.get("description") or "").strip()
            preview = str(raw.get("preview_url") or "").strip()
            voices.append(
                VoiceInfo(
                    id=voice_id,
                    name=str(raw.get("name") or "").strip() or voice_id,
                    description=description or ", ".join(labels.values()),
                    preview_url=preview or None,
                    labels=labels,
                )
            )
        return voices

    def speak(
        self,
        text: str,
        voice_id: str,
        *,
        speed: float = 1.0,
        model_id: str = DEFAULT_MODEL,
        output_format: str = DEFAULT_OUTPUT_FORMAT,
    ) -> SpeechResult:
        """``text`` spoken by ``voice_id``, with the time of every character. One request,
        never repeated: it costs characters."""
        body = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": STABILITY,
                "similarity_boost": SIMILARITY_BOOST,
                "speed": round(float(speed), 3),
            },
        }
        response = self._request(
            "POST",
            f"/v1/text-to-speech/{quote(voice_id, safe='')}/with-timestamps",
            params={"output_format": output_format},
            json=body,
        )
        data = _json_of(response)
        if not isinstance(data, dict):
            raise ProviderError(UNREADABLE)
        characters, starts, ends = _alignment_of(data)
        return SpeechResult(
            audio=_audio_of(data.get("audio_base64")),
            characters=characters,
            starts=starts,
            ends=ends,
            model_id=model_id,
            voice_id=voice_id,
        )


# The answers -----------------------------------------------------------------------------------


def _json_of(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(UNREADABLE) from exc


def _labels_of(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): str(value).strip()
        for key, value in raw.items()
        if value is not None and str(value).strip()
    }


def _audio_of(raw: Any) -> bytes:
    if not isinstance(raw, str) or not raw.strip():
        raise ProviderError(NO_SOUND)
    try:
        audio = base64.b64decode(raw)
    except (binascii.Error, ValueError) as exc:
        raise ProviderError(NO_SOUND) from exc
    if not audio:
        raise ProviderError(NO_SOUND)
    return audio


def _alignment_of(data: dict[str, Any]) -> tuple[list[str], list[float], list[float]]:
    """The characters and their times from ``alignment`` (else ``normalized_alignment``);
    empty lists when the answer has none or they do not line up (the caller estimates)."""
    raw = data.get("alignment") or data.get("normalized_alignment")
    if not isinstance(raw, dict):
        return [], [], []
    try:
        characters = [str(item) for item in raw.get("characters") or []]
        starts = [float(item) for item in raw.get("character_start_times_seconds") or []]
        ends = [float(item) for item in raw.get("character_end_times_seconds") or []]
    except (TypeError, ValueError):
        return [], [], []
    if not characters or not (len(characters) == len(starts) == len(ends)):
        return [], [], []
    return characters, starts, ends


def error_for(response: httpx.Response) -> ProviderError:
    """The plain-English error for an answer of 400 or more."""
    status = response.status_code
    code, message = _detail_of(response)
    if status == 402 or "quota" in code.lower():  # quota_exceeded comes as 401 too
        return ProviderError(NO_CHARACTERS)
    if status == 401:
        return ProviderError(BAD_KEY)
    if status == 429:
        return ProviderError(BUSY)
    text = " ".join((message or response.text or "").split())
    if len(text) > DETAIL_CHARS:
        text = text[:DETAIL_CHARS].rstrip() + "..."
    if not text:
        return ProviderError(f"ElevenLabs answered {status}.")
    return ProviderError(f"ElevenLabs answered {status}: {text}")


def _detail_of(response: httpx.Response) -> tuple[str, str]:
    """ElevenLabs's ``detail``: ``{status, message}``, a string, or a list of problems."""
    try:
        data = response.json()
    except ValueError:
        return "", ""
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("status") or ""), str(detail.get("message") or "")
    if isinstance(detail, str):
        return "", detail
    if isinstance(detail, list):
        messages = [str(item.get("msg") or "") for item in detail if isinstance(item, dict)]
        return "", "; ".join(message for message in messages if message)
    return "", ""


__all__ = [
    "BAD_KEY", "BASE_URL", "BUSY", "DEFAULT_MODEL", "DEFAULT_OUTPUT_FORMAT", "NO_CHARACTERS",
    "ElevenLabsClient", "ProviderError", "SpeechResult", "TIMED_OUT", "UNREACHABLE", "error_for",
]
