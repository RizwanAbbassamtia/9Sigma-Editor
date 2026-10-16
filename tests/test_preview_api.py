"""The live preview's routes (``api/preview.py``): the timeline bundle, thumbnails, peaks, one
sentence of the voice, the raw music track, the popup preview, the music stem and the fonts.
Signed-in people only (every signed-in person is equal); paths stay inside the edit's folder;
cache headers. An edit here is made straight in the store, its files written by hand: the
scene plan, ``timing.json``, a voice with a ramp in it and the pictures."""

from __future__ import annotations

import io
import json
import threading
import time
import wave
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sigma_editor.api.preview import inside_project, preview_frame
from sigma_editor.edit.builder import write_scene_plan
from sigma_editor.edit.renderer import write_timeline as save_timeline
from sigma_editor.models.timeline import Timeline
from sigma_editor.render import thumbs
from sigma_editor.render.fonts import FONT_FILES
from sigma_editor.render.popups import popup_position, popup_scale
from sigma_editor.render.presets import load_render_config
from sigma_editor.render.timeline_builder import (
    BuildRequest,
    build_timeline,
    parse_timing,
    resolve_scene_images,
)
from test_render_fixtures import (
    SENTENCES,
    make_storyboard,
    make_timing,
    requires_ffmpeg,
    write_music_track,
    write_picture,
)

RATE = 48000


def write_wav(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2").tobytes())
    return path


def write_sources(folder: Path, *, images: bool = True, voice: bool = True) -> dict:
    """The scene plan, the timing, a voice with a ramp in it and the pictures."""
    plan = make_storyboard()
    write_scene_plan(folder, plan)
    timing = make_timing()
    if voice:
        (folder / "05_voice" / "timing.json").write_text(json.dumps(timing), "utf-8")
        total = int(timing["duration_s"] * RATE)
        write_wav(folder / "05_voice" / "voice.wav", np.linspace(-0.5, 0.5, total))
    if images:
        for index in range(len(SENTENCES)):
            write_picture(folder / "06_images" / f"scene_{index + 1:02d}.png", "16:9", index + 1)
    return {"storyboard": plan, "timing": timing}


def write_timeline(folder: Path, sources: dict, music: Path | None = None) -> Timeline:
    """Build ``07_edit/timeline.json`` from the scene plan and the timing (the engine's own
    builder); ``music`` names a track, usually inside the edit's ``01_media`` folder."""
    config = load_render_config()
    request = BuildRequest(
        project_id="p", fmt="long", aspect="16:9", language="English", folder=folder,
        storyboard=sources["storyboard"], timing=parse_timing(sources["timing"]),
        sources=resolve_scene_images(folder, sources["storyboard"]), width=1920, height=1080,
        proxy=(854, 480), presets=[], config=config,
        music_choice=str(music) if music else "",
        music_folder=str(folder / "01_media"),
    )
    timeline, _ = build_timeline(request)
    save_timeline(folder / "07_edit", timeline)
    return timeline


# The bundle ------------------------------------------------------------------------------------


def test_the_bundle_has_everything_the_preview_needs(client: TestClient, edit) -> None:
    project_id, folder = edit
    timeline = write_timeline(folder, write_sources(folder))
    response = client.get(f"/api/projects/{project_id}/timeline")
    assert response.status_code == 200, response.text
    bundle = response.json()
    assert bundle["virtual"] is False and bundle["timing_estimated"] is False
    assert bundle["revision"] == 1 and bundle["timeline"]["revision"] == 1
    assert Timeline.model_validate(bundle["timeline"]) == timeline
    assert [s["id"] for s in bundle["sentences"]] == [sid for sid, _ in SENTENCES]
    first = bundle["sentences"][0]
    assert first["start_s"] == 0.0 and first["words"][0] == {
        "w": "The", "start_s": 0.0, "end_s": pytest.approx(0.1143, abs=1e-3)}
    assert bundle["sections"] == []  # no script in this app: nothing groups the scenes
    pictures = bundle["pictures"]
    assert [p["scene"] for p in pictures] == [0, 1, 2]
    assert pictures[0]["path"] == "06_images/scene_01.png"
    assert pictures[0]["width"] > 0 and pictures[0]["height"] > 0
    assert pictures[0]["thumb_url"] == (
        f"/api/projects/{project_id}/media/thumb?path=06_images/scene_01.png&h=128")
    media = bundle["media"]
    assert media["voice_url"] == f"/api/projects/{project_id}/files/05_voice/voice.wav"
    assert media["stem_url"] is None and media["envelope_url"] is None  # no music
    assert media["music_url"] is None
    families = {font["family"]: font["url"] for font in media["fonts"]}
    assert families["Noto Sans"] == "/api/fonts/NotoSans%5Bwdth%2Cwght%5D.ttf"
    assert len(families) == len(FONT_FILES)
    assert bundle["preview_gain_db"] is None
    # The builder's additions: sentence ids per scene, sentence clips, cue and popup ids.
    assert bundle["timeline"]["scenes"][0]["sentence_ids"] == [SENTENCES[0][0]]
    segments = bundle["timeline"]["voice"]["segments"]
    assert [s["sentence_id"] for s in segments] == [sid for sid, _ in SENTENCES]
    assert segments[1]["src_start_s"] == segments[1]["at_s"] == pytest.approx(1.7)
    assert [p["id"] for p in bundle["timeline"]["popups"]] == ["pop-001", "pop-002"]
    cue_ids = [c["id"] for c in bundle["timeline"]["captions"]["cues"]]
    assert cue_ids == ["cue-0001", "cue-0002", "cue-0003"]
    assert [w["w"] for w in bundle["cue_words"]["cue-0002"]] == SENTENCES[1][1].split()


def test_without_a_timeline_there_is_nothing_to_play(client: TestClient, edit) -> None:
    """No timeline is ever worked out on the fly: until the edit has ``timeline.json`` the
    bundle is a 404 that says so, and nothing is saved."""
    project_id, folder = edit
    url = f"/api/projects/{project_id}/timeline"
    missing = client.get(url)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "This edit has no timeline yet, so there is nothing to play."
    write_sources(folder)  # the scene plan, the voice and the pictures alone are not enough
    assert client.get(url).status_code == 404
    assert not (folder / "07_edit" / "timeline.json").exists()
    assert client.get("/api/projects/no-such-edit/timeline").status_code == 404


def test_the_bundle_says_when_the_times_are_estimates(client: TestClient, edit) -> None:
    project_id, folder = edit
    sources = write_sources(folder)
    write_timeline(folder, sources)
    (folder / "05_voice" / "timing.json").unlink()  # the narration timing went missing
    bundle = client.get(f"/api/projects/{project_id}/timeline").json()
    assert bundle["timing_estimated"] is True and bundle["virtual"] is False
    assert bundle["sentences"] == [] and len(bundle["timeline"]["scenes"]) == 3


# Thumbnails ------------------------------------------------------------------------------------


def test_thumbnails_are_small_jpegs_with_an_etag(client: TestClient, edit) -> None:
    project_id, folder = edit
    write_sources(folder, voice=False)
    url = f"/api/projects/{project_id}/media/thumb?path=06_images/scene_01.png"
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-cache"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.height == 128 and image.width == pytest.approx(128 * 16 / 9, abs=2)
    etag = response.headers["etag"]
    again = client.get(url, headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    tall = client.get(url + "&h=300")
    with Image.open(io.BytesIO(tall.content)) as image:
        assert image.height == 300
    assert tall.headers["etag"] != etag
    assert len(list((folder / "07_edit" / "preview" / "thumbs").glob("*.jpg"))) == 2
    # A picture replaced under the same name gets a new thumbnail (the old version's
    # thumbnails, at every height, are removed).
    Image.new("RGB", (64, 36), (250, 0, 0)).save(folder / "06_images" / "scene_01.png")
    fresh = client.get(url)
    assert fresh.headers["etag"] != etag
    with Image.open(io.BytesIO(fresh.content)) as image:
        assert image.height == 36  # never enlarged
        red = image.convert("RGB").getpixel((10, 10))
        assert red[0] > 200 and red[1] < 60
    assert len(list((folder / "07_edit" / "preview" / "thumbs").glob("*.jpg"))) == 1
    # every ask taller than the picture shares the one picture-sized JPEG
    taller = client.get(url + "&h=300")
    assert taller.headers["etag"] == fresh.headers["etag"]
    assert len(list((folder / "07_edit" / "preview" / "thumbs").glob("*.jpg"))) == 1


@pytest.mark.parametrize("path", [
    "../outside.png", "06_images/../../outside.png", "C:/Windows/win.ini", "/etc/passwd",
    "06_images/none.png", "05_voice\\..\\..\\x.png",
])
def test_thumbnails_stay_inside_the_edit(client: TestClient, edit, path: str) -> None:
    project_id, folder = edit
    write_sources(folder, voice=False)
    (folder.parent / "outside.png").write_bytes(b"")
    response = client.get(f"/api/projects/{project_id}/media/thumb?path={quote(path)}")
    assert response.status_code == 404


def test_thumbnails_only_of_pictures(client: TestClient, edit) -> None:
    project_id, folder = edit
    write_sources(folder)
    response = client.get(f"/api/projects/{project_id}/media/thumb?path=05_voice/timing.json")
    assert response.status_code == 404
    assert response.json()["detail"] == "That file is not a picture."
    assert client.get(
        f"/api/projects/{project_id}/media/thumb?path=06_images/scene_01.png&h=4"
    ).status_code == 422
    assert client.get(
        f"/api/projects/{project_id}/media/thumb?path=06_images/scene_01.png&h=2161"
    ).status_code == 422
    assert client.get("/api/projects/nope/media/thumb?path=x.png").status_code == 404


def test_tall_thumbnails_for_portrait_previews(client: TestClient, edit) -> None:
    """A 9:16 preview asks for pictures up to twice its height: up to 2160 come as JPEGs."""
    project_id, folder = edit
    write_sources(folder, voice=False)
    Image.new("RGB", (1200, 2400), (0, 90, 200)).save(folder / "06_images" / "tall.png")
    response = client.get(f"/api/projects/{project_id}/media/thumb?path=06_images/tall.png&h=1920")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(response.content)) as image:
        assert (image.width, image.height) == (960, 1920)
        large = sum(image.quantization[0])
    # the preview's pictures are kept closer to the picture than the strip's small ones
    small = client.get(f"/api/projects/{project_id}/media/thumb?path=06_images/tall.png&h=128")
    with Image.open(io.BytesIO(small.content)) as image:
        assert sum(image.quantization[0]) > 2 * large


def test_the_preview_heights_are_the_engine_s() -> None:
    """``render/thumbs.py`` works out the heights ``pictures.ts`` ``decodeHeight`` asks for
    (the same cases as the engine's tests, src/preview/__tests__/extras.test.ts)."""
    assert thumbs.decode_height(1536, 1024, 540, 864) == 1024
    assert thumbs.decode_height(4000, 3000, 540, 2250) == 1440
    assert thumbs.decode_height(6000, 2000, 540, 2000) == 640
    assert thumbs.decode_height(0, 0, 540, None) == 1080
    # a few steps, shared by nearby window sizes and by Half quality
    assert thumbs.decode_height(2048, 1152, 528, 1152) == 1080
    assert thumbs.decode_height(2048, 1152, 550, 1152) == 1080  # 2 % over a step still uses it
    assert thumbs.decode_height(0, 0, 600, None) == 1440  # size unknown yet
    assert thumbs.decode_height(4096, 2304, 600, 2304) == 1080  # never over 1920 wide
    assert thumbs.decode_height(2048, 1152, 270, 1152) == 540
    assert thumbs.decode_height(2048, 2048, 540, 1152) == 1920
    assert thumbs.step_height(1101) == 1080 and thumbs.step_height(1102) == 1440
    assert thumbs.step_height(2500) == 2500
    # Full at 960 x 540 and 1280 x 720, then Half of each; never taller than the picture
    assert thumbs.preview_heights((2048, 1152), (1920, 1080)) == [1080, 540, 720]
    assert thumbs.preview_heights((2048, 2048), (1920, 1080)) == [1920, 1080, 1440]
    assert thumbs.preview_heights((1024, 576), (1920, 1080)) == [576, 540]
    assert thumbs.preview_heights((1080, 1920), (1080, 1920)) == [1920, 1080, 1440]
    assert thumbs.preview_heights((0, 0), (1920, 1080)) == []


def test_the_bundle_has_the_preview_pictures_made_ahead(client: TestClient, edit,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /timeline`` makes the sized JPEGs the preview will ask for in the background, so
    a seek never waits for one to be made."""
    project_id, folder = edit
    timeline = write_timeline(folder, write_sources(folder))
    assert client.get(f"/api/projects/{project_id}/timeline").status_code == 200
    assert thumbs.wait_for_background(60)
    with Image.open(folder / "06_images" / "scene_02.png") as image:
        size = image.size
    heights = thumbs.preview_heights(size, (timeline.width, timeline.height))
    assert heights
    made = list((folder / "07_edit" / "preview" / "thumbs").glob("*.jpg"))
    assert len(made) == len(timeline.scenes) * len(heights)

    def made_again(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the JPEG was made again")

    monkeypatch.setattr(thumbs, "make_thumbnails", made_again)
    for height in heights:
        response = client.get(
            f"/api/projects/{project_id}/media/thumb?path=06_images/scene_02.png&h={height}")
        assert response.status_code == 200, response.text
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.height == height
    # asked again, nothing new is queued or made
    assert client.get(f"/api/projects/{project_id}/timeline").status_code == 200
    assert thumbs.wait_for_background(60)
    assert len(list((folder / "07_edit" / "preview" / "thumbs").glob("*.jpg"))) == len(made)


def test_two_requests_make_one_thumbnail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "proj"
    (folder / "06_images").mkdir(parents=True)
    source = (folder / "06_images" / "a.png").resolve()
    Image.new("RGB", (640, 360), (10, 200, 30)).save(source)
    real = thumbs.make_thumbnails
    calls: list[list[int]] = []
    gate = threading.Event()

    def slow(path: Path, heights: Iterable[int], done=None) -> dict[int, bytes]:  # noqa: ANN001
        calls.append(list(heights))
        gate.wait(5)
        return real(path, calls[-1], done)

    monkeypatch.setattr(thumbs, "make_thumbnails", slow)
    results: list[tuple[bytes, str]] = []

    def ask() -> None:
        results.append(thumbs.thumbnail(folder, source, 300))

    workers = [threading.Thread(target=ask) for _ in range(3)]
    for worker in workers:
        worker.start()
    time.sleep(0.3)
    gate.set()
    for worker in workers:
        worker.join(10)
    assert len(calls) == 1 and calls[0] == [300]
    assert len(results) == 3 and len({data for data, _etag in results}) == 1
    assert len(list((folder / "07_edit" / "preview" / "thumbs").glob("*.jpg"))) == 1


def test_the_maker_goes_on_from_the_picture_asked_for_and_waits_for_requests(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a jump the scenes next to it come first, and a request making a JPEG (someone is
    waiting for it) has the CPU to itself."""
    folder = tmp_path / "proj"
    (folder / "06_images").mkdir(parents=True)
    names = [f"06_images/scene_{index:02d}.png" for index in range(1, 7)]
    for name in names:
        Image.new("RGB", (1280, 720), (90, 90, 90)).save(folder / name)
    jobs = thumbs.picture_jobs(folder, names, (1920, 1080))
    assert [job.source.name for job in jobs] == [Path(name).name for name in names]
    made: list[tuple[str, float]] = []

    def record(source: Path, heights: Iterable[int], done=None) -> dict[int, bytes]:  # noqa: ANN001
        made.append((source.name, time.monotonic()))
        out = {}
        for height in heights:
            out[height] = b"jpeg"
            if done is not None:
                done(height, b"jpeg")
        return out

    monkeypatch.setattr(thumbs, "make_thumbnails", record)
    thumbs._asked(jobs[3].source)  # a request for scene 4
    released = threading.Event()

    def busy_request() -> None:
        with thumbs._on_demand():
            released.wait(5)

    worker = threading.Thread(target=busy_request)
    worker.start()
    time.sleep(0.05)
    started = time.monotonic()
    threading.Timer(0.4, released.set).start()
    assert thumbs.prepare_pictures(folder, jobs) == sum(len(job.heights) for job in jobs)
    worker.join(5)
    assert [name for name, _at in made] == [
        "scene_04.png", "scene_05.png", "scene_06.png", "scene_01.png", "scene_02.png",
        "scene_03.png"]
    assert made[0][1] - started >= 0.35  # it waited for the request


def test_a_picture_too_big_to_open_loses_only_its_scene(client: TestClient, edit,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, folder = edit
    write_timeline(folder, write_sources(folder))
    # every picture now counts as a decompression bomb (more than twice the limit)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    response = client.get(f"/api/projects/{project_id}/timeline")
    assert response.status_code == 200, response.text
    pictures = response.json()["pictures"]
    assert all(p["thumb_url"] is None and p["width"] == 0 for p in pictures)
    assert client.get(
        f"/api/projects/{project_id}/media/thumb?path=06_images/scene_01.png&h=540"
    ).status_code == 404
    assert thumbs.wait_for_background(30)


# Peaks -------------------------------------------------------------------------------------------


def test_voice_peaks_every_10_ms_cached_by_file(client: TestClient, edit) -> None:
    project_id, folder = edit
    timeline = write_timeline(folder, write_sources(folder))
    url = f"/api/projects/{project_id}/media/peaks/voice.json"
    response = client.get(url)
    assert response.status_code == 200, response.text
    peaks = response.json()
    voice_s = make_timing()["duration_s"]
    assert peaks["step_s"] == 0.01 and peaks["source"] == "voice"
    assert len(peaks["min"]) == len(peaks["max"]) == int(round(voice_s / 0.01))
    assert peaks["duration_s"] == pytest.approx(voice_s, abs=1e-3)
    assert peaks["min"][0] == pytest.approx(-0.5, abs=1e-3)  # the ramp starts at -0.5
    assert peaks["max"][-1] == pytest.approx(0.5, abs=1e-3)
    assert all(lo <= hi for lo, hi in zip(peaks["min"], peaks["max"], strict=True))
    assert client.get(url, headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    cached = list((folder / "07_edit" / "preview" / "peaks").glob("voice-*.json"))
    assert len(cached) == 1
    assert timeline.voice.path == "05_voice/voice.wav"
    music = client.get(f"/api/projects/{project_id}/media/peaks/music.json")
    assert music.status_code == 404
    assert music.json()["detail"] == "This video has no background music."
    assert client.get(f"/api/projects/{project_id}/media/peaks/other.json").status_code == 404


# One sentence of the voice -----------------------------------------------------------------------


def _samples(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as handle:
        assert handle.getframerate() == RATE
        return np.frombuffer(handle.readframes(handle.getnframes()), "<i2")


def test_sentence_audio_is_cut_once_from_the_voice(client: TestClient, edit) -> None:
    project_id, folder = edit
    write_timeline(folder, write_sources(folder))
    sid = SENTENCES[1][0]
    url = f"/api/projects/{project_id}/media/voice/segments/{sid}.wav"
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-cache"
    voice = _samples((folder / "05_voice" / "voice.wav").read_bytes())
    start, end = round(1.7 * RATE), round(3.3 * RATE)
    assert np.array_equal(_samples(response.content), voice[start:end])
    cached = list((folder / "07_edit" / "preview" / "voice").glob(f"{sid}-*.wav"))
    assert len(cached) == 1
    assert client.get(url).content == response.content  # split once, then served
    etag = response.headers["etag"]
    assert client.get(url, headers={"If-None-Match": etag}).status_code == 304
    # A sentence file of its own (the voice tool's) is served as it is.
    own = write_wav(folder / "05_voice" / "sentences" / f"{sid}.wav",
                    np.full(end - start, 0.25))
    assert client.get(url).content == own.read_bytes()
    unknown = client.get(f"/api/projects/{project_id}/media/voice/segments/s-99.wav")
    assert unknown.status_code == 404
    bad = client.get(f"/api/projects/{project_id}/media/voice/segments/..%2F..%2Fx.wav")
    assert bad.status_code == 404


def test_an_own_recording_s_clips_carry_their_pauses(client: TestClient, edit) -> None:
    """An own recording's voice clips cover the whole file; the sentence audio the preview
    plays reaches as far as its clip does."""
    project_id, folder = edit
    sources = write_sources(folder)
    sources["timing"]["own_recording"] = True
    (folder / "05_voice" / "timing.json").write_text(json.dumps(sources["timing"]), "utf-8")
    timeline = write_timeline(folder, sources)
    assert [(s.src_start_s, s.src_end_s, s.at_s) for s in timeline.voice.segments or []] == [
        (0.0, 1.7, 0.0), (1.7, 3.4, 1.7), (3.4, 5.0, 3.4)]
    voice = _samples((folder / "05_voice" / "voice.wav").read_bytes())
    base = f"/api/projects/{project_id}/media/voice/segments"
    first = client.get(f"{base}/{SENTENCES[0][0]}.wav")
    assert first.status_code == 200, first.text
    assert np.array_equal(_samples(first.content), voice[:round(1.7 * RATE)])
    # A sentence file of the sentence alone is too short for the clip: the clip is cut.
    sid = SENTENCES[1][0]
    write_wav(folder / "05_voice" / "sentences" / f"{sid}.wav",
              np.zeros(round(1.6 * RATE)))
    middle = client.get(f"{base}/{sid}.wav")
    assert np.array_equal(_samples(middle.content), voice[round(1.7 * RATE):round(3.4 * RATE)])


# Popup preview -----------------------------------------------------------------------------------


def test_popup_preview_draws_and_places_the_popup(client: TestClient, edit) -> None:
    project_id, folder = edit
    write_timeline(folder, write_sources(folder))
    body = {"text": "Five  every morning", "style": "", "position": "bottom-right",
            "anim": "pop", "aspect": "16:9", "height": 540}
    response = client.post(f"/api/projects/{project_id}/popups/preview", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"png_url", "x", "y", "w", "h"}
    prefix = f"/api/projects/{project_id}/files/07_edit/preview/popups/"
    assert result["png_url"].startswith(prefix)
    png = client.get(result["png_url"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(png.content)) as image:
        assert (image.width, image.height) == (result["w"], result["h"])
        assert image.mode == "RGBA"
    width, height = preview_frame("16:9", 540)
    assert (width, height) == (960, 540)
    config = load_render_config()
    scale = popup_scale(width, height)
    style_size, lines = 48, 2  # the captions are on: the popup sits above them
    reserved = round((style_size * lines * 1.4 + config.captions_margin_v_px) * scale)
    assert (result["x"], result["y"]) == popup_position(
        "bottom-right", (result["w"], result["h"]), (width, height),
        margin_px=round(config.popups.margin_px * scale), bottom_reserved_px=reserved)
    # The same popup again is the same picture; portrait frames are narrower.
    assert client.post(f"/api/projects/{project_id}/popups/preview", json=body).json() == result
    portrait = client.post(f"/api/projects/{project_id}/popups/preview",
                           json={**body, "aspect": "9:16", "height": 960}).json()
    assert portrait["png_url"] != result["png_url"]
    assert preview_frame("9:16", 960) == (540, 960)
    assert client.post(f"/api/projects/{project_id}/popups/preview",
                       json={**body, "text": ""}).status_code == 422
    assert client.post(f"/api/projects/{project_id}/popups/preview",
                       json={**body, "text": "   "}).status_code == 422


# Fonts -------------------------------------------------------------------------------------------


def test_fonts_are_served_from_an_allow_list(client: TestClient) -> None:
    name = FONT_FILES["Noto Sans"]
    response = client.get(f"/api/fonts/{quote(name, safe='')}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "font/ttf"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.content[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")
    for other in ("OFL.txt", "..%2Fpyproject.toml", "NotoSans.ttf", "x"):
        assert client.get(f"/api/fonts/{other}").status_code == 404


# Who may call what -------------------------------------------------------------------------------


def test_everything_needs_a_signed_in_person(anon_client: TestClient) -> None:
    for url in ("/api/projects/p/timeline", "/api/projects/p/media/thumb?path=a.png",
                "/api/projects/p/media/music", "/api/projects/p/media/peaks/voice.json",
                "/api/projects/p/media/voice/segments/s-1.wav",
                f"/api/fonts/{quote(FONT_FILES['Noto Sans'], safe='')}"):
        response = anon_client.get(url)
        assert response.status_code == 401, url
        assert response.json() == {"detail": "Please sign in."}
    assert anon_client.post("/api/projects/p/popups/preview",
                            json={"text": "x"}).status_code == 401
    assert anon_client.post("/api/projects/p/music/stem").status_code == 401


def test_inside_project_refuses_other_places(tmp_path: Path) -> None:
    folder = tmp_path / "proj"
    (folder / "05_voice").mkdir(parents=True)
    (folder / "05_voice" / "voice.wav").write_bytes(b"x")
    (tmp_path / "other.wav").write_bytes(b"x")
    assert inside_project(folder, "05_voice/voice.wav") == "05_voice/voice.wav"
    assert inside_project(folder, str(folder / "05_voice" / "voice.wav")) == "05_voice/voice.wav"
    assert inside_project(folder, "../other.wav") is None
    assert inside_project(folder, str(tmp_path / "other.wav")) is None
    assert inside_project(folder, "05_voice/none.wav") is None
    assert inside_project(folder, None) is None


# The music stem -----------------------------------------------------------------------------------


@requires_ffmpeg
def test_the_music_stem_is_made_for_the_timeline_and_reused(client: TestClient, edit) -> None:
    project_id, folder = edit
    track = write_music_track(folder / "01_media")
    timeline = write_timeline(folder, write_sources(folder), music=track)
    assert timeline.music.path == str(track)
    url = f"/api/projects/{project_id}/music/stem"
    response = client.post(url)
    assert response.status_code == 200, response.text
    result = response.json()
    stem = folder / "07_edit" / "music_ducked.wav"
    envelope = folder / "07_edit" / "music_envelope.json"
    assert stem.is_file() and envelope.is_file()
    assert result["stem_url"].startswith(
        f"/api/projects/{project_id}/files/07_edit/music_ducked.wav?v=")
    assert result["envelope_url"].startswith(
        f"/api/projects/{project_id}/files/07_edit/music_envelope.json?v=")
    assert result["preview_gain_db"] is None or -30 <= result["preview_gain_db"] <= 30
    doc = json.loads(envelope.read_text(encoding="utf-8"))
    assert doc["step_s"] == 0.01 and len(doc["gain_db"]) > 0
    made = stem.stat().st_mtime_ns
    assert client.post(url).json() == result and stem.stat().st_mtime_ns == made  # reused
    # The bundle now points at the stem; the timeline file itself was not changed.
    bundle = client.get(f"/api/projects/{project_id}/timeline").json()
    assert bundle["media"]["stem_url"] == result["stem_url"]
    assert bundle["media"]["envelope_url"] == result["envelope_url"]
    assert bundle["preview_gain_db"] == result["preview_gain_db"]
    if result["preview_gain_db"] is not None:  # loudnorm's own gain, every 100 ms
        curve = bundle["preview_gain_curve"]
        assert curve["step_s"] == 0.1
        assert len(curve["gain_db"]) == pytest.approx(timeline.duration_s * 10, abs=1)
    assert json.loads((folder / "07_edit" / "timeline.json").read_text("utf-8"))[
        "music"]["stem"] is None
    peaks = client.get(f"/api/projects/{project_id}/media/peaks/music.json")
    assert peaks.status_code == 200 and peaks.json()["source"] == "stem"
    # Another dip depth in the timeline: the old stem no longer counts (the music lane shows
    # the raw track until the new stem is made).
    changed = timeline.model_copy(deep=True)
    changed.music.duck_db = -20
    (folder / "07_edit" / "timeline.json").write_text(changed.model_dump_json(), "utf-8")
    assert client.get(f"/api/projects/{project_id}/timeline").json()["media"]["stem_url"] is None
    raw = client.get(f"/api/projects/{project_id}/media/peaks/music.json").json()
    assert raw["source"] == "track" and raw["duration_s"] == pytest.approx(2.0, abs=0.05)
    assert client.post(url).json()["stem_url"] != result["stem_url"]
    again = client.get(f"/api/projects/{project_id}/media/peaks/music.json").json()
    assert again["source"] == "stem"
    # The track gone from this PC and no current stem: nothing to draw yet.
    track.unlink()
    gone = client.get(f"/api/projects/{project_id}/media/peaks/music.json")
    assert gone.status_code == 404 and gone.json()["detail"] == (
        "The music for the preview is not ready yet.")


def test_the_raw_music_track_comes_from_the_edits_media_folder(client: TestClient,
                                                                 edit) -> None:
    """While the stem is not current the preview plays the timeline's own track, whose path
    is absolute (the edit's ``01_media`` folder): the bundle names it by ``media.music_url``
    and only a track inside the edit's folder is served."""
    project_id, folder = edit
    bundle_url = f"/api/projects/{project_id}/timeline"
    route = f"/api/projects/{project_id}/media/music"
    tone = 0.3 * np.sin(np.linspace(0.0, 900.0, RATE))
    # a track outside the edit's folder is never served, wherever the timeline points
    elsewhere = write_wav(folder.parent / "music" / "calm.wav", tone)
    sources = write_sources(folder)
    timeline = write_timeline(folder, sources, music=elsewhere)
    assert timeline.music.path == str(elsewhere)
    assert client.get(bundle_url).json()["media"]["music_url"] is None
    refused = client.get(route)
    assert refused.status_code == 404 and refused.json()["detail"] == (
        "The music track is not on this PC.")
    # the same track in the edit's 01_media folder plays
    track = write_wav(folder / "01_media" / "calm.wav", tone)
    timeline = write_timeline(folder, sources, music=track)
    assert timeline.music.path == str(track)
    media = client.get(bundle_url).json()["media"]
    assert media["stem_url"] is None
    assert media["music_url"].startswith(f"{route}?v=")
    response = client.get(media["music_url"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == track.read_bytes()
    piece = client.get(media["music_url"], headers={"Range": "bytes=0-99"})
    assert piece.status_code == 206 and len(piece.content) == 100
    assert client.get(route, headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    # a track replaced on disk gets a new URL
    write_wav(track, 0.2 * np.sin(np.linspace(0.0, 500.0, RATE // 2)))
    assert client.get(bundle_url).json()["media"]["music_url"] != media["music_url"]
    # a file outside the edit's folder named in the timeline is refused; no music is a 404
    other = write_wav(folder.parent / "other.wav", np.zeros(RATE // 10))
    changed = timeline.model_copy(deep=True)
    changed.music.path = str(other)
    (folder / "07_edit" / "timeline.json").write_text(changed.model_dump_json(), "utf-8")
    assert client.get(bundle_url).json()["media"]["music_url"] is None
    assert client.get(route).status_code == 404
    changed.music.path = None
    (folder / "07_edit" / "timeline.json").write_text(changed.model_dump_json(), "utf-8")
    silent = client.get(route)
    assert silent.status_code == 404 and silent.json()["detail"] == (
        "This video has no background music.")


def test_the_music_stem_needs_a_timeline(client: TestClient, edit) -> None:
    project_id, folder = edit
    write_sources(folder)
    response = client.post(f"/api/projects/{project_id}/music/stem")
    assert response.status_code == 404 and "timeline" in response.json()["detail"]
