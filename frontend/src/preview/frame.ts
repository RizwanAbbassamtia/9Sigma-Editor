/**
 * v0.2 (docs/CONTRACT-v0.2.md sections 4 and 7): where a picture or clip sits in the frame and
 * how its colour is adjusted, as the preview draws them. The placement matches the renderer's
 * chain (crop, fit, scale, rotate, position at the centre plus x and y of the frame).
 */

export interface FrameDesc {
  /** the centre as a fraction of the frame width (0 = the middle) */
  x: number;
  y: number;
  /** 1 = fills the frame as `fit` says */
  scale: number;
  /** degrees, clockwise */
  rotation: number;
  opacity: number;
  /** left, top, right, bottom: fractions of the source cut away */
  crop: [number, number, number, number];
  fit: "cover" | "contain";
  flipH: boolean;
  flipV: boolean;
}

export interface AdjustDesc {
  brightness: number;
  contrast: number;
  saturation: number;
}

export const IDENTITY_FRAME: FrameDesc = { x: 0, y: 0, scale: 1, rotation: 0, opacity: 1, crop: [0, 0, 0, 0], fit: "contain", flipH: false, flipV: false };

function num(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** A frame from the timeline's loose shape (`frame` of a scene or a layer clip); null when absent. */
export function frameOf(raw: unknown): FrameDesc | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const cropRaw = Array.isArray(r.crop) && r.crop.length === 4 ? r.crop : [0, 0, 0, 0];
  const crop = cropRaw.map((v) => clamp(num(v, 0), 0, 0.95)) as [number, number, number, number];
  return {
    x: clamp(num(r.x, 0), -2, 2),
    y: clamp(num(r.y, 0), -2, 2),
    scale: clamp(num(r.scale, 1), 0.01, 10),
    rotation: clamp(num(r.rotation, 0), -360, 360),
    opacity: clamp(num(r.opacity, 1), 0, 1),
    crop,
    fit: r.fit === "cover" ? "cover" : "contain",
    flipH: r.flip_h === true,
    flipV: r.flip_v === true,
  };
}

export function adjustOf(raw: unknown): AdjustDesc | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const adjust = { brightness: clamp(num(r.brightness, 0), -1, 1), contrast: clamp(num(r.contrast, 0), -1, 1), saturation: clamp(num(r.saturation, 0), -1, 1) };
  return adjust.brightness === 0 && adjust.contrast === 0 && adjust.saturation === 0 ? null : adjust;
}

/** No change to the picture apart from how it fits. */
export function isIdentityFrame(frame: FrameDesc | null): boolean {
  return !frame || (frame.x === 0 && frame.y === 0 && frame.scale === 1 && frame.rotation === 0 && frame.opacity === 1 && !frame.crop.some(Boolean) && !frame.flipH && !frame.flipV);
}

export interface Placement {
  /** the part of the source drawn, in source pixels */
  sx: number;
  sy: number;
  sw: number;
  sh: number;
  /** the centre in output pixels */
  cx: number;
  cy: number;
  /** the drawn size in output pixels (before the rotation) */
  w: number;
  h: number;
  /** radians, clockwise */
  rotation: number;
  alpha: number;
  flipH: boolean;
  flipV: boolean;
}

/** Where a `srcW` x `srcH` source lands in an `outW` x `outH` frame. */
export function placeInFrame(frame: FrameDesc, srcW: number, srcH: number, outW: number, outH: number): Placement {
  const [l, t, r, b] = frame.crop;
  const sx = srcW * l;
  const sy = srcH * t;
  const sw = Math.max(1, srcW * (1 - l - r));
  const sh = Math.max(1, srcH * (1 - t - b));
  const base = frame.fit === "cover" ? Math.max(outW / sw, outH / sh) : Math.min(outW / sw, outH / sh);
  const k = base * frame.scale;
  return {
    sx,
    sy,
    sw,
    sh,
    cx: outW / 2 + frame.x * outW,
    cy: outH / 2 + frame.y * outH,
    w: sw * k,
    h: sh * k,
    rotation: (frame.rotation * Math.PI) / 180,
    alpha: frame.opacity,
    flipH: frame.flipH,
    flipV: frame.flipV,
  };
}

/** The CSS filter of an adjust (the Canvas 2D path): the same curve as FFmpeg's `eq`. */
export function cssFilter(adjust: AdjustDesc | null): string {
  if (!adjust) return "none";
  const parts: string[] = [];
  if (adjust.brightness !== 0) parts.push(`brightness(${(1 + adjust.brightness).toFixed(3)})`);
  if (adjust.contrast !== 0) parts.push(`contrast(${(1 + adjust.contrast).toFixed(3)})`);
  if (adjust.saturation !== 0) parts.push(`saturate(${(1 + adjust.saturation).toFixed(3)})`);
  return parts.length ? parts.join(" ") : "none";
}
