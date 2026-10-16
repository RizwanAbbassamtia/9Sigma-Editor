/**
 * Transition easing (backend/sigma_editor/render/transitions/easing.py): the curves of
 * TRANSITION-PARITY.md section 3, the speed used for motion blur and the taper that keeps
 * speed-driven effects off the first and last frame.
 */

export type EasingFn = (x: number) => number;

export function clamp01(x: number): number {
  return x <= 0 ? 0 : x >= 1 ? 1 : x;
}

export const TRANSITION_EASINGS: Readonly<Record<string, EasingFn>> = {
  linear: (x) => clamp01(x),
  ease_in_out_cubic: (x) => {
    const v = clamp01(x);
    return v < 0.5 ? 4 * v * v * v : 1 - (-2 * v + 2) ** 3 / 2;
  },
  ease_out_quart: (x) => 1 - (1 - clamp01(x)) ** 4,
  snap: (x) => {
    const v = clamp01(x);
    return v < 0.5 ? 16 * v ** 5 : 1 - (-2 * v + 2) ** 5 / 2;
  },
  ease_in_quad: (x) => {
    const v = clamp01(x);
    return v * v;
  },
  ease_out_quad: (x) => {
    const v = clamp01(x);
    return 1 - (1 - v) * (1 - v);
  },
  ease_in_cubic: (x) => {
    const v = clamp01(x);
    return v * v * v;
  },
  ease_out_cubic: (x) => 1 - (1 - clamp01(x)) ** 3,
};

export const DEFAULT_TRANSITION_EASING = "ease_in_out_cubic";

export function knownTransitionEasing(name: string | null | undefined, fallback = DEFAULT_TRANSITION_EASING): string {
  return name && Object.prototype.hasOwnProperty.call(TRANSITION_EASINGS, name) ? name : fallback;
}

export function getEasing(name: string | null | undefined, fallback = DEFAULT_TRANSITION_EASING): EasingFn {
  if (name && Object.prototype.hasOwnProperty.call(TRANSITION_EASINGS, name)) return TRANSITION_EASINGS[name]!;
  return TRANSITION_EASINGS[fallback] ?? TRANSITION_EASINGS.ease_in_out_cubic!;
}

export function transitionEase(name: string | null | undefined, x: number): number {
  return getEasing(name)(x);
}

/** `d ease(x) / dx` by a central difference (one-sided at the ends). */
export function transitionSpeed(name: string | null | undefined, x: number, h = 1e-3): number {
  const curve = getEasing(name);
  const lo = Math.max(0, x - h);
  const hi = Math.min(1, x + h);
  if (hi <= lo) return 0;
  return (curve(hi) - curve(lo)) / (hi - lo);
}

/** GLSL `smoothstep`. */
export function smoothstep(edge0: number, edge1: number, x: number): number {
  if (edge1 === edge0) return x < edge0 ? 0 : 1;
  const u = clamp01((x - edge0) / (edge1 - edge0));
  return u * u * (3 - 2 * u);
}

/** 1 in the middle, easing to 0 at t = 0 and t = 1. */
export function taper(t: number, edge = 0.08): number {
  return smoothstep(0, edge, t) * smoothstep(0, edge, 1 - t);
}

/** Linear progress of frame `k` of an `n`-frame cut: the middle of its slot. */
export function frameTime(k: number, n: number): number {
  return (k + 0.5) / Math.max(n, 1);
}
