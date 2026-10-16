"""``05_voice/timing.json`` for an edit: made from the estimate or a caption file, read back
for the timing tool, nudged by the person and re-laid when scenes move
(docs/CONTRACT-v0.1.md section 4).

Two clocks live in the file:

* ``start_frame`` / ``end_frame``: where a sentence's audio sits in ``voice.wav`` (samples).
  The spans tile the recording: a sentence runs from its start mark to the next sentence's
  start mark in recording order (the pause after it travels with it), the first from the
  start of the file and the last to its end. The marks are what the timing tool moves.
* ``start_s`` / ``end_s``: where the sentence plays on the timeline. The sentences of the
  file are in timeline order; the timeline is the spans laid end to end, so reordering
  scenes reorders the sentences and the audio follows. The words are spread inside the
  sentence's timeline span.

The sentences of ``edit/sentences.py`` are the working shape; :class:`TimingDoc`
(``models/timing.py``) is what the render engine and the live preview read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..models.timeline import VoiceSegment
from ..models.timing import DEFAULT_SAMPLE_RATE, TimedSentence, TimedWord, TimingDoc
from ..storage.files import atomic_write_text
from .caption_files import ImportedCue
from .sentences import Sentence, Word, estimate_sentences, sentences_from_cues, word_tokens

TIMING_FILE = "timing.json"
MAX_SHIFT_S = 30.0
MIN_SENTENCE_S = 0.3


class TimingError(ValueError):
    """Plain English, safe to show."""


def _spread_words(text: str, start: float, end: float) -> list[TimedWord]:
    tokens = word_tokens(text)
    if not tokens:
        return []
    span = max(end - start, 0.0)
    total = sum(len(token) for token in tokens) or 1
    clock = start
    words: list[TimedWord] = []
    for token in tokens:
        length = span * len(token) / total
        words.append(TimedWord(text=token, start_s=round(clock, 3), end_s=round(clock + length, 3)))
        clock += length
    if words:
        words[-1] = TimedWord(text=words[-1].text, start_s=words[-1].start_s, end_s=round(end, 3))
    return words


def relayout(doc: TimingDoc) -> TimingDoc:
    """Make the spans tile the recording (in recording order) and lay the sentences end to
    end on the timeline (in the file's order). Words are spread again."""
    rate = doc.sample_rate or DEFAULT_SAMPLE_RATE
    total_frames = int(round(doc.duration_s * rate))
    rows = list(doc.sentences)
    if not rows:
        return doc
    marks = sorted(range(len(rows)), key=lambda i: (rows[i].start_frame or 0, i))
    spans: dict[int, tuple[int, int]] = {}
    for position, index in enumerate(marks):
        start = 0 if position == 0 else int(rows[index].start_frame or 0)
        if position + 1 < len(marks):
            end = int(rows[marks[position + 1]].start_frame or 0)
        else:
            end = max(total_frames, start)
        spans[index] = (start, max(end, start))
    clock = 0.0
    laid: list[TimedSentence] = []
    for index, row in enumerate(rows):
        start_frame, end_frame = spans[index]
        length = (end_frame - start_frame) / rate
        start_s = round(clock, 3)
        end_s = round(clock + length, 3)
        laid.append(
            row.model_copy(
                update={
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_s": start_s,
                    "end_s": end_s,
                    "words": _spread_words(row.text, start_s, end_s),
                }
            )
        )
        clock = end_s
    updated = doc.model_copy(update={"sentences": laid})
    updated.duration_s = round(max(doc.duration_s, clock), 3)
    return updated


def to_timing_doc(
    sentences: list[Sentence],
    duration_s: float,
    *,
    source: str = "estimated",
    language: str = "",
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    nudged: bool = False,
    warnings: list[str] | None = None,
) -> TimingDoc:
    """The engine's timing document for ``sentences`` (their ``start_s`` are the start marks
    in the recording) over a narration ``duration_s`` long."""
    total = max(float(duration_s), 0.0)
    if sentences:
        total = max(total, max(sentence.end_s for sentence in sentences))
    rows: list[TimedSentence] = []
    for sentence in sentences:
        start_frame = int(round(max(sentence.start_s, 0.0) * sample_rate))
        rows.append(
            TimedSentence(
                id=sentence.id,
                text=sentence.text,
                start_s=round(sentence.start_s, 3),
                end_s=round(max(sentence.end_s, sentence.start_s), 3),
                words=[],
                words_source="estimated",
                start_frame=start_frame,
                end_frame=start_frame,
                paragraph=sentence.paragraph,
            )
        )
    doc = TimingDoc(
        sample_rate=sample_rate,
        duration_s=round(total, 3),
        source=source,  # type: ignore[arg-type]
        sentences=rows,
        timing_confidence="medium" if source == "captions" else "low",
        language=language,
        own_recording=True,
        nudged=nudged,
        generated_at=datetime.now(UTC),
        warnings=list(warnings or []),
    )
    return relayout(doc)


def from_timing_doc(doc: TimingDoc) -> list[Sentence]:
    return [
        Sentence(
            id=row.id,
            text=row.text,
            start_s=row.start_s,
            end_s=row.end_s,
            paragraph=row.paragraph,
            words=[Word(word.text, word.start_s, word.end_s) for word in row.words],
        )
        for row in doc.sentences
    ]


def estimated_timing(text: str, duration_s: float | None, *, language: str = "") -> TimingDoc:
    """Typed caption text shared out over the narration (or at 150 words a minute)."""
    sentences = estimate_sentences(text, duration_s)
    if not sentences:
        raise TimingError("The caption text has no sentences.")
    return to_timing_doc(sentences, duration_s or 0.0, source="estimated", language=language)


def timing_from_cues(cues: list[ImportedCue], duration_s: float | None, *,
                     language: str = "") -> TimingDoc:
    """A caption file's own times, joined into sentences."""
    sentences = sentences_from_cues(cues)
    if not sentences:
        raise TimingError("The caption file has no cues with text.")
    return to_timing_doc(sentences, duration_s or 0.0, source="captions", language=language)


def scene_timing(lengths: list[float], *, language: str = "") -> TimingDoc:
    """One empty sentence per scene, ``lengths`` seconds each: the clock of an edit with no
    narration text (pictures 6 s each, clips their own length)."""
    if not lengths:
        raise TimingError("There are no scenes to time.")
    sentences: list[Sentence] = []
    clock = 0.0
    for number, length in enumerate(lengths, start=1):
        start = round(clock, 3)
        end = round(clock + max(float(length), MIN_SENTENCE_S), 3)
        sentences.append(Sentence(id=f"s-{number:03d}", text="", start_s=start, end_s=end))
        clock = end
    return to_timing_doc(sentences, clock, source="scenes", language=language)


# The timing tool -------------------------------------------------------------------------------


def _recording_order(doc: TimingDoc) -> list[int]:
    return sorted(range(len(doc.sentences)), key=lambda i: (doc.sentences[i].start_frame or 0, i))


def nudged(doc: TimingDoc, *, index: int, start_s: float | None = None,
           shift_s: float | None = None) -> TimingDoc:
    """The timing tool's two moves on sentence ``index`` (timeline order): drag its start
    mark to ``start_s`` seconds into the recording, or shift its mark and every later mark
    (in recording order) by ``shift_s`` seconds (at most 30 s either way)."""
    if index < 0 or index >= len(doc.sentences):
        raise TimingError("That sentence is not in the timing.")
    if (start_s is None) == (shift_s is None):
        raise TimingError("Say either where the sentence should start or how far to shift.")
    rate = doc.sample_rate or DEFAULT_SAMPLE_RATE
    total = int(round(doc.duration_s * rate))
    order = _recording_order(doc)
    position = order.index(index)
    marks = [int(doc.sentences[i].start_frame or 0) for i in order]
    minimum = int(MIN_SENTENCE_S * rate)
    if start_s is not None:
        if start_s < 0:
            raise TimingError("A sentence cannot start before the recording does.")
        wanted = int(round(start_s * rate))
        lower = 0 if position == 0 else marks[position - 1] + minimum
        upper = (total if position + 1 >= len(marks) else marks[position + 1]) - minimum
        marks[position] = max(lower, min(wanted, max(upper, lower)))
    else:
        assert shift_s is not None
        if abs(shift_s) > MAX_SHIFT_S:
            raise TimingError(f"A shift is at most {MAX_SHIFT_S:g} seconds at a time.")
        delta = int(round(shift_s * rate))
        lower = 0 if position == 0 else marks[position - 1] + minimum
        delta = max(delta, lower - marks[position])
        last = marks[-1] + delta
        if last > total - minimum:
            delta -= last - (total - minimum)
        for later in range(position, len(marks)):
            marks[later] = max(marks[later] + delta, 0)
    rows = list(doc.sentences)
    for mark, i in zip(marks, order, strict=True):
        rows[i] = rows[i].model_copy(update={"start_frame": mark})
    updated = doc.model_copy(update={"sentences": rows, "nudged": True})
    return relayout(updated)


def reordered(doc: TimingDoc, order: list[int]) -> TimingDoc:
    """The sentences in a new timeline order (``order`` lists the old indexes); the audio
    spans stay where they are in the recording."""
    if sorted(order) != list(range(len(doc.sentences))):
        raise TimingError("The new order must list every sentence exactly once.")
    rows = [doc.sentences[i] for i in order]
    return relayout(doc.model_copy(update={"sentences": rows}))


def voice_segments_of(doc: TimingDoc) -> list[VoiceSegment]:
    """The voice track as clips: each sentence's span of the recording, played where the
    sentence sits on the timeline."""
    rate = doc.sample_rate or DEFAULT_SAMPLE_RATE
    segments: list[VoiceSegment] = []
    for row in doc.sentences:
        start = (row.start_frame or 0) / rate
        end = (row.end_frame or 0) / rate
        if end - start <= 1e-6:
            continue
        segments.append(
            VoiceSegment(
                sentence_id=row.id, src_start_s=round(start, 6), src_end_s=round(end, 6),
                at_s=round(row.start_s, 6),
            )
        )
    return segments


def read_timing(path: Path | str) -> TimingDoc | None:
    """The timing file, or ``None`` when it does not exist or cannot be read."""
    source = Path(path)
    try:
        return TimingDoc.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def write_timing(path: Path | str, doc: TimingDoc) -> Path:
    return atomic_write_text(Path(path), doc.model_dump_json(indent=2) + "\n")
