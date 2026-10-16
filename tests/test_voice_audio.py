"""The audio helpers behind the narration: sample-accurate WAV stitching and cutting,
timing estimates and the FFmpeg wrappers (real ffmpeg, skipped when it is not installed).
Nothing touches the network."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import pytest

from sigma_editor.audio import AudioError, AudioToolMissing
from sigma_editor.audio import ffmpeg as ff
from sigma_editor.audio.estimate import (
    distribute_sentences,
    estimate_words,
    sentence_weight,
    word_tokens,
    word_weight,
)
from sigma_editor.audio.wav import (
    concat_wavs,
    is_standard_wav,
    read_wav_info,
    slice_wav,
    write_pcm_wav,
    write_silence,
)

SR = 48000


def tone_wav(path: Path, frames: int, *, sample_rate: int = SR, channels: int = 1) -> Path:
    """A short PCM file whose samples are a ramp, so cut points can be checked by value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray()
    for i in range(frames):
        value = (i % 32000) - 16000
        for _ in range(channels):
            data += int(value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(bytes(data))
    return path


def frames_of(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes()


# WAV ----------------------------------------------------------------------------------------


def test_concat_offsets_are_sample_accurate(tmp_path: Path) -> None:
    parts = [
        tone_wav(tmp_path / "a.wav", 100),
        tone_wav(tmp_path / "b.wav", 250),
        tone_wav(tmp_path / "c.wav", 7),
    ]
    out = tmp_path / "all.wav"
    segments = concat_wavs(parts, out, sample_rate=SR, gap_s=0.5)
    gap = 24000
    assert [(s.start_frame, s.end_frame) for s in segments] == [
        (0, 100), (100 + gap, 350 + gap), (350 + 2 * gap, 357 + 2 * gap)
    ]
    assert frames_of(out) == 357 + 2 * gap
    assert segments[1].start_s(SR) == pytest.approx((100 + gap) / SR)
    info = read_wav_info(out)
    assert info.is_standard() and info.duration_s == pytest.approx(frames_of(out) / SR)
    # The samples of each part sit exactly at their offset (ramp value check).
    with wave.open(str(out), "rb") as handle:
        handle.setpos(segments[1].start_frame)
        first = int.from_bytes(handle.readframes(1), "little", signed=True)
    assert first == -16000  # the ramp restarts at the start of part b
    # No gap by default.
    tight = concat_wavs(parts, tmp_path / "tight.wav")
    assert [(s.start_frame, s.end_frame) for s in tight] == [(0, 100), (100, 350), (350, 357)]


def test_concat_rejects_other_formats_and_empty_lists(tmp_path: Path) -> None:
    wrong = tone_wav(tmp_path / "w.wav", 10, sample_rate=44100)
    with pytest.raises(AudioError, match="44100 Hz"):
        concat_wavs([wrong], tmp_path / "x.wav")
    with pytest.raises(AudioError, match="nothing to stitch"):
        concat_wavs([], tmp_path / "x.wav")
    assert not (tmp_path / "x.wav").exists()


def test_slice_write_and_info(tmp_path: Path) -> None:
    source = tone_wav(tmp_path / "s.wav", 1000)
    cut = slice_wav(source, tmp_path / "cut.wav", 100, 350)
    assert cut.frames == 250 and frames_of(tmp_path / "cut.wav") == 250
    with wave.open(str(tmp_path / "cut.wav"), "rb") as handle:
        assert int.from_bytes(handle.readframes(1), "little", signed=True) == 100 - 16000
    clipped = slice_wav(source, tmp_path / "clip.wav", 900, 5000)
    assert clipped.frames == 100
    assert write_silence(tmp_path / "quiet.wav", 480) == 480
    assert read_wav_info(tmp_path / "quiet.wav").duration_s == pytest.approx(0.01)
    pcm = write_pcm_wav(tmp_path / "pcm.wav", bytes(96000), SR)
    assert pcm.frames == 48000 and is_standard_wav(tmp_path / "pcm.wav")
    (tmp_path / "not.wav").write_bytes(b"not a wav at all")
    with pytest.raises(AudioError, match="not a PCM WAV"):
        read_wav_info(tmp_path / "not.wav")
    with pytest.raises(AudioError, match="does not exist"):
        read_wav_info(tmp_path / "missing.wav")
    assert not is_standard_wav(tmp_path / "missing.wav")
    assert not is_standard_wav(tone_wav(tmp_path / "stereo.wav", 10, channels=2))


# Estimates ------------------------------------------------------------------------------------


def test_word_tokens_and_weights() -> None:
    assert word_tokens("  Hello,  world! -- 42 ") == ["Hello,", "world!", "42"]
    assert word_tokens("") == []
    assert word_weight("a") == 1.0
    assert word_weight("hello,") == pytest.approx(5.6)
    assert sentence_weight("") == 1.0
    assert sentence_weight("ab cd") == 4.0


def test_estimate_words_spreads_by_character_length() -> None:
    words = estimate_words("A tremendously long sentence", 10.0, 14.0)
    assert [w.text for w in words] == ["A", "tremendously", "long", "sentence"]
    assert words[0].start_s == 10.0 and words[-1].end_s == 14.0
    for first, second in zip(words, words[1:], strict=False):
        assert first.end_s == pytest.approx(second.start_s)
    assert words[1].duration_s > words[2].duration_s > words[0].duration_s
    assert all(w.confidence == pytest.approx(0.3) for w in words)
    assert estimate_words("", 0.0, 1.0) == []
    assert estimate_words("one", 2.0, 2.0)[0].duration_s == 0.0


def test_distribute_sentences_shares_the_recording() -> None:
    texts = ["Short one.", "A much longer second sentence with many words in it.", "End."]
    spans = distribute_sentences(texts, 20.0, lead_s=0.5, tail_s=0.5)
    assert spans[0][0] == 0.5 and spans[-1][1] == pytest.approx(19.5)
    for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
        assert end == pytest.approx(start)
    lengths = [end - start for start, end in spans]
    assert lengths[1] > lengths[0] > lengths[2]
    assert distribute_sentences([], 10.0) == []
    # Margins never eat more than half of a tiny recording.
    tiny = distribute_sentences(["a", "b"], 0.4, lead_s=1.0, tail_s=1.0)
    assert tiny[0][0] == pytest.approx(0.1) and tiny[-1][1] == pytest.approx(0.3)


# FFmpeg -------------------------------------------------------------------------------------


needs_ffmpeg = pytest.mark.skipif(not ff.tools_available(), reason="ffmpeg/ffprobe not on PATH")


@needs_ffmpeg
def test_convert_to_wav_and_probe_duration(tmp_path: Path) -> None:
    source = tone_wav(tmp_path / "in.wav", 22050, sample_rate=22050, channels=2)  # 1.0 s stereo
    assert not is_standard_wav(source)
    out = ff.convert_to_wav(source, tmp_path / "out" / "o.wav")
    info = read_wav_info(out)
    assert info.is_standard()
    assert math.isclose(info.duration_s, 1.0, abs_tol=0.01)
    assert math.isclose(ff.probe_duration(out), 1.0, abs_tol=0.02)
    assert not list((tmp_path / "out").glob("*.part.wav"))
    with pytest.raises(AudioError, match="does not exist"):
        ff.convert_to_wav(tmp_path / "nope.mp3", tmp_path / "x.wav")
    (tmp_path / "bad.mp3").write_bytes(b"garbage")
    with pytest.raises(AudioError, match="failed"):
        ff.convert_to_wav(tmp_path / "bad.mp3", tmp_path / "y.wav")
    assert not (tmp_path / "y.wav").exists()


def test_missing_tools_raise_plain_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.delenv("FFPROBE_PATH", raising=False)
    assert not ff.tools_available()
    with pytest.raises(AudioToolMissing, match="winget install"):
        ff.ffmpeg_path()
    with pytest.raises(AudioToolMissing):
        ff.ffprobe_path()
