"""The measures of the preview parity proof (docs/M5-CONTRACT.md section 6): SSIM of two
frames, the box of the caption on a frame and the overlap of two boxes, where a sentence sits in
a render's sound (cross-correlation) and the level of 400 ms windows. Pure numpy and OpenCV, no
browser and no FFmpeg, so ``test_parity_metrics.py`` checks them in the normal test run."""

from __future__ import annotations

import math
from collections.abc import Sequence

import cv2
import numpy as np

Box = tuple[int, int, int, int]
"""``(x0, y0, x1, y1)``, x1 and y1 excluded."""

LUMA_709 = np.array([0.2126, 0.7152, 0.0722])


def luma(rgb: np.ndarray) -> np.ndarray:
    """BT.709 luma of an ``H x W x 3`` (or 4) picture, as float64 in 0..255."""
    return np.asarray(rgb, dtype=np.float64)[..., :3] @ LUMA_709


def ssim_map(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The SSIM index per pixel (Wang et al. 2004: Gaussian window, sigma 1.5, K1 0.01,
    K2 0.03) of two luma pictures in 0..255."""
    if a.shape != b.shape:
        raise ValueError(f"the pictures differ in size: {a.shape} and {b.shape}")
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    def blur(x: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(x, (11, 11), 1.5, borderType=cv2.BORDER_REFLECT)

    mu_a, mu_b = blur(a), blur(b)
    var_a = blur(a * a) - mu_a ** 2
    var_b = blur(b * b) - mu_b ** 2
    cov = blur(a * b) - mu_a * mu_b
    return ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / (
        (mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2))


def ssim(a: np.ndarray, b: np.ndarray, exclude: Sequence[Box] = ()) -> float:
    """Mean SSIM of two RGB pictures on their luma, leaving out the ``exclude`` boxes (the
    captions, which are compared as boxes, not pixels)."""
    values = ssim_map(luma(a), luma(b))
    keep = np.ones(values.shape, bool)
    for x0, y0, x1, y1 in exclude:
        keep[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)] = False
    return float(values[keep].mean()) if keep.any() else 1.0


def changed_box(picture: np.ndarray, background: np.ndarray | float, threshold: float = 20.0,
                min_pixels: int = 12) -> Box | None:
    """The box around the pixels of ``picture`` that differ from ``background`` (a picture
    or one grey level) by more than ``threshold`` in any channel; None when fewer than
    ``min_pixels`` do."""
    pic = np.asarray(picture, dtype=np.int32)[..., :3]
    ref = np.asarray(background, dtype=np.int32)
    if ref.ndim == 3:
        ref = ref[..., :3]
    mask = (np.abs(pic - ref) > threshold).any(axis=-1)
    if int(mask.sum()) < min_pixels:
        return None
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def scale_box(box: Box | None, factor: float) -> Box | None:
    if box is None:
        return None
    x0, y0, x1, y1 = box
    return (math.floor(x0 * factor), math.floor(y0 * factor), math.ceil(x1 * factor),
            math.ceil(y1 * factor))


def iou(a: Box | None, b: Box | None) -> float:
    """Intersection over union of two boxes (1 when both are None, 0 when one is)."""
    if a is None and b is None:
        return 1.0
    if a is None or b is None:
        return 0.0
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def grow(box: Box | None, pixels: int, size: tuple[int, int]) -> Box | None:
    if box is None:
        return None
    width, height = size
    return (max(box[0] - pixels, 0), max(box[1] - pixels, 0), min(box[2] + pixels, width),
            min(box[3] + pixels, height))


def find_offset(signal: np.ndarray, template: np.ndarray, expected: int,
                search: int) -> tuple[int, float]:
    """Where ``template`` sits in ``signal`` near sample ``expected`` (within +/- ``search``):
    the start sample with the highest normalised cross-correlation, and that correlation."""
    template = np.asarray(template, dtype=np.float64)
    lo = max(expected - search, 0)
    hi = min(expected + search + template.size, signal.size)
    window = np.asarray(signal[lo:hi], dtype=np.float64)
    if window.size < template.size or not np.any(template):
        return expected, 0.0
    n = window.size + template.size
    size = 1 << (n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(window, size) * np.conj(np.fft.rfft(template, size)), size)
    corr = corr[: window.size - template.size + 1]
    energy = np.convolve(window ** 2, np.ones(template.size), mode="valid")
    norm = np.sqrt(np.maximum(energy, 1e-12) * float(np.sum(template ** 2)))
    score = corr / norm
    best = int(np.argmax(score))
    return lo + best, float(score[best])


def window_db(samples: np.ndarray, window: int) -> np.ndarray:
    """RMS level in dBFS of each whole ``window`` of ``samples`` (-200 for silence)."""
    count = samples.size // window
    if count == 0:
        return np.zeros(0)
    blocks = np.asarray(samples[: count * window], dtype=np.float64).reshape(count, window)
    rms = np.sqrt(np.mean(blocks ** 2, axis=1))
    return np.where(rms > 1e-10, 20 * np.log10(np.maximum(rms, 1e-10)), -200.0)


def summary(values: Sequence[float]) -> dict[str, float]:
    data = np.asarray(list(values), dtype=np.float64)
    if data.size == 0:
        return {"count": 0}
    return {"count": int(data.size), "min": float(data.min()), "mean": float(data.mean()),
            "max": float(data.max())}
