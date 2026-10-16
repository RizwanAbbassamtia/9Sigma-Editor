"""The preview's performance on this laptop (docs/M5-CONTRACT.md section 0.3 and 6; dev only):
the 12-scene project and the 60-scene, ten-minute one in headless Chromium at 960 x 540 with
sound (muted): the first frame, frames dropped while playing, seeks to a cached and to a cold
frame, the drawing cost per frame, the renderer process's memory, and the audio clock's
scheduling of the voice clips and music pieces (where each source starts on the video clock).

Cold seeks are also measured from no preview JPEG on the server at all (docs/M5-CONTRACT.md
8.7.1), on the 60-scene project over photo-like pictures (``ten_minutes_photo``):
``after_render`` makes them the way the edit step does while it renders, then opens the preview
in a fresh browser (the first view after a render); ``never_made`` leaves them to the background
maker that ``GET /timeline`` starts (a project rendered before the edit step made them) and says
how long it took to make them all.

Budgets (design 5.6): 2 % or fewer dropped frames at 30 fps, a cached seek within 50 ms and a
cold one within 250 ms, the first frame of a 60-scene project within 1.5 s, the renderer process
within 700 MB. The preview plays at Full quality (960 x 540, the size the budget is for) unless
``quality`` says otherwise ("auto" lets the engine drop to Half under load, as the app does).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import parity_harness as ph
import parity_metrics as pm

BROWSER_ARGS = ["--disable-direct-composition"]
"""Headless Chromium on Windows presents a WebGL canvas through a DirectComposition swap chain
whose ``Present`` blocks for about 400 ms every second or so on this laptop (a trace shows
``DXGISwapChainImageBacking::Present`` in the GPU process; an empty WebGL page does not do it).
A window on a real screen does not, so the run turns DirectComposition off."""
OPEN_SETTLE_S = 3.0
"""Seeks are timed this long after the engine's warm-up (see :func:`measure`)."""
DROPPED_SHARE = 0.02
SEEK_CACHED_MS = 50.0
SEEK_COLD_MS = 250.0
FIRST_FRAME_MS = 1500.0
MEMORY_MB = 700.0
STEM_PIECE_S = 5.0
"""The music stem's pieces (frontend/src/preview/audio.ts ``STEM_CHUNK_S``)."""
SOUND_LATE_MS = 34.0
"""A play's first sounds start within one frame of where they belong (docs/M5-CONTRACT.md 8.7.4:
late frames are dropped, sound never is)."""
START_WINDOW_S = 3.0
"""The sounds whose place is this soon after the play's start count as its start."""
COLD_CACHE_SCENES = [10, 20, 30, 40, 50]


def browser_memory() -> dict[str, Any]:
    """Working set and private bytes (MB) of the harness browser's processes, by type."""
    browsers = ph.chromium_path() or ""
    folder = str(Path(browsers).parent) if browsers else "ms-playwright"
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or Name='headless_shell.exe'\""
        " | Where-Object { $_.ExecutablePath -like '" + folder.replace("/", "\\") + "*' }"
        " | ForEach-Object { [pscustomobject]@{ws=$_.WorkingSetSize; priv=$_.PrivatePageCount;"
        " cmd=$_.CommandLine} } | ConvertTo-Json -Compress"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                         text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        rows = json.loads(out.stdout or "[]")
    except ValueError:
        rows = []
    if isinstance(rows, dict):
        rows = [rows]
    result: dict[str, Any] = {"renderer_mb": [], "gpu_mb": None, "browser_mb": None,
                              "renderer_private_mb": []}
    for row in rows:
        command = row.get("cmd") or ""
        ws = (row.get("ws") or 0) / 1048576
        private = (row.get("priv") or 0) / 1048576
        if "--type=renderer" in command:
            result["renderer_mb"].append(round(ws, 1))
            result["renderer_private_mb"].append(round(private, 1))
        elif "--type=gpu-process" in command:
            result["gpu_mb"] = round(ws, 1)
        elif "--type=" not in command:
            result["browser_mb"] = round(ws, 1)
    return result


def scheduling(sources: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    """Where each sound the engine started lands on the video clock against where the plan
    puts it (a voice clip at ``atS``, a music piece at a multiple of :data:`STEM_PIECE_S`)."""
    starts = [clip["atS"] - clip["offsetS"] for clip in plan.get("clips", [])]
    errors = []
    for source in sources:
        at = source.get("at")
        if at is None or at != at:  # NaN: started before the clock was known
            continue
        implied = at - source["offset"]
        candidates = [*starts, round(implied / STEM_PIECE_S) * STEM_PIECE_S]
        nearest = min(candidates, key=lambda value: abs(value - implied))
        errors.append((implied - nearest) * 1000.0)
    return {"sources": len(errors), "error_ms": pm.summary(errors),
            "worst_ms": max((abs(e) for e in errors), default=0.0)}


def sound_start(sources: list[dict[str, Any]], start: float) -> dict[str, Any]:
    """How late a play's first sounds started: each source's place is where its clip (or music
    piece) is under the playhead, ``max(start, at - offset)``; a source that started late was
    started further into its clip, so it starts after that place. The placement check above
    cannot see this (a late start keeps its place in the clip)."""
    late = []
    for source in sources:
        at = source.get("at")
        if at is None or at != at:
            continue
        place = max(start, at - source["offset"])
        if place - start <= START_WINDOW_S:
            late.append((at - place) * 1000.0)
    return {"sources": len(late), "late_ms": pm.summary(late),
            "worst_ms": round(max(late, default=0.0), 1)}


def measure(page: Any, key: str, entry: dict[str, Any], plays: list[tuple[float, float]],
            cold_scenes: list[int], quality: str = "full") -> dict[str, Any]:
    width, height = ph.preview_size(entry["aspect"])
    page.evaluate("() => parity.watchSources()")
    opened = page.evaluate(
        "([id, w, h, q]) => parity.open(id, {width: w, height: h, audio: true, quality: q})",
        [entry["id"], width, height, quality])
    desc = opened["description"]
    stats = page.evaluate("() => parity.stats()")
    # the engine compiles the transitions' shaders in the background right after it opens; the
    # seeks below are timed once that is done (about 1 s for 8 to 11 transitions), the frame on
    # screen redraws promptly, and the preview has been open a few seconds: in its first
    # seconds the second of two jumps in a row waited 0.2 to 0.6 s on the GPU process's
    # present in about half the runs (whatever the engine did or did not do then, see
    # docs/M5-CONTRACT.md 8.3); three seconds after the warm-up it never did
    warm_ms = page.evaluate("() => parity.warmedUp()")
    warmed = time.monotonic()
    settle_ms = page.evaluate("() => parity.settleGpu()")
    time.sleep(max(0.0, OPEN_SETTLE_S - (time.monotonic() - warmed)))
    scenes = desc["scenes"]
    fps = desc["fps"]

    def scene_time(position: int, share: float = 0.5) -> float:
        scene = scenes[position]
        return (scene["bodyStart"] + scene["body"] * share) / fps

    cold = []
    for position in cold_scenes:
        ms = page.evaluate("t => parity.seek(t)", scene_time(position))
        cold.append({"scene": position + 1, "ms": ms})
    cached = []
    for position in cold_scenes:
        page.evaluate("t => parity.seek(t)", scene_time(position))
        targets = [(position + step, share) for step in (-1, 0, 1, 2, 3)
                   for share in (0.3, 0.7) if 0 <= position + step < len(scenes)]
        # the neighbours the engine asked for are decoded and on the GPU before a seek counts as
        # cached, and the GPU has finished uploading them: right after a cold seek it is still
        # busy with the textures of up to five pictures (and their mipmaps), and a frame then
        # waits for it (seeks of about 100 ms there, see docs/M5-CONTRACT.md 8.4)
        ready = page.evaluate("ts => parity.settlePictures(ts)",
                              [scene_time(target, share) for target, share in targets])
        settled = page.evaluate("() => parity.settleGpu()")
        for target, share in targets:
            ms = page.evaluate("t => parity.seek(t)", scene_time(target, share))
            cached.append({"scene": target + 1, "ms": ms, "pictures_ready": ready,
                           "gpu_settle_ms": round(settled, 1)})
    played = []
    sources: list[dict[str, Any]] = page.evaluate("() => parity.takeSources()")
    starts = []
    for start, seconds in plays:
        row = page.evaluate("([f, s]) => parity.play(f, s)", [start, seconds])
        heard = page.evaluate("() => parity.takeSources()")
        row["sound_start"] = sound_start(heard, start)
        starts.append(row["sound_start"]["worst_ms"])
        sources.extend(heard)
        played.append(row)
    memory = page.evaluate("() => parity.memory()")
    process = browser_memory()
    sources.extend(page.evaluate("() => parity.takeSources()"))
    page.evaluate("() => parity.close()")
    shown = sum(p["shown"] for p in played)
    dropped = sum(p["dropped"] for p in played)
    cold_ms = [c["ms"] for c in cold if c["ms"] is not None]
    cached_ms = [c["ms"] for c in cached if c["ms"] is not None]
    renderer_mb = max(process["renderer_mb"], default=0.0)
    return {
        "project": key,
        "quality": quality,
        "scenes": len(scenes),
        "seconds": round(desc["durationS"], 1),
        "renderer": opened["status"],
        "bundle_ms": round(opened["bundleMs"], 1),
        "open_to_first_frame_ms": round(opened["readyMs"], 1),
        "engine_first_frame_ms": round(stats["firstFrameMs"], 1),
        "shader_warm_up_ms": round(warm_ms, 1),
        "gpu_settle_ms": round(settle_ms, 1),
        "plays": played,
        "frames_shown": shown,
        "frames_dropped": dropped,
        "dropped_share": dropped / max(shown + dropped, 1),
        "seek_cold_ms": pm.summary(cold_ms),
        "seek_cached_ms": pm.summary(cached_ms),
        "seeks": {"cold": cold, "cached": cached},
        "memory": {**memory, **process},
        "audio_scheduling": scheduling(sources, desc["audio"]),
        "sound_start_late_ms": pm.summary(starts),
        "passed": {
            "sound_starts_with_picture": max(starts, default=0.0) <= SOUND_LATE_MS,
            "dropped": dropped / max(shown + dropped, 1) <= DROPPED_SHARE,
            "seek_cached": bool(cached_ms) and max(cached_ms) <= SEEK_CACHED_MS,
            "seek_cold": bool(cold_ms) and max(cold_ms) <= SEEK_COLD_MS,
            "first_frame": opened["readyMs"] <= FIRST_FRAME_MS,
            "memory": renderer_mb <= MEMORY_MB,
        },
    }


def _thumbs(entry: dict[str, Any]) -> Path:
    return Path(entry["folder"]) / "07_edit" / "preview" / "thumbs"


def _jpegs(entry: dict[str, Any]) -> int:
    folder = _thumbs(entry)
    return len(list(folder.glob("*.jpg"))) if folder.is_dir() else 0


def _picture_jobs(entry: dict[str, Any]) -> list[Any]:
    from sigma_editor.pipeline.stages.edit import read_timeline

    from sigma_editor.render import thumbs

    folder = Path(entry["folder"])
    timeline = read_timeline(folder / "07_edit")
    assert timeline is not None
    return thumbs.picture_jobs(folder, [scene.image for scene in timeline.scenes],
                               (timeline.width, timeline.height))


def empty_thumbs(entry: dict[str, Any]) -> int:
    """Removes every preview JPEG of the project (the server has made none yet)."""
    removed = 0
    folder = _thumbs(entry)
    if folder.is_dir():
        for path in folder.iterdir():
            path.unlink()
            removed += 1
    return removed


def _cold_seeks_only(page: Any, key: str, entry: dict[str, Any], quality: str) -> dict[str, Any]:
    width, height = ph.preview_size(entry["aspect"])
    opened = page.evaluate(
        "([id, w, h, q]) => parity.open(id, {width: w, height: h, audio: true, quality: q})",
        [entry["id"], width, height, quality])
    desc = opened["description"]
    page.evaluate("() => parity.warmedUp()")
    warmed = time.monotonic()
    page.evaluate("() => parity.settleGpu()")
    time.sleep(max(0.0, OPEN_SETTLE_S - (time.monotonic() - warmed)))
    seeks = []
    for position in COLD_CACHE_SCENES:
        scene = desc["scenes"][position]
        at = (scene["bodyStart"] + scene["body"] * 0.5) / desc["fps"]
        made = _jpegs(entry)
        seeks.append({"scene": position + 1, "jpegs_on_server": made,
                      "ms": page.evaluate("t => parity.seek(t)", at)})
    page.evaluate("() => parity.close()")
    cold_ms = [s["ms"] for s in seeks if s["ms"] is not None]
    return {"project": key, "quality": quality, "scenes": len(desc["scenes"]),
            "open_to_first_frame_ms": round(opened["readyMs"], 1),
            "seek_cold_ms": pm.summary(cold_ms), "seeks": {"cold": seeks}}


def cold_cache(base: str, env: ph.ParityEnv, key: str, entry: dict[str, Any], *,
               after_render: bool, quality: str = "full") -> dict[str, Any]:
    """Cold seeks (and the first frame) starting from no preview JPEG on the server; with
    ``after_render`` they are made first the way the edit step makes them while it renders."""
    from sigma_editor.render import thumbs

    removed = empty_thumbs(entry)
    jobs = _picture_jobs(entry)
    expected = sum(len(job.heights) for job in jobs)
    prepared: dict[str, Any] | None = None
    if after_render:
        started = time.monotonic()
        made = thumbs.prepare_pictures(Path(entry["folder"]), jobs)
        prepared = {"jpegs": made, "seconds": round(time.monotonic() - started, 1)}
    with ph.browser_page(base, env.account(), extra_args=BROWSER_ARGS) as page:
        if after_render:
            row = measure(page, key, entry, plays=[(0.0, 10.0), (300.0, 10.0)],
                          cold_scenes=COLD_CACHE_SCENES, quality=quality)
        else:
            row = _cold_seeks_only(page, key, entry, quality)
    # how long the background maker (GET /timeline) needed for the rest
    started = time.monotonic()
    while _jpegs(entry) < expected and time.monotonic() - started < 600:
        time.sleep(1.0)
    row["cold_cache"] = {
        "after_render": after_render, "jpegs_removed": removed, "jpegs_expected": expected,
        "prepared_like_the_edit_step": prepared,
        "background_done_after_s": round(time.monotonic() - started, 1),
        "jpegs_at_end": _jpegs(entry),
    }
    if not after_render:
        # for information: a project whose JPEGs were never made has them a little later
        row["passed"] = {}
    return row


def performance(root: Path | None = None, quality: str = "full") -> dict[str, Any]:
    env = ph.ParityEnv(root)
    manifest = ph.prepare_projects(env, ["twelve_a", "ten_minutes", "ten_minutes_photo"])
    ph.build_site(env)
    result: dict[str, Any] = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "budgets": {"dropped_share": DROPPED_SHARE,
                                          "seek_cached_ms": SEEK_CACHED_MS,
                                          "seek_cold_ms": SEEK_COLD_MS,
                                          "first_frame_ms": FIRST_FRAME_MS,
                                          "memory_mb": MEMORY_MB},
                              "quality": quality,
                              "browser_args": BROWSER_ARGS,
                              "projects": {}}
    with ph.server(env) as base:
        # a fresh browser per project: its HTTP cache starts cold
        with ph.browser_page(base, env.account(), extra_args=BROWSER_ARGS) as page:
            result["projects"]["twelve_a"] = measure(
                page, "twelve_a", manifest["twelve_a"], plays=[(0.0, 20.0), (18.0, 12.0)],
                cold_scenes=[6, 11], quality=quality)
        with ph.browser_page(base, env.account(), extra_args=BROWSER_ARGS) as page:
            result["projects"]["ten_minutes"] = measure(
                page, "ten_minutes", manifest["ten_minutes"],
                plays=[(0.0, 10.0), (150.0, 10.0), (300.0, 10.0), (450.0, 10.0),
                       (580.0, 12.0)],
                cold_scenes=COLD_CACHE_SCENES, quality=quality)
        # photo-like pictures and no preview JPEG on the server to start with
        photo = manifest["ten_minutes_photo"]
        result["projects"]["ten_minutes_photo"] = cold_cache(
            base, env, "ten_minutes_photo", photo, after_render=True, quality=quality)
        result["projects"]["ten_minutes_photo_never_made"] = cold_cache(
            base, env, "ten_minutes_photo", photo, after_render=False, quality=quality)
    result["passed"] = all(all(p["passed"].values()) for p in result["projects"].values())
    name = "perf.json" if quality == "full" else f"perf_{quality}.json"
    (env.root / name).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
