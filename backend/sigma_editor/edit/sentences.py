"""Sentences and their estimated times, for an edit made from the person's own files.

Without a speech-to-text tool the app only knows the narration's length, so it shares that
length out over the sentences by word count (docs/CONTRACT-v0.1.md section 3): a sentence with
twice the words gets twice the time, with a short pause between sentences and a longer one
between paragraphs. The words inside a sentence are spread evenly. The timing tool in the
editor then lets the person drag the sentence starts to where they really are.

The same shapes serve captions from a file: the cues are joined into sentences at sentence
ends (``.``, ``!``, ``?``) with their real times, so scenes can follow them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .caption_files import ImportedCue

SENTENCE_END = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+(?=\S)")
_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "etc.",
                  "e.g.", "i.e.", "no.", "fig.", "inc.", "ltd.", "co."}
PAUSE_SENTENCE_S = 0.25
PAUSE_PARAGRAPH_S = 0.6
MIN_SENTENCE_S = 0.6
SECONDS_PER_WORD_DEFAULT = 0.4
"""150 words a minute when there is no narration to measure (about 6 s per picture for a
15-word sentence, the contract's fallback length)."""


@dataclass(frozen=True)
class Word:
    text: str
    start_s: float
    end_s: float


@dataclass
class Sentence:
    id: str
    text: str
    start_s: float
    end_s: float
    paragraph: int = 0
    words: list[Word] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 3)


def split_paragraphs(text: str) -> list[str]:
    """Paragraphs = runs of text separated by a blank line; single line breaks join."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", normalised)]
    return [part for part in paragraphs if part]


def split_sentences(paragraph: str) -> list[str]:
    """Sentences of one paragraph: cut after ``.``, ``!``, ``?`` (and closing quotes) when a
    word follows; common abbreviations and decimal numbers do not cut."""
    text = " ".join(paragraph.split())
    if not text:
        return []
    pieces: list[str] = []
    start = 0
    for match in SENTENCE_END.finditer(text):
        candidate = text[start:match.end()].strip()
        last_word = candidate.split()[-1].lower() if candidate.split() else ""
        if last_word in _ABBREVIATIONS:
            continue
        following = text[match.end():match.end() + 1]
        if following.islower():  # "dollars... and left": the sentence goes on
            continue
        pieces.append(candidate)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def word_tokens(text: str) -> list[str]:
    return [token for token in text.split() if token.strip()]


def sentences_from_text(text: str) -> list[tuple[int, str]]:
    """``(paragraph index, sentence)`` for every sentence of the typed text, in order."""
    result: list[tuple[int, str]] = []
    for number, paragraph in enumerate(split_paragraphs(text)):
        for sentence in split_sentences(paragraph):
            result.append((number, sentence))
    return result


def _spread_words(text: str, start: float, end: float) -> list[Word]:
    tokens = word_tokens(text)
    if not tokens:
        return []
    span = max(end - start, 0.0)
    total = sum(len(token) for token in tokens) or 1
    clock = start
    words: list[Word] = []
    for token in tokens:
        length = span * len(token) / total
        words.append(Word(token, round(clock, 3), round(clock + length, 3)))
        clock += length
    if words:
        words[-1] = Word(words[-1].text, words[-1].start_s, round(end, 3))
    return words


def sentence_id(number: int) -> str:
    return f"s-{number:03d}"


def estimate_sentences(
    text: str,
    duration_s: float | None,
    *,
    pause_sentence_s: float = PAUSE_SENTENCE_S,
    pause_paragraph_s: float = PAUSE_PARAGRAPH_S,
    seconds_per_word: float = SECONDS_PER_WORD_DEFAULT,
) -> list[Sentence]:
    """The sentences of ``text`` with estimated times over ``duration_s`` seconds of narration
    (shared out by word count), or, without a narration, at ``seconds_per_word``."""
    parts = sentences_from_text(text)
    if not parts:
        return []
    counts = [max(1, len(word_tokens(sentence))) for _, sentence in parts]
    pauses: list[float] = []
    for index in range(1, len(parts)):
        same_paragraph = parts[index][0] == parts[index - 1][0]
        pauses.append(pause_sentence_s if same_paragraph else pause_paragraph_s)
    if duration_s is None or duration_s <= 0:
        speech = [count * seconds_per_word for count in counts]
    else:
        pause_total = sum(pauses)
        available = duration_s - pause_total
        if available < MIN_SENTENCE_S * len(parts):  # pauses would eat the time: shrink them
            scale = max(duration_s - MIN_SENTENCE_S * len(parts), 0.0) / (pause_total or 1.0)
            pauses = [pause * scale for pause in pauses]
            available = duration_s - sum(pauses)
        total_words = sum(counts)
        speech = [available * count / total_words for count in counts]
    sentences: list[Sentence] = []
    clock = 0.0
    for index, ((paragraph, sentence_text), length) in enumerate(zip(parts, speech, strict=True)):
        start = round(clock, 3)
        end = round(clock + max(length, 0.0), 3)
        sentences.append(
            Sentence(
                id=sentence_id(index + 1), text=sentence_text, start_s=start, end_s=end,
                paragraph=paragraph, words=_spread_words(sentence_text, start, end),
            )
        )
        clock = end + (pauses[index] if index < len(pauses) else 0.0)
    if duration_s is not None and duration_s > 0 and sentences:
        last = sentences[-1]
        last.end_s = round(duration_s, 3)  # the narration's own end, whatever rounding did
        last.words = _spread_words(last.text, last.start_s, last.end_s)
    return sentences


def sentences_from_cues(cues: list[ImportedCue]) -> list[Sentence]:
    """Join caption cues into sentences: a sentence ends with the cue whose text ends in
    ``.``, ``!`` or ``?`` (or at the last cue); its time runs from its first cue's start to
    its last cue's end, and the words are spread over the cues they come from."""
    sentences: list[Sentence] = []
    group: list[ImportedCue] = []

    def flush() -> None:
        if not group:
            return
        text = " ".join(" ".join(cue.text.split()) for cue in group)
        start, end = group[0].start_s, max(cue.end_s for cue in group)
        words: list[Word] = []
        for cue in group:
            words.extend(_spread_words(" ".join(cue.text.split()), cue.start_s, cue.end_s))
        sentences.append(
            Sentence(id=sentence_id(len(sentences) + 1), text=text, start_s=start, end_s=end,
                     words=words)
        )
        group.clear()

    for cue in cues:
        group.append(cue)
        if cue.text.rstrip().endswith((".", "!", "?", "…")):
            flush()
    flush()
    for previous, current in zip(sentences, sentences[1:], strict=False):
        if current.start_s < previous.end_s:
            previous.end_s = current.start_s  # cues that overlapped: no sentence runs back
    return sentences


def shift_from(sentences: list[Sentence], index: int, delta_s: float) -> list[Sentence]:
    """"Shift all after this by delta": every sentence from ``index`` on moves by
    ``delta_s`` (words included); nothing moves before 0."""
    if index < 0 or index >= len(sentences):
        return sentences
    earliest = min(sentence.start_s for sentence in sentences[index:])
    delta = max(delta_s, -earliest)
    for sentence in sentences[index:]:
        sentence.start_s = round(sentence.start_s + delta, 3)
        sentence.end_s = round(sentence.end_s + delta, 3)
        sentence.words = [
            Word(word.text, round(word.start_s + delta, 3), round(word.end_s + delta, 3))
            for word in sentence.words
        ]
    return sentences


def move_start(sentences: list[Sentence], index: int, start_s: float,
               *, min_sentence_s: float = MIN_SENTENCE_S) -> list[Sentence]:
    """Drag one sentence's start to ``start_s``: the sentence before it ends there (keeping at
    least ``min_sentence_s``), this sentence keeps its end, and the words of both are spread
    again over their new lengths."""
    if index < 0 or index >= len(sentences):
        return sentences
    current = sentences[index]
    lower = 0.0 if index == 0 else sentences[index - 1].start_s + min_sentence_s
    upper = current.end_s - min_sentence_s
    target = round(min(max(start_s, lower), max(upper, lower)), 3)
    if index > 0:
        previous = sentences[index - 1]
        previous.end_s = max(target, previous.start_s + min_sentence_s)
        previous.words = _spread_words(previous.text, previous.start_s, previous.end_s)
    current.start_s = target
    if current.end_s < current.start_s + min_sentence_s:
        current.end_s = round(current.start_s + min_sentence_s, 3)
    current.words = _spread_words(current.text, current.start_s, current.end_s)
    return sentences
