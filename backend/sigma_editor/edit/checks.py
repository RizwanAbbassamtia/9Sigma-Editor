"""The editor's own checks (docs/CONTRACT-v0.1.md section 4): pictures missing and caption
speed. The renderer's gates (music licence, length, true peaks) come from ``policy/gates.py``;
both kinds share the gate result shape ``{id, title, severity, passed, detail}``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models.timeline import Timeline
from ..render import ffmpeg as ff

MAX_CAPTION_CPS = 20.0
"""Characters per second above which a caption is hard to read (the usual broadcast limit)."""
MIN_CUE_FOR_SPEED_S = 0.3


def _result(check_id: str, title: str, severity: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "title": title, "severity": severity, "passed": passed,
            "detail": detail}


def check_pictures_present(timeline: Timeline, folder: Path) -> dict[str, Any]:
    missing = [
        scene.index + 1 for scene in timeline.scenes
        if not scene.image or not ff.resolve_path(folder, scene.image).is_file()
    ]
    if not missing:
        return _result("edit.pictures_present", "Every scene has its picture", "block", True,
                       f"All {len(timeline.scenes)} scenes have a picture on this PC.")
    listed = ", ".join(str(n) for n in missing[:12]) + (" ..." if len(missing) > 12 else "")
    return _result(
        "edit.pictures_present", "Every scene has its picture", "block", False,
        f"The picture of scene(s) {listed} is missing. Use Replace picture on those scenes.",
    )


def caption_speed_cps(text: str, start_s: float, end_s: float) -> float:
    """Characters per second of one cue (line breaks and spaces do not count)."""
    length = max(float(end_s) - float(start_s), MIN_CUE_FOR_SPEED_S)
    characters = len(text.replace("\\N", "").replace(" ", ""))
    return characters / length


def check_caption_speed(timeline: Timeline, *, limit: float = MAX_CAPTION_CPS) -> dict[str, Any]:
    cues = timeline.captions.cues if timeline.captions.enabled else []
    if not cues:
        return _result("edit.caption_speed", "Captions are readable", "warn", True,
                       "No captions to check." if not timeline.captions.cues else
                       "Captions are switched off.")
    fast = [(cue, caption_speed_cps(cue.text, cue.start_s, cue.end_s)) for cue in cues]
    fast = [(cue, cps) for cue, cps in fast if cps > limit]
    if not fast:
        return _result("edit.caption_speed", "Captions are readable", "warn", True,
                       f"All {len(cues)} captions stay under {limit:g} characters a second.")
    worst_cue, worst = max(fast, key=lambda item: item[1])
    sample = worst_cue.text.replace("\\N", " ")
    if len(sample) > 40:
        sample = sample[:37] + "..."
    return _result(
        "edit.caption_speed", "Captions are readable", "warn", False,
        f"{len(fast)} of {len(cues)} captions run faster than {limit:g} characters a second "
        f"(the fastest, '{sample}', at {worst:.0f}). Move the sentence starts in the timing "
        "tool or shorten the text.",
    )


def editor_checks(timeline: Timeline, folder: Path | str) -> list[dict[str, Any]]:
    return [check_pictures_present(timeline, Path(folder)), check_caption_speed(timeline)]


def blocking_reasons(results: list[dict[str, Any]]) -> list[str]:
    return [
        f"{r['title']}: {r['detail']}" if r.get("detail") else str(r["title"])
        for r in results if r.get("severity") == "block" and not r.get("passed")
    ]
