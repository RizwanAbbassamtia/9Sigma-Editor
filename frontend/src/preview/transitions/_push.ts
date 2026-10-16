/**
 * `push_*` and `whip_*` (render/transitions/push_whip.py): both pictures travel as one strip,
 * `[A|B]` (or `[B|A]`; stacked for up/down), seen through a window that slides `p` frame lengths.
 * A push overshoots by 2 % and settles (`p = t + c t^2 (1 - t)`) with a light motion blur while it
 * is fast; a whip has `p = t` and a heavy blur along the travel. The blur is a centred box of
 * `round(L) | 1` pixels over the drawn frame (BORDER_REFLECT_101 at its edges).
 */
import { num, speedPerSecond, type TransitionFilter, type TransitionFrame } from "./common";
import { pyRound } from "../motion";
import { taper } from "./easing";

export type PushDirection = "left" | "right" | "up" | "down";

export const PUSH_MOTION_BLUR = 0.5;
/** The most box-blur taps the shader reads per pixel; longer boxes are sampled evenly. */
export const MAX_BLUR_TAPS = 64;

const DIRECTIONS: Record<PushDirection, [number, number]> = {
  left: [-1, 0],
  right: [1, 0],
  up: [0, -1],
  down: [0, 1],
};

function overshootOf(c: number): number {
  if (c <= 1) return 0;
  let peak = (2 * c + (4 * c * c + 12 * c) ** 0.5) / (6 * c);
  peak = Math.min(Math.max(peak, 0), 1);
  return peak + c * peak * peak * (1 - peak) - 1;
}

const coefficientCache = new Map<number, number>();

/** The `c` whose curve overshoots by `overshoot` (bisection; 0 -> c = 0). */
export function overshootCoefficient(overshoot: number): number {
  if (overshoot <= 0) return 0;
  const cached = coefficientCache.get(overshoot);
  if (cached !== undefined) return cached;
  let lo = 1;
  let hi = 8;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (overshootOf(mid) < overshoot) lo = mid;
    else hi = mid;
  }
  const c = (lo + hi) / 2;
  coefficientCache.set(overshoot, c);
  return c;
}

export function pushPosition(t: number, overshoot: number): number {
  if (overshoot <= 0) return t;
  const c = overshootCoefficient(Math.min(overshoot, 0.25));
  return t + c * t * t * (1 - t);
}

/** `int(round(L)) | 1`, or 0 when the box would be shorter than 2 pixels (`box_blur`). */
export function boxSize(length: number): number {
  const size = pyRound(length);
  return size < 2 ? 0 : size | 1;
}

export interface StripTerms {
  /** how far the strip has travelled, in frame lengths */
  p: number;
  /** the motion blur length in output pixels (before rounding) */
  blur: number;
  /** the box in whole pixels (odd), 0 = none */
  box: number;
}

function travelBlur(frame: TransitionFrame, direction: PushDirection, dpDt: number, shutterS: number, maxShare: number): number {
  const horizontal = DIRECTIONS[direction][0] !== 0;
  const length = horizontal ? frame.width : frame.height;
  const travel = length * Math.abs(dpDt) * speedPerSecond(frame) * shutterS;
  return Math.min(travel, maxShare * length) * taper(frame.t);
}

export function pushTerms(frame: TransitionFrame, direction: PushDirection): StripTerms {
  const t = frame.t;
  const overshoot = Math.max(num(frame.params, "overshoot", 0.02), 0);
  const p = pushPosition(t, overshoot);
  const amount = Math.max(num(frame.params, "motion_blur", PUSH_MOTION_BLUR), 0);
  let blur = 0;
  if (amount > 0) {
    const h = 1e-3;
    const lo = Math.max(t - h, 0);
    const hi = Math.min(t + h, 1);
    const dpDt = (pushPosition(hi, overshoot) - pushPosition(lo, overshoot)) / (hi - lo);
    const shutter = Math.max(num(frame.params, "shutter_s", 1 / 30), 0) * amount;
    blur = travelBlur(frame, direction, dpDt, shutter, 0.25);
  }
  return { p, blur, box: boxSize(blur) };
}

export function whipTerms(frame: TransitionFrame, direction: PushDirection): StripTerms {
  const shutter = Math.max(num(frame.params, "shutter_s", 1 / 30), 0) * Math.max(num(frame.params, "motion_blur", 1), 0);
  const maxShare = Math.min(Math.max(num(frame.params, "max_blur", 0.5), 0), 1);
  const blur = travelBlur(frame, direction, 1, shutter, maxShare);
  return { p: frame.t, blur, box: boxSize(blur) };
}

/**
 * GLSL of the strip window with its box blur. `uAxis` is 0 for left/right, 1 for up/down;
 * `uFirstIsA` is 1 when the strip is [A|B] (left, up) and 0 when it is [B|A] (right, down);
 * `uOffset` = p len (left/up) or (1 - p) len (right/down), in pixels.
 */
const STRIP_GLSL = /* glsl */ `
int stripLength() {
  return uAxis == 0 ? int(uSize.x + 0.5) : int(uSize.y + 0.5);
}
/* Strip pixel j (0 .. 2 len - 1) in the row or column of p; outside the strip the picture is
   mirrored like OpenCV's BORDER_REFLECT_101. */
vec3 stripTexel(int j, vec2 p) {
  int len = stripLength();
  int last = 2 * len - 1;
  if (j < 0) j = -j;
  if (j > last) j = 2 * last - j;
  j = clamp(j, 0, last);
  bool inFirst = j < len;
  int local = inFirst ? j : j - len;
  vec2 q = uAxis == 0 ? vec2(float(local) + 0.5, p.y) : vec2(p.x, float(local) + 0.5);
  bool useA = inFirst == (uFirstIsA == 1);
  return useA ? sampleA(q) : sampleB(q);
}
/* The strip at p, OpenCV's cubic (A = -0.75) along the travel (warpAffine INTER_CUBIC). */
vec3 stripCubic(vec2 p) {
  float x = (uAxis == 0 ? p.x : p.y) + uOffset - 0.5;
  float i0 = floor(x);
  float t = x - i0;
  const float A = -0.75;
  float w0 = ((A * (t + 1.0) - 5.0 * A) * (t + 1.0) + 8.0 * A) * (t + 1.0) - 4.0 * A;
  float w1 = ((A + 2.0) * t - (A + 3.0)) * t * t + 1.0;
  float w2 = ((A + 2.0) * (1.0 - t) - (A + 3.0)) * (1.0 - t) * (1.0 - t) + 1.0;
  float w3 = 1.0 - w0 - w1 - w2;
  int i = int(i0);
  vec3 c = w0 * stripTexel(i - 1, p) + w1 * stripTexel(i, p) + w2 * stripTexel(i + 1, p) + w3 * stripTexel(i + 2, p);
  return clamp(c, 0.0, 1.0);
}
/* The strip at p with bilinear filtering (inside the motion blur, where the kernel no longer
   shows): one filtered read when both neighbours lie in the same picture. */
vec3 stripLinear(vec2 p) {
  float along = (uAxis == 0 ? p.x : p.y) + uOffset;
  float x = along - 0.5;
  float i0 = floor(x);
  int i = int(i0);
  int len = stripLength();
  bool first = i >= 0 && i + 1 < len;
  bool second = i >= len && i + 1 < 2 * len;
  if (first || second) {
    float local = first ? along : along - float(len);
    vec2 q = uAxis == 0 ? vec2(local, p.y) : vec2(p.x, local);
    bool useA = first == (uFirstIsA == 1);
    return useA ? sampleA(q) : sampleB(q);
  }
  float t = x - i0;
  return mix(stripTexel(i, p), stripTexel(i + 1, p), t);
}
void main() {
  vec2 p = pixel();
  if (uBox < 2) {
    finalColor = vec4(stripCubic(p), 1.0);
    return;
  }
  float len = uAxis == 0 ? uSize.x : uSize.y;
  float halfBox = float(uBox - 1) * 0.5;
  int taps = min(uBox, ${MAX_BLUR_TAPS});
  float stepPx = taps > 1 ? float(uBox - 1) / float(taps - 1) : 0.0;
  vec3 acc = vec3(0.0);
  for (int i = 0; i < ${MAX_BLUR_TAPS}; i++) {
    if (i >= taps) break;
    float o = -halfBox + float(i) * stepPx;
    // the blur runs over the drawn frame, mirrored at its edges (BORDER_REFLECT_101; the box is
    // never wider than the frame, so one reflection is enough)
    float along = (uAxis == 0 ? p.x : p.y) + o;
    if (along < 0.5) along = 1.0 - along;
    else if (along > len - 0.5) along = 2.0 * len - 1.0 - along;
    vec2 q = uAxis == 0 ? vec2(along, p.y) : vec2(p.x, along);
    acc += stripLinear(q);
  }
  finalColor = vec4(acc / float(taps), 1.0);
}
`;

export function stripFilter(id: string, direction: PushDirection, kind: "push" | "whip"): TransitionFilter {
  const [dx, dy] = DIRECTIONS[direction];
  const firstIsA = dx < 0 || dy < 0;
  return {
    id,
    uniforms: {
      uAxis: { type: "i32" },
      uFirstIsA: { type: "i32" },
      uOffset: { type: "f32" },
      uBox: { type: "i32" },
    },
    values(frame) {
      const terms = kind === "push" ? pushTerms(frame, direction) : whipTerms(frame, direction);
      const len = dx !== 0 ? frame.width : frame.height;
      const offset = firstIsA ? terms.p * len : (1 - terms.p) * len;
      return { uAxis: dx !== 0 ? 0 : 1, uFirstIsA: firstIsA ? 1 : 0, uOffset: offset, uBox: terms.box };
    },
    glsl: STRIP_GLSL,
    fallback: "push",
    exactInFallback: false,
  };
}

/** For the Canvas 2D fallback: where A and B sit (top-left corners in pixels) at this frame. */
export function stripLayout(frame: TransitionFrame, direction: PushDirection, kind: "push" | "whip"): { a: [number, number]; b: [number, number] } {
  const terms = kind === "push" ? pushTerms(frame, direction) : whipTerms(frame, direction);
  const [dx, dy] = DIRECTIONS[direction];
  if (dx !== 0) {
    const shift = terms.p * frame.width;
    return dx < 0
      ? { a: [-shift, 0], b: [frame.width - shift, 0] }
      : { a: [shift, 0], b: [shift - frame.width, 0] };
  }
  const shift = terms.p * frame.height;
  return dy < 0 ? { a: [0, -shift], b: [0, frame.height - shift] } : { a: [0, shift], b: [0, shift - frame.height] };
}
