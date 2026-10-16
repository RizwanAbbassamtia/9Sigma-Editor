"""``05_voice/timing.json`` for an edit: made from the estimate or a caption file, read back
for the timing tool, and nudged by the person (docs/CONTRACT-v0.1.md section 4).

The sentences of ``edit/sentences.py`` are the working shape; :class:`TimingDoc`
(``models/timing.py``) is what the render engine and the live preview read. Both directions
are here so nothing else has to know the file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..models.timing import DEFAULT_SAMPLE_RATE, TimedSentence, TimedWord, TimingDoc
from ..storage.files import atomic_write_text
from .caption_files import ImportedCue
from .sentences import (
    Sentence,
    Word,
    estimate_sentences,
    move_start,
    sentences_from_cues,
    shift_from,
)

TIMING_FILE = "timing.json"
MAX_SHIFT_S = 30.0


class TimingError(ValueError):
    """Plain English, safe to show."""


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
    """The engine's timing document for ``sentences`` over a narration ``duration_s`` long
    (0 when there is no narration: the estimate's own length is used)."""
    total = max(float(duration_s), 0.0)
    if sentences:
        total = max(total, max(sentence.end_s for sentence in sentences))
    rows: list[TimedSentence] = []
    for sentence in sentences:
        rows.append(
            TimedSentence(
                id=sentence.id,
                text=sentence.text,
                start_s=round(sentence.start_s, 3),
                end_s=round(max(sentence.end_s, sentence.start_s), 3),
                words=[
                    TimedWord(text=word.text, start_s=round(word.start_s, 3),
                              end_s=round(max(word.end_s, word.start_s), 3))
                    for word in sentence.words
                ],
                words_source="estimated",
                start_frame=int(round(sentence.start_s * sample_rate)),
                end_frame=int(round(max(sentence.end_s, sentence.start_s) * sample_rate)),
                paragraph=sentence.paragraph,
            )
        )
    return TimingDoc(
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


def nudged(doc: TimingDoc, *, index: int, start_s: float | None = None,
           shift_s: float | None = None) -> TimingDoc:
    """The timing tool's two moves: drag sentence ``index`` to start at ``start_s``, or shift
    it and everything after it by ``shift_s`` seconds (at most 30 s either way)."""
    if index < 0 or index >= len(doc.sentences):
        raise TimingError("That sentence is not in the timing.")
    if (start_s is None) == (shift_s is None):
        raise TimingError("Say either where the sentence should start or how far to shift.")
    sentences = from_timing_doc(doc)
    if start_s is not None:
        if start_s < 0:
            raise TimingError("A sentence cannot start before the narration does.")
        move_start(sentences, index, start_s)
    else:
        assert shift_s is not None
        if abs(shift_s) > MAX_SHIFT_S:
            raise TimingError(f"A shift is at most {MAX_SHIFT_S:g} seconds at a time.")
        shift_from(sentences, index, shift_s)
    updated = to_timing_doc(
        sentences, doc.duration_s, source=doc.source, language=doc.language,
        sample_rate=doc.sample_rate, nudged=True, warnings=doc.warnings,
    )
    updated.timing_confidence = doc.timing_confidence
    return updated


def read_timing(path: Path | str) -> TimingDoc | None:
    """The timing file, or ``None`` when it does not exist or cannot be read."""
    source = Path(path)
    try:
        return TimingDoc.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def write_timing(path: Path | str, doc: TimingDoc) -> Path:
    return atomic_write_text(Path(path), doc.model_dump_json(indent=2) + "\n")
