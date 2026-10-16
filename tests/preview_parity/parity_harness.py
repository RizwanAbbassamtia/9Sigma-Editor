"""Drives the preview parity and performance proof (docs/M5-CONTRACT.md section 6; dev only).

    set SGE_PREVIEW_PARITY=1 and run pytest tests/preview_parity, or
    .venv/Scripts/python.exe tests/preview_parity/run_parity.py [parity|perf|all]

1. **Projects.** A private app data, shared and projects folder under the parity root
   (``SGE_PARITY_ROOT``, default ``F:/cfs_tmp/m5/parity``) with a test admin, a channel with a
   licensed music bed and the mock projects of ``parity_projects.py``, rendered by the real
   edit step (``final_1080p.mp4``; the ten-minute one as the proxy only). Made once and
   reused (``env/projects-root/manifest.json``).
2. **Site.** The built app (``frontend/dist``) plus the harness page (``harness/``, built with
   the front end's Vite) served by the real backend on a free 127.0.0.1 port.
3. **Browser.** Playwright's headless Chromium (``PLAYWRIGHT_BROWSERS_PATH``, or the browser in
   ``SGE_PARITY_CHROMIUM``) signs in and draws the frames with the engine at 960 x 540.
4. **Measures.** FFmpeg's 1080p frames scaled to the preview size against the engine's (SSIM on
   luma, the captions left out: they are compared as boxes), the caption boxes against libass
   drawing the same ASS file, each sentence of the voice found in the render's sound by
   cross-correlation against the engine's schedule, the picture at the cuts over ten minutes,
   and the level of every 400 ms window of the engine's mix (voice clips plus the music stem
   at the preview gain) against the render's.

Results go to ``<root>/report.json`` (``perf.json`` for the performance run) and the worst
frame pairs to ``<root>/worst/`` as PNGs (engine | render | difference x 4).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO / "backend") not in sys.path:
    sys.path.insert(0, str(REPO / "backend"))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import parity_metrics as pm  # noqa: E402
import parity_projects as pp  # noqa: E402

DEFAULT_ROOT = Path("F:/cfs_tmp/m5/parity")
RATE = 48000
SLUG = "parity-channel"
ACCOUNT = {"username": "parity-admin", "display_name": "Parity Admin"}
"""The test admin of the private app data (its password is made once and kept in
``env/account.json`` under the parity root, never in the repository)."""
FRAME_COUNT = 40
PREVIEW_LONG, PREVIEW_SHORT = 960, 540
MOTION_SSIM = 0.95
TRANSITION_SSIM = 0.92
CAPTION_IOU = 0.9
SYNC_FRAMES = 1.0
MUSIC_DB = 1.0
WINDOW_S = 0.4
QUIET_DB = -50.0
"""Windows quieter than this in both mixes are not compared (fades to silence)."""
FRAME_PROJECTS = ("three", "twelve_a", "twelve_b", "twelve_c", "short")
SOUND_ONLY = ("ten_minutes", "pauses")
PERF_ONLY = ("ten_minutes_photo",)
"""Projects only the performance run uses (parity_perf.py)."""
"""Projects rendered as the proxy only: their sound (and, for ``ten_minutes``, the picture at
the cuts over ten minutes) is compared, not their frames."""


def say(message: str) -> None:
    print(f"[parity {time.strftime('%H:%M:%S')}] {message}", flush=True)


def node_exe() -> str:
    found = shutil.which("node")
    if found:
        return found
    candidate = Path("C:/Program Files/nodejs/node.exe")
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("Node.js is needed to build the harness page.")


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("FFmpeg is needed for the parity run.")
    return found


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)


# The private app ---------------------------------------------------------------------------------


class ParityEnv:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or os.environ.get("SGE_PARITY_ROOT") or DEFAULT_ROOT)
        self.env_dir = self.root / "env"
        self.app_dir = self.env_dir / "app"
        self.shared_dir = self.env_dir / "shared"
        self.projects_dir = self.env_dir / "projects"
        self.exports_dir = self.env_dir / "exports"
        self.music_dir = self.env_dir / "music"
        self.site_dir = self.root / "site"
        self.work_dir = self.root / "work"
        self.worst_dir = self.root / "worst"

    def environ(self) -> dict[str, str]:
        values = {
            "SGE_APP_DATA_DIR": str(self.app_dir),
            "SGE_SHARED_DIR": str(self.shared_dir),
            "SGE_PROJECTS_DIR": str(self.projects_dir),
            "SGE_EXPORTS_DIR": str(self.exports_dir),
            "SGE_FRONTEND_DIST": str(self.site_dir),
        }
        for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
            values[f"SGE_{name}_PROVIDER"] = "mock"
        return values

    @contextlib.contextmanager
    def applied(self) -> Iterator[None]:
        """This app's folders in ``os.environ`` while the block runs."""
        saved = {name: os.environ.get(name) for name in self.environ()}
        os.environ.update(self.environ())
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def account(self) -> dict[str, str]:
        path = self.env_dir / "account.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        import secrets

        account = {**ACCOUNT, "password": "parity-" + secrets.token_urlsafe(18)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(account), encoding="utf-8")
        return account

    @property
    def manifest_path(self) -> Path:
        return self.projects_dir / "manifest.json"

    def manifest(self) -> dict[str, dict[str, Any]]:
        if not self.manifest_path.is_file():
            return {}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))


def channel_body(music_dir: Path) -> dict[str, Any]:
    return {
        "channel": {
            "name": "Parity Channel",
            "url": "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx",
            "language": "English",
            "niche": "Preview parity tests",
            "formats": "both",
            "brand_colors": ["#1F3864", "#FFC000"],
            "owner": "Parity",
            "music_folder": str(music_dir),
        },
        "competitors": [],
        "frameworks": [],
        "voice": {"tool": "fish_audio", "api_key_env": "FISH_AUDIO_API_KEY"},
        "images": {"tool": "google_gemini", "api_key_env": "GEMINI_API_KEY"},
        "reviewer": "Parity",
    }


def prepare_projects(env: ParityEnv, keys: list[str] | None = None, *,
                     rerender: bool = False) -> dict[str, dict[str, Any]]:
    """The test account, the channel and the rendered projects (made once, then reused). With
    ``rerender`` the edit step runs again on projects already made (after a change to the
    renderer: only what changed is drawn again)."""
    from fastapi.testclient import TestClient

    from sigma_editor.app import create_app

    wanted = keys or list(pp.project_specs())
    manifest = env.manifest()
    if not rerender and all(manifest.get(key, {}).get("done") for key in wanted):
        return manifest
    pp.write_music(env.music_dir)
    account = env.account()
    with env.applied():
        app = create_app()
        with TestClient(app) as client:
            status = client.get("/api/auth/status").json()
            if status.get("needs_first_run"):
                made = client.post("/api/auth/first-run", json={**account,
                                                               "confirm_team_folder": True})
                if made.status_code != 200:
                    raise RuntimeError(f"first run failed: {made.text}")
            else:
                login = client.post("/api/auth/login", json={
                    "username": account["username"], "password": account["password"]})
                if login.status_code != 200:
                    raise RuntimeError(f"sign-in failed: {login.text}")
            engine = app.state.engine
            try:
                engine.channels.get(SLUG)
            except Exception:  # noqa: BLE001 - not made yet
                made = client.post("/api/channels", json=channel_body(env.music_dir))
                if made.status_code != 201:
                    raise RuntimeError(f"the channel could not be made: {made.text}") from None
            settings = app.state.settings
            settings = settings.model_copy(update={
                "render": settings.render.model_copy(update={"x264_preset": "veryfast"})})
            if rerender:
                for key in wanted:
                    entry = manifest.get(key)
                    if entry and entry.get("done"):
                        say(f"[{key}] edit step again")
                        pp.run_edit(engine, settings, entry["id"],
                                    {"presets": list(entry.get("presets") or [])})
            return pp.make_projects(engine, settings, SLUG, env.projects_dir, wanted, log=say)


# The site and the server -------------------------------------------------------------------------


def build_site(env: ParityEnv) -> Path:
    """``frontend/dist`` (when built) plus the harness page under ``parity/``."""
    site = env.site_dir
    dist = REPO / "frontend" / "dist"
    if site.exists():
        shutil.rmtree(site)
    if (dist / "index.html").is_file():
        shutil.copytree(dist, site)
    else:
        site.mkdir(parents=True)
        (site / "index.html").write_text("<!doctype html><title>9Sigma Editor</title>",
                                         encoding="utf-8")
        catalogue = REPO / "frontend" / "public" / "transitions" / "catalogue.json"
        (site / "transitions").mkdir()
        shutil.copy2(catalogue, site / "transitions" / "catalogue.json")
    environ = dict(os.environ)
    environ["SGE_PARITY_OUT"] = str(site / "parity")
    environ["SGE_PARITY_VITE_CACHE"] = str(env.work_dir)
    vite = REPO / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    run([node_exe(), str(vite), "build", "--config", str(HERE / "harness" / "vite.config.mjs")],
        cwd=str(REPO / "frontend"), env=environ)
    return site


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def server(env: ParityEnv, port: int | None = None) -> Iterator[str]:
    """The real backend on 127.0.0.1 with the private app's folders; yields its base URL."""
    import urllib.request

    port = port or free_port()
    environ = dict(os.environ)
    environ.update(env.environ())
    environ["PYTHONPATH"] = str(REPO / "backend")
    log_path = env.root / "server.log"
    log = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - closed below
    process = subprocess.Popen(
        [sys.executable, "-m", "sigma_editor.cli", "serve", "--port", str(port)],
        cwd=str(REPO), env=environ, stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(f"{base}/api/auth/status", timeout=2) as answer:
                    if answer.status == 200:
                        break
            except OSError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the server did not start; see {log_path}")
            time.sleep(0.25)
        say(f"server at {base}")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


def chromium_path() -> str | None:
    explicit = os.environ.get("SGE_PARITY_CHROMIUM", "").strip()
    if explicit:
        return explicit
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if browsers:
        found = sorted(Path(browsers).glob("chromium-*/chrome-win64/chrome.exe"))
        if found:
            return str(found[-1])
    return None


@contextlib.contextmanager
def browser_page(base: str, account: dict[str, str], *, size: tuple[int, int] = (1280, 1100),
                 extra_args: list[str] | None = None) -> Iterator[Any]:
    """A signed-in harness page in headless Chromium (muted)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        args = ["--mute-audio", "--autoplay-policy=no-user-gesture-required",
                *(extra_args or [])]
        browser = playwright.chromium.launch(executable_path=chromium_path(), headless=True,
                                             args=args)
        context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                      device_scale_factor=1)
        page = context.new_page()
        page.set_default_timeout(120_000)
        page.goto(f"{base}/parity/index.html")
        page.wait_for_function("() => window.parity !== undefined")
        page.evaluate("([u, p]) => parity.signIn(u, p)",
                      [account["username"], account["password"]])
        try:
            yield page
        finally:
            context.close()
            browser.close()


# Frames ------------------------------------------------------------------------------------------


def decode_png(data: str | bytes) -> np.ndarray:
    raw = base64.b64decode(data) if isinstance(data, str) else data
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("a frame could not be decoded")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def ffmpeg_frames(source: list[str], frames: list[int], vf_before: str, size: tuple[int, int],
                  out_dir: Path) -> dict[int, np.ndarray]:
    """Frames ``frames`` (output frame numbers) of an FFmpeg input, scaled to ``size`` with an
    area filter (BT.709, TV range in), as RGB arrays."""
    wanted = sorted(set(frames))
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    select = "+".join(f"eq(n,{n})" for n in wanted)
    chain = [part for part in (vf_before, f"select='{select}'",
                               f"scale={size[0]}:{size[1]}:flags=area:in_color_matrix=bt709"
                               ":in_range=tv:out_range=pc", "format=rgb24") if part]
    run([ffmpeg_exe(), "-v", "error", "-y", *source, "-vf", ",".join(chain),
         "-fps_mode", "passthrough", str(out_dir / "f_%04d.png")])
    files = sorted(out_dir.glob("f_*.png"))
    if len(files) != len(wanted):
        raise RuntimeError(f"FFmpeg gave {len(files)} frames for {len(wanted)} asked")
    return {n: decode_png(path.read_bytes()) for n, path in zip(wanted, files, strict=True)}


def render_frames(video: Path, frames: list[int], size: tuple[int, int],
                  out_dir: Path) -> dict[int, np.ndarray]:
    return ffmpeg_frames(["-i", str(video)], frames, "", size, out_dir)


def libass_frames(ass: Path, frame_size: tuple[int, int], frames: list[int], seconds: float,
                  size: tuple[int, int], out_dir: Path) -> dict[int, np.ndarray]:
    """The captions alone, drawn by libass from the render's own ASS file on flat grey at the
    render's size (the same filter the assembly pass runs), scaled to ``size``."""
    from sigma_editor.render.ffmpeg import escape_filter_path
    from sigma_editor.render.fonts import fonts_dir

    source = ["-f", "lavfi", "-i",
              f"color=c=0x808080:s={frame_size[0]}x{frame_size[1]}:r={pp.FPS}:d={seconds:.3f}"]
    burn = (f"format=yuv420p,subtitles=filename={escape_filter_path(ass)}"
            f":fontsdir={escape_filter_path(fonts_dir())}")
    return ffmpeg_frames(source, frames, burn, size, out_dir)


def corner_colour(picture: np.ndarray) -> np.ndarray:
    patch = picture[:8, :8, :3].reshape(-1, 3)
    return np.median(patch, axis=0)


def pick_frames(desc: dict[str, Any], count: int = FRAME_COUNT) -> list[tuple[int, str]]:
    """``count`` frames spread over the video: two inside every cut (a quarter and three
    quarters through it), the middle of the end fade, and the rest in the scenes' bodies."""
    chosen: dict[int, str] = {}
    for cut in desc["cuts"]:
        n_frames = int(cut["frames"])
        if n_frames <= 0:
            continue
        kind = "fade" if cut["to"] is None else f"cut:{cut['transition']}"
        picks = [n_frames // 2] if cut["to"] is None else sorted({n_frames // 4,
                                                                  (3 * n_frames) // 4})
        for k in picks:
            chosen[int(cut["startFrame"]) + k] = kind
    bodies = [(int(s["bodyStart"]), int(s["body"])) for s in desc["scenes"] if s["body"] > 2]
    remaining = max(count - len(chosen), 0)
    total = sum(length for _, length in bodies) or 1
    for start, length in bodies:
        share = max(1, round(remaining * length / total))
        for i in range(share):
            n = start + 1 + int((length - 2) * (i + 0.5) / share)
            chosen.setdefault(n, "motion")
    # rounding left the count short: fill the widest gap inside a scene's body, one at a time
    while len(chosen) < count and bodies:
        best: tuple[int, int] | None = None
        for start, length in bodies:
            inside = sorted(n for n in chosen if start <= n < start + length)
            edges = [start, *inside, start + length - 1]
            for a, b in zip(edges, edges[1:], strict=False):
                if b - a >= 2 and (best is None or b - a > best[1] - best[0]):
                    best = (a, b)
        if best is None:
            break
        chosen[(best[0] + best[1]) // 2] = "motion"
    ordered = sorted(chosen.items())
    if len(ordered) > count:  # keep every transition frame, thin the motion ones
        motion = [item for item in ordered if item[1] == "motion"]
        others = [item for item in ordered if item[1] != "motion"]
        keep = max(count - len(others), 0)
        step = len(motion) / keep if keep else 1
        motion = [motion[int(i * step)] for i in range(keep)]
        ordered = sorted(others + motion)
    return ordered


def side_by_side(engine: np.ndarray, render: np.ndarray, path: Path) -> None:
    diff = np.clip(np.abs(engine.astype(np.int16) - render.astype(np.int16)) * 4, 0, 255)
    strip = np.concatenate([engine, render, diff.astype(np.uint8)], axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(strip, cv2.COLOR_RGB2BGR))


def caption_pair(engine: np.ndarray, render: np.ndarray, a: pm.Box | None, b: pm.Box | None,
                 path: Path, pad: int = 16) -> None:
    """The caption area of the engine's and libass's frames (and their difference x 4), one
    above the other at twice the size."""
    boxes = [box for box in (a, b) if box]
    if not boxes:
        return
    x0 = max(min(box[0] for box in boxes) - pad, 0)
    y0 = max(min(box[1] for box in boxes) - pad, 0)
    x1 = min(max(box[2] for box in boxes) + pad, engine.shape[1])
    y1 = min(max(box[3] for box in boxes) + pad, engine.shape[0])
    crops = [engine[y0:y1, x0:x1], render[y0:y1, x0:x1]]
    diff = np.clip(np.abs(crops[0].astype(np.int16) - crops[1].astype(np.int16)) * 4, 0, 255)
    strip = np.concatenate([*crops, diff.astype(np.uint8)], axis=0)
    strip = cv2.resize(strip, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(strip, cv2.COLOR_RGB2BGR))


def preview_size(aspect: str) -> tuple[int, int]:
    return (PREVIEW_SHORT, PREVIEW_LONG) if aspect == "9:16" else (PREVIEW_LONG, PREVIEW_SHORT)


def frame_parity(page: Any, env: ParityEnv, key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """The 40 frames of one project at the preview size: SSIM against the 1080p render scaled
    down (the captions left out), and the caption boxes against libass.

    Caption boxes are compared at the preview size (the engine's captions alone on flat grey
    at 960 x 540, libass's drawing of the render's ASS file at the render's size scaled down,
    ``caption_iou_preview``) and at the render's own size (the engine drawing them at 1920 x
    1080, or 1080 x 1920, ``caption_iou``); both must reach :data:`CAPTION_IOU`. A one-line
    caption is about 15 px tall at 960 x 540, so a box edge a pixel off costs about 0.06."""
    project_id, folder = entry["id"], Path(entry["folder"])
    width, height = preview_size(entry["aspect"])
    frame_w, frame_h = (1080, 1920) if entry["aspect"] == "9:16" else (1920, 1080)
    opened = page.evaluate("([id, w, h]) => parity.open(id, {width: w, height: h})",
                           [project_id, width, height])
    desc = opened["description"]
    picks = pick_frames(desc)
    frames = [n for n, _ in picks]
    say(f"[{key}] {len(frames)} frames, renderer {opened['status']}")
    engine = {n: decode_png(page.evaluate("n => parity.frame(n)", n)) for n in frames}
    grey: dict[tuple[int, int], dict[int, np.ndarray]] = {}
    for size in ((width, height), (frame_w, frame_h)):
        page.evaluate("([id, w, h]) => parity.open(id, {width: w, height: h, grey: true})",
                      [project_id, size[0], size[1]])
        grey[size] = {n: decode_png(page.evaluate("n => parity.frame(n)", n)) for n in frames}
    page.evaluate("() => parity.close()")
    work = env.work_dir / key
    video = folder / "07_edit" / "final_1080p.mp4"
    render = render_frames(video, frames, (width, height), work / "render")
    ass = folder / "07_edit" / "captions.ass"
    seconds = desc["totalFrames"] / desc["fps"]
    reference = (libass_frames(ass, (frame_w, frame_h), frames, seconds, (frame_w, frame_h),
                               work / "libass") if ass.is_file() else {})
    factor = width / frame_w
    rows = []
    for n, kind in picks:
        small = grey[(width, height)][n]
        full = grey[(frame_w, frame_h)][n]
        engine_box = pm.changed_box(full, corner_colour(full))
        engine_small = pm.changed_box(small, corner_colour(small))
        render_box = render_small = None
        if n in reference:
            render_box = pm.changed_box(reference[n], corner_colour(reference[n]))
            shrunk = cv2.resize(reference[n], (width, height), interpolation=cv2.INTER_AREA)
            render_small = pm.changed_box(shrunk, corner_colour(shrunk))
        exclude = [box for box in (pm.grow(engine_small, 3, (width, height)),
                                   pm.grow(pm.scale_box(render_box, factor), 3, (width, height)))
                   if box]
        captioned = engine_box is not None or render_box is not None
        rows.append({
            "n": n,
            "t": round(n / desc["fps"], 3),
            "kind": kind,
            "ssim": pm.ssim(engine[n], render[n], exclude),
            "ssim_with_captions": pm.ssim(engine[n], render[n]),
            "caption_engine": engine_box,
            "caption_render": render_box,
            "caption_iou": pm.iou(engine_box, render_box) if captioned else None,
            "caption_iou_preview": pm.iou(engine_small, render_small) if captioned else None,
        })
    worst = sorted(rows, key=lambda row: row["ssim"])[:3]
    for row in worst:
        name = f"{key}_n{row['n']:05d}_{row['kind'].replace(':', '-')}.png"
        side_by_side(engine[row["n"]], render[row["n"]], env.worst_dir / name)
        row["pair"] = str(env.worst_dir / name)
    worst_caption = min((r for r in rows if r["caption_iou"] is not None),
                        key=lambda r: r["caption_iou"], default=None)
    if worst_caption is not None:
        n = worst_caption["n"]
        name = f"{key}_n{n:05d}_caption.png"
        caption_pair(grey[(frame_w, frame_h)][n], reference[n], worst_caption["caption_engine"],
                     worst_caption["caption_render"], env.worst_dir / name)
        worst_caption["pair"] = str(env.worst_dir / name)
    motion = [r["ssim"] for r in rows if r["kind"] == "motion"]
    transition = [r["ssim"] for r in rows if r["kind"] != "motion"]
    ious = [r["caption_iou"] for r in rows if r["caption_iou"] is not None]
    ious_small = [r["caption_iou_preview"] for r in rows if r["caption_iou_preview"] is not None]
    return {
        "project": key,
        "frames": len(rows),
        "size": [width, height],
        "renderer": opened["status"],
        "engine_errors": opened["errors"],
        "motion_ssim": pm.summary(motion),
        "transition_ssim": pm.summary(transition),
        "caption_iou": pm.summary(ious),
        "caption_iou_preview": pm.summary(ious_small),
        "passed": {
            "motion": bool(motion) and min(motion) >= MOTION_SSIM,
            "transitions": not transition or min(transition) >= TRANSITION_SSIM,
            "captions": (not ious or min(ious) >= CAPTION_IOU)
            and (not ious_small or min(ious_small) >= CAPTION_IOU),
        },
        "worst": [dict(row) for row in worst],
        "worst_caption": worst_caption,
        "rows": rows,
    }


# Sound -------------------------------------------------------------------------------------------


def read_wav_bytes(data: bytes) -> np.ndarray:
    """A 16-bit PCM WAV as float mono (channels averaged) at its own rate (48 kHz here)."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise RuntimeError("only 16-bit WAV files are read here")
    samples = np.frombuffer(raw, "<i2").astype(np.float64) / 32768.0
    return samples.reshape(-1, channels).mean(axis=1)


def render_sound(video: Path) -> np.ndarray:
    """The render's sound as float mono at 48 kHz (the two channels averaged)."""
    out = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(video), "-vn", "-ac", "2", "-ar", str(RATE),
         "-f", "f32le", "-"], check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return np.frombuffer(out, "<f4").astype(np.float64).reshape(-1, 2).mean(axis=1)


def voice_intervals(plan: dict[str, Any]) -> list[tuple[float, float]]:
    return sorted((c["atS"], c["atS"] + c["durationS"]) for c in plan.get("clips", []))


def overlaps_voice(intervals: list[tuple[float, float]], start: float, end: float) -> bool:
    return any(a < end and b > start for a, b in intervals)


def level_rows(engine: np.ndarray, engine_start_s: float, heard: np.ndarray,
               intervals: list[tuple[float, float]], skip_s: float = 0.5) -> list[dict[str, Any]]:
    """400 ms windows on the video clock inside a stretch of the engine's sound that starts at
    video time ``engine_start_s``: the level of each in the engine's sound and in the render's,
    and whether the voice speaks in it."""
    window = WINDOW_S
    first = math.ceil((engine_start_s + skip_s) / window)
    rows = []
    k = first
    while True:
        start = k * window
        end = start + window
        a = round((start - engine_start_s) * RATE)
        b = round((end - engine_start_s) * RATE)
        if b > engine.size or round(end * RATE) > heard.size:
            break
        mine = float(pm.window_db(engine[a:b], b - a)[0])
        theirs = float(pm.window_db(heard[round(start * RATE):round(end * RATE)], b - a)[0])
        rows.append({"t": round(start, 2), "engine_db": round(mine, 2),
                     "render_db": round(theirs, 2), "diff_db": round(theirs - mine, 3),
                     "voice": overlaps_voice(intervals, start, end)})
        k += 1
    return rows


def level_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    loud = [r for r in rows if r["engine_db"] > QUIET_DB or r["render_db"] > QUIET_DB]
    music = [r for r in loud if not r["voice"]]
    worst = max(loud, key=lambda r: abs(r["diff_db"]), default=None)
    worst_music = max(music, key=lambda r: abs(r["diff_db"]), default=None)
    return {
        "windows": len(loud),
        "diff_db": pm.summary([r["diff_db"] for r in loud]),
        "abs_diff_db_max": max((abs(r["diff_db"]) for r in loud), default=0.0),
        "worst_window": worst,
        "music_only_windows": len(music),
        "music_only_diff_db": pm.summary([r["diff_db"] for r in music]),
        "music_only_abs_diff_db_max": max((abs(r["diff_db"]) for r in music), default=0.0),
        "music_only_worst_window": worst_music,
    }


def recording_windows(seconds: float) -> list[tuple[float, float]]:
    """Where the engine's sound is recorded: the whole of a short video, or four 12 s
    stretches spread over a long one."""
    if seconds <= 45:
        return [(0.0, seconds - 0.2)]
    return [(seconds * share, 12.0) for share in (0.05, 0.33, 0.62, 0.93)]


def sound_parity(page: Any, base: str, key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """The sound of the preview against the render's.

    * **Plan, whole video:** the engine's voice clips (fetched from the same routes) and music
      stem mixed as the engine mixes them (voice at its up-mix gain, the master gain on top:
      loudnorm's measured curve, which the server measures first as EditReview has it do):
      every sentence found in the render by cross-correlation (its place against the plan's) and
      the level of every 400 ms window.
    * **Recorded:** what the engine really plays (its Web Audio output, recorded in the browser)
      on the video clock against the render at the same times: the offset of the sound
      (cross-correlation every 2 s) and the level of every 400 ms window.
    """
    width, height = preview_size(entry["aspect"])
    # the stem and the loudness (one gain and loudnorm's curve) for this sound, as EditReview
    # has the server prepare them (measured once, then read from 07_edit/preview/audio.json)
    prepared = page.evaluate(
        """id => fetch(`/api/projects/${id}/music/stem`, {method: "POST", credentials:
           "same-origin", headers: {"Content-Type": "application/json"}, body: "{}"})
           .then(r => r.status)""", entry["id"])
    if prepared != 200:
        raise RuntimeError(f"[{key}] the music could not be prepared ({prepared})")
    opened = page.evaluate("([id, w, h]) => parity.open(id, {width: w, height: h})",
                           [entry["id"], width, height])
    desc = opened["description"]
    voice_gain = float(opened.get("voiceGain") or 1.0)
    page.evaluate("() => parity.close()")
    plan = desc["audio"]
    fps = desc["fps"]
    total = round(desc["totalFrames"] * RATE / fps)
    fetch: Callable[[str], bytes] = lambda url: page.request.get(base + url).body()  # noqa: E731
    voice = np.zeros(total)
    clips = []
    for clip in plan["clips"]:
        samples = read_wav_bytes(fetch(clip["url"]))
        first = round(clip["offsetS"] * RATE)
        count = round(clip["durationS"] * RATE)
        piece = samples[first:first + count]
        at = round(clip["atS"] * RATE)
        end = min(at + piece.size, total)
        if end > at:
            voice[at:end] += piece[: end - at]
        clips.append((clip["id"], at, piece))
    stem = np.zeros(total)
    if plan.get("stemUrl"):
        music = read_wav_bytes(fetch(plan["stemUrl"]))
        stem[: min(total, music.size)] = music[:total]
    curve = plan.get("gainCurve") or None
    if curve and curve.get("gainDb"):
        values = np.asarray(curve["gainDb"], dtype=np.float64)
        middles = (np.arange(values.size) + 0.5) * float(curve["stepS"]) * RATE
        gain = 10 ** (np.interp(np.arange(total), middles, values) / 20)
    else:
        gain = np.full(total, 10 ** (float(plan.get("gainDb") or 0.0) / 20))
    mix = (voice * voice_gain + stem) * gain
    video = Path(entry["folder"]) / "07_edit" / ("final_1080p.mp4" if entry["presets"]
                                                else "proxy.mp4")
    heard = render_sound(video)[:total]
    if heard.size < total:
        heard = np.concatenate([heard, np.zeros(total - heard.size)])
    frame_ms = 1000.0 / fps
    errors = []
    for sid, at, piece in clips:
        template = piece[: min(piece.size, 3 * RATE)]
        found, score = pm.find_offset(heard, template, at, RATE // 2)
        errors.append({"sentence": sid, "at_s": round(at / RATE, 4),
                       "error_ms": round((found - at) * 1000.0 / RATE, 3),
                       "correlation": round(score, 3)})
    intervals = voice_intervals(plan)
    plan_levels = level_summary(level_rows(mix, 0.0, heard, intervals, skip_s=0.0))
    worst_plan = max((abs(e["error_ms"]) for e in errors), default=0.0)

    recordings = []
    level_all: list[dict[str, Any]] = []
    offsets_ms: list[float] = []
    page.evaluate("([id, w, h]) => parity.open(id, {width: w, height: h, audio: true})",
                  [entry["id"], width, height])
    for start, seconds in recording_windows(total / RATE):
        rec = page.evaluate("([f, s]) => parity.record(f, s)", [start, seconds])
        left = np.frombuffer(base64.b64decode(rec["left"]), "<f4").astype(np.float64)
        right = np.frombuffer(base64.b64decode(rec["right"]), "<f4").astype(np.float64)
        played = (left + right) / 2
        begin = float(rec["startVideoS"])
        rows = level_rows(played, begin, heard, intervals)
        level_all.extend(rows)
        # the plan's mix against what the engine really played (checks the model above)
        model_rows = level_rows(played, begin, mix, intervals)
        model_diff = max((abs(r["diff_db"]) for r in model_rows
                          if r["engine_db"] > QUIET_DB or r["render_db"] > QUIET_DB),
                         default=0.0)
        found_offsets = []
        for probe in np.arange(begin + 0.6, begin + played.size / RATE - 2.2, 2.0):
            a = round((probe - begin) * RATE)
            segment = played[a:a + 2 * RATE]
            if float(np.sqrt(np.mean(segment ** 2))) < 1e-4:
                continue
            expected = round(probe * RATE)
            where, score = pm.find_offset(heard, segment, expected, RATE // 4)
            if score > 0.5:
                found_offsets.append((where - expected) * 1000.0 / RATE)
        offsets_ms.extend(found_offsets)
        recordings.append({"from_s": start, "seconds": seconds,
                           "start_video_s": round(begin, 4),
                           "recorded_s": round(played.size / RATE, 2),
                           "audio_clock": rec["audioDriven"],
                           "offset_ms": pm.summary(found_offsets),
                           "plan_vs_played_db_max": round(model_diff, 3),
                           "levels": level_summary(rows)})
    page.evaluate("() => parity.close()")
    recorded = level_summary(level_all)
    worst_offset = max((abs(v) for v in offsets_ms), default=0.0)
    return {
        "project": key,
        "seconds": round(total / RATE, 2),
        "preview_gain_db": plan.get("gainDb"),
        "gain_curve": bool(curve and curve.get("gainDb")),
        "voice_gain": voice_gain,
        "stem": bool(plan.get("stemUrl")),
        "plan": {
            "sentences": len(errors),
            "sync_error_ms": pm.summary([e["error_ms"] for e in errors]),
            "sync_worst_ms": worst_plan,
            "weakest_correlation": min((e["correlation"] for e in errors), default=None),
            "levels": plan_levels,
        },
        "recorded": {
            "windows": recordings,
            "offset_ms": pm.summary(offsets_ms),
            "offset_worst_ms": worst_offset,
            "levels": recorded,
        },
        "sync_worst_frames": max(worst_plan, worst_offset) / frame_ms,
        # Both sides scale the same voice + music mix: the engine by the preview gain, the
        # render by loudnorm's gain. Every window that is not silence counts; the windows with
        # no voice in them (``pauses`` has them between its sentences) compare the music alone.
        "passed": {
            "sync": max(worst_plan, worst_offset) <= SYNC_FRAMES * frame_ms and bool(offsets_ms),
            "music": recorded["windows"] > 0 and recorded["abs_diff_db_max"] <= MUSIC_DB
            and plan_levels["abs_diff_db_max"] <= MUSIC_DB
            and recorded["music_only_abs_diff_db_max"] <= MUSIC_DB
            and plan_levels["music_only_abs_diff_db_max"] <= MUSIC_DB,
        },
        "errors": errors,
    }


def picture_sync(page: Any, env: ParityEnv, key: str, entry: dict[str, Any],
                 cuts: int = 8) -> dict[str, Any]:
    """Over a long video: the engine's frame in the middle of cuts spread over the whole video
    against the render's frames n - 1, n and n + 1 (the best match must be n)."""
    width, height = preview_size(entry["aspect"])
    opened = page.evaluate("([id, w, h]) => parity.open(id, {width: w, height: h})",
                           [entry["id"], width, height])
    desc = opened["description"]
    with_frames = [c for c in desc["cuts"] if c["to"] is not None and c["frames"] >= 4]
    step = max(len(with_frames) // cuts, 1)
    picked = with_frames[::step][:cuts]
    picked[-1:] = [with_frames[-1]]
    targets = [int(c["startFrame"]) + int(c["frames"]) // 2 for c in picked]
    engine = {n: decode_png(page.evaluate("n => parity.frame(n)", n)) for n in targets}
    page.evaluate("() => parity.close()")
    video = Path(entry["folder"]) / "07_edit" / ("final_1080p.mp4" if entry["presets"]
                                                else "proxy.mp4")
    around = sorted({n + d for n in targets for d in (-1, 0, 1)})
    render = render_frames(video, around, (width, height), env.work_dir / key / "sync")
    rows = []
    for n, cut in zip(targets, picked, strict=True):
        scores = {d: pm.ssim(engine[n], render[n + d]) for d in (-1, 0, 1)}
        best = max(scores, key=lambda d: scores[d])
        rows.append({"n": n, "t": round(n / desc["fps"], 2), "transition": cut["transition"],
                     "ssim": {str(d): round(v, 4) for d, v in scores.items()},
                     "offset_frames": best})
    return {"project": key, "cuts": rows,
            "max_offset_frames": max(abs(r["offset_frames"]) for r in rows),
            "passed": all(abs(r["offset_frames"]) <= SYNC_FRAMES for r in rows)}


def parity(keys: list[str] | None = None, root: Path | None = None) -> dict[str, Any]:
    """The whole parity proof; writes ``report.json`` and returns it."""
    env = ParityEnv(root)
    frame_keys = [k for k in (keys or FRAME_PROJECTS) if k not in SOUND_ONLY]
    sound_keys = [*frame_keys, *(k for k in SOUND_ONLY if keys is None or k in keys)]
    manifest = prepare_projects(env, sorted(set(frame_keys + sound_keys),
                                            key=list(pp.project_specs()).index))
    build_site(env)
    if env.worst_dir.exists() and not keys:
        shutil.rmtree(env.worst_dir)
    report_path = env.root / "report.json"
    previous: dict[str, Any] = {}
    if keys and report_path.is_file():  # a partial run updates the projects it ran
        previous = json.loads(report_path.read_text(encoding="utf-8"))
    report: dict[str, Any] = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "thresholds": {"motion_ssim": MOTION_SSIM,
                                             "transition_ssim": TRANSITION_SSIM,
                                             "caption_iou": CAPTION_IOU,
                                             "sync_frames": SYNC_FRAMES,
                                             "music_db": MUSIC_DB},
                              "frames": dict(previous.get("frames", {})),
                              "sound": dict(previous.get("sound", {})),
                              "picture_sync": dict(previous.get("picture_sync", {}))}
    with server(env) as base, browser_page(base, env.account()) as page:
        report["browser"] = page.evaluate(
            "() => { const gl = document.createElement('canvas').getContext('webgl2');"
            " const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');"
            " return {userAgent: navigator.userAgent, webgl: ext ?"
            " gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null}; }")
        for key in frame_keys:
            report["frames"][key] = frame_parity(page, env, key, manifest[key])
            say(f"[{key}] motion {report['frames'][key]['motion_ssim']} "
                f"transitions {report['frames'][key]['transition_ssim']} "
                f"captions {report['frames'][key]['caption_iou']}")
        for key in sound_keys:
            report["sound"][key] = sound_parity(page, base, key, manifest[key])
            sound = report["sound"][key]
            say(f"[{key}] sync plan {sound['plan']['sync_worst_ms']} ms, recorded "
                f"{sound['recorded']['offset_worst_ms']:.2f} ms; music-only windows "
                f"{sound['recorded']['levels']['music_only_abs_diff_db_max']:.2f} dB, all "
                f"{sound['recorded']['levels']['abs_diff_db_max']:.2f} dB")
        if "ten_minutes" in sound_keys:
            report["picture_sync"]["ten_minutes"] = picture_sync(page, env, "ten_minutes",
                                                                 manifest["ten_minutes"])
    checks = [all(r["passed"].values()) for r in report["frames"].values()]
    checks += [all(r["passed"].values()) for r in report["sound"].values()]
    checks += [r["passed"] for r in report["picture_sync"].values()]
    # the music must have been heard alone somewhere (not only under the voice)
    report["music_only_windows"] = sum(r["recorded"]["levels"]["music_only_windows"]
                                       for r in report["sound"].values())
    checks.append(report["music_only_windows"] > 0)
    report["passed"] = all(checks)
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report
