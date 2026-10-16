"""The music stem (docs/M5-CONTRACT.md section 3.1): one ducking function for the renderer and
the live preview. The envelope maths (look-ahead attack, release, reading it back), where
the voice lands on the timeline, then real (tiny) FFmpeg work: the stem dips by ``duck_db``
where the voice is loud (within 0.5 dB), is exactly as long as the timeline, is only made
again when something it is made of changed, and a render that mixes it is as loud (within
1 LU) as the old ``sidechaincompress`` mix. The preview's loudness follows ``loudnorm``'s own
gain curve (within 0.3 dB per 400 ms where one gain is more than 1 dB off)."""

from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from sigma_editor.edit.renderer import load_review_payload
from sigma_editor.models.timeline import Timeline
from sigma_editor.render import ffmpeg as ff
from sigma_editor.render.music import (
    AUDIO_RATE,
    ENVELOPE_STEP_S,
    build_ducked_stem,
    cached_preview_loudness,
    compose_voice,
    duck_envelope,
    ensure_ducked_stem,
    ensure_preview_gain,
    envelope_gain_at,
    envelope_key,
    preview_gain_from,
    stem_is_current,
    stem_paths,
    timeline_frames,
    timeline_samples,
    voice_placements,
    window_rms,
)
from sigma_editor.render.presets import load_render_config
from test_render_fixtures import (
    make_ctx,
    make_render_tmp_fixture,
    requires_ffmpeg,
    run_edit,
    write_project,
)

render_tmp = make_render_tmp_fixture()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HOLD = {"preset": "hold", "start_rect": [0, 0, 1024, 576], "end_rect": [0, 0, 1024, 576]}


def make_timeline(
    total_s: float = 4.0, *, music: str | None = None, segments: list | None = None, **music_block
) -> Timeline:
    third = round(total_s / 3, 3)
    bounds = [(0.0, third), (third, 2 * third), (2 * third, total_s)]
    return Timeline.model_validate({
        "project_id": "p-stem", "format": "long", "aspect": "16:9", "width": 854,
        "height": 480, "duration_s": total_s,
        "scenes": [
            {"index": i, "image": f"06_images/scene_{i + 1:02d}.png", "start_s": start,
             "end_s": end, "motion": HOLD, "transition_out": {"type": "fade", "duration_s": 0.3}}
            for i, (start, end) in enumerate(bounds)
        ],
        "voice": {"path": "05_voice/voice.wav", "segments": segments},
        "music": {"path": music, **music_block},
        "captions": {"enabled": False},
    })


def write_wav(path: Path, samples: np.ndarray, channels: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(AUDIO_RATE)
        out.writeframes(data.tobytes())
    return path


def loud_voice(total_s: float = 4.0, start_s: float = 1.0, end_s: float = 3.0) -> np.ndarray:
    """Silence, then noise at about -10 dBFS RMS, then silence."""
    voice = np.zeros(int(total_s * AUDIO_RATE))
    rng = np.random.default_rng(7)
    span = slice(int(start_s * AUDIO_RATE), int(end_s * AUDIO_RATE))
    voice[span] = np.clip(rng.normal(0.0, 0.3, span.stop - span.start), -0.95, 0.95)
    return voice


def sine_track(path: Path, seconds: float = 2.0, amplitude: float = 0.5) -> Path:
    """A stereo 440 Hz tone (2 s = 880 whole cycles, so it loops seamlessly)."""
    t = np.arange(int(seconds * AUDIO_RATE)) / AUDIO_RATE
    tone = amplitude * np.sin(2 * np.pi * 440.0 * t)
    return write_wav(path, np.stack([tone, tone], axis=1).reshape(-1), channels=2)


def read_stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 2 and handle.getframerate() == AUDIO_RATE
        assert handle.getsampwidth() == 2
        data = handle.readframes(handle.getnframes())
    return np.frombuffer(data, "<i2").reshape(-1, 2).astype(np.float64) / 32768.0


def rms_db(samples: np.ndarray, start_s: float, end_s: float) -> float:
    part = samples[int(start_s * AUDIO_RATE):int(end_s * AUDIO_RATE)]
    return 20 * math.log10(float(np.sqrt(np.mean(part ** 2))))


# The envelope -------------------------------------------------------------------------------


def test_the_dip_starts_before_the_voice_holds_and_comes_back_after() -> None:
    rms = [0.0] * 100 + [0.2] * 100 + [0.0] * 200
    gains = duck_envelope(rms, threshold=0.03, duck_db=-12, attack_s=0.3, release_s=0.8)
    assert len(gains) == 400
    assert all(g == -12 for g in gains[100:200])
    # Attack 0.3 s = 30 windows of 10 ms, before the voice (a look-ahead): 0 at window 70,
    # half way at 85, fully down when the voice starts at 100.
    assert gains[69] == 0 and gains[70] == 0
    assert gains[85] == pytest.approx(-6.0) and gains[99] == pytest.approx(-11.6)
    # Release 0.8 s = 80 windows after the voice: 0.15 dB per window.
    assert gains[200] == pytest.approx(-11.85) and gains[239] == pytest.approx(-6.0)
    assert gains[279] == 0 and all(g == 0 for g in gains[280:])
    assert all(b <= a + 1e-9 for a, b in zip(gains[70:100], gains[71:101], strict=True))
    assert not any(str(g) == "-0.0" for g in gains)


def test_a_short_pause_keeps_the_music_down_and_odd_settings_are_safe() -> None:
    rms = [0.2] * 50 + [0.0] * 20 + [0.2] * 50  # a 0.2 s pause between two sentences
    gains = duck_envelope(rms, duck_db=-12, attack_s=0.3, release_s=0.8)
    assert gains[49] == -12 and gains[70] == -12
    assert max(gains[50:70]) == pytest.approx(-9.75)  # the music never comes back up
    jump = duck_envelope([0, 0.5, 0.5, 0], duck_db=-6, attack_s=0, release_s=0)
    assert list(jump) == [0, -6, -6, 0]
    assert list(duck_envelope([0.5, 0.5], duck_db=3)) == [0, 0]  # a dip only, never a boost
    assert list(duck_envelope([0.01, 0.02], threshold=0.03)) == [0, 0]  # quiet: no voice
    assert duck_envelope([]).size == 0


def test_reading_the_envelope_is_linear_in_db_and_holds_at_the_ends() -> None:
    gains = [0.0, -12.0, -12.0, 0.0]
    times = np.array([-1.0, 0.0, 0.005, 0.01, 0.025, 0.03, 0.05])
    assert envelope_gain_at(gains, times).tolist() == pytest.approx(
        [0.0, 0.0, -6.0, -12.0, -6.0, 0.0, 0.0])
    assert ENVELOPE_STEP_S == 0.01


def test_window_rms_of_a_constant_and_of_silence() -> None:
    samples = np.full(1000, 16384, dtype=np.int16)  # half of full scale
    rms = window_rms(samples, 480)
    assert len(rms) == 3 and rms[0] == pytest.approx(0.5) and rms[1] == pytest.approx(0.5)
    assert rms[2] == pytest.approx(0.5 * math.sqrt(40 / 480))  # the padded last window
    assert window_rms(np.zeros(0, dtype=np.int16), 480).size == 0


# Where the voice plays ------------------------------------------------------------------------


def test_voice_placements_follow_start_s_or_the_segments() -> None:
    whole = make_timeline(4.0)
    total = timeline_samples(whole)
    assert timeline_frames(whole) == 120 and total == 192000
    assert [(p.src_start, p.src_end, p.at) for p in voice_placements(whole, 150000, total)] == [
        (0, 150000, 0)]
    late = whole.model_copy(deep=True)
    late.voice.start_s = 0.5004  # adelay works in whole milliseconds
    assert voice_placements(late, 300000, total)[0].at == 500 * 48
    assert voice_placements(late, 300000, total)[0].src_end == total - 24000
    segments = [
        {"sentence_id": "b", "src_start_s": 1.0, "src_end_s": 2.0, "at_s": 0.5},
        {"sentence_id": "a", "src_start_s": 0.0, "src_end_s": 1.0, "at_s": 1.25},
        {"sentence_id": "c", "src_start_s": 2.0, "src_end_s": 3.5, "at_s": 3.0},
    ]
    swapped = make_timeline(4.0, segments=segments)
    places = voice_placements(swapped, 3 * AUDIO_RATE, total)
    # b at 0.5 s; a overlaps b's last 0.25 s and loses its start; c is cut at the file's end
    # (3 s) and at the end of the timeline (4 s).
    assert [(p.src_start, p.src_end, p.at) for p in places] == [
        (48000, 96000, 24000), (12000, 48000, 72000), (96000, 144000, 144000)]
    assert voice_placements(make_timeline(4.0, segments=[]), 1000, total) == []


def test_the_composed_voice_has_the_segments_in_their_new_places(render_tmp: Path) -> None:
    ramp = np.arange(3 * AUDIO_RATE) / (3 * AUDIO_RATE) * 0.5
    voice = write_wav(render_tmp / "voice.wav", ramp)
    segments = [{"sentence_id": "b", "src_start_s": 1.0, "src_end_s": 2.0, "at_s": 0.0},
                {"sentence_id": "a", "src_start_s": 0.0, "src_end_s": 1.0, "at_s": 2.0}]
    timeline = make_timeline(4.0, segments=segments)
    track = compose_voice(timeline, voice, timeline_samples(timeline))
    source = np.clip(np.round(ramp * 32767), -32768, 32767).astype(np.int16)
    assert np.array_equal(track[:AUDIO_RATE], source[AUDIO_RATE:2 * AUDIO_RATE])
    assert not track[AUDIO_RATE:2 * AUDIO_RATE].any()  # the gap is silence
    assert np.array_equal(track[2 * AUDIO_RATE:3 * AUDIO_RATE], source[:AUDIO_RATE])
    assert compose_voice(timeline, render_tmp / "none.wav", 10).tolist() == [0] * 10


def test_preview_gain_brings_the_mix_to_the_target() -> None:
    assert preview_gain_from(-23.0, -16.0) == 7.0
    assert preview_gain_from(-10.5, -16.0) == -5.5
    assert preview_gain_from(-70.0, -16.0) is None and preview_gain_from(None, -16.0) is None
    assert preview_gain_from(-65.0, -16.0) == 30.0  # at most +30 dB


def test_the_gain_curve_comes_from_the_levels_before_and_after_loudnorm(tmp_path: Path) -> None:
    levels = tmp_path / "levels.txt"
    levels.write_text(
        "frame:0    pts:0       pts_time:0\n"
        f"{ff.RMS_KEY}=-inf\n"
        "frame:1    pts:4800    pts_time:0.1\n"
        f"{ff.RMS_KEY}=-20.500000\n"
        "frame:2    pts:9600    pts_time:0.2\n"
        f"{ff.RMS_KEY}=-19.250000\n",
        encoding="utf-8",
    )
    assert ff.read_levels(levels) == [None, -20.5, -19.25]
    assert ff.read_levels(tmp_path / "none.txt") == []
    # Silent steps hold the gain of the step before (the first audible one's at the start).
    before = [None, -20.0, -80.0, -21.0, -20.0]
    after = [None, -14.0, -75.0, -14.5, -13.0]
    assert ff.gain_curve_from(before, after) == [6.0, 6.0, 6.0, 6.5, 7.0]
    assert ff.gain_curve_from([None, -90.0], [None, -80.0]) is None
    assert ff.gain_curve_from([-20.0, -20.0, -20.0], [-14.0]) == [6.0]


def _f32(path: Path) -> np.ndarray:
    return np.fromfile(path, "<f4").astype(np.float64).reshape(-1, 2)


def _window_db(samples: np.ndarray, seconds: float = 0.4) -> np.ndarray:
    size = int(seconds * AUDIO_RATE)
    count = samples.shape[0] // size
    blocks = samples[: count * size].reshape(count, size, 2)
    return 10 * np.log10(np.mean(blocks ** 2, axis=(1, 2)) + 1e-20)


@requires_ffmpeg
def test_the_preview_follows_loudnorm_where_one_gain_would_not(render_tmp: Path) -> None:
    """One-pass ``loudnorm`` starts from the loudness of the first 3 s and then follows the
    loudness so far, so a video that starts loud plays quieter at first. With the measured
    curve the preview is within 0.3 dB of the render in every 400 ms; one gain is not."""
    folder = render_tmp / "proj"
    total_s = 12.0
    voice = np.random.default_rng(11).normal(0.0, 0.05, int(total_s * AUDIO_RATE))
    voice[: 3 * AUDIO_RATE] *= 2.0  # the first 3 s are 6 dB louder
    write_wav(folder / "05_voice" / "voice.wav", np.clip(voice, -0.95, 0.95))
    track = sine_track(render_tmp / "music" / "tone.wav", amplitude=0.2)
    timeline = make_timeline(total_s, music=str(track), gain_db=-12.0, fade_in_s=0.0,
                             fade_out_s=0.0)
    config = load_render_config()
    stem = ensure_ducked_stem(timeline, folder, config=config)
    assert stem is not None
    gain = ensure_preview_gain(timeline, folder, config=config, stem=stem)
    loudness = cached_preview_loudness(timeline, folder, config)
    assert gain is not None and loudness is not None and loudness.gain_db == gain
    assert loudness.curve_db is not None and loudness.curve_step_s == 0.1
    curve = np.array(loudness.curve_db)
    assert curve.size == 120
    assert curve[-1] - curve[0] > 1.5  # quieter at first

    # The render's mix before and after its loudnorm (the assembly pass's graph).
    graph = ff.audio_graph(timeline, folder, config, frames=timeline_frames(timeline),
                           stem=stem.stem)
    aformat = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    script = ";".join([
        *graph.parts, f"{graph.label}asplit=2[pre][norm]",
        f"[norm]{ff.loudnorm_filter(config)},{aformat}[post]",
    ])
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for item in graph.inputs:
        command.extend(item)
    pre_file, post_file = render_tmp / "pre.f32", render_tmp / "post.f32"
    command += ["-filter_complex", script,
                "-map", "[pre]", "-f", "f32le", "-ac", "2", str(pre_file),
                "-map", "[post]", "-f", "f32le", "-ac", "2", str(post_file)]
    subprocess.run(command, check=True, creationflags=NO_WINDOW)
    pre, post = _f32(pre_file), _f32(post_file)
    count = min(pre.shape[0], post.shape[0])
    step = round(0.1 * AUDIO_RATE)
    centres = (np.arange(curve.size) + 0.5) * step
    following = pre[:count] * (10 ** (np.interp(np.arange(count), centres, curve) / 20))[:, None]
    constant = pre[:count] * 10 ** (gain / 20)
    heard = _window_db(post[:count])
    assert np.max(np.abs(_window_db(following) - heard)) < 0.3
    assert np.max(np.abs(_window_db(constant) - heard)) > 1.0


# The stem (FFmpeg) ------------------------------------------------------------------------------


@requires_ffmpeg
def test_the_stem_dips_by_duck_db_where_the_voice_is_loud(render_tmp: Path) -> None:
    folder = render_tmp / "proj"
    write_wav(folder / "05_voice" / "voice.wav", loud_voice())
    track = sine_track(render_tmp / "music" / "tone.wav")
    timeline = make_timeline(4.0, music=str(track), gain_db=0.0, fade_in_s=0.0,
                             fade_out_s=0.0, duck_db=-12.0)
    config = load_render_config()
    stem, envelope = stem_paths(folder)
    info = build_ducked_stem(timeline, folder / "05_voice" / "voice.wav", stem, envelope,
                             folder=folder, config=config)
    assert info.built and info.stem == stem and stem.is_file()
    samples = read_stereo(stem)
    # Exactly as long as the timeline (4 s = 120 frames at 30 fps = 192000 samples).
    assert samples.shape[0] == timeline_samples(timeline) == 192000
    reference = rms_db(samples, 0.1, 0.6)  # before the dip starts (0.7 s)
    ducked = rms_db(samples, 1.5, 2.5)  # the voice is loud, the dip is complete
    assert reference == pytest.approx(20 * math.log10(0.5 / math.sqrt(2)), abs=0.2)
    assert ducked - reference == pytest.approx(-12.0, abs=0.5)
    assert rms_db(samples, 3.85, 4.0) == pytest.approx(reference, abs=0.3)  # back up
    # The envelope file: the contract's shape plus the stem's key at the top.
    doc = json.loads(envelope.read_text(encoding="utf-8"))
    assert doc["step_s"] == 0.01 and len(doc["gain_db"]) == 400
    assert doc["gain_db"][150] == -12 and doc["gain_db"][10] == 0
    assert doc["key"] == info.key == envelope_key(envelope)
    assert doc["lookahead"] is True and doc["sample_rate"] == AUDIO_RATE


@requires_ffmpeg
def test_the_stem_follows_the_music_block_and_is_made_only_when_needed(render_tmp: Path) -> None:
    folder = render_tmp / "proj"
    write_wav(folder / "05_voice" / "voice.wav", loud_voice())
    track = sine_track(render_tmp / "music" / "tone.wav")
    timeline = make_timeline(4.0, music=str(track), gain_db=-6.0, start_s=0.5,
                             fade_in_s=1.0, fade_out_s=1.0)
    config = load_render_config()
    first = ensure_ducked_stem(timeline, folder, config=config)
    assert first is not None and first.built
    assert stem_is_current(timeline, folder, config) == first.key
    mtime = first.stem.stat().st_mtime_ns
    again = ensure_ducked_stem(timeline, folder, config=config)
    assert again is not None and not again.built and again.key == first.key
    assert first.stem.stat().st_mtime_ns == mtime
    samples = read_stereo(first.stem)
    assert rms_db(samples, 0.0, 0.05) < rms_db(samples, 0.6, 0.65) - 10  # fading in
    assert rms_db(samples, 3.95, 4.0) < -40  # faded out at the very end
    # Another dip depth: a new stem with a new key.
    deeper = timeline.model_copy(deep=True)
    deeper.music.duck_db = -20.0
    assert stem_is_current(deeper, folder, config) is None
    remade = ensure_ducked_stem(deeper, folder, config=config)
    assert remade is not None and remade.built and remade.key != first.key
    # No music, or a track that is gone: no stem.
    silent = timeline.model_copy(deep=True)
    silent.music.path = None
    assert ensure_ducked_stem(silent, folder, config=config) is None
    gone = timeline.model_copy(deep=True)
    gone.music.path = str(render_tmp / "music" / "gone.wav")
    assert ensure_ducked_stem(gone, folder, config=config) is None
    with pytest.raises(ff.RenderError, match="music track is missing"):
        build_ducked_stem(gone, None, render_tmp / "x.wav", render_tmp / "x.json",
                          folder=folder)


def _integrated_lufs(path: Path) -> float:
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
         "ebur128=framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW, check=True,
    )
    value = ff.parse_integrated_loudness(completed.stderr)
    assert value is not None
    return value


@requires_ffmpeg
def test_a_render_with_the_stem_is_as_loud_as_the_old_ducking(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    voice = files.folder / "05_voice" / "voice.wav"
    write_wav(voice, loud_voice(4.0, 0.8, 3.2) * 0.5)
    track = sine_track(render_tmp / "music" / "tone.wav", amplitude=0.6)
    timeline = make_timeline(4.0, music=str(track))  # the defaults: -18 dB, -12 dB dip
    config = load_render_config()

    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    assert "sidechaincompress" not in plan.filter_graph
    assert plan.audio is not None and plan.audio.stem is not None
    output = files.edit_dir / "with_stem.mp4"
    ff.run_render(plan, output, work_dir=files.edit_dir, log_path=files.edit_dir / "render.log")
    assert plan.audio.stem.is_file()  # made just before the assembly pass
    assert "music stem: made" in (files.edit_dir / "render.log").read_text("utf-8")

    # The mix the renderer ran before M5.1: sidechaincompress on the looped track.
    scenes_s = "4"
    aformat = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    old = (
        f"[0:a]{aformat},atrim=0:{scenes_s},apad=whole_dur={scenes_s},"
        "asplit=2[voice_mix][voice_sc];"
        f"[1:a]{aformat},atrim=0:{scenes_s},asetpts=PTS-STARTPTS,volume=-18dB,"
        "afade=t=in:st=0:d=1,afade=t=out:st=2:d=2[music_in];"
        "[music_in][voice_sc]sidechaincompress=threshold=0.03:ratio=8:attack=300"
        ":release=800[ducked];"
        "[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix];"
        f"[mix]loudnorm=I=-16:TP=-1.5:LRA=11,{aformat}[out]"
    )
    before = render_tmp / "before.m4a"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(voice),
         "-stream_loop", "-1", "-i", str(track), "-filter_complex", old, "-map", "[out]",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", scenes_s,
         str(before)],
        check=True, creationflags=NO_WINDOW,
    )
    assert abs(_integrated_lufs(output) - _integrated_lufs(before)) <= 1.0

    # The preview gain is measured on the same mix before loudnorm, then cached.
    info = ensure_ducked_stem(timeline, files.folder, config=config)
    assert info is not None and not info.built
    gain = ensure_preview_gain(timeline, files.folder, config=config, stem=info)
    assert gain is not None and -30 <= gain <= 30
    cached = json.loads((files.edit_dir / "preview" / "audio.json").read_text("utf-8"))
    assert cached["preview_gain_db"] == gain
    # loudnorm's own gain every 100 ms over the 4 s, close to the one gain on average
    assert cached["curve_step_s"] == 0.1 and len(cached["curve_gain_db"]) == 40
    assert abs(sum(cached["curve_gain_db"]) / 40 - gain) < 1.5
    assert ensure_preview_gain(timeline, files.folder, config=config, stem=info) == gain


@requires_ffmpeg
def test_the_edit_step_makes_the_stem_before_rendering_and_shows_it(render_tmp: Path) -> None:
    """Contract section 3.5: the edit step makes the stem and the envelope and measures
    ``preview_gain_db`` before it renders; the timeline and the editor payload carry them."""
    files = write_project(render_tmp, popups=False)  # 3 scenes, a licensed music track
    messages: list[str] = []
    ctx = make_ctx(files, edits={"presets": [], "music_path": "calm.wav"},
                   progress=lambda message, pct=None: messages.append(message))
    result = run_edit(ctx)  # no sizes: only the proxy (854 x 480)
    assert result.rendered == ["proxy"]
    assert any(note.startswith("Music stem made in") for note in result.outcome.notes)

    edit = files.edit_dir
    preparing = next(i for i, m in enumerate(messages) if m.startswith("Preparing the music"))
    rendering = next(i for i, m in enumerate(messages) if m.startswith("Rendering proxy"))
    assert preparing < rendering
    log = (edit / "render.log").read_text(encoding="utf-8")
    assert "music stem: reused music_ducked.wav" in log  # made before, found current

    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.music.stem == "07_edit/music_ducked.wav"
    assert timeline.music.envelope == "07_edit/music_envelope.json"
    assert (edit / "music_ducked.wav").is_file() and (edit / "music_envelope.json").is_file()
    gain = timeline.render.preview_gain_db
    assert gain is not None and -30 <= gain <= 30
    config = load_render_config()
    key = stem_is_current(timeline, files.folder, config)
    assert key is not None and key == envelope_key(edit / "music_envelope.json")

    payload = result.needs_review_payload
    base = "/api/projects/p-edit-test/files/07_edit"
    assert payload["stem_url"] == f"{base}/music_ducked.wav?v={key}"
    assert payload["envelope_url"] == f"{base}/music_envelope.json?v={key}"
    assert payload["preview_gain_db"] == gain
    assert payload["timeline"]["music"]["stem"] == "07_edit/music_ducked.wav"
    assert payload["timeline"]["render"]["preview_gain_db"] == gain
    rebuilt = load_review_payload(ctx.project, ctx.settings)
    assert rebuilt is not None and rebuilt["stem_url"] == payload["stem_url"]
    assert rebuilt["preview_gain_db"] == gain
