"""Sentence splitting and the estimated timing (edit/sentences.py)."""

from __future__ import annotations

import pytest

from sigma_editor.edit.caption_files import ImportedCue
from sigma_editor.edit.sentences import (
    estimate_sentences,
    move_start,
    sentences_from_cues,
    sentences_from_text,
    shift_from,
    split_paragraphs,
    split_sentences,
)

TEXT = """First sentence here. Second one is a bit longer than the first!
Still the first paragraph? Yes.

A new paragraph starts. Mr. Smith paid 3.50 dollars... and left.
"""


def test_split_paragraphs_joins_single_breaks():
    paragraphs = split_paragraphs(TEXT)
    assert len(paragraphs) == 2
    assert paragraphs[0].startswith("First sentence here. Second one")
    assert "Still the first paragraph? Yes." in paragraphs[0]


def test_split_sentences_keeps_abbreviations_and_decimals():
    sentences = split_sentences("A new paragraph starts. Mr. Smith paid 3.50 dollars... and left.")
    assert sentences == ["A new paragraph starts.", "Mr. Smith paid 3.50 dollars... and left."]
    assert split_sentences('He said "Go!" Then he left.') == ['He said "Go!"', "Then he left."]
    assert split_sentences("") == []


def test_sentences_from_text_numbers_paragraphs():
    parts = sentences_from_text(TEXT)
    assert [p for p, _ in parts] == [0, 0, 0, 0, 1, 1]
    assert parts[3] == (0, "Yes.")


def test_estimate_over_a_narration_shares_time_by_words():
    sentences = estimate_sentences("One two three four. Five six.", 10.0)
    assert [s.id for s in sentences] == ["s-001", "s-002"]
    assert sentences[0].start_s == 0.0
    assert sentences[-1].end_s == 10.0
    # 4 words against 2 words, with a 0.25 s pause between: (10 - 0.25) * 4 / 6 = 6.5 s
    assert sentences[0].end_s == pytest.approx(6.5, abs=0.01)
    assert sentences[1].start_s == pytest.approx(6.75, abs=0.01)
    assert [w.text for w in sentences[0].words] == ["One", "two", "three", "four."]
    assert sentences[0].words[0].start_s == 0.0
    assert sentences[0].words[-1].end_s == sentences[0].end_s
    assert sentences[1].words[-1].end_s == 10.0


def test_estimate_without_narration_uses_words_per_minute():
    sentences = estimate_sentences("One two three four five.\n\nSix seven eight nine ten.", None)
    assert sentences[0].duration_s == pytest.approx(2.0)
    assert sentences[1].start_s == pytest.approx(2.6)  # the paragraph pause
    assert sentences[1].paragraph == 1


def test_estimate_short_narration_shrinks_pauses():
    sentences = estimate_sentences("A. B. C. D.", 2.0)
    assert len(sentences) == 4
    assert sentences[-1].end_s == 2.0
    for previous, current in zip(sentences, sentences[1:], strict=False):
        assert current.start_s >= previous.end_s


def test_sentences_from_cues_joins_at_sentence_ends():
    cues = [
        ImportedCue(1.0, 2.0, "Hello there,"),
        ImportedCue(2.0, 3.0, "how are you?"),
        ImportedCue(3.5, 5.0, "Fine."),
        ImportedCue(5.0, 6.0, "And you"),
    ]
    sentences = sentences_from_cues(cues)
    assert [s.text for s in sentences] == ["Hello there, how are you?", "Fine.", "And you"]
    assert (sentences[0].start_s, sentences[0].end_s) == (1.0, 3.0)
    assert [w.text for w in sentences[0].words] == ["Hello", "there,", "how", "are", "you?"]
    assert sentences[0].words[2].start_s == 2.0
    assert (sentences[2].start_s, sentences[2].end_s) == (5.0, 6.0)


def test_shift_from_moves_later_sentences_not_before_zero():
    sentences = estimate_sentences("One two. Three four. Five six.", 9.0)
    second_start = sentences[1].start_s
    shift_from(sentences, 1, 0.5)
    assert sentences[1].start_s == pytest.approx(second_start + 0.5)
    assert sentences[2].words[0].start_s == pytest.approx(sentences[2].start_s)
    shift_from(sentences, 0, -5.0)
    assert sentences[0].start_s == 0.0


def test_move_start_keeps_order_and_minimum_lengths():
    sentences = estimate_sentences("One two. Three four. Five six.", 9.0)
    move_start(sentences, 1, 1.0)
    assert sentences[1].start_s == 1.0
    assert sentences[0].end_s == 1.0
    assert sentences[0].words[-1].end_s == 1.0
    move_start(sentences, 1, 0.1)  # too early: the first sentence keeps its minimum length
    assert sentences[1].start_s == pytest.approx(0.6)
    move_start(sentences, 2, 100.0)  # too late: stays before its own end
    assert sentences[2].start_s <= sentences[2].end_s - 0.6
