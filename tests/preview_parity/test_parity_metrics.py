"""The measures of the preview parity proof (``parity_metrics.py``) and its frame choice, so the
dev-only proof itself is checked by the normal test run (no browser, no FFmpeg)."""

from __future__ import annotations

import numpy as np
import parity_harness as ph
import parity_metrics as pm
import parity_projects as pp
import pytest


def picture(seed: int = 1, size: tuple[int, int] = (96, 64)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.uniform(0, 255, (size[1] // 8, size[0] // 8, 3))
    return np.kron(base, np.ones((8, 8, 1))).astype(np.uint8)


def test_ssim_is_one_for_the_same_picture_and_drops_with_a_shift() -> None:
    a = picture()
    assert pm.ssim(a, a) == pytest.approx(1.0)
    shifted = np.roll(a, 3, axis=1)
    assert pm.ssim(a, shifted) < 0.9
    noisy = np.clip(a.astype(int) + np.random.default_rng(2).integers(-3, 4, a.shape), 0, 255)
    assert 0.9 < pm.ssim(a, noisy.astype(np.uint8)) < 1.0


def test_ssim_leaves_out_the_excluded_boxes() -> None:
    a = picture()
    b = a.copy()
    b[40:56, 20:70] = 255 - b[40:56, 20:70]  # a "caption" that differs
    assert pm.ssim(a, b) < 0.9
    assert pm.ssim(a, b, exclude=[pm.grow((20, 40, 70, 56), 6, (96, 64))]) > 0.99


def test_caption_boxes_and_their_overlap() -> None:
    grey = np.full((40, 80, 3), 128, np.uint8)
    text = grey.copy()
    text[30:35, 20:61] = 255
    assert pm.changed_box(grey, 128.0) is None
    assert pm.changed_box(text, 128.0) == (20, 30, 61, 35)
    assert pm.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert pm.iou((0, 0, 10, 10), (0, 1, 10, 11)) == pytest.approx(90 / 110)
    assert pm.iou(None, None) == 1.0 and pm.iou((0, 0, 1, 1), None) == 0.0
    assert pm.scale_box((757, 934, 1163, 1018), 0.5) == (378, 467, 582, 509)


def test_a_sentence_is_found_where_it_sits_in_the_mix() -> None:
    rng = np.random.default_rng(3)
    sentence = rng.standard_normal(4800) * 0.2
    mix = rng.standard_normal(48000) * 0.02
    mix[12345:12345 + sentence.size] += sentence
    found, score = pm.find_offset(mix, sentence, expected=12000, search=2400)
    assert found == 12345 and score > 0.95


def test_window_levels() -> None:
    tone = np.sin(np.arange(48000) / 48000 * 2 * np.pi * 440) * 0.5
    levels = pm.window_db(tone, 19200)
    assert levels.size == 2 and levels == pytest.approx(20 * np.log10(0.5 / np.sqrt(2)), abs=0.05)
    assert pm.window_db(np.zeros(100), 50).tolist() == [-200.0, -200.0]


def test_forty_frames_cover_every_cut_and_the_scenes() -> None:
    desc = {
        "scenes": [{"bodyStart": 0, "body": 150}, {"bodyStart": 168, "body": 140},
                   {"bodyStart": 320, "body": 150}],
        "cuts": [
            {"startFrame": 150, "frames": 18, "to": 1, "transition": "film_dissolve"},
            {"startFrame": 308, "frames": 12, "to": 2, "transition": "push_left"},
            {"startFrame": 470, "frames": 24, "to": None, "transition": "dip_black"},
        ],
    }
    picks = ph.pick_frames(desc)
    assert len(picks) == 40
    kinds = [kind for _, kind in picks]
    assert kinds.count("cut:film_dissolve") == 2 and kinds.count("cut:push_left") == 2
    assert kinds.count("fade") == 1 and kinds.count("motion") == 35
    assert [n for n, _ in picks] == sorted({n for n, _ in picks})


def test_the_parity_projects_use_every_transition() -> None:
    specs = pp.project_specs()
    used = {scene.transition for key in ("twelve_a", "twelve_b", "twelve_c")
            for scene in specs[key].scenes[:-1]}
    assert set(pp.TRANSITIONS) <= used
    assert set(pp.LEGACY) <= used
    assert all(len(specs[key].scenes) == 12 for key in ("twelve_a", "twelve_b", "twelve_c"))
    assert specs["short"].aspect == "9:16" and specs["three"].aspect == "16:9"
    ten = specs["ten_minutes"]
    assert len(ten.scenes) == 60 and sum(s.sentences for s in ten.scenes) == 120
    # the music is heard alone between the sentences of one project (the music level check)
    assert specs["pauses"].pause_s >= 3 * ph.WINDOW_S and not specs["pauses"].presets
    assert set(ph.SOUND_ONLY) | set(ph.FRAME_PROJECTS) | set(ph.PERF_ONLY) == set(specs)
    # the performance run's long project over photo-like pictures (cold seeks, first frame)
    photo = specs["ten_minutes_photo"]
    assert photo.pictures == "photo" and photo.scenes == ten.scenes and not photo.presets
    assert {specs[k].caption_preset for k in specs} == {
        "bold-outline", "active-word", "boxed", "single-word-pop"}


def test_the_synthetic_voice_is_speech_like_and_unique() -> None:
    one, words_one = pp.synth_sentence(1, ["the", "morning", "light"])
    two, _ = pp.synth_sentence(2, ["the", "morning", "light"])
    assert len(words_one) == 3 and all(b > a for a, b in words_one)
    speaking = one[np.abs(one) > 1e-6]
    assert -24 < 20 * np.log10(np.sqrt(np.mean(speaking ** 2))) < -16
    assert np.max(np.abs(one)) <= 0.7 + 1e-9
    size = min(one.size, two.size)
    correlation = np.corrcoef(one[:size], two[:size])[0, 1]
    assert abs(correlation) < 0.2
