"""Captions: the words of ``timing.json`` grouped into cues, and the ASS file libass burns in.

Rules (docs/M3-M4-CONTRACT.md section 3): a cue holds at most ``max_chars_per_line *
max_lines`` characters, breaks at sentence ends and at pauses longer than ``pause_break_s``,
and uses the language's font and direction from ``config/captions.yaml`` (Arabic is
right-to-left in Noto Naskh Arabic, Hindi uses Noto Sans Devanagari, Japanese breaks by
character count in Noto Sans JP, Korean uses Noto Sans KR, everything else Noto Sans).

The ASS file is written in a reference resolution (1920x1080 or 1080x1920) and libass scales
it to the frame, so the same file serves the proxy and every preset: style ``\\an2`` (bottom
centre), outline 2, shadow 0, 60 px bottom margin at 1080p.

**Look presets** (M5.1, ``captions.style.preset``; ``presets:`` in ``config/captions.yaml``):

* ``bold-outline`` (default): one event per cue, as above;
* ``active-word``: the cue stays on screen and the word being spoken takes
  ``captions.style.active_colour``: one event per word, from that word's start to the next
  word's start (the last to the cue's end), each holding the whole cue with that word
  coloured (``{\\1c&H..&}``); events touch, so nothing flickers;
* ``single-word-pop``: one word at a time (same slices), growing from 100 % to
  ``pop_scale`` (112 %) over ``pop_s`` (0.12 s) with ``\\t``; ``layout: single_word`` gives
  the single words without the pop for any preset;
* ``boxed``: the text on a see-through box (``BorderStyle 3``, the box in
  ``box_colour`` at ``box_opacity``, ``box_padding_px`` around the text at 1080p).

The word times come from ``timing.json`` (:func:`cue_word_times`): a cue's words are the
timing words (characters for a language without spaces) that start inside it; when the cue
text no longer has the same number of words (edited by hand) its time is shared out by
length instead. The live preview reads the same slices (``cue_words`` of
``GET /api/projects/{id}/timeline`` and ``tools/make_preview_fixtures.py``).
"""

from __future__ import annotations

import bisect
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from ..config_files import load_yaml
from ..models.timeline import (
    CAPTION_PRESETS,
    DEFAULT_ACTIVE_COLOUR,
    DEFAULT_CAPTION_PRESET,
    CaptionCue,
    CaptionStyle,
    normalize_caption_preset,
)
from ..storage.settings_store import atomic_write_text

CAPTIONS_FILE = "captions.yaml"
ASS_FILE = "captions.ass"
DEFAULT_PAUSE_S = 0.35
MIN_CUE_S = 0.4
REFERENCE_LANDSCAPE = (1920, 1080)
REFERENCE_PORTRAIT = (1080, 1920)
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


class WordLike(Protocol):
    text: str
    start_s: float
    end_s: float


class SentenceLike(Protocol):
    text: str
    start_s: float
    end_s: float
    words: Sequence[Any]


# Language rules and named styles -----------------------------------------------------------


@dataclass(frozen=True)
class LanguageRules:
    font: str = "Noto Sans"
    popup_font: str = "Noto Sans"
    rtl: bool = False
    max_chars_per_line: int = 42
    max_lines: int = 2
    space_dependent: bool = True
    size: int = 48


@dataclass(frozen=True)
class NamedStyle:
    name: str = "bold-white"
    primary: str = "#FFFFFF"
    outline_colour: str = "#000000"
    bold: bool = True
    outline: int = 2


@lru_cache(maxsize=1)
def load_captions_config() -> dict[str, Any]:
    return load_yaml(CAPTIONS_FILE)


def clear_caches() -> None:
    load_captions_config.cache_clear()


def pause_break_s() -> float:
    try:
        return float(load_captions_config().get("pause_break_s", DEFAULT_PAUSE_S))
    except (TypeError, ValueError):
        return DEFAULT_PAUSE_S


def _rules_from(raw: Any, base: LanguageRules) -> LanguageRules:
    data = raw if isinstance(raw, dict) else {}
    return LanguageRules(
        font=str(data.get("font") or base.font),
        popup_font=str(data.get("popup_font") or data.get("font") or base.popup_font),
        rtl=bool(data.get("rtl", base.rtl)),
        max_chars_per_line=max(4, int(data.get("max_chars_per_line", base.max_chars_per_line))),
        max_lines=max(1, int(data.get("max_lines", base.max_lines))),
        space_dependent=bool(data.get("space_dependent", base.space_dependent)),
        size=max(8, int(data.get("size", base.size))),
    )


def rules_for(language: str | None) -> LanguageRules:
    """The caption rules of a language (``config/captions.yaml``), ``default`` otherwise."""
    config = load_captions_config()
    default = _rules_from(config.get("default"), LanguageRules())
    by_language = config.get("by_language") if isinstance(config.get("by_language"), dict) else {}
    if language and language in by_language:
        return _rules_from(by_language[language], default)
    return default


def named_style(name: str | None) -> NamedStyle:
    config = load_captions_config()
    styles = config.get("styles") if isinstance(config.get("styles"), dict) else {}
    raw = styles.get(name or "")
    if not isinstance(raw, dict):
        raw = styles.get("bold-white") if isinstance(styles.get("bold-white"), dict) else {}
        name = "bold-white"
    base = NamedStyle()
    return NamedStyle(
        name=str(name or base.name),
        primary=str(raw.get("primary") or base.primary),
        outline_colour=str(raw.get("outline_colour") or base.outline_colour),
        bold=bool(raw.get("bold", base.bold)),
        outline=int(raw.get("outline", base.outline)),
    )


@dataclass(frozen=True)
class CaptionLook:
    """A look preset (``presets:`` in ``config/captions.yaml``; see the module docstring)."""

    id: str = DEFAULT_CAPTION_PRESET
    label: str = "Bold outline"
    layout: str = "phrase"
    active_colour: str = DEFAULT_ACTIVE_COLOUR
    pop_scale: float = 1.0
    pop_s: float = 0.0
    box: bool = False
    box_colour: str = "#000000"
    box_opacity: float = 0.55
    box_padding_px: int = 12

    @property
    def highlights(self) -> bool:
        return self.id == "active-word"

    @property
    def pops(self) -> bool:
        return self.pop_s > 0 and abs(self.pop_scale - 1.0) > 1e-6

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "layout": self.layout,
            "active_colour": self.active_colour, "pop_scale": self.pop_scale,
            "pop_s": self.pop_s, "box": self.box, "box_colour": self.box_colour,
            "box_opacity": self.box_opacity, "box_padding_px": self.box_padding_px,
        }


BUILTIN_LOOKS: dict[str, CaptionLook] = {
    "bold-outline": CaptionLook("bold-outline", "Bold outline"),
    "active-word": CaptionLook("active-word", "Active word"),
    "single-word-pop": CaptionLook(
        "single-word-pop", "Single word pop", layout="single_word", pop_scale=1.12, pop_s=0.12
    ),
    "boxed": CaptionLook("boxed", "Boxed", box=True),
}
"""The presets when ``config/captions.yaml`` says nothing (the file may tune each one)."""


def _look_from(name: str, raw: Any, base: CaptionLook) -> CaptionLook:
    data = raw if isinstance(raw, dict) else {}

    def number(key: str, fallback: float, low: float, high: float) -> float:
        try:
            value = float(data.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return min(max(value, low), high) if math.isfinite(value) else fallback

    layout = str(data.get("layout") or base.layout).strip().lower().replace("-", "_")
    return CaptionLook(
        id=name,
        label=str(data.get("label") or base.label),
        layout=layout if layout in ("phrase", "single_word") else base.layout,
        active_colour=str(data.get("active_colour") or base.active_colour),
        pop_scale=number("pop_scale", base.pop_scale, 1.0, 2.0),
        pop_s=number("pop_s", base.pop_s, 0.0, 2.0),
        box=bool(data.get("box", base.box)),
        box_colour=str(data.get("box_colour") or base.box_colour),
        box_opacity=number("box_opacity", base.box_opacity, 0.0, 1.0),
        box_padding_px=int(number("box_padding_px", base.box_padding_px, 0, 200)),
    )


def caption_looks() -> dict[str, CaptionLook]:
    """Every look preset: the built-in ones tuned by ``presets:`` in captions.yaml."""
    config = load_captions_config()
    raw = config.get("presets") if isinstance(config.get("presets"), dict) else {}
    return {name: _look_from(name, raw.get(name), base) for name, base in BUILTIN_LOOKS.items()}


def caption_look(preset: str | None) -> CaptionLook:
    """The look of ``captions.style.preset`` (unknown names: ``bold-outline``)."""
    looks = caption_looks()
    return looks.get(normalize_caption_preset(preset), looks[DEFAULT_CAPTION_PRESET])


def caption_style_for(
    language: str | None, style_name: str | None = None, preset: str | None = None
) -> CaptionStyle:
    """The timeline's caption style for a language, a named look (colours) and a look
    preset (``bold-outline`` unless named)."""
    rules = rules_for(language)
    look = named_style(style_name)
    chosen = caption_look(preset)
    return CaptionStyle(
        font=rules.font,
        size=rules.size,
        primary=look.primary,
        outline=look.outline,
        position="bottom",
        max_lines=rules.max_lines,
        max_chars_per_line=rules.max_chars_per_line,
        rtl=rules.rtl,
        bold=look.bold,
        outline_colour=look.outline_colour,
        preset=chosen.id,  # type: ignore[arg-type]
        active_colour=chosen.active_colour,
        layout=chosen.layout,  # type: ignore[arg-type]
    )


# Chunking ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Word:
    text: str
    start_s: float
    end_s: float


def _join(words: Sequence[Word], space_dependent: bool) -> str:
    glue = " " if space_dependent else ""
    return glue.join(w.text for w in words)


def _spread(text: str, start: float, end: float, space_dependent: bool) -> list[Word]:
    """``text`` cut into words (or single characters for a language without spaces), each
    given its share of ``start``..``end`` by length."""
    pieces = text.split(" ") if space_dependent else [c for c in text if not c.isspace()]
    total = sum(len(p) for p in pieces) or 1
    span = max(float(end) - float(start), 0.0)
    clock = float(start)
    spread: list[Word] = []
    for piece in pieces:
        length = span * len(piece) / total
        spread.append(Word(piece, round(clock, 3), round(clock + length, 3)))
        clock += length
    return spread


def _words_of(sentence: SentenceLike, space_dependent: bool) -> list[Word]:
    """The sentence's words; a sentence without word times gets them spread by length.

    For a language without spaces (Japanese) the voice tools and the estimator still cut
    on whitespace, so a whole sentence arrives as one "word": every word is re-split into
    its characters (times spread by position) so the cue and line budgets can be kept."""
    words = [
        Word(str(w.text), float(w.start_s), float(w.end_s))
        for w in (sentence.words or [])
        if str(getattr(w, "text", "")).strip()
    ]
    if words and not space_dependent:
        split: list[Word] = []
        for word in words:
            if len(word.text.strip()) <= 1:
                split.append(Word(word.text.strip(), word.start_s, word.end_s))
            else:
                split.extend(_spread(word.text, word.start_s, word.end_s, False))
        return split
    if words:
        return words
    text = " ".join(str(sentence.text or "").split())
    if not text:
        return []
    return _spread(text, float(sentence.start_s), float(sentence.end_s), space_dependent)


def _split_balanced(words: Sequence[Word], budget: int, space_dependent: bool) -> list[list[Word]]:
    """Cut a run of words into the fewest chunks of at most ``budget`` characters, each
    about the same length (no one-word tail)."""
    if not words:
        return []
    text_len = len(_join(words, space_dependent))
    if text_len <= budget:
        return [list(words)]
    count = max(2, math.ceil(text_len / budget))
    while True:
        target = text_len / count
        chunks: list[list[Word]] = []
        current: list[Word] = []
        for index, word in enumerate(words):
            remaining_chunks = count - len(chunks)
            remaining_words = len(words) - index
            if current and remaining_chunks < remaining_words:
                with_word = len(_join([*current, word], space_dependent))
                without = len(_join(current, space_dependent))
                if with_word > budget or (
                    abs(with_word - target) > abs(without - target) and remaining_chunks > 1
                ):
                    chunks.append(current)
                    current = []
            current.append(word)
        if current:
            chunks.append(current)
        if all(len(_join(c, space_dependent)) <= budget for c in chunks) or count >= len(words):
            return chunks
        count += 1


def chunk_cues(
    sentences: Iterable[SentenceLike],
    *,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    pause_s: float = DEFAULT_PAUSE_S,
    space_dependent: bool = True,
    single_word: bool = False,
) -> list[CaptionCue]:
    """Words -> cues of at most ``max_chars_per_line * max_lines`` characters, broken at
    sentence ends and at silences longer than ``pause_s``. Cue text is wrapped into lines
    with ``\\N`` (the ASS line break). ``single_word``: one cue per word (the
    ``single_word`` layout)."""
    budget = max(4, int(max_chars_per_line)) * max(1, int(max_lines))
    cues: list[CaptionCue] = []
    for sentence in sentences:
        words = _words_of(sentence, space_dependent)
        if not words:
            continue
        runs: list[list[Word]] = [[words[0]]]
        for previous, word in zip(words, words[1:], strict=False):
            if single_word or word.start_s - previous.end_s > pause_s + 1e-9:
                runs.append([word])
            else:
                runs[-1].append(word)
        for run in runs:
            for chunk in _split_balanced(run, budget, space_dependent):
                text = "\\N".join(
                    wrap_lines(_join(chunk, space_dependent), max_chars_per_line, max_lines,
                               space_dependent)
                )
                start = chunk[0].start_s
                end = max(chunk[-1].end_s, start + MIN_CUE_S)
                cues.append(CaptionCue(start_s=round(start, 3), end_s=round(end, 3), text=text))
    for current, following in zip(cues, cues[1:], strict=False):
        if current.end_s > following.start_s:
            current.end_s = round(max(following.start_s, current.start_s + 0.1), 3)
    return cues


def wrap_lines(
    text: str, max_chars_per_line: int, max_lines: int, space_dependent: bool = True
) -> list[str]:
    """Break ``text`` into at most ``max_lines`` lines of about ``max_chars_per_line``
    characters, balanced so the lines are of similar length."""
    text = " ".join(text.split()) if space_dependent else text.strip()
    if not text:
        return [""]
    if len(text) <= max_chars_per_line:
        return [text]
    units = text.split(" ") if space_dependent else list(text)
    glue = " " if space_dependent else ""
    lines_wanted = min(max_lines, max(2, math.ceil(len(text) / max_chars_per_line)))
    chunks = _split_units(units, lines_wanted, glue)
    return [glue.join(chunk) for chunk in chunks]


def _split_units(units: list[str], count: int, glue: str) -> list[list[str]]:
    if count <= 1 or len(units) <= 1:
        return [units]
    total = len(glue.join(units))
    target = total / count
    chunks: list[list[str]] = []
    current: list[str] = []
    for index, unit in enumerate(units):
        remaining_chunks = count - len(chunks)
        remaining_units = len(units) - index
        if current and remaining_chunks > 1 and remaining_chunks < remaining_units + 1:
            with_unit = len(glue.join([*current, unit]))
            without = len(glue.join(current))
            if abs(with_unit - target) > abs(without - target):
                chunks.append(current)
                current = []
        current.append(unit)
    if current:
        chunks.append(current)
    return chunks


# ASS output --------------------------------------------------------------------------------


def ass_colour(value: str, alpha: int = 0) -> str:
    """``#RRGGBB`` -> ``&HAABBGGRR`` (libass byte order). Unknown values become white."""
    match = _HEX_RE.match((value or "").strip())
    rgb = match.group(1) if match else "FFFFFF"
    red, green, blue = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def ass_time(seconds: float) -> str:
    """``H:MM:SS.cc`` (centiseconds, as ASS wants it)."""
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total % 60
    centis = int(round(secs * 100))
    if centis >= 6000:  # rounding pushed 59.995 to 60.00
        centis = 5999
    return f"{hours}:{minutes:02d}:{centis // 100:02d}.{centis % 100:02d}"


def _clean_text(text: str) -> str:
    """Keep ``\\N`` line breaks, drop characters libass would read as override tags."""
    cleaned = text.replace("\r", "").replace("\n", "\\N")
    return cleaned.replace("{", "(").replace("}", ")")


# Words of a cue (active-word and single-word looks) --------------------------------------------

LINE_BREAK = "\\N"


def cue_tokens(text: str, space_dependent: bool = True) -> list[tuple[str, bool]]:
    """The cue text in pieces, in order: ``(piece, is_word)``. Words (single characters for
    a language without spaces) and the separators between them (spaces, ``\\N``), so the
    pieces joined give the text back."""
    pieces: list[tuple[str, bool]] = []
    cleaned = (text or "").replace("\r", "").replace("\n", LINE_BREAK)
    for number, line in enumerate(cleaned.split(LINE_BREAK)):
        if number:
            pieces.append((LINE_BREAK, False))
        if space_dependent:
            for part in re.split(r"( +)", line):
                if part:
                    pieces.append((part, not part.startswith(" ")))
        else:
            for char in line:
                pieces.append((char, not char.isspace()))
    return pieces


def _share_out(words: Sequence[str], start: float, end: float) -> list[Word]:
    """``words`` with ``start`` .. ``end`` shared out by length (as :func:`_spread` does)."""
    total = sum(len(w) for w in words) or 1
    span = max(float(end) - float(start), 0.0)
    clock = float(start)
    shared: list[Word] = []
    for word in words:
        length = span * len(word) / total
        shared.append(Word(word, round(clock, 3), round(clock + length, 3)))
        clock += length
    return shared


def cue_word_times(
    cues: Sequence[CaptionCue],
    sentences: Iterable[SentenceLike] | None,
    space_dependent: bool = True,
) -> list[list[Word]]:
    """Per cue, its words with their times: the timing words (as :func:`chunk_cues` cut
    them) that start inside the cue when there are as many as the cue has words, else the
    cue's time shared out by word length."""
    units = [u for sentence in (sentences or []) for u in _words_of(sentence, space_dependent)]
    units.sort(key=lambda u: u.start_s)
    starts = [u.start_s for u in units]
    result: list[list[Word]] = []
    for cue in cues:
        words = [piece for piece, is_word in cue_tokens(cue.text, space_dependent) if is_word]
        low = bisect.bisect_left(starts, cue.start_s - 1e-6)
        high = bisect.bisect_left(starts, cue.end_s - 1e-9)
        inside = units[low:high]
        if words and len(inside) == len(words):
            result.append([
                Word(word, unit.start_s, unit.end_s)
                for word, unit in zip(words, inside, strict=True)
            ])
        else:
            result.append(_share_out(words, cue.start_s, cue.end_s))
    return result


@dataclass(frozen=True)
class CaptionEvent:
    """One ASS event: ``text`` from ``start_s`` to ``end_s``; ``active`` is the word shown
    in the active colour (or alone, for single words), ``None`` for the plain cue."""

    start_s: float
    end_s: float
    text: str
    active: int | None = None


def _override_colour(value: str) -> str:
    """``#RRGGBB`` -> ``&HBBGGRR&`` (an override tag's colour)."""
    return "&H" + ass_colour(value)[4:] + "&"


def word_slices(cue: CaptionCue, words: Sequence[Word]) -> list[tuple[float, float, int]]:
    """``(start, end, word index)``: each word from its start to the next word's start, the
    last to the cue's end, all inside the cue (so the events touch and never overlap)."""
    slices: list[tuple[float, float, int]] = []
    for index, word in enumerate(words):
        start = min(max(word.start_s, cue.start_s), cue.end_s)
        following = words[index + 1].start_s if index + 1 < len(words) else cue.end_s
        end = min(max(following, start), cue.end_s)
        if end - start > 1e-6:
            slices.append((round(start, 3), round(end, 3), index))
    return slices


def caption_events(
    cue: CaptionCue,
    words: Sequence[Word],
    style: CaptionStyle,
    look: CaptionLook | None = None,
    space_dependent: bool = True,
) -> list[CaptionEvent]:
    """The ASS events of one cue for the style's preset (see the module docstring)."""
    look = look or caption_look(style.preset)
    text = _clean_text(cue.text)
    if not text.strip():
        return []
    single = style.single_words or look.layout == "single_word"
    highlight = style.preset == "active-word"
    if not single and not highlight:
        return [CaptionEvent(cue.start_s, cue.end_s, "{\\an2}" + text)]
    if not words:
        return [CaptionEvent(cue.start_s, cue.end_s, "{\\an2}" + text)]
    colour = _override_colour(style.active_colour or look.active_colour)
    plain = _override_colour(style.primary)
    events: list[CaptionEvent] = []
    slices = word_slices(cue, words)
    if single:
        pop = ""
        if style.preset == "single-word-pop" and look.pops:
            scale = int(round(look.pop_scale * 100))
            millis = int(round(look.pop_s * 1000))
            pop = f"\\fscx100\\fscy100\\t(0,{millis},\\fscx{scale}\\fscy{scale})"
        for start, end, index in slices:
            word = _clean_text(words[index].text)
            body = f"{{\\1c{colour}}}{word}" if highlight else word
            events.append(CaptionEvent(start, end, f"{{\\an2{pop}}}{body}", index))
        return events
    pieces = cue_tokens(cue.text, space_dependent)
    if slices and slices[0][0] > cue.start_s + 1e-6:
        events.append(CaptionEvent(cue.start_s, slices[0][0], "{\\an2}" + text))
    for start, end, index in slices:
        parts: list[str] = []
        word_number = -1
        for piece, is_word in pieces:
            if not is_word:
                parts.append(piece if piece == LINE_BREAK else _clean_text(piece))
                continue
            word_number += 1
            cleaned = _clean_text(piece)
            if word_number == index:
                parts.append(f"{{\\1c{colour}}}{cleaned}{{\\1c{plain}}}")
            else:
                parts.append(cleaned)
        events.append(CaptionEvent(start, end, "{\\an2}" + "".join(parts), index))
    return events


def build_ass(
    cues: Sequence[CaptionCue],
    style: CaptionStyle,
    aspect: str,
    *,
    margin_v_px: int = 60,
    shadow: int = 0,
    style_name: str = "Default",
    sentences: Iterable[SentenceLike] | None = None,
    space_dependent: bool = True,
) -> str:
    """The whole ASS document for ``cues`` in the reference resolution of ``aspect``, in the
    look of ``style.preset``. ``sentences`` (the timing) give the word times of the
    active-word and single-word looks; without them a cue's time is shared out by length."""
    width, height = REFERENCE_PORTRAIT if aspect == "9:16" else REFERENCE_LANDSCAPE
    margin_h = 40 if width >= height else 24
    look = caption_look(style.preset)
    border_style, outline_value = 1, int(style.outline)
    outline_colour = ass_colour(style.outline_colour)
    back_colour = ass_colour("#000000", alpha=0x80)
    if look.box:
        # libass draws BorderStyle 3 as a box in the outline colour (padding = "outline");
        # the back colour is set the same so either reading gives the same box.
        border_style, outline_value = 3, int(look.box_padding_px)
        alpha = int(round((1.0 - look.box_opacity) * 255))
        outline_colour = back_colour = ass_colour(look.box_colour, alpha=alpha)
    lines = [
        "[Script Info]",
        "; Written by 9Sigma Editor",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: {name},{font},{size},{primary},{secondary},{outline_colour},{back},{bold},0,0,0,"
        "100,100,0,0,{border},{outline},{shadow},2,{margin_h},{margin_h},{margin_v},1".format(
            name=style_name,
            font=style.font,
            size=int(style.size),
            primary=ass_colour(style.primary),
            secondary=ass_colour("#FF0000"),
            outline_colour=outline_colour,
            back=back_colour,
            bold=-1 if style.bold else 0,
            border=border_style,
            outline=outline_value,
            shadow=int(shadow),
            margin_h=margin_h,
            margin_v=int(margin_v_px),
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    needs_words = style.preset == "active-word" or style.single_words or look.layout == (
        "single_word"
    )
    timed = (
        cue_word_times(cues, sentences, space_dependent) if needs_words else [[] for _ in cues]
    )
    for cue, words in zip(cues, timed, strict=True):
        for event in caption_events(cue, words, style, look, space_dependent):
            lines.append(
                f"Dialogue: 0,{ass_time(event.start_s)},{ass_time(event.end_s)},{style_name},"
                f",0,0,0,,{event.text}"
            )
    return "\n".join(lines) + "\n"


def write_ass(
    path: Path,
    cues: Sequence[CaptionCue],
    style: CaptionStyle,
    aspect: str,
    *,
    margin_v_px: int = 60,
    shadow: int = 0,
    sentences: Iterable[SentenceLike] | None = None,
    space_dependent: bool = True,
) -> Path:
    atomic_write_text(
        Path(path),
        build_ass(cues, style, aspect, margin_v_px=margin_v_px, shadow=shadow,
                  sentences=sentences, space_dependent=space_dependent),
    )
    return Path(path)


def presets_json() -> list[dict[str, Any]]:
    """Every caption look preset as plain data, in :data:`CAPTION_PRESETS` order."""
    looks = caption_looks()
    return [looks[name].to_json() for name in CAPTION_PRESETS if name in looks]
