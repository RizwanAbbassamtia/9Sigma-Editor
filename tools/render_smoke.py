"""Transitions v2 smoke test: a mock project rendered through the real pipeline
(docs/TRANSITIONS-V2-CONTRACT.md section 6).

    .venv/Scripts/python.exe tools/render_smoke.py                   # 30 scenes at 1080p
    .venv/Scripts/python.exe tools/render_smoke.py --scenes 6 --preset proxy
    .venv/Scripts/python.exe tools/render_smoke.py --parallax 3      # 3 scenes in 2.5D

In a scratch folder (default ``F:/sge_tmp/smoke``, a short path, or ``sge_smoke`` in the
system temp folder; it is emptied first, but only when it is missing, empty or one this tool
made: any other ``--out`` folder is left alone and the tool stops):

1. draws N placeholder pictures (the offline mock image provider, 2K) and a silent voice
   track, and builds a timeline of N scenes of 3.5-4.5 s. The cuts go through every
   transition of the engine in turn, the camera through every motion preset, and the video
   ends on a dip to black. ``--parallax K`` sets the first K scenes to 2.5D parallax with a
   made-up depth map next to the picture (no depth model needed), to compare its cost with
   the Ken Burns scenes;
2. renders it (scene clips, transition clips, assembly) and prints the total time, the time
   of each phase and the slowest clips (from ``07_edit/render.log``);
3. gives one scene another camera move and renders again: only that scene and the two cuts
   around it are drawn again, the rest is reused;
4. writes ``cuts.png`` (a frame 40% into every cut, labelled, for a visual check) and
   ``smoke.json`` (every number above).

It refuses to start with less than 2.5 GB of memory free (``--force`` skips the check), and
never touches the network.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "backend") not in sys.path:  # runs without `pip install -e .` too
    sys.path.insert(0, str(REPO / "backend"))

from sigma_editor.providers.image.base import ImageRequest  # noqa: E402
from sigma_editor.providers.image.mock import MockImageProvider  # noqa: E402
from sigma_editor.providers.voice.mock import write_silence_wav  # noqa: E402

from sigma_editor.models.timeline import Timeline  # noqa: E402
from sigma_editor.render import ffmpeg as ff  # noqa: E402
from sigma_editor.render import transitions as tr  # noqa: E402
from sigma_editor.render.geometry import rect_in_pixels  # noqa: E402
from sigma_editor.render.motion import PRESET_IDS, preset_rects  # noqa: E402
from sigma_editor.render.presets import PROXY_ID, load_render_config  # noqa: E402
from sigma_editor.render.scene_clips import TO_RGB  # noqa: E402

MIN_FREE_MB = 2500
SHEET_THUMB = (320, 180)
SHEET_COLUMNS = 6
SCENE_LINE = re.compile(r"^scene (\d+): drawn in ([\d.]+) s \((.+), (\d+) frames\)")
CUT_LINE = re.compile(r"^cut (\d+) \((.+?), (\d+) frames\): drawn in ([\d.]+) s")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def default_out() -> str:
    """``F:/sge_tmp/smoke`` on the development PC, else a folder in the system temp."""
    if Path("F:/sge_tmp").is_dir():
        return "F:/sge_tmp/smoke"
    return str(Path(tempfile.gettempdir()) / "sge_smoke")


SCRATCH_MARKER = ".sge_smoke"
SCRATCH_NAMES = frozenset({SCRATCH_MARKER, "05_voice", "06_images", "07_edit", "cuts.png",
                           "smoke.json"})
"""What this tool leaves in its folder: a folder holding only these is one of its own."""


def prepare_scratch(folder: Path) -> str | None:
    """Empty ``folder`` for a run, or say why not. Only a missing or empty folder, or one this
    tool made (its marker, or nothing but what it writes), is emptied: ``--out`` pointing at
    a real folder by mistake must never delete it."""
    if folder.exists():
        if not folder.is_dir():
            return f"{folder} is a file, not a folder. Choose another --out folder."
        names = {entry.name for entry in folder.iterdir()}
        if names and SCRATCH_MARKER not in names and not names <= SCRATCH_NAMES:
            return (f"{folder} is not empty and was not made by this tool, so it was left "
                    "alone. Choose another --out folder.")
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SCRATCH_MARKER).write_text(
        "Scratch folder of tools/render_smoke.py; emptied on every run.\n", encoding="utf-8")
    return None


def free_memory_mb() -> int | None:
    """Physical memory free right now (Windows), or ``None`` where it cannot be read."""
    if sys.platform != "win32":
        return None
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    return int(status.ullAvailPhys // (1024 * 1024))


# The mock project ------------------------------------------------------------------------------


def make_pictures(folder: Path, count: int) -> list[Path]:
    provider = MockImageProvider()
    paths = []
    for index in range(count):
        path = folder / "06_images" / f"scene_{index + 1:02d}.png"
        provider.generate(ImageRequest(  # the seed sets the hue: neighbours look different
            prompt=f"Smoke scene {index + 1}: a placeholder picture", output_path=path,
            aspect="16:9", size="2K", seed=(index * 47) % 360 + 360, scene_id=str(index + 1),
        ))
        paths.append(path)
    return paths


def write_depth_map(picture: Path) -> None:
    """A made-up 16-bit depth map next to the picture: far at the top, near at the bottom,
    with a nearer disc in the middle (what ``render/parallax`` reads as-is)."""
    with Image.open(picture) as image:
        width, height = image.size
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    depth = 0.2 + 0.5 * ys / height
    disc = ((xs - width * 0.55) ** 2 + (ys - height * 0.55) ** 2) < (height * 0.18) ** 2
    depth[disc] = 0.95
    Image.fromarray((depth * 65535).astype(np.uint16)).save(picture.with_name(
        picture.stem + ".depth.png"))


def make_timeline(pictures: list[Path], folder: Path, size: tuple[int, int],
                  parallax: int) -> Timeline:
    ids = tr.ids()
    scenes = []
    clock = 0.0
    for index, picture in enumerate(pictures):
        length = 3.5 if index % 2 else 4.5
        with Image.open(picture) as image:
            image_w, image_h = image.size
        preset = PRESET_IDS[index % len(PRESET_IDS)]
        start, end = preset_rects(preset) or ((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0))
        last = index == len(pictures) - 1
        kind = "dip_black" if last else ids[index % len(ids)]
        scenes.append({
            "index": index,
            "image": picture.relative_to(folder).as_posix(),
            "start_s": round(clock, 3),
            "end_s": round(clock + length, 3),
            "motion": {
                "preset": preset,
                "start_rect": rect_in_pixels(start, image_w, image_h, *size),
                "end_rect": rect_in_pixels(end, image_w, image_h, *size),
                "style": "parallax" if index < parallax else "ken_burns",
            },
            "transition_out": {"type": kind, "duration_s": tr.get_spec(kind).duration_s},
        })
        clock += length
    return Timeline.model_validate({
        "project_id": "smoke", "format": "long", "aspect": "16:9", "width": size[0],
        "height": size[1], "fps": 30, "duration_s": round(clock, 3), "scenes": scenes,
        "voice": {"path": "05_voice/voice.wav"}, "presets": [],
    })


# Rendering -------------------------------------------------------------------------------------


def render(timeline: Timeline, folder: Path, preset_id: str, x264: str | None,
           name: str) -> tuple[Any, ff.RenderResult, list[str]]:
    config = load_render_config()
    preset = config.proxy if preset_id == PROXY_ID else config.presets[preset_id]
    plan = ff.build_plan(timeline, preset, folder=folder, config=config, x264_preset=x264)
    edit = folder / "07_edit"
    log_path = edit / "render.log"
    before = log_path.read_text(encoding="utf-8").splitlines() if log_path.is_file() else []
    shown = {"pct": -10.0}

    def progress(message: str, pct: float) -> None:
        if pct >= shown["pct"] + 10 or (pct in (55.0, 70.0, 100.0) and pct != shown["pct"]):
            shown["pct"] = pct
            print(f"  {message}", flush=True)

    result = ff.run_render(plan, edit / name, work_dir=edit, log_path=log_path,
                           progress=progress)
    lines = log_path.read_text(encoding="utf-8").splitlines()[len(before):]
    return plan, result, lines


def clip_timings(lines: list[str]) -> dict[str, list[dict[str, Any]]]:
    scenes, cuts = [], []
    for line in lines:
        if match := SCENE_LINE.match(line):
            scenes.append({"scene": int(match[1]), "seconds": float(match[2]),
                           "renderer": match[3], "frames": int(match[4])})
        elif match := CUT_LINE.match(line):
            cuts.append({"cut": int(match[1]), "transition": match[2],
                         "frames": int(match[3]), "seconds": float(match[4])})
    return {"scenes": scenes, "cuts": cuts}


def contact_sheet(video: Path, layout: Any, out: Path) -> int:
    """A frame 40% into every cut between two scenes (where most transitions are easiest to
    recognise; a dip is not yet all one colour), labelled, in one picture."""
    marks = []
    for cut in layout.cuts:
        if cut.b is None:
            continue
        start, length = layout.scene_frames[cut.a.number]
        marks.append((start + length + cut.frames * 2 // 5, f"{cut.number:02d} {cut.label}"))
    if not marks:
        return 0
    width, height = SHEET_THUMB
    expr = "+".join(f"eq(n\\,{frame})" for frame, _ in marks)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vf",
         f"select='{expr}',{TO_RGB},scale={width}:{height},format=rgb24",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True, check=True, creationflags=NO_WINDOW,
    ).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)
    rows = (len(frames) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
    sheet = Image.new("RGB", (SHEET_COLUMNS * width, rows * (height + 18)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for number, (frame, (_mark, label)) in enumerate(zip(frames, marks, strict=False)):
        x = (number % SHEET_COLUMNS) * width
        y = (number // SHEET_COLUMNS) * (height + 18)
        sheet.paste(Image.fromarray(frame), (x, y))
        draw.text((x + 4, y + height + 3), label, fill=(235, 235, 235))
    sheet.save(out)
    return len(frames)


def summary_line(label: str, result: ff.RenderResult) -> str:
    phases = ", ".join(f"{k} {v:.1f} s" for k, v in result.phase_seconds.items())
    return (f"{label}: {result.seconds_taken:.1f} s ({phases}); scene clips reused "
            f"{result.scene_clips_reused} of {result.scene_clips_total}, transitions reused "
            f"{result.cuts_reused} of {result.cuts_total}; video {result.duration_s:.2f} s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenes", type=int, default=30)
    parser.add_argument("--preset", default="1080p", help="proxy, 720p, 1080p or 2160p")
    parser.add_argument("--x264", default=None, help="x264 speed of the final (default: config)")
    parser.add_argument("--parallax", type=int, default=0, help="scenes drawn in 2.5D")
    parser.add_argument("--out", default=default_out(), help="scratch folder (emptied first)")
    parser.add_argument("--force", action="store_true", help="skip the free-memory check")
    args = parser.parse_args(argv)
    if shutil.which("ffmpeg") is None:
        print("FFmpeg is not installed.")
        return 2
    free = free_memory_mb()
    if free is not None and free < MIN_FREE_MB and not args.force:
        print(f"Only {free} MB of memory is free (at least {MIN_FREE_MB} MB needed). Close "
              "some programs and try again, or pass --force.")
        return 3
    folder = Path(args.out)
    problem = prepare_scratch(folder)
    if problem is not None:
        print(problem)
        return 4
    (folder / "05_voice").mkdir(parents=True)
    (folder / "07_edit").mkdir(parents=True)
    count = max(2, args.scenes)
    config = load_render_config()
    preset = config.proxy if args.preset == PROXY_ID else config.presets[args.preset]
    size = preset.size("16:9")

    print(f"Making {count} placeholder pictures ...", flush=True)
    pictures = make_pictures(folder, count)
    for picture in pictures[: max(0, args.parallax)]:
        write_depth_map(picture)
    timeline = make_timeline(pictures, folder, size, max(0, args.parallax))
    write_silence_wav(folder / "05_voice" / "voice.wav", timeline.duration_s, 48000)
    print(f"{count} scenes, {timeline.duration_s:g} s, {args.preset} {size[0]}x{size[1]}; "
          f"free memory {free if free is not None else '?'} MB", flush=True)

    print("First render:", flush=True)
    plan, first, lines = render(timeline, folder, args.preset, args.x264, "smoke.mp4")
    timings = clip_timings(lines)
    print(summary_line("First render", first), flush=True)

    edited = timeline.model_copy(deep=True)
    middle = count // 2
    scene = edited.scenes[middle]
    picture = folder / scene.image
    with Image.open(picture) as image:
        image_w, image_h = image.size
    other = "hold" if scene.motion.preset != "hold" else "zoom_in"
    start, end = preset_rects(other) or ((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0))
    scene.motion = scene.motion.model_copy(update={
        "preset": other,
        "start_rect": rect_in_pixels(start, image_w, image_h, *size),
        "end_rect": rect_in_pixels(end, image_w, image_h, *size),
    })
    print(f"Second render after giving scene {middle + 1} another move ({other}):", flush=True)
    _plan2, second, _lines2 = render(edited, folder, args.preset, args.x264, "smoke_edit.mp4")
    print(summary_line("Second render", second), flush=True)

    sheet = folder / "cuts.png"
    shown = contact_sheet(first.path, plan.layout, sheet)
    slow_scenes = sorted(timings["scenes"], key=lambda row: -row["seconds"])[:3]
    slow_cuts = sorted(timings["cuts"], key=lambda row: -row["seconds"])[:5]
    per_frame: dict[str, float] = {}
    for kind in ("zoompan", "motion:parallax"):
        rows = [r for r in timings["scenes"] if r["renderer"].startswith(kind)]
        if rows:
            per_frame[kind] = round(
                1000 * sum(r["seconds"] for r in rows) / sum(r["frames"] for r in rows), 1)
    report = {
        "scenes": count, "preset": args.preset, "size": list(size),
        "video_s": timeline.duration_s, "free_memory_mb": free,
        "first": {"seconds": first.seconds_taken, "phases": first.phase_seconds,
                  "duration_s": first.duration_s},
        "second": {"seconds": second.seconds_taken, "phases": second.phase_seconds,
                   "scene_clips_reused": second.scene_clips_reused,
                   "scene_clips_total": second.scene_clips_total,
                   "cuts_reused": second.cuts_reused, "cuts_total": second.cuts_total},
        "clips": timings, "ms_per_frame": per_frame, "contact_sheet": str(sheet),
        "cuts_on_sheet": shown,
    }
    (folder / "smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Slowest scene clips: " + ", ".join(
        f"{r['scene']:02d} {r['seconds']:.1f} s ({r['renderer']})" for r in slow_scenes))
    print("Slowest transitions: " + ", ".join(
        f"{r['cut']:02d} {r['transition']} {r['seconds']:.1f} s" for r in slow_cuts))
    if per_frame:
        print("Scene clip cost per frame: " + ", ".join(
            f"{k} {v:.1f} ms" for k, v in per_frame.items()))
    print(f"Contact sheet with {shown} cuts: {sheet}")
    print(f"Report: {folder / 'smoke.json'}")
    return 0


if __name__ == "__main__":
    started = time.monotonic()
    code = main()
    print(f"Done in {time.monotonic() - started:.0f} s.")
    sys.exit(code)
