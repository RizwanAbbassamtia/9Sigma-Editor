/**
 * Camera moves (Motion v2): the same maths as backend/sigma_editor/render/motion.py and
 * render/geometry.py, written out in docs/design/MOTION-PARITY.md. Tested against the Python
 * reference to 1e-6 (src/preview/__tests__/motion.test.ts).
 *
 * A scene's move is two rectangles of its picture, `[x, y, w, h]` in source pixels, an easing
 * and a preset. At progress `t` the window is
 *   E = ease(t); centre = c0 + (c1 - c0) E; size = s0 (s1 / s0)^E   (even zoom rate)
 * plus the handheld wobble, then kept inside the picture's cover region.
 */

/** `[x, y, w, h]` */
export type Rect = [number, number, number, number];

// Python numbers --------------------------------------------------------------------------

/**
 * Python's `round(x)` and `round(x, ndigits)`: the nearest value, ties to even, decided on the
 * exact binary value (`toFixed` is exact for doubles; it breaks exact ties away from zero).
 */
export function pyRound(x: number, ndigits = 0): number {
  if (!Number.isFinite(x)) return x;
  if (ndigits === 0) {
    const floor = Math.floor(x);
    const diff = x - floor;
    if (diff > 0.5) return floor + 1;
    if (diff < 0.5) return floor;
    return floor % 2 === 0 ? floor : floor + 1;
  }
  const magnitude = Math.abs(x);
  if (magnitude >= 1e15) return x;
  const digits = Math.min(100, ndigits + 40);
  const exact = magnitude.toFixed(digits);
  const dot = exact.indexOf(".");
  const tail = exact.slice(dot + 1 + ndigits);
  let rounded = Number(magnitude.toFixed(ndigits));
  if (/^50*$/.test(tail)) {
    const kept = exact.slice(0, dot + 1 + ndigits);
    const last = Number(kept[kept.length - 1]);
    if (last % 2 === 0) rounded = Number(kept);
  }
  return x < 0 ? -rounded : rounded;
}

/** Python's `int(x)`: truncation towards zero. */
export function pyInt(x: number): number {
  return Math.trunc(x);
}

/** Python's `a // b` for numbers (floor division). */
export function floorDiv(a: number, b: number): number {
  return Math.floor(a / b);
}

export function clamp01(x: number): number {
  return x <= 0 ? 0 : x >= 1 ? 1 : x;
}

// Easing ----------------------------------------------------------------------------------

export const MOTION_EASINGS = ["ease_in_out_cubic", "ease_out_quart", "ease_in_out_sine", "linear", "snap"] as const;
export type MotionEasing = (typeof MOTION_EASINGS)[number];
export const DEFAULT_MOTION_EASING: MotionEasing = "ease_in_out_cubic";
export const HANDHELD_AMPLITUDE = 0.004;

export function knownEasing(name: string | null | undefined): MotionEasing {
  return normalizeMotionEase(name) ?? DEFAULT_MOTION_EASING;
}

/**
 * `motion.ease` as models/timeline.py `normalize_motion_ease` reads it ("Ease-Out Quart" ->
 * ease_out_quart); empty or unknown -> null (the preset's own curve).
 */
export function normalizeMotionEase(value: string | null | undefined): MotionEasing | null {
  const key = String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_")
    .replace(/ /g, "_");
  return (MOTION_EASINGS as readonly string[]).includes(key) ? (key as MotionEasing) : null;
}

/** Eased progress for linear progress `t` (clamped to [0, 1]); `render/motion.py` `ease`. */
export function ease(name: string | null | undefined, t: number): number {
  const x = t <= 0 ? 0 : t >= 1 ? 1 : t;
  switch (knownEasing(name)) {
    case "linear":
      return x;
    case "ease_out_quart":
      return 1 - (1 - x) ** 4;
    case "ease_in_out_sine":
      return (1 - Math.cos(Math.PI * x)) / 2;
    case "snap":
      return x < 0.5 ? 16 * x ** 5 : 1 - (2 - 2 * x) ** 5 / 2;
    default:
      return x < 0.5 ? 4 * x ** 3 : 1 - (2 - 2 * x) ** 3 / 2;
  }
}

/** `t` of frame `frame` (0-based) in a clip of `frames` frames: `k / (n - 1)`, 0 for one frame. */
export function frameProgress(frame: number, frames: number): number {
  if (frames <= 1) return 0;
  return Math.min(Math.max(frame / (frames - 1), 0), 1);
}

// Presets ---------------------------------------------------------------------------------

export interface MotionPresetSpec {
  id: string;
  label: string;
  energy: "calm" | "medium" | "high";
  /** fraction rectangles of the cover region */
  start: Rect;
  end: Rect;
  easing: MotionEasing;
  handheld: boolean;
}

const FULL: Rect = [0, 0, 1, 1];

function preset(
  id: string,
  label: string,
  energy: MotionPresetSpec["energy"],
  start: Rect,
  end: Rect,
  extra: Partial<Pick<MotionPresetSpec, "easing" | "handheld">> = {},
): MotionPresetSpec {
  return { id, label, energy, start, end, easing: extra.easing ?? DEFAULT_MOTION_EASING, handheld: extra.handheld ?? false };
}

/** `render/motion.py` PRESETS, in the same order. */
export const MOTION_PRESETS: Record<string, MotionPresetSpec> = Object.fromEntries(
  [
    preset("zoom_in", "Zoom in", "medium", FULL, [0.1, 0.1, 0.8, 0.8]),
    preset("zoom_out", "Zoom out", "medium", [0.1, 0.1, 0.8, 0.8], FULL),
    preset("pan_left", "Pan left", "medium", [0.15, 0, 0.85, 1], [0, 0, 0.85, 1]),
    preset("pan_right", "Pan right", "medium", [0, 0, 0.85, 1], [0.15, 0, 0.85, 1]),
    preset("pan_up", "Pan up", "medium", [0, 0.15, 1, 0.85], [0, 0, 1, 0.85]),
    preset("pan_down", "Pan down", "medium", [0, 0, 1, 0.85], [0, 0.15, 1, 0.85]),
    preset("hold", "Hold still", "calm", FULL, FULL),
    preset("slow_push", "Slow push in", "calm", FULL, [0.04, 0.04, 0.92, 0.92]),
    preset("drift", "Drift", "calm", [0, 0.03, 0.94, 0.94], [0.05, 0.03, 0.94, 0.94], { easing: "ease_in_out_sine" }),
    preset("tilt_up", "Tilt up", "medium", [0.1, 0.2, 0.8, 0.8], [0.11, 0, 0.78, 0.78]),
    preset("tilt_down", "Tilt down", "medium", [0.1, 0, 0.8, 0.8], [0.11, 0.22, 0.78, 0.78]),
    preset("dolly_in", "Dolly in", "medium", FULL, [0.13, 0.07, 0.78, 0.78]),
    preset("dolly_out", "Dolly out", "medium", [0.13, 0.07, 0.78, 0.78], FULL),
    preset("handheld", "Handheld", "medium", [0.02, 0.02, 0.96, 0.96], [0.02, 0.02, 0.96, 0.96], { handheld: true }),
  ].map((spec) => [spec.id, spec]),
);

/** The preset for `name` (case, dashes and spaces ignored), or null. */
export function presetSpec(name: string | null | undefined): MotionPresetSpec | null {
  const key = String(name ?? "")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_")
    .replace(/ /g, "_");
  return Object.prototype.hasOwnProperty.call(MOTION_PRESETS, key) ? MOTION_PRESETS[key]! : null;
}

// Geometry (render/geometry.py) -----------------------------------------------------------

/** The centred part of the picture with the frame's aspect ratio, in picture pixels. */
export function coverRegion(imageW: number, imageH: number, frameW: number, frameH: number): Rect {
  if (imageW <= 0 || imageH <= 0 || frameW <= 0 || frameH <= 0) {
    return [0, 0, Math.max(imageW, 1), Math.max(imageH, 1)];
  }
  const target = frameW / frameH;
  let width: number;
  let height: number;
  if (imageW / imageH > target) {
    height = imageH;
    width = height * target;
  } else {
    width = imageW;
    height = width / target;
  }
  return [(imageW - width) / 2, (imageH - height) / 2, width, height];
}

/** A fraction rectangle -> picture pixels, fitted to the frame's aspect, inside the cover region. */
export function rectInPixels(frac: readonly number[], imageW: number, imageH: number, frameW: number, frameH: number): Rect {
  const [rx, ry, rw, rh] = coverRegion(imageW, imageH, frameW, frameH);
  const fw = Math.min(Math.max(frac[2] ?? 1, 0.01), 1);
  const fh = Math.min(Math.max(frac[3] ?? 1, 0.01), 1);
  const fx = Math.min(Math.max(frac[0] ?? 0, 0), 1 - fw);
  const fy = Math.min(Math.max(frac[1] ?? 0, 0), 1 - fh);
  const target = frameH ? frameW / frameH : 1;
  let width = fw * rw;
  let height = fh * rh;
  const centreX = rx + (fx + fw / 2) * rw;
  const centreY = ry + (fy + fh / 2) * rh;
  if (width / height > target) width = height * target;
  else height = width / target;
  if (width > rw) {
    width = rw;
    height = rw / target;
  }
  if (height > rh) {
    height = rh;
    width = rh * target;
  }
  const x = Math.min(Math.max(centreX - width / 2, rx), rx + rw - width);
  const y = Math.min(Math.max(centreY - height / 2, ry), ry + rh - height);
  return [pyRound(x, 2), pyRound(y, 2), pyRound(width, 2), pyRound(height, 2)];
}

// The path --------------------------------------------------------------------------------

const SHAKE_WEIGHTS = [0.55, 0.3, 0.15] as const;
const SHAKE_HZ_X = [0.23, 0.57, 1.13] as const;
const SHAKE_HZ_Y = [0.31, 0.67, 1.31] as const;
const GOLDEN = 0.6180339887498949;

/** A repeatable phase in radians: `2 pi * frac(0.618... * (seed + 1) * (index + 1))`. */
export function shakePhase(seed: number, index: number): number {
  const value = GOLDEN * (Math.trunc(seed) + 1) * (index + 1);
  return 2 * Math.PI * (value - Math.floor(value));
}

/** The handheld wobble at `timeS` seconds, each axis in [-1, 1]. */
export function shakeOffsets(timeS: number, seed: number): [number, number] {
  let x = 0;
  let y = 0;
  for (let i = 0; i < 3; i++) {
    x += SHAKE_WEIGHTS[i]! * Math.sin(2 * Math.PI * SHAKE_HZ_X[i]! * timeS + shakePhase(seed, i));
  }
  for (let i = 0; i < 3; i++) {
    y += SHAKE_WEIGHTS[i]! * Math.sin(2 * Math.PI * SHAKE_HZ_Y[i]! * timeS + shakePhase(seed, i + 3));
  }
  return [x, y];
}

/** Where the camera is relative to the middle of its move (for 2.5D parallax). */
export interface CameraTerms {
  px: number;
  py: number;
  zoom: number;
}

function toRect(values: readonly number[]): Rect {
  return [Number(values[0]), Number(values[1]), Number(values[2]), Number(values[3])];
}

export interface MotionPathInit {
  start: readonly number[];
  end: readonly number[];
  easing?: string | null;
  handheld?: number;
  seed?: number;
  bounds?: readonly number[] | null;
}

/** One camera move in one pixel space (`render/motion.py` `MotionPath`). */
export class MotionPath {
  readonly start: Rect;
  readonly end: Rect;
  readonly easing: MotionEasing;
  /** wobble amplitude as a share of the visible width (0 = none) */
  readonly handheld: number;
  readonly seed: number;
  readonly bounds: Rect | null;

  constructor(init: MotionPathInit) {
    this.start = toRect(init.start);
    this.end = toRect(init.end);
    this.easing = knownEasing(init.easing ?? DEFAULT_MOTION_EASING);
    this.handheld = init.handheld ?? 0;
    this.seed = Math.trunc(init.seed ?? 0);
    this.bounds = init.bounds ? toRect(init.bounds) : null;
  }

  /**
   * The path a renderer draws for a timeline scene: the preset's easing unless `easing` is
   * given (the scene's `motion.ease`), the wobble for `handheld`.
   */
  static forPreset(
    presetName: string | null | undefined,
    start: readonly number[],
    end: readonly number[],
    options: { seed?: number; bounds?: readonly number[] | null; easing?: string | null; handheldAmplitude?: number } = {},
  ): MotionPath {
    const spec = presetSpec(presetName);
    const wanted = normalizeMotionEase(options.easing) ?? (spec ? spec.easing : DEFAULT_MOTION_EASING);
    return new MotionPath({
      start,
      end,
      easing: wanted,
      handheld: spec !== null && spec.handheld ? (options.handheldAmplitude ?? HANDHELD_AMPLITUDE) : 0,
      seed: options.seed ?? 0,
      bounds: options.bounds ?? null,
    });
  }

  withBounds(bounds: readonly number[] | null): MotionPath {
    return new MotionPath({ start: this.start, end: this.end, easing: this.easing, handheld: this.handheld, seed: this.seed, bounds });
  }

  eased(t: number): number {
    return ease(this.easing, t);
  }

  size(e: number): [number, number] {
    const w0 = Math.max(this.start[2], 1e-9);
    const h0 = Math.max(this.start[3], 1e-9);
    const w1 = Math.max(this.end[2], 1e-9);
    const h1 = Math.max(this.end[3], 1e-9);
    return [w0 * (w1 / w0) ** e, h0 * (h1 / h0) ** e];
  }

  centreAt(e: number): [number, number] {
    const cx0 = this.start[0] + this.start[2] / 2;
    const cy0 = this.start[1] + this.start[3] / 2;
    const cx1 = this.end[0] + this.end[2] / 2;
    const cy1 = this.end[1] + this.end[3] / 2;
    return [cx0 + (cx1 - cx0) * e, cy0 + (cy1 - cy0) * e];
  }

  clamp(x: number, y: number, w: number, h: number): Rect {
    if (this.bounds === null) return [x, y, w, h];
    const [bx, by, bw, bh] = this.bounds;
    const cx = w >= bw ? bx : Math.min(Math.max(x, bx), bx + bw - w);
    const cy = h >= bh ? by : Math.min(Math.max(y, by), by + bh - h);
    return [cx, cy, w, h];
  }

  /** The window at progress `t` (and `timeS` seconds into the clip for the wobble). */
  rect(t: number, timeS = 0): Rect {
    const e = this.eased(t);
    const [w, h] = this.size(e);
    let [cx, cy] = this.centreAt(e);
    if (this.handheld) {
      const [sx, sy] = shakeOffsets(timeS, this.seed);
      cx += this.handheld * w * sx;
      cy += this.handheld * w * sy;
    }
    return this.clamp(cx - w / 2, cy - h / 2, w, h);
  }

  frameRect(frame: number, frames: number, fps: number): Rect {
    return this.rect(frameProgress(frame, frames), frame / Math.max(fps, 1e-6));
  }

  /** The parallax camera terms at progress `t`. */
  camera(t: number, timeS = 0): CameraTerms {
    const [x, y, w, h] = this.rect(t, timeS);
    const [sizeW, sizeH] = this.size(0.5);
    const [midX, midY] = this.centreAt(0.5);
    const [refX, refY] = this.clamp(midX - sizeW / 2, midY - sizeH / 2, sizeW, sizeH);
    const cxRef = refX + sizeW / 2;
    const cyRef = refY + sizeH / 2;
    const wRef = Math.max(sizeW, 1e-9);
    return {
      px: (x + w / 2 - cxRef) / wRef,
      py: (y + h / 2 - cyRef) / wRef,
      zoom: Math.log(wRef / Math.max(w, 1e-9)),
    };
  }

  frameCamera(frame: number, frames: number, fps: number): CameraTerms {
    return this.camera(frameProgress(frame, frames), frame / Math.max(fps, 1e-6));
  }

  /** The narrowest and the widest window of the move. */
  widthRange(): [number, number] {
    return [Math.min(this.start[2], this.end[2]), Math.max(this.start[2], this.end[2])];
  }
}

// Parallax (the formula; parallax.ts applies it on a mesh) --------------------------------

export const PARALLAX_REFERENCE_MOVE = 0.18;

/** Output pixels a pixel moves per unit of `(depth - mean) * camera`. */
export function parallaxGain(strength: number, outWidth: number): number {
  return (strength * outWidth) / PARALLAX_REFERENCE_MOVE;
}

/**
 * Extra screen movement in output pixels: `gain (depth - mean) (n zoom - (px, py))` with
 * `n = (p - centre) / (W / 2)` on both axes.
 */
export function parallaxOffsets(
  depth: number,
  meanDepth: number,
  nx: number,
  ny: number,
  camera: CameraTerms,
  strength: number,
  outWidth: number,
): [number, number] {
  const gain = parallaxGain(strength, outWidth) * (depth - meanDepth);
  return [gain * (nx * camera.zoom - camera.px), gain * (ny * camera.zoom - camera.py)];
}
