"""The mock projects of the preview parity proof (docs/M5-CONTRACT.md section 6).

Each project is written as the earlier stages would leave it (storyboard, voice timing, the
voice and its sentence files, pictures, a licensed music track) and then goes through the real
edit step, which builds ``timeline.json``, the music stem and envelope, the preview gain, the
captions file, the popups and the renders (the proxy always; ``final_1080p.mp4`` unless the
project is proxy-only).

* ``three``: 3 scenes of the classic mock pictures, two sentences each, bold-outline captions,
  two popups (16:9).
* ``twelve_a``, ``twelve_b``, ``twelve_c``: 12 scenes over photo-like pictures of four sizes
  and orientations; together their 33 cuts use all 27 transitions (``twelve_c`` also the old
  FFmpeg names), every camera move, three popup animations and the active-word, boxed and
  bold-outline captions; ``twelve_b`` ends on the white fade.
* ``short``: a 9:16 Short of 5 scenes with single-word-pop captions.
* ``ten_minutes``: 60 scenes and 120 sentences, a little over ten minutes, proxy only: the voice and
  picture sync and the music level over a long video, and the performance run.
* ``ten_minutes_photo``: the same 60 scenes over the photo-like pictures of the twelve-scene
  projects (2048 pixel PNGs, about 150 to 450 ms each to turn into the preview's JPEG), proxy
  only: the performance run's first frame and cold seeks with pictures like real ones, starting
  from no preview JPEG on the server (docs/M5-CONTRACT.md 8.7.1).
* ``pauses``: 6 scenes of one sentence with 1.6 s of silence after each, proxy only: the music
  alone between the sentences (where it comes back up after the dip), so the music level is
  compared in windows with no voice in them.

The voice is synthetic but speech-like (voiced syllables with formants and breath noise, a
different random waveform per sentence) so every sentence can be found again in a render by
cross-correlation and the music dips under it. Nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import json
import math
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

RATE = 48000
FPS = 30

TRANSITIONS = [
    "film_dissolve", "dip_black", "dip_white", "blur_dissolve", "zoom_through", "push_left",
    "push_right", "push_up", "push_down", "whip_left", "whip_right", "whip_up", "whip_down",
    "luma_wipe_left", "luma_wipe_right", "luma_wipe_up", "luma_wipe_down", "luma_wipe_diag",
    "crosswarp", "directional_warp", "light_leak_warm", "film_burn", "glitch_subtle",
    "spin_zoom", "morph_blend", "cube_left", "cube_right",
]
LEGACY = ["fade", "wipeleft", "slideleft", "zoomin", "fadeblack", "fadewhite"]
PRESETS = [
    "zoom_in", "pan_right", "zoom_out", "pan_left", "slow_push", "pan_up", "pan_down", "hold",
    "drift", "tilt_up", "dolly_in", "tilt_down", "dolly_out", "handheld",
]
POSITIONS = ["top-left", "bottom-right", "top-right", "center", "bottom-left"]
POPUP_STYLES = ["", "pop", "fade"]  # slide_left, pop and fade (render/timeline_builder.py)
WORDS = (
    "the morning light came slowly over quiet hills while old friends gathered near a small "
    "wooden table and talked about the long winter the harbour the missing letter the strange "
    "visitor who paid for every meal and never told anyone his name until the very last day "
    "when the whole town finally understood what he had done for them years before"
).split()
POPUP_TEXTS = [
    "Five every morning", "A folded note", "Nobody knew", "Years later", "The last day",
    "Paid in full", "A quiet promise", "Back home",
]
PICTURE_SIZES = [(2048, 1152), (1536, 1024), (1024, 1536), (2048, 2048)]


@dataclass(frozen=True)
class SceneSpec:
    sentences: int
    preset: str
    transition: str
    duration_s: float
    popup: str | None = None
    popup_style: str = ""
    position: str = "bottom-left"


@dataclass(frozen=True)
class ProjectSpec:
    key: str
    aspect: str
    scenes: tuple[SceneSpec, ...]
    pictures: str  # "mock" or "photo"
    caption_preset: str = "bold-outline"
    presets: tuple[str, ...] = ("1080p",)
    picture_size: str = "2K"
    seed: int = 1
    words_per_sentence: tuple[int, int] = (6, 9)
    long: bool = False
    pause_s: float = 0.25
    """The silence between two sentences of the voice."""
    notes: list[str] = field(default_factory=list)

    @property
    def fmt(self) -> str:
        return "shorts" if self.aspect == "9:16" else "long"


def _twelve(key: str, transitions: list[str], caption: str, seed: int) -> ProjectSpec:
    scenes = []
    for i in range(12):
        popup = POPUP_TEXTS[(i + seed) % len(POPUP_TEXTS)] if i in (1, 4, 8) else None
        scenes.append(SceneSpec(
            sentences=1, preset=PRESETS[(i + seed) % len(PRESETS)],
            transition=transitions[i], duration_s=[0.6, 0.5, 0.7, 0.45][i % 4],
            popup=popup, popup_style=POPUP_STYLES[[1, 4, 8].index(i)] if popup else "",
            position=POSITIONS[i % len(POSITIONS)],
        ))
    return ProjectSpec(key=key, aspect="16:9", scenes=tuple(scenes), pictures="photo",
                       caption_preset=caption, seed=seed, words_per_sentence=(7, 9))


def project_specs() -> dict[str, ProjectSpec]:
    """Every parity project by key (the order they are made in)."""
    three = ProjectSpec(
        key="three", aspect="16:9", pictures="mock", seed=3,
        scenes=(
            SceneSpec(2, "zoom_in", "film_dissolve", 0.7, "Five every morning", "",
                      "top-left"),
            SceneSpec(2, "pan_right", "push_left", 0.5),
            SceneSpec(2, "handheld", "dip_black", 0.8, "A folded note", "pop", "bottom-right"),
        ),
    )
    a = _twelve("twelve_a", TRANSITIONS[0:11] + ["dip_black"], "active-word", 5)
    b = _twelve("twelve_b", TRANSITIONS[11:22] + ["dip_white"], "boxed", 6)
    c = _twelve("twelve_c", TRANSITIONS[22:27] + LEGACY + ["dip_black"], "bold-outline", 7)
    short = ProjectSpec(
        key="short", aspect="9:16", pictures="mock", seed=9, caption_preset="single-word-pop",
        words_per_sentence=(5, 7),
        scenes=(
            SceneSpec(1, "pan_up", "whip_up", 0.35, "Wait for it", "pop", "center"),
            SceneSpec(1, "tilt_down", "push_down", 0.4),
            SceneSpec(1, "dolly_in", "zoom_through", 0.45, "The last day", "", "top-right"),
            SceneSpec(1, "zoom_out", "luma_wipe_up", 0.4),
            SceneSpec(1, "drift", "dip_black", 0.6),
        ),
    )
    long_scenes = []
    cinematic = ["film_dissolve", "blur_dissolve", "luma_wipe_left", "zoom_through",
                 "light_leak_warm", "dip_black", "push_left", "luma_wipe_diag"]
    for i in range(60):
        popup = POPUP_TEXTS[i % len(POPUP_TEXTS)] if i % 4 == 1 else None
        long_scenes.append(SceneSpec(
            sentences=2, preset=PRESETS[i % len(PRESETS)],
            transition=cinematic[i % len(cinematic)] if i < 59 else "dip_black",
            duration_s=0.7, popup=popup, popup_style=POPUP_STYLES[i % 3],
            position=POSITIONS[i % len(POSITIONS)],
        ))
    ten = ProjectSpec(key="ten_minutes", aspect="16:9", scenes=tuple(long_scenes),
                      pictures="mock", picture_size="1K", presets=(), seed=11,
                      words_per_sentence=(14, 18), long=True)
    ten_photo = ProjectSpec(key="ten_minutes_photo", aspect="16:9", scenes=tuple(long_scenes),
                            pictures="photo", presets=(), seed=11,
                            words_per_sentence=(14, 18), long=True)
    pauses = ProjectSpec(
        key="pauses", aspect="16:9", pictures="mock", picture_size="1K", presets=(), seed=13,
        pause_s=1.6, words_per_sentence=(5, 7),
        scenes=tuple(SceneSpec(1, PRESETS[i], ["film_dissolve", "dip_black", "push_left",
                                                "luma_wipe_left", "zoom_through", "dip_black"][i],
                               0.6) for i in range(6)),
    )
    return {spec.key: spec for spec in (three, a, b, c, short, ten, pauses, ten_photo)}


# The voice ------------------------------------------------------------------------------------


def _formant_filter(signal: np.ndarray, centres: list[float], width: float) -> np.ndarray:
    """``signal`` through a few resonant band-passes (FFT domain), summed."""
    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(signal.size, 1 / RATE)
    gain = np.zeros_like(freqs)
    for number, centre in enumerate(centres):
        gain += (0.8 ** number) * np.exp(-0.5 * ((freqs - centre) / width) ** 2)
    return np.fft.irfft(spectrum * gain, n=signal.size)


def synth_sentence(seed: int, words: list[str]) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """A speech-like sentence: per word one to three voiced syllables (a glottal pulse train
    through formants plus a little breath noise) and short pauses. Returns the samples
    (float, about -20 dBFS RMS while speaking) and each word's start and end in seconds."""
    rng = np.random.default_rng(seed)
    pieces: list[np.ndarray] = [np.zeros(int(0.04 * RATE))]
    clock = 0.04
    word_times: list[tuple[float, float]] = []
    f0_base = rng.uniform(105, 175)
    for word in words:
        start = clock
        for _syllable in range(max(1, min(3, round(len(word) / 3)))):
            length = int(rng.uniform(0.10, 0.17) * RATE)
            t = np.arange(length) / RATE
            f0 = f0_base * (1 + 0.08 * math.sin(rng.uniform(0, 6)) + 0.05 * t / t[-1])
            phase = 2 * np.pi * f0 * t + rng.uniform(0, 2 * np.pi)
            voiced = sum(np.sin(k * phase) / k for k in range(1, 16))
            breath = rng.standard_normal(length) * 0.35
            raw = voiced + breath
            vowel = [rng.uniform(300, 800), rng.uniform(900, 2200), rng.uniform(2400, 3200)]
            shaped = _formant_filter(raw, vowel, 140.0)
            attack = np.minimum(1.0, t / 0.015)
            decay = np.minimum(1.0, (t[-1] - t) / 0.03)
            pieces.append(shaped * attack * decay)
            gap = int(rng.uniform(0.008, 0.025) * RATE)
            pieces.append(np.zeros(gap))
            clock += (length + gap) / RATE
        word_times.append((round(start, 3), round(clock, 3)))
        pause = int(rng.uniform(0.04, 0.09) * RATE)
        pieces.append(np.zeros(pause))
        clock += pause / RATE
    pieces.append(np.zeros(int(0.06 * RATE)))
    samples = np.concatenate(pieces)
    loud = samples[np.abs(samples) > 1e-6]
    rms = float(np.sqrt(np.mean(loud ** 2))) if loud.size else 1.0
    samples = samples * (10 ** (-20 / 20) / max(rms, 1e-9))
    peak = float(np.max(np.abs(samples)))
    if peak > 0.7:
        samples *= 0.7 / peak
    return samples.astype(np.float64), word_times


def write_wav(path: Path, samples: np.ndarray, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(data.tobytes())


def write_voice(folder: Path, spec: ProjectSpec) -> dict[str, Any]:
    """``05_voice``: one WAV per sentence, ``voice.wav`` (the sentences with ``pause_s`` of
    silence between them) and ``timing.json`` with sample offsets and word times."""
    rng = np.random.default_rng(spec.seed)
    voice_dir = folder / "05_voice"
    rows: list[dict[str, Any]] = []
    parts: list[np.ndarray] = []
    cursor = 0
    gap = int(spec.pause_s * RATE)
    number = 0
    for scene_number, scene in enumerate(spec.scenes, start=1):
        for sentence in range(scene.sentences):
            number += 1
            count = int(rng.integers(spec.words_per_sentence[0], spec.words_per_sentence[1] + 1))
            start_word = int(rng.integers(0, len(WORDS) - count))
            words = WORDS[start_word:start_word + count]
            text = " ".join(words).capitalize() + "."
            words[-1] = words[-1] + "."
            words[0] = words[0].capitalize()
            samples, times = synth_sentence(spec.seed * 1000 + number, words)
            sid = f"s-{scene_number:02d}-{sentence + 1:02d}"
            write_wav(voice_dir / "sentences" / f"{sid}.wav", samples)
            start_s = cursor / RATE
            rows.append({
                "id": sid,
                "text": text,
                "start_s": round(start_s, 6),
                "end_s": round((cursor + samples.size) / RATE, 6),
                "start_frame": cursor,
                "end_frame": cursor + samples.size,
                "words": [{"text": w, "start_s": round(start_s + a, 3),
                           "end_s": round(start_s + b, 3), "confidence": 0.9}
                          for w, (a, b) in zip(words, times, strict=True)],
            })
            parts.append(samples)
            parts.append(np.zeros(gap))
            cursor += samples.size + gap
    voice = np.concatenate(parts[:-1])
    write_wav(voice_dir / "voice.wav", voice)
    timing = {
        "sample_rate": RATE,
        "duration_s": round(voice.size / RATE, 6),
        "source": "provider_word",
        "timing_confidence": 0.9,
        "sentences": rows,
    }
    (voice_dir / "timing.json").write_text(json.dumps(timing, indent=1), encoding="utf-8")
    return timing


# Music ----------------------------------------------------------------------------------------


def write_music(folder: Path, seconds: float = 24.0) -> Path:
    """A licensed stereo music bed: slow chords with a soft beat, so its level moves a little
    from window to window (the music check compares 400 ms windows)."""
    folder.mkdir(parents=True, exist_ok=True)
    track = folder / "parity-bed.wav"
    if track.is_file():
        return track
    t = np.arange(int(seconds * RATE)) / RATE
    chords = [(220.0, 277.18, 329.63), (196.0, 246.94, 293.66), (174.61, 220.0, 261.63),
              (196.0, 246.94, 329.63)]
    left = np.zeros_like(t)
    right = np.zeros_like(t)
    bar = 2.0
    for index, chord in enumerate(chords * int(seconds / (bar * len(chords)) + 1)):
        start = index * bar
        if start >= seconds:
            break
        sel = (t >= start) & (t < start + bar)
        local = t[sel] - start
        env = np.minimum(1.0, local / 0.08) * np.exp(-local * 0.6)
        for k, f in enumerate(chord):
            left[sel] += env * np.sin(2 * np.pi * f * t[sel] + k) / (k + 1)
            right[sel] += env * np.sin(2 * np.pi * f * 1.002 * t[sel] + 2 * k) / (k + 1)
    rng = np.random.default_rng(42)
    beat = np.zeros_like(t)
    for start in np.arange(0, seconds, 0.5):
        sel = (t >= start) & (t < start + 0.12)
        local = t[sel] - start
        beat[sel] += rng.standard_normal(sel.sum()) * np.exp(-local * 40) * 0.5
    left += beat
    right += beat
    stereo = np.stack([left, right], axis=1)
    stereo *= 0.5 / np.max(np.abs(stereo))
    data = np.clip(np.round(stereo * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(track), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(data.tobytes())
    (folder / "LICENSE.txt").write_text(
        "Music bed made by the preview parity harness. Free to use.\n", encoding="utf-8")
    return track


# Pictures ------------------------------------------------------------------------------------


def photo_picture(path: Path, size: tuple[int, int], seed: int, label: str) -> None:
    """A picture with natural-image statistics (1/f noise with correlated colours), a few
    soft and hard edged shapes and some text, so motion and transitions are compared on real
    detail rather than flat colour."""
    width, height = size
    rng = np.random.default_rng(seed)
    fy = np.fft.fftfreq(height)[:, None]
    fx = np.fft.rfftfreq(width)[None, :]
    radius = np.sqrt(fx ** 2 + fy ** 2)
    radius[0, 0] = 1.0
    amplitude = 1.0 / radius ** 1.05
    amplitude[0, 0] = 0.0
    channels = []
    for _ in range(3):
        phase = rng.uniform(0, 2 * np.pi, amplitude.shape)
        field = np.fft.irfft2(amplitude * np.exp(1j * phase), s=(height, width))
        field = (field - field.mean()) / (field.std() + 1e-9)
        channels.append(field.astype(np.float32))
    base = np.stack(channels, axis=-1)
    mix = rng.normal(0, 0.35, (3, 3)) + np.eye(3) * 0.6 + 0.4
    colour = base @ mix.T.astype(np.float32)
    colour = colour / (np.abs(colour).max() + 1e-9)
    tint = rng.uniform(70, 170, 3).astype(np.float32)
    pixels = np.clip(tint + colour * 85.0, 0, 255).astype(np.uint8)
    image = Image.fromarray(pixels, "RGB")
    draw = ImageDraw.Draw(image)
    for _ in range(5):
        w = int(width * rng.uniform(0.08, 0.3))
        h = int(height * rng.uniform(0.08, 0.3))
        x = int(rng.uniform(0, width - w))
        y = int(rng.uniform(0, height - h))
        fill = tuple(int(v) for v in rng.uniform(20, 235, 3))
        if rng.random() < 0.5:
            draw.ellipse([x, y, x + w, y + h], fill=fill)
        else:
            draw.rectangle([x, y, x + w, y + h], fill=fill, outline=(250, 250, 250), width=3)
    image = image.filter(ImageFilter.GaussianBlur(0.6))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(18, min(width, height) // 14))
    draw.text((width * 0.08, height * 0.12), label, font=font, fill=(255, 255, 255),
              stroke_width=3, stroke_fill=(10, 10, 10))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def mock_picture(path: Path, aspect: str, size: str, scene: int, prompt: str) -> None:
    """The mock image provider's placeholder, exactly as the app makes it offline."""
    from sigma_editor.providers.image.base import ImageRequest
    from sigma_editor.providers.image.mock import MockImageProvider

    MockImageProvider().generate(ImageRequest(
        prompt=prompt, output_path=path, aspect=aspect, size=size, scene_id=str(scene)))


# The storyboard -----------------------------------------------------------------------------


def write_storyboard(folder: Path, spec: ProjectSpec, timing: dict[str, Any]) -> None:
    from sigma_editor.pipeline.stages.storyboard import MOTION_RECTS

    from sigma_editor.models.storyboard import (
        Scene,
        SceneImage,
        SceneMotion,
        ScenePopup,
        SceneTransition,
        StoryboardDoc,
    )

    by_scene: dict[int, list[dict[str, Any]]] = {}
    for row in timing["sentences"]:
        by_scene.setdefault(int(row["id"].split("-")[1]), []).append(row)
    scenes = []
    clock = 0.0
    for index, scene in enumerate(spec.scenes):
        rows = by_scene[index + 1]
        start, end = MOTION_RECTS[scene.preset]
        length = rows[-1]["end_s"] - rows[0]["start_s"] + spec.pause_s
        scenes.append(Scene(
            index=index,
            sentence_ids=[row["id"] for row in rows],
            narration=" ".join(row["text"] for row in rows),
            est_start_s=round(clock, 3), est_end_s=round(clock + length, 3),
            est_duration_s=round(length, 3),
            image_prompt=f"Picture {index + 1}",
            popup=ScenePopup(text=scene.popup, style=scene.popup_style,
                             position=scene.position),  # type: ignore[arg-type]
            motion=SceneMotion(preset=scene.preset, start_rect=list(start),  # type: ignore[arg-type]
                               end_rect=list(end)),
            transition_out=SceneTransition(type=scene.transition, duration_s=scene.duration_s),
            image=SceneImage(path=f"06_images/scene_{index + 1:02d}.png", status="generated"),
        ))
        clock += length
    doc = StoryboardDoc(
        project_id="parity", format=spec.fmt, aspect=spec.aspect,  # type: ignore[arg-type]
        style_guide="Parity test", generated_at=datetime(2026, 10, 12, tzinfo=UTC),
        model="parity", motion_style="ken_burns", scenes=scenes,
    )
    path = folder / "04_storyboard" / "storyboard.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc.model_dump_json(indent=1), encoding="utf-8")


def write_pictures(folder: Path, spec: ProjectSpec) -> None:
    for index in range(len(spec.scenes)):
        path = folder / "06_images" / f"scene_{index + 1:02d}.png"
        if path.is_file():
            continue
        if spec.pictures == "photo":
            size = PICTURE_SIZES[index % len(PICTURE_SIZES)]
            photo_picture(path, size, spec.seed * 100 + index, f"Scene {index + 1}")
        else:
            mock_picture(path, spec.aspect, spec.picture_size, index + 1,
                         f"Scene {index + 1} of {spec.key}: a quiet street at dawn")


def write_project(folder: Path, spec: ProjectSpec) -> dict[str, Any]:
    """Everything the edit step reads, as the earlier stages leave it."""
    for name in ("03_script", "04_storyboard", "05_voice", "06_images", "07_edit"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    timing = write_voice(folder, spec)
    write_storyboard(folder, spec, timing)
    write_pictures(folder, spec)
    return timing


# The edit step --------------------------------------------------------------------------------


def run_edit(engine: Any, settings: Any, project_id: str, edits: dict[str, Any]) -> Any:
    """Runs the real edit stage on one project (as an approval with ``edits`` would)."""
    from sigma_editor.pipeline.stages.base import StageContext
    from sigma_editor.pipeline.stages.edit import EditStage

    project = engine.get(project_id)
    channel = engine.channels.get(project.channel_slug)
    ctx = StageContext(project=project, channel=channel, settings=settings,
                       folder=Path(project.folder), edits=dict(edits))
    return asyncio.run(EditStage().run(ctx))


def make_projects(engine: Any, settings: Any, channel_slug: str, root: Path,
                  keys: list[str] | None = None, log: Any = print) -> dict[str, dict[str, Any]]:
    """Creates (or reuses, see ``root/manifest.json``) and renders the parity projects."""
    from sigma_editor.pipeline.stages.edit import read_timeline

    from sigma_editor.models.project import ProjectCreate, ProjectSource
    from sigma_editor.models.timeline import Timeline

    manifest_path = root / "manifest.json"
    manifest: dict[str, dict[str, Any]] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    specs = project_specs()
    channel = engine.channels.get(channel_slug)
    for key in keys or list(specs):
        spec = specs[key]
        entry = manifest.get(key)
        if entry and entry.get("done"):
            try:
                engine.get(entry["id"])
                continue
            except Exception:  # noqa: BLE001 - the store was reset: make it again
                pass
        created = engine.create_project(
            ProjectCreate(channel_slug=channel_slug, format=spec.fmt,
                          source=ProjectSource(kind="own_topic", topic_text=f"Parity {key}")),
            channel,
        )
        folder = Path(created.folder)
        log(f"[{key}] writing the sources in {folder}")
        write_project(folder, spec)
        # 1) the timeline and the proxy; 2) the look this project tests and its final size
        log(f"[{key}] edit step: timeline and proxy")
        run_edit(engine, settings, created.id, {"presets": []})
        timeline = read_timeline(folder / "07_edit")
        assert timeline is not None
        data = json.loads(timeline.model_dump_json())
        data["captions"]["style"]["preset"] = spec.caption_preset
        changed = Timeline.model_validate(data)
        if spec.caption_preset != timeline.captions.style.preset or spec.presets:
            log(f"[{key}] edit step: {spec.caption_preset} captions, sizes {list(spec.presets)}")
            run_edit(engine, settings, created.id,
                     {"timeline": json.loads(changed.model_dump_json()),
                      "presets": list(spec.presets)})
        manifest[key] = {"id": created.id, "folder": str(folder), "done": True,
                         "aspect": spec.aspect, "presets": list(spec.presets)}
        manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest
