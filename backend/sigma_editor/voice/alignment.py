"""The narration timing from the provider's character times (docs/CONTRACT-v0.2.md section
5). The text is split into sentences and words exactly as the rest of the app does
(``edit/sentences.py``), every word is matched to its characters in the provider's list (the
characters follow the text one to one, whitespace aside), and the sentences tile the
recording as ``edit/timing_doc.py`` describes: the first from the start of the file, each
to the start of the next, the last to the end. When the times do not fit the text, the
timing is estimated from the text instead and the document's ``warnings`` say so.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..edit.sentences import sentence_id, sentences_from_text, word_tokens
from ..edit.timing_doc import TimingError, estimated_timing
from ..models.timing import DEFAULT_SAMPLE_RATE, TimedSentence, TimedWord, TimingDoc
from ..models.voice import PROVIDER_ID

NO_TIMES = (
    "ElevenLabs sent no word times for this voice, so the timing was estimated from the "
    "text. Check the sentence starts in the timing tool."
)
MISMATCH = (
    "The word times ElevenLabs sent did not match the text, so the timing was estimated "
    "from the text. Check the sentence starts in the timing tool."
)

Span = tuple[float, float]


class AlignmentMismatch(ValueError):
    """The provider's characters do not follow the text."""


def _skippable(character: str) -> bool:
    return not character or character.isspace()


def word_spans(
    sentences: list[str], characters: list[str], starts: list[float], ends: list[float]
) -> list[list[Span]]:
    """For every sentence, the ``(start, end)`` of each of its words: the start of its first
    character and the end of its last in the provider's list, walked in order with the
    whitespace skipped. :class:`AlignmentMismatch` when the characters do not follow the
    text (or the provider spoke more than the text)."""
    if not (len(characters) == len(starts) == len(ends)):
        raise AlignmentMismatch("the character times do not line up")
    position, total = 0, len(characters)
    result: list[list[Span]] = []
    for sentence in sentences:
        spans: list[Span] = []
        for token in word_tokens(sentence):
            first: float | None = None
            last = 0.0
            for wanted in token:
                while position < total and _skippable(characters[position]):
                    position += 1
                if position >= total or characters[position] != wanted:
                    raise AlignmentMismatch(f"the characters stop fitting at {token!r}")
                if first is None:
                    first = float(starts[position])
                last = float(ends[position])
                position += 1
            spans.append((first if first is not None else 0.0, last))
        result.append(spans)
    while position < total and _skippable(characters[position]):
        position += 1
    if position < total:
        raise AlignmentMismatch("the provider spoke more than the text")
    return result


def timing_from_alignment(
    text: str,
    characters: list[str],
    starts: list[float],
    ends: list[float],
    *,
    duration_s: float,
    language: str = "",
    voice_id: str = "",
    model_id: str = "",
) -> TimingDoc:
    """The timing document of ``text`` spoken over ``duration_s`` seconds, with the sentence
    and word times from the provider's character times (``source: "aligned"``), or
    estimated from the text, with a warning, when the times do not fit the text."""
    parts = sentences_from_text(text)
    if not parts:
        raise TimingError("The text has no sentences.")
    if not characters:
        return _estimated(text, duration_s, language, voice_id, model_id, NO_TIMES)
    try:
        spans = word_spans([sentence for _, sentence in parts], characters, starts, ends)
    except AlignmentMismatch:
        return _estimated(text, duration_s, language, voice_id, model_id, MISMATCH)

    # Words never run backwards, and the recording is never shorter than its last word.
    clock = 0.0
    ordered: list[list[Span]] = []
    for sentence_spans in spans:
        rows: list[Span] = []
        for start, end in sentence_spans:
            start = max(round(start, 3), clock)
            end = max(round(end, 3), start)
            rows.append((start, end))
            clock = end
        ordered.append(rows)

    # The start marks tile the recording: the first from 0, each sentence to the next mark,
    # the last to the end of the file. The timeline is the same spans end to end.
    rate = DEFAULT_SAMPLE_RATE
    total_frames = int(round(max(float(duration_s), clock, 0.0) * rate))
    marks: list[int] = []
    for index, rows in enumerate(ordered):
        if index == 0:
            marks.append(0)
            continue
        mark = int(round(rows[0][0] * rate)) if rows else marks[-1]
        marks.append(min(max(mark, marks[-1]), total_frames))
    sentences: list[TimedSentence] = []
    for index, ((paragraph, sentence_text), rows) in enumerate(zip(parts, ordered, strict=True)):
        start_frame = marks[index]
        end_frame = marks[index + 1] if index + 1 < len(marks) else total_frames
        end_frame = max(end_frame, start_frame)
        start_s = round(start_frame / rate, 3)
        end_s = round(end_frame / rate, 3)
        words: list[TimedWord] = []
        for token, (start, end) in zip(word_tokens(sentence_text), rows, strict=True):
            word_start = min(max(start, start_s), end_s)
            word_end = min(max(end, word_start), end_s)
            words.append(TimedWord(text=token, start_s=word_start, end_s=word_end, confidence=1.0))
        sentences.append(
            TimedSentence(
                id=sentence_id(index + 1),
                text=sentence_text,
                start_s=start_s,
                end_s=end_s,
                words=words,
                words_source="provider",
                start_frame=start_frame,
                end_frame=end_frame,
                paragraph=paragraph,
            )
        )
    return TimingDoc(
        sample_rate=rate,
        duration_s=round(total_frames / rate, 3),
        source="aligned",
        sentences=sentences,
        timing_confidence="high",
        provider=PROVIDER_ID,
        model=model_id,
        voice_id=voice_id,
        language=language,
        aligner="none",
        own_recording=False,
        nudged=False,
        generated_at=datetime.now(UTC),
        warnings=[],
    )


def _estimated(
    text: str, duration_s: float, language: str, voice_id: str, model_id: str, warning: str
) -> TimingDoc:
    """The text shared out over the recording (``edit/timing_doc.py``), marked as the AI
    voice's, with ``warning`` for the person."""
    doc = estimated_timing(text, duration_s if duration_s > 0 else None, language=language)
    return doc.model_copy(
        update={
            "provider": PROVIDER_ID,
            "model": model_id,
            "voice_id": voice_id,
            "own_recording": False,
            "warnings": [*doc.warnings, warning],
        }
    )


__all__ = ["MISMATCH", "NO_TIMES", "AlignmentMismatch", "timing_from_alignment", "word_spans"]
