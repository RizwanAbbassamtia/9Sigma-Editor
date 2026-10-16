"""The AI voice (docs/CONTRACT-v0.2.md section 5) against a fake ElevenLabs: the API key
kept on this laptop, the voices, a voice made from text as a sound and as the narration
(with the timing from the provider's character times), and the plain-English errors. No
test reaches the internet: ``api/voice.py``'s ``TRANSPORT`` answers every request."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from sigma_editor.api import voice as voice_api
from sigma_editor.config import repo_root
from sigma_editor.voice import elevenlabs
from sigma_editor.voice.alignment import MISMATCH, NO_TIMES, timing_from_alignment
from sigma_editor.voice.elevenlabs import ElevenLabsClient, ProviderError
from sigma_editor.voice.secrets import key_hint, read_api_key, write_api_key
from test_render_fixtures import make_render_tmp_fixture, requires_ffmpeg, write_picture

render_tmp = make_render_tmp_fixture()

KEY = "sk_test_0123456789abcdef"
TEXT = "Hello there. Welcome back."
STEP_S = 0.075
"""Each character of TEXT takes 75 ms in the fake alignment: 26 characters over 1.95 s."""
NO_KEY = "Add your ElevenLabs API key in Settings first."
VOICES = {
    "voices": [
        {
            "voice_id": "v-rachel",
            "name": "Rachel",
            "description": "Calm and clear",
            "preview_url": "https://example.test/rachel.mp3",
            "labels": {"accent": "american", "gender": "female"},
        },
        {"voice_id": "v-adam", "name": "Adam", "labels": {"accent": "british", "age": None}},
    ]
}


def alignment_for(text: str, step: float = STEP_S) -> dict[str, Any]:
    """The provider's character times for ``text``: one character every ``step`` seconds."""
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(i * step, 3) for i in range(len(text))],
        "character_end_times_seconds": [round((i + 1) * step, 3) for i in range(len(text))],
    }


def times_of(alignment: dict[str, Any]) -> tuple[list[str], list[float], list[float]]:
    return (
        alignment["characters"],
        alignment["character_start_times_seconds"],
        alignment["character_end_times_seconds"],
    )


def make_mp3(path: Path, seconds: float = 2.0) -> bytes:
    """A real, silent mp3 of ``seconds`` (ffmpeg's anullsrc)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(seconds),
         "-c:a", "libmp3lame", "-b:a", "64k", str(path)],
        check=True, capture_output=True,
    )
    return path.read_bytes()


class FakeElevenLabs:
    """Answers the provider's two requests. ``error`` (a status and a JSON body) replaces
    every answer; a wrong key is refused like the real service does."""

    def __init__(self) -> None:
        self.audio = b""
        self.alignment: dict[str, Any] | None = None
        self.error: tuple[int, Any] | None = None
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            status, body = self.error
            return httpx.Response(status, json=body)
        if request.headers.get("xi-api-key") != KEY:
            return httpx.Response(
                401, json={"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}
            )
        path = request.url.path
        if path == "/v1/voices":
            return httpx.Response(200, json=VOICES)
        if path.startswith("/v1/text-to-speech/") and path.endswith("/with-timestamps"):
            answer: dict[str, Any] = {"audio_base64": base64.b64encode(self.audio).decode("ascii")}
            if self.alignment is not None:
                answer["alignment"] = self.alignment
            return httpx.Response(200, json=answer)
        return httpx.Response(404, json={"detail": "not found"})

    def speech_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if "text-to-speech" in r.url.path]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeElevenLabs:
    provider = FakeElevenLabs()
    monkeypatch.setattr(voice_api, "TRANSPORT", httpx.MockTransport(provider))
    monkeypatch.setattr(voice_api, "VOICE_NAMES", {})
    return provider


def blank(client: TestClient, name: str = "Voice test") -> dict[str, Any]:
    response = client.post(
        "/api/edits/blank", json={"name": name, "format": "long", "language": "English"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def set_key(client: TestClient, key: str = KEY) -> dict[str, Any]:
    response = client.put("/api/voice/key", json={"api_key": key})
    assert response.status_code == 200, response.text
    return response.json()


def speak(client: TestClient, project_id: str, **fields: Any) -> httpx.Response:
    body = {"text": TEXT, "voice_id": "v-rachel", **fields}
    return client.post(f"/api/edits/{project_id}/voice", json=body)


# The key ---------------------------------------------------------------------------------------


def test_the_key_lives_in_secrets_json_on_this_laptop_only(
    client: TestClient, data_dirs: dict[str, Path]
) -> None:
    status = client.get("/api/voice/status")
    assert status.status_code == 200, status.text
    assert status.json() == {
        "provider": "elevenlabs", "provider_label": "ElevenLabs", "key_set": False, "key_hint": "",
    }
    saved = set_key(client, f"  {KEY} ")
    assert saved["key_set"] is True and saved["key_hint"] == "...cdef"
    assert saved["provider"] == "elevenlabs"
    assert client.get("/api/voice/status").json()["key_hint"] == "...cdef"

    secrets = data_dirs["app"] / "secrets.json"
    assert json.loads(secrets.read_text(encoding="utf-8")) == {"elevenlabs_api_key": KEY}
    assert repo_root() not in secrets.resolve().parents
    assert not list(repo_root().glob("secrets.json"))
    assert KEY not in (data_dirs["app"] / "settings.json").read_text(encoding="utf-8")
    assert KEY not in client.get("/api/settings").text
    assert KEY not in client.get("/api/system/info").text
    assert not list(data_dirs["projects"].rglob("secrets.json"))

    removed = set_key(client, "")
    assert removed["key_set"] is False and removed["key_hint"] == ""
    assert json.loads(secrets.read_text(encoding="utf-8")) == {}
    assert read_api_key(data_dirs["app"]) == ""
    assert set_key(client, "   ")["key_set"] is False

    # the helpers themselves: other entries of the file are kept, the hint is the tail
    secrets.write_text(json.dumps({"other": 1}), encoding="utf-8")
    write_api_key(data_dirs["app"], "k-1234")
    assert read_api_key(data_dirs["app"]) == "k-1234"
    assert json.loads(secrets.read_text(encoding="utf-8")) == {
        "other": 1, "elevenlabs_api_key": "k-1234",
    }
    assert key_hint("") == "" and key_hint("abc") == "...abc" and key_hint(KEY) == "...cdef"
    assert read_api_key(data_dirs["app"] / "nowhere") == ""


# The voices ------------------------------------------------------------------------------------


def test_voices_need_a_key_then_come_from_the_account(
    client: TestClient, fake: FakeElevenLabs
) -> None:
    refused = client.get("/api/voice/voices")
    assert refused.status_code == 422 and refused.json()["detail"] == NO_KEY
    assert fake.requests == []

    set_key(client)
    voices = client.get("/api/voice/voices")
    assert voices.status_code == 200, voices.text
    rows = voices.json()
    assert [(v["id"], v["name"]) for v in rows] == [("v-rachel", "Rachel"), ("v-adam", "Adam")]
    assert rows[0]["description"] == "Calm and clear"
    assert rows[0]["preview_url"] == "https://example.test/rachel.mp3"
    assert rows[0]["labels"] == {"accent": "american", "gender": "female"}
    assert rows[1]["description"] == "british" and rows[1]["preview_url"] is None
    assert rows[1]["labels"] == {"accent": "british"}
    assert fake.requests[-1].url.path == "/v1/voices"
    assert fake.requests[-1].headers["xi-api-key"] == KEY
    assert fake.requests[-1].url.host == "api.elevenlabs.io"


def test_provider_errors_are_plain_english(client: TestClient, fake: FakeElevenLabs) -> None:
    set_key(client, "sk_wrong_key")
    refused = client.get("/api/voice/voices")
    assert refused.status_code == 502
    assert refused.json()["detail"] == (
        "ElevenLabs did not accept the API key. Check it in Settings."
    )

    def message(status: int, body: Any) -> str:
        fake.error = (status, body)
        with pytest.raises(ProviderError) as caught:
            ElevenLabsClient(KEY, transport=httpx.MockTransport(fake)).voices()
        return str(caught.value)

    quota = {"detail": {"status": "quota_exceeded", "message": "You have 12 characters left"}}
    assert message(401, quota) == "Your ElevenLabs plan has no characters left this month."
    assert message(402, {"detail": "Payment required"}) == elevenlabs.NO_CHARACTERS
    assert message(429, {"detail": {"status": "too_many", "message": "Slow down"}}) == (
        "ElevenLabs is busy; try again in a moment."
    )
    assert message(500, {"detail": {"status": "internal", "message": "Server fell over"}}) == (
        "ElevenLabs answered 500: Server fell over"
    )
    problems = {"detail": [{"loc": ["body", "text"], "msg": "text is too long"}]}
    assert message(422, problems) == "ElevenLabs answered 422: text is too long"
    assert message(503, {"detail": "x" * 300}) == "ElevenLabs answered 503: " + "x" * 200 + "..."
    assert message(418, {}) == "ElevenLabs answered 418: {}"

    def failing(exc: Exception) -> str:
        def raise_it(request: httpx.Request) -> httpx.Response:
            raise exc

        with pytest.raises(ProviderError) as caught:
            ElevenLabsClient(KEY, transport=httpx.MockTransport(raise_it)).voices()
        return str(caught.value)

    assert failing(httpx.ReadTimeout("slow")) == (
        "ElevenLabs did not answer in time. Check the internet connection and try again."
    )
    assert failing(httpx.ConnectError("no route")) == (
        "ElevenLabs could not be reached. Check the internet connection."
    )

    # an answer without sound, and one without word times (the caller estimates then)
    def speak_with(answer: dict[str, Any]) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=answer)

        return ElevenLabsClient(KEY, transport=httpx.MockTransport(handler)).speak("Hi.", "v")

    with pytest.raises(ProviderError, match="no sound"):
        speak_with({"audio_base64": ""})
    bare = speak_with({"audio_base64": base64.b64encode(b"mp3").decode("ascii")})
    assert bare.audio == b"mp3" and bare.characters == [] and bare.starts == []
    normalised = speak_with({
        "audio_base64": base64.b64encode(b"mp3").decode("ascii"),
        "alignment": None,
        "normalized_alignment": alignment_for("Hi."),
    })
    assert normalised.characters == ["H", "i", "."] and normalised.ends[-1] == 0.225


# A voice for an edit ---------------------------------------------------------------------------


@requires_ffmpeg
def test_a_voice_as_a_sound_lands_in_the_library(
    client: TestClient, fake: FakeElevenLabs, render_tmp: Path
) -> None:
    project = blank(client)
    folder = Path(project["folder"])
    refused = speak(client, project["id"])
    assert refused.status_code == 422 and refused.json()["detail"] == NO_KEY
    assert fake.requests == []

    set_key(client)
    assert client.get("/api/voice/voices").status_code == 200  # the editor lists them first
    fake.audio = make_mp3(render_tmp / "fake.mp3")
    fake.alignment = alignment_for(TEXT)
    made = speak(client, project["id"], speed=1.1, use="sound", at_s=3.0)
    assert made.status_code == 200, made.text
    result = made.json()
    item = result["item"]
    assert item["path"] == "01_media/voice/hello-there-welcome-back.mp3"
    assert item["name"] == "hello-there-welcome-back.mp3" and item["kind"] == "audio"
    assert item["ai_voice"] is True and item["text"] == TEXT and item["voice"] == "Rachel"
    assert 1.9 <= item["duration_s"] <= 2.2 and 1.9 <= result["duration_s"] <= 2.2
    assert item["url"].startswith("/api/projects/") and item["in_use"] == []
    assert result["payload"] is None and result["warnings"] == []

    mp3 = folder / "01_media" / "voice" / "hello-there-welcome-back.mp3"
    assert mp3.read_bytes() == fake.audio
    sidecar = json.loads(mp3.with_name(mp3.name + ".alignment.json").read_text(encoding="utf-8"))
    assert sidecar["text"] == TEXT and sidecar["voice"] == "v-rachel"
    assert sidecar["voice_name"] == "Rachel" and sidecar["model"] == "eleven_multilingual_v2"
    assert sidecar["speed"] == 1.1 and sidecar["characters"] == list(TEXT)
    assert len(sidecar["starts"]) == len(TEXT) == len(sidecar["ends"]) and sidecar["made_at"]

    # what was asked of the provider: one paid request, the text, the model and the speed
    assert len(fake.speech_requests()) == 1
    request = fake.speech_requests()[0]
    assert request.url.path == "/v1/text-to-speech/v-rachel/with-timestamps"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == KEY
    sent = json.loads(request.content)
    assert sent["text"] == TEXT and sent["model_id"] == "eleven_multilingual_v2"
    assert sent["voice_settings"] == {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.1}

    # the library lists it; a name of its own; the same text again gets its own file
    library = client.get(f"/api/edits/{project['id']}/media").json()
    assert [i["name"] for i in library["items"]] == ["hello-there-welcome-back.mp3"]
    assert library["items"][0]["ai_voice"] is True and library["items"][0]["voice"] == "Rachel"
    named = speak(client, project["id"], name="Intro: take 2", voice_id="v-new")
    assert named.status_code == 200, named.text
    assert named.json()["item"]["path"] == "01_media/voice/Intro_ take 2.mp3"
    assert named.json()["item"]["voice"] == "v-new"  # not in the voices list: the id
    again = speak(client, project["id"])
    assert again.json()["item"]["path"] == "01_media/voice/hello-there-welcome-back-2.mp3"
    voices_dir = folder / "01_media" / "voice"
    assert (voices_dir / "hello-there-welcome-back-2.mp3.alignment.json").is_file()
    # a sound leaves the edit itself alone: still no scenes, no narration
    payload = client.get(f"/api/edits/{project['id']}/edit").json()
    assert payload["summary"]["scene_count"] == 0 and payload["timing_source"] == "scenes"
    # the voice can be removed from the library with its sidecar
    gone = client.delete(
        f"/api/edits/{project['id']}/media", params={"path": "01_media/voice/Intro_ take 2.mp3"}
    )
    assert gone.status_code == 200, gone.text
    assert not list((folder / "01_media" / "voice").glob("Intro*"))

    # a provider error on the way is a 502 with the plain message
    fake.error = (429, {"detail": {"status": "too_many", "message": "Slow down"}})
    busy = speak(client, project["id"])
    assert busy.status_code == 502
    assert busy.json()["detail"] == "ElevenLabs is busy; try again in a moment."
    # bad requests never reach the provider
    fake.error = None
    before = len(fake.requests)
    assert speak(client, project["id"], text="  ").status_code == 422
    assert speak(client, project["id"], text="x" * 5001).status_code == 422
    assert speak(client, project["id"], speed=2.0).status_code == 422
    assert speak(client, project["id"], use="music").status_code == 422
    missing = client.post("/api/edits/nope/voice", json={"text": TEXT, "voice_id": "v"})
    assert missing.status_code == 404
    assert len(fake.requests) == before


@requires_ffmpeg
def test_a_voice_as_the_narration_times_the_edit_exactly(
    client: TestClient, fake: FakeElevenLabs, render_tmp: Path
) -> None:
    project = blank(client, "Narrated")
    folder = Path(project["folder"])
    pictures = [write_picture(render_tmp / "in" / f"p{n}.png", "16:9", n) for n in (1, 2)]
    parts = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in pictures]
    assert client.post(f"/api/edits/{project['id']}/media", files=parts).status_code == 200
    for name in ("p1.png", "p2.png"):
        added = client.post(
            f"/api/edits/{project['id']}/scenes", data={"media": f"01_media/{name}"}
        )
        assert added.status_code == 200, added.text
    assert client.get(f"/api/edits/{project['id']}/edit").json()["timing_source"] == "scenes"

    set_key(client)
    fake.audio = make_mp3(render_tmp / "fake.mp3")
    fake.alignment = alignment_for(TEXT)
    made = speak(client, project["id"], use="narration", voice_name="Rachel")
    assert made.status_code == 200, made.text
    result = made.json()
    assert result["item"]["path"] == "01_media/voice/hello-there-welcome-back.mp3"
    assert result["item"]["ai_voice"] is True and result["item"]["voice"] == "Rachel"
    assert 1.9 <= result["duration_s"] <= 2.2
    assert len(fake.speech_requests()) == 1

    # the editor payload came back, rebuilt over the voice
    payload = result["payload"]
    assert payload is not None and payload["timing_source"] == "aligned"
    assert payload["summary"]["scene_count"] == 2
    timeline = payload["timeline"]
    assert timeline["voice"]["path"] == "05_voice/voice.wav"
    segments = timeline["voice"]["segments"]
    assert [s["sentence_id"] for s in segments] == ["s-001", "s-002"]
    assert segments[0]["src_start_s"] == 0.0 and segments[0]["src_end_s"] == 0.975
    assert segments[1]["src_start_s"] == 0.975 and segments[1]["at_s"] == 0.975
    assert len(timeline["scenes"]) == 2
    assert abs(timeline["scenes"][1]["start_s"] - 0.975) < 0.05
    assert payload == client.get(f"/api/edits/{project['id']}/edit").json()

    # the timing: the sentences and words exactly as the provider timed the characters
    timing = client.get(f"/api/edits/{project['id']}/timing").json()
    assert timing["source"] == "aligned" and timing["timing_confidence"] == "high"
    assert timing["provider"] == "elevenlabs" and timing["voice_id"] == "v-rachel"
    assert timing["model"] == "eleven_multilingual_v2" and timing["own_recording"] is False
    assert timing["language"] == "English" and timing["warnings"] == []
    assert 1.9 <= timing["duration_s"] <= 2.2
    first, second = timing["sentences"]
    assert [first["text"], second["text"]] == ["Hello there.", "Welcome back."]
    assert first["words_source"] == "provider" and second["words_source"] == "provider"
    assert [(w["text"], w["start_s"], w["end_s"]) for w in first["words"]] == [
        ("Hello", 0.0, 0.375), ("there.", 0.45, 0.9),
    ]
    assert [(w["text"], w["start_s"], w["end_s"]) for w in second["words"]] == [
        ("Welcome", 0.975, 1.5), ("back.", 1.575, 1.95),
    ]
    assert all(w["confidence"] == 1.0 for s in timing["sentences"] for w in s["words"])
    assert (first["start_frame"], first["end_frame"]) == (0, 46800)
    assert second["start_frame"] == 46800
    assert second["end_frame"] == round(timing["duration_s"] * 48000)
    assert (first["start_s"], first["end_s"]) == (0.0, 0.975)
    assert second["start_s"] == 0.975 and second["end_s"] == timing["duration_s"]

    # the files: the voice converted for the engine, the original kept, the library item
    assert (folder / "05_voice" / "voice.wav").is_file()
    assert (folder / "05_voice" / "original-hello-there-welcome-back.mp3").is_file()
    assert (folder / "01_media" / "voice" / "hello-there-welcome-back.mp3").is_file()
    assert client.get("/api/edits").json()[0]["status"] == "ready"

    # word times that do not fit the text: the voice still lands, the timing is estimated
    fake.alignment = alignment_for("Something else entirely.")
    again = speak(client, project["id"], use="narration", name="take2")
    assert again.status_code == 200, again.text
    assert again.json()["warnings"] == [MISMATCH]
    assert again.json()["payload"]["timing_source"] == "estimated"
    timing = client.get(f"/api/edits/{project['id']}/timing").json()
    assert [s["text"] for s in timing["sentences"]] == ["Hello there.", "Welcome back."]
    assert timing["source"] == "estimated" and timing["warnings"] == [MISMATCH]


# The timing from the character times -----------------------------------------------------------


def test_timing_from_alignment_words_frames_and_fallback() -> None:
    doc = timing_from_alignment(
        TEXT, *times_of(alignment_for(TEXT)), duration_s=2.0, language="English",
        voice_id="v-rachel", model_id="m",
    )
    assert doc.source == "aligned" and doc.timing_confidence == "high"
    assert doc.duration_s == 2.0 and doc.sample_rate == 48000
    assert [s.text for s in doc.sentences] == ["Hello there.", "Welcome back."]
    first, second = doc.sentences
    assert [(w.text, w.start_s, w.end_s) for w in first.words] == [
        ("Hello", 0.0, 0.375), ("there.", 0.45, 0.9),
    ]
    assert [(w.text, w.start_s, w.end_s) for w in second.words] == [
        ("Welcome", 0.975, 1.5), ("back.", 1.575, 1.95),
    ]
    assert (first.start_frame, first.end_frame) == (0, 46800)
    assert (second.start_frame, second.end_frame) == (46800, 96000)
    assert (first.start_s, first.end_s, second.start_s, second.end_s) == (0.0, 0.975, 0.975, 2.0)
    assert (first.id, second.id) == ("s-001", "s-002")
    assert first.words_source == "provider" and all(w.confidence == 1.0 for w in first.words)
    assert doc.provider == "elevenlabs" and doc.voice_id == "v-rachel" and doc.model == "m"
    assert doc.language == "English" and doc.own_recording is False
    assert doc.warnings == [] and doc.generated_at is not None and doc.nudged is False

    # the provider's leading space, newlines and extra blanks are skipped; paragraphs kept
    text = "One two.\n\nThree  four."
    spoken = " One two.\n\nThree four. \n"
    starts = [round(i * 0.1, 3) for i in range(len(spoken))]
    ends = [round((i + 1) * 0.1, 3) for i in range(len(spoken))]
    doc = timing_from_alignment(text, list(spoken), starts, ends, duration_s=2.5)
    assert [(s.text, s.paragraph) for s in doc.sentences] == [("One two.", 0), ("Three four.", 1)]
    assert [w.text for w in doc.sentences[1].words] == ["Three", "four."]
    assert doc.sentences[0].words[0].start_s == 0.1  # the leading space is not the word
    assert doc.sentences[1].start_frame == round(doc.sentences[1].words[0].start_s * 48000)
    assert doc.sentences[1].end_frame == 120000 and doc.duration_s == 2.5

    # times that run backwards are straightened, words stay inside their sentence, and the
    # recording is never shorter than its last word
    chars = list("Go. Now.")
    starts = [0.0, 0.1, 0.2, 0.3, 0.9, 0.8, 0.7, 0.6]
    ends = [0.1, 0.2, 0.3, 0.4, 1.0, 0.9, 0.8, 0.7]
    doc = timing_from_alignment("Go. Now.", chars, starts, ends, duration_s=0.5)
    go, now = doc.sentences
    assert (go.start_s, go.end_s) == (0.0, 0.9) and go.words[0].end_s == 0.3
    assert (now.start_s, now.end_s) == (0.9, 0.9)
    assert now.words[0].start_s == 0.9 and now.words[0].end_s == 0.9
    assert doc.duration_s == 0.9 and now.end_frame == 43200
    for sentence in doc.sentences:
        for word in sentence.words:
            assert sentence.start_s <= word.start_s <= word.end_s <= sentence.end_s

    # a mismatch (the provider spoke another text) falls back to the estimate with a warning
    doc = timing_from_alignment(
        TEXT, *times_of(alignment_for("Something else entirely.")), duration_s=2.0,
        language="English", voice_id="v-rachel", model_id="m",
    )
    assert doc.source == "estimated" and doc.timing_confidence == "low"
    assert doc.warnings == [MISMATCH]
    assert [s.text for s in doc.sentences] == ["Hello there.", "Welcome back."]
    assert doc.duration_s == 2.0 and doc.sentences[-1].end_s == 2.0
    assert doc.provider == "elevenlabs" and doc.voice_id == "v-rachel" and doc.model == "m"
    assert doc.own_recording is False and doc.sentences[0].words_source == "estimated"
    # no times at all, lists that do not line up, and more speech than text
    assert timing_from_alignment(TEXT, [], [], [], duration_s=2.0).warnings == [NO_TIMES]
    short = timing_from_alignment(TEXT, list(TEXT), [0.0] * 3, [0.1] * 3, duration_s=2.0)
    assert short.source == "estimated" and short.warnings == [MISMATCH]
    extra = timing_from_alignment(TEXT, *times_of(alignment_for(TEXT + " Bye.")), duration_s=2.0)
    assert extra.warnings == [MISMATCH]
    # a provider that spoke the text with different punctuation does not fit either
    other = timing_from_alignment(TEXT, *times_of(alignment_for(TEXT.replace(".", "!"))),
                                  duration_s=2.0)
    assert other.warnings == [MISMATCH]
