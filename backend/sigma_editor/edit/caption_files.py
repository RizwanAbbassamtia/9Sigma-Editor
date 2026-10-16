"""Caption files: read SRT and WebVTT into cues, and write SRT back out.

Read leniently: a BOM, Windows line ends, ``WEBVTT`` headers with text after them, ``NOTE``
and ``STYLE`` blocks, numbered cue identifiers, cue settings after the arrow, ``HH:MM:SS,mmm``
and ``HH:MM:SS.mmm`` and ``MM:SS.mmm`` times, inline tags (``<b>``, ``<i>``, ``<c.name>``,
``<v Speaker>``) and inline timestamps (``<00:00:01.500>``) are all accepted; the tags are
dropped and the text kept. Cues that end before they start, or carry no text, are skipped.
A file nothing can be read from raises :class:`CaptionFileError` with a plain reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TIME = re.compile(
    r"(?:(?P<h>\d{1,2}):)?(?P<m>\d{1,2}):(?P<s>\d{1,2})(?:[.,](?P<ms>\d{1,3}))?"
)
_ARROW = re.compile(
    r"^\s*(?P<start>[\d:.,]+)\s*-->\s*(?P<end>[\d:.,]+)(?P<rest>.*)$"
)
_TAG = re.compile(r"<[^>]*>")
_SRT_TAGS = re.compile(r"\{\\[^}]*\}")  # {\an8} and the like, from SRT files made of ASS


class CaptionFileError(ValueError):
    """The file is not a caption file this app can read; the message says why."""


@dataclass(frozen=True)
class ImportedCue:
    start_s: float
    end_s: float
    text: str
    """Lines joined with ``\\n``."""


def parse_time(value: str) -> float | None:
    """``01:02:03,456`` / ``02:03.456`` / ``2:03`` -> seconds; ``None`` when it is not a time."""
    match = _TIME.fullmatch(value.strip())
    if match is None:
        return None
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    millis = match.group("ms") or "0"
    millis = (millis + "000")[:3]
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds + int(millis) / 1000.0


def clean_text(raw: str) -> str:
    """The cue text without tags, with lines trimmed and blank lines dropped."""
    text = _SRT_TAGS.sub("", raw)
    text = _TAG.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _blocks(text: str) -> list[list[str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def parse_captions(text: str) -> list[ImportedCue]:
    """Cues of an SRT or WebVTT document, in time order."""
    content = text.lstrip("﻿")
    cues: list[ImportedCue] = []
    for block in _blocks(content):
        head = block[0].strip()
        if head.startswith("WEBVTT") or head.startswith("NOTE") or head in ("STYLE", "REGION"):
            continue
        arrow_at = next((i for i, line in enumerate(block) if "-->" in line), None)
        if arrow_at is None:
            continue
        match = _ARROW.match(block[arrow_at])
        if match is None:
            continue
        start = parse_time(match.group("start"))
        end = parse_time(match.group("end"))
        if start is None or end is None or end <= start:
            continue
        body = clean_text("\n".join(block[arrow_at + 1:]))
        if not body:
            continue
        cues.append(ImportedCue(round(start, 3), round(end, 3), body))
    cues.sort(key=lambda cue: (cue.start_s, cue.end_s))
    return cues


def read_caption_file(path: Path | str) -> list[ImportedCue]:
    """Read an SRT or VTT file. Raises :class:`CaptionFileError` when nothing can be read."""
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise CaptionFileError(f"The caption file could not be read: {exc}") from exc
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise CaptionFileError("The caption file is not text this app can read.")
    cues = parse_captions(text)
    if not cues:
        raise CaptionFileError(
            f"No captions were found in {source.name}. It should be an SRT or VTT file with "
            "times like 00:00:01,000 --> 00:00:03,500 followed by the words."
        )
    return cues


def format_srt_time(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(cues: list[ImportedCue]) -> str:
    """An SRT document for ``cues`` (what "Save captions as SRT" gives)."""
    out: list[str] = []
    for number, cue in enumerate(cues, start=1):
        out.append(str(number))
        out.append(f"{format_srt_time(cue.start_s)} --> {format_srt_time(cue.end_s)}")
        out.append(cue.text.replace("\\N", "\n"))
        out.append("")
    return "\n".join(out)
