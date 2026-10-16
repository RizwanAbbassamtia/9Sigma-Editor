"""The live preview against the renderer, frame by frame and sample by sample
(docs/M5-CONTRACT.md section 6; design 5.8 "frame level"). Dev only and slow: it renders the
mock projects with FFmpeg and drives Playwright's headless Chromium.

    set SGE_PREVIEW_PARITY=1         (the parity proof; SGE_PREVIEW_PERF=1 for the laptop budget)
    set PLAYWRIGHT_BROWSERS_PATH=...  (or SGE_PARITY_CHROMIUM=<chrome.exe>)
    .venv/Scripts/python.exe -m pytest tests/preview_parity -q

Playwright for Python must be importable (``pip install playwright`` into any folder on
``PYTHONPATH``; it is not a dependency of the app). Results go to ``SGE_PARITY_ROOT`` (default
``F:/cfs_tmp/m5/parity``): ``report.json``, ``perf.json`` and the worst frame pairs.
"""

from __future__ import annotations

import json
import os

import pytest

PARITY = os.environ.get("SGE_PREVIEW_PARITY") == "1"
PERF = os.environ.get("SGE_PREVIEW_PERF") == "1"


def _summary(report: dict) -> str:
    lines = []
    for key, row in report.get("frames", {}).items():
        lines.append(f"{key}: motion {row['motion_ssim']} transitions {row['transition_ssim']} "
                     f"captions {row['caption_iou']} passed {row['passed']}")
    for key, row in report.get("sound", {}).items():
        lines.append(f"{key}: sync {row['sync_worst_frames']:.3f} frames, levels "
                     f"{row['recorded']['levels']['abs_diff_db_max']:.2f} dB passed "
                     f"{row['passed']}")
    for key, row in report.get("picture_sync", {}).items():
        lines.append(f"{key}: picture offsets {[r['offset_frames'] for r in row['cuts']]}")
    return "\n".join(lines)


@pytest.mark.skipif(not PARITY, reason="set SGE_PREVIEW_PARITY=1 to run the preview parity proof")
def test_the_live_preview_looks_and_sounds_like_the_render() -> None:
    pytest.importorskip("playwright.sync_api")
    import parity_harness

    report = parity_harness.parity()
    assert report["passed"], _summary(report)


@pytest.mark.skipif(not PERF, reason="set SGE_PREVIEW_PERF=1 to measure the preview on this PC")
def test_the_live_preview_plays_within_the_laptop_budget() -> None:
    pytest.importorskip("playwright.sync_api")
    import parity_perf

    result = parity_perf.performance()
    assert result["passed"], json.dumps(
        {key: {name: row[name] for name in ("dropped_share", "seek_cached_ms", "seek_cold_ms",
                                            "open_to_first_frame_ms", "memory", "passed")}
         for key, row in result["projects"].items()}, indent=1)
