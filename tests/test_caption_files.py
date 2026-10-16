"""SRT and WebVTT reading (edit/caption_files.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sigma_editor.edit.caption_files import (
    CaptionFileError,
    ImportedCue,
    clean_text,
    parse_captions,
    parse_time,
    read_caption_file,
    write_srt,
)

SRT = """﻿1
00:00:01,000 --> 00:00:03,500
Hello there.

2
00:00:03,600 --> 00:00:06,000
<i>This is</i> the second line
with two rows.

3
00:00:07,000 --> 00:00:06,000
Ends before it starts.

4
00:00:08,000 --> 00:00:09,000

"""

VTT = """WEBVTT - some title

NOTE a comment
that spans lines

STYLE
::cue { color: lime }

intro
00:01.000 --> 00:02.500 align:start position:10%
<v Narrator>Welcome <c.loud>back</c>.

00:00:02.600 --> 00:00:04.000
Word <00:00:03.000>by <00:00:03.500>word
"""


def test_parse_time_forms():
    assert parse_time("00:00:01,000") == 1.0
    assert parse_time("01:02:03.456") == pytest.approx(3723.456)
    assert parse_time("02:03.5") == pytest.approx(123.5)
    assert parse_time("2:03") == 123.0
    assert parse_time("1:75") is None
    assert parse_time("abc") is None


def test_parse_srt_drops_bad_cues_and_tags():
    cues = parse_captions(SRT)
    assert cues == [
        ImportedCue(1.0, 3.5, "Hello there."),
        ImportedCue(3.6, 6.0, "This is the second line\nwith two rows."),
    ]


def test_parse_vtt_headers_notes_styles_and_inline_tags():
    cues = parse_captions(VTT)
    assert cues == [
        ImportedCue(1.0, 2.5, "Welcome back."),
        ImportedCue(2.6, 4.0, "Word by word"),
    ]


def test_cues_sorted_by_time():
    text = "1\n00:00:05,000 --> 00:00:06,000\nlater\n\n2\n00:00:01,000 --> 00:00:02,000\nearlier\n"
    assert [cue.text for cue in parse_captions(text)] == ["earlier", "later"]


def test_clean_text_keeps_words_drops_markup():
    assert clean_text("{\\an8}<b>Bold</b> &amp; plain  \n\n  second") == "Bold & plain\nsecond"


def test_read_file_utf16_and_errors(tmp_path: Path):
    path = tmp_path / "a.srt"
    path.write_text(SRT, encoding="utf-16")
    assert len(read_caption_file(path)) == 2
    empty = tmp_path / "empty.vtt"
    empty.write_text("WEBVTT\n\n", encoding="utf-8")
    with pytest.raises(CaptionFileError) as excinfo:
        read_caption_file(empty)
    assert "No captions were found in empty.vtt" in str(excinfo.value)
    with pytest.raises(CaptionFileError):
        read_caption_file(tmp_path / "missing.srt")


def test_write_srt_round_trip():
    cues = [ImportedCue(1.0, 3.5, "Hello there."), ImportedCue(3.6, 6.0, "Two\nrows")]
    text = write_srt(cues)
    assert "00:00:01,000 --> 00:00:03,500" in text
    assert parse_captions(text) == cues
