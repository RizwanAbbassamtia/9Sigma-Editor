"""timing.json for an edit (edit/timing_doc.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sigma_editor.edit.caption_files import ImportedCue
from sigma_editor.edit.timing_doc import (
    TimingError,
    estimated_timing,
    from_timing_doc,
    nudged,
    read_timing,
    timing_from_cues,
    write_timing,
)


def test_estimated_timing_is_the_engine_shape():
    doc = estimated_timing("One two three. Four five six.", 12.0, language="English")
    assert doc.source == "estimated"
    assert doc.duration_s == 12.0
    assert doc.own_recording is True
    assert doc.nudged is False
    assert [s.id for s in doc.sentences] == ["s-001", "s-002"]
    first = doc.sentences[0]
    assert first.start_frame == 0
    assert first.end_frame == round(first.end_s * 48000)
    assert [w.text for w in first.words] == ["One", "two", "three."]
    assert doc.sentences[-1].end_s == 12.0
    assert doc.language == "English"


def test_estimated_timing_needs_text():
    with pytest.raises(TimingError):
        estimated_timing("   ", 10.0)


def test_timing_from_cues_keeps_the_file_times():
    cues = [ImportedCue(0.5, 2.0, "Hello there,"), ImportedCue(2.1, 3.0, "friend."),
            ImportedCue(4.0, 5.5, "Bye.")]
    doc = timing_from_cues(cues, 6.0)
    assert doc.source == "captions"
    assert doc.timing_confidence == "medium"
    assert [(s.start_s, s.end_s) for s in doc.sentences] == [(0.5, 3.0), (4.0, 5.5)]
    assert doc.duration_s == 6.0
    with pytest.raises(TimingError):
        timing_from_cues([], 6.0)


def test_nudge_start_and_shift():
    doc = estimated_timing("One two. Three four. Five six.", 9.0)
    moved = nudged(doc, index=1, start_s=2.0)
    assert moved.nudged is True
    assert moved.sentences[1].start_s == 2.0
    assert moved.sentences[0].end_s == 2.0
    assert moved.sentences[1].start_frame == 96000
    shifted = nudged(moved, index=2, shift_s=0.5)
    assert shifted.sentences[2].start_s == pytest.approx(moved.sentences[2].start_s + 0.5)
    assert shifted.sentences[1].start_s == 2.0
    assert shifted.duration_s >= shifted.sentences[-1].end_s
    with pytest.raises(TimingError):
        nudged(doc, index=9, start_s=1.0)
    with pytest.raises(TimingError):
        nudged(doc, index=0, start_s=1.0, shift_s=1.0)
    with pytest.raises(TimingError):
        nudged(doc, index=0, shift_s=99.0)
    with pytest.raises(TimingError):
        nudged(doc, index=0, start_s=-1.0)


def test_read_write_round_trip(tmp_path: Path):
    doc = estimated_timing("A sentence here. Another one.", 5.0)
    path = write_timing(tmp_path / "05_voice" / "timing.json", doc)
    back = read_timing(path)
    assert back is not None
    assert back.model_dump(exclude={"generated_at"}) == doc.model_dump(exclude={"generated_at"})
    assert [s.text for s in from_timing_doc(back)] == ["A sentence here.", "Another one."]
    assert read_timing(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert read_timing(tmp_path / "bad.json") is None
