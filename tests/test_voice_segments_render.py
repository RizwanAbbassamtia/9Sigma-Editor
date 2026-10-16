"""The voice as sentence clips (docs/M5-CONTRACT.md section 3.2): with ``voice.segments`` the
renderer cuts each clip from the voice file with ``atrim`` (in samples) and joins them with
``concat`` at their ``at_s``, silence in the gaps; without segments the whole file plays as
before. A real render (480p, 3 scenes, 4 s) with two sentences swapped has the words in the
swapped order, found by cross-correlation against the sentence files. The timeline builder
fills the segments from ``timing.json`` (and the sentence files); an own recording's clips
cover the whole file, so its track is the file played whole."""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from sigma_editor.models.timeline import Timeline, VoiceSegment
from sigma_editor.render import ffmpeg as ff
from sigma_editor.render.music import compose_voice, timeline_samples
from sigma_editor.render.presets import load_render_config
from sigma_editor.render.timeline_builder import parse_timing, voice_segments
from test_render_fixtures import make_render_tmp_fixture, requires_ffmpeg, write_project

render_tmp = make_render_tmp_fixture()
RATE = 48000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HOLD = {"preset": "hold", "start_rect": [0, 0, 1024, 576], "end_rect": [0, 0, 1024, 576]}
SPANS = {"s-1": (0.2, 1.2), "s-2": (1.4, 2.4), "s-3": (2.6, 3.6)}


def timeline_with(segments: list[dict] | None, total_s: float = 4.0) -> Timeline:
    third = round(total_s / 3, 3)
    bounds = [(0.0, third), (third, 2 * third), (2 * third, total_s)]
    return Timeline.model_validate({
        "project_id": "p-voice", "format": "long", "aspect": "16:9", "width": 854,
        "height": 480, "duration_s": total_s,
        "scenes": [
            {"index": i, "image": f"06_images/scene_{i + 1:02d}.png", "start_s": a, "end_s": b,
             "motion": HOLD, "transition_out": {"type": "fade", "duration_s": 0.3}}
            for i, (a, b) in enumerate(bounds)
        ],
        "voice": {"path": "05_voice/voice.wav", "segments": segments},
        "captions": {"enabled": False},
    })


def write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2").tobytes())


def write_distinct_voice(folder: Path) -> dict[str, np.ndarray]:
    """``voice.wav`` with three different noise bursts at SPANS (silence between them) and
    each burst as ``05_voice/sentences/<id>.wav``."""
    voice = np.zeros(4 * RATE)
    sentences: dict[str, np.ndarray] = {}
    for seed, (sid, (start, end)) in enumerate(SPANS.items(), start=11):
        burst = np.random.default_rng(seed).uniform(-0.5, 0.5, int(round((end - start) * RATE)))
        voice[int(round(start * RATE)):int(round(start * RATE)) + burst.size] = burst
        write_wav(folder / "05_voice" / "sentences" / f"{sid}.wav", burst)
        sentences[sid] = burst
    write_wav(folder / "05_voice" / "voice.wav", voice)
    return sentences


def segment(sid: str, at_s: float) -> dict:
    start, end = SPANS[sid]
    return {"sentence_id": sid, "src_start_s": start, "src_end_s": end, "at_s": at_s}


# The graph -------------------------------------------------------------------------------------


def test_segments_become_atrim_and_concat_in_samples(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    write_distinct_voice(files.folder)
    config = load_render_config()
    timeline = timeline_with([segment("s-2", 0.2), segment("s-1", 1.4), segment("s-3", 2.6)])
    graph = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config).filter_graph
    assert "aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=mono,asplit=3" in graph
    # s-2 first, delayed to 0.2 s (9600 samples) and padded to the next clip (1.4 s).
    assert ("[vsrc0]atrim=start_sample=67200:end_sample=115200,asetpts=PTS-STARTPTS,"
            "adelay=delays=9600S:all=1,apad=whole_len=67200[vseg0]") in graph
    # s-1 from 1.4 s, padded to s-3 at 2.6 s; s-3 padded to the end (4 s = 192000 samples).
    assert ("[vsrc1]atrim=start_sample=9600:end_sample=57600,asetpts=PTS-STARTPTS,"
            "apad=whole_len=57600[vseg1]") in graph
    assert "[vsrc2]atrim=start_sample=124800:end_sample=172800" in graph
    assert "apad=whole_len=67200[vseg2]" in graph
    assert "[vseg0][vseg1][vseg2]concat=n=3:v=0:a=1," in graph
    assert "apad=whole_len=192000,atrim=end_sample=192000[voice]" in graph
    assert "[voice]anull[mix]" in graph and "sidechaincompress" not in graph

    # No segments: the whole file from start_s, exactly the chain from before M5.1.
    whole = ff.build_plan(timeline_with(None), config.proxy, folder=files.folder,
                          config=config).filter_graph
    assert ("[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            "atrim=0:4,apad=whole_dur=4[mix]") in whole
    assert "asplit" not in whole and "concat=n=" not in whole
    # An empty list: no voice at all (silence of the right length).
    empty = ff.build_plan(timeline_with([]), config.proxy, folder=files.folder,
                          config=config).filter_graph
    assert "anullsrc=r=48000:cl=stereo,atrim=end_sample=192000[voice]" in empty


def test_a_segment_must_have_a_length() -> None:
    with pytest.raises(ValueError, match="ends"):
        VoiceSegment(sentence_id="s", src_start_s=1.0, src_end_s=1.0, at_s=0.0)
    clip = VoiceSegment(sentence_id="s", src_start_s=1.0, src_end_s=1.75, at_s=2.0)
    assert clip.duration_s == 0.75 and clip.end_s == 2.75


def test_the_builder_lays_the_voice_out_as_sentence_clips(render_tmp: Path) -> None:
    folder = render_tmp / "proj"
    write_distinct_voice(folder)
    timing = parse_timing({
        "sample_rate": RATE, "duration_s": 4.0, "sentences": [
            {"id": "s-1", "text": "One", "start_s": 0.2, "end_s": 1.2,
             "start_frame": 9600, "end_frame": 57600},
            {"id": "s-2", "text": "Two", "start_s": 1.4, "end_s": 2.4},
            {"id": "s-3", "text": "Three", "start_s": 2.6, "end_s": 2.6},
            {"id": "", "text": "No id", "start_s": 3.0, "end_s": 3.5},
        ]})
    segments = voice_segments(timing, folder)
    # s-3 has no length in the timing but its sentence file is 1 s long: the file wins.
    assert [(s.sentence_id, s.src_start_s, s.src_end_s, s.at_s) for s in segments] == [
        ("s-1", 0.2, 1.2, 0.2), ("s-2", 1.4, 2.4, 1.4), ("s-3", 2.6, 3.6, 2.6)]
    assert [s.sentence_id for s in voice_segments(timing)] == ["s-1", "s-2"]  # no files
    shifted = voice_segments(timing, None, voice_start_s=0.5)
    assert shifted[0].at_s == pytest.approx(0.7) and shifted[0].src_start_s == pytest.approx(0.2)


def test_an_own_recording_is_laid_out_without_gaps(render_tmp: Path) -> None:
    """An own recording's sentence times are estimated or aligned: its clips cover the whole
    file (the first from 0, each to the next sentence, the last to the end of the file), so
    nothing is cut away and the track is exactly the file played whole."""
    folder = render_tmp / "proj"
    write_distinct_voice(folder)
    rows = [{"id": sid, "text": sid, "start_s": a, "end_s": b} for sid, (a, b) in SPANS.items()]
    timing = parse_timing({"sample_rate": RATE, "duration_s": 3.9, "own_recording": True,
                           "source": "aligned", "sentences": rows})
    assert timing.own_recording
    segments = voice_segments(timing, folder)
    assert [(s.sentence_id, s.src_start_s, s.src_end_s, s.at_s) for s in segments] == [
        ("s-1", 0.0, 1.4, 0.0), ("s-2", 1.4, 2.6, 1.4), ("s-3", 2.6, 4.0, 2.6)]
    # Without the file the last clip runs to the timing's end.
    assert voice_segments(timing)[-1].src_end_s == 3.9
    whole = timeline_with(None)
    clips = timeline_with([s.model_dump() for s in segments])
    total = timeline_samples(whole)
    voice = folder / "05_voice" / "voice.wav"
    assert np.array_equal(compose_voice(clips, voice, total), compose_voice(whole, voice, total))
    # The voice tool's sentences (no own_recording) keep their exact spans and silent gaps.
    tool = parse_timing({"sample_rate": RATE, "duration_s": 3.9, "sentences": rows})
    assert [(s.src_start_s, s.src_end_s) for s in voice_segments(tool, folder)] == [
        (0.2, 1.2), (1.4, 2.4), (2.6, 3.6)]


# A real render -----------------------------------------------------------------------------------


def _decode_mono(path: Path) -> np.ndarray:
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(RATE), "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"],
        capture_output=True, check=True, creationflags=NO_WINDOW,
    )
    return np.frombuffer(completed.stdout, dtype="<f4").astype(np.float64)


def _best_lag(signal: np.ndarray, template: np.ndarray) -> tuple[float, float]:
    """``(seconds, strength)``: where ``template`` sits in ``signal`` (FFT cross-correlation)
    and how much better that peak is than the next best one."""
    size = 1 << (len(signal) + len(template)).bit_length()
    spectrum = np.fft.rfft(signal, size) * np.conj(np.fft.rfft(template, size))
    correlation = np.fft.irfft(spectrum, size)[: len(signal)]
    best = int(np.argmax(correlation))
    others = correlation.copy()
    others[max(0, best - 2400):best + 2400] = 0
    return best / RATE, float(correlation[best] / max(others.max(), 1e-9))


@requires_ffmpeg
def test_swapped_sentences_render_in_the_swapped_order(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    sentences = write_distinct_voice(files.folder)
    config = load_render_config()
    timeline = timeline_with([segment("s-2", 0.2), segment("s-1", 1.4), segment("s-3", 2.6)])
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    assert (plan.width, plan.height) == (854, 480) and len(plan.layout.scenes) == 3
    output = files.edit_dir / "swapped.mp4"
    result = ff.run_render(plan, output, work_dir=files.edit_dir,
                           log_path=files.edit_dir / "render.log")
    assert result.duration_s == pytest.approx(4.0, abs=0.1)
    heard = _decode_mono(output)
    places = {}
    for sid, burst in sentences.items():
        seconds, strength = _best_lag(heard, burst)
        assert strength > 3.0, (sid, strength)
        places[sid] = seconds
    assert places["s-2"] == pytest.approx(0.2, abs=0.002)
    assert places["s-1"] == pytest.approx(1.4, abs=0.002)
    assert places["s-3"] == pytest.approx(2.6, abs=0.002)
    log = (files.edit_dir / "render.log").read_text(encoding="utf-8")
    assert "exit code 0" in log
    assert json.loads(timeline.model_dump_json())["voice"]["segments"][0]["sentence_id"] == "s-2"
