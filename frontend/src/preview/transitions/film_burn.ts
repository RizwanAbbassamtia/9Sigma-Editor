/**
 * `film_burn` (render/transitions/leaks.py): A burns away behind a ragged glowing edge into warm
 * white, then B comes up out of the white.
 *   F = (1 - roughness) G + roughness fbm(cells 3, octaves 4, seed)   (G = u for "right")
 *   t < 0.5: s = 2t, e = -w + s (1 + 2w), z = (e - F) / w
 *            out = mix(A, WARM, smoothstep(0, 1, z)) (1 - char ring) + 0.9 glow HOT
 *   t >= 0.5: s = 2t - 1, out = mix(WARM, B, 1 - (1 - s)^3)
 * The burn order uses the integer-hash noise, so the edge has the same shape as the render.
 */
import { colour, num, text, type RGB, type TransitionFilter, type TransitionFrame } from "./common";
import { TRANSITION_EASINGS, smoothstep } from "./easing";

export const WARM_WHITE: RGB = [1, 240 / 255, 220 / 255];
export const HOT_GLOW: RGB = [1, 140 / 255, 40 / 255];

const GRADIENTS: Record<string, number> = { right: 0, left: 1, down: 2, up: 3 };

export interface BurnTerms {
  phase: 0 | 1;
  s: number;
  /** second half: how much of B shows */
  weight: number;
  /** first half: the edge position and width */
  e: number;
  w: number;
  char: number;
}

export function burnTerms(frame: TransitionFrame): BurnTerms {
  const t = frame.t;
  if (t >= 0.5) {
    const s = (t - 0.5) / 0.5;
    return { phase: 1, s, weight: TRANSITION_EASINGS.ease_out_cubic!(s), e: 0, w: 0, char: 0 };
  }
  const s = t / 0.5;
  const w = Math.min(Math.max(num(frame.params, "edge", 0.06), 1e-3), 0.5);
  return { phase: 0, s, weight: 0, e: -w + s * (1 + 2 * w), w, char: 0.5 * smoothstep(0, 0.15, s) };
}

export const filmBurn: TransitionFilter = {
  id: "film_burn",
  uniforms: {
    uPhase: { type: "f32" },
    uWeight: { type: "f32" },
    uEdge: { type: "f32" },
    uWidth: { type: "f32" },
    uChar: { type: "f32" },
    uWarm: { type: "vec3<f32>" },
    uHot: { type: "vec3<f32>" },
    uGlowStrength: { type: "f32" },
    uRoughness: { type: "f32" },
    uCells: { type: "f32" },
    uOctaves: { type: "i32" },
    uGradient: { type: "i32" },
  },
  values(frame) {
    const terms = burnTerms(frame);
    const p = frame.params;
    return {
      uPhase: terms.phase,
      uWeight: terms.weight,
      uEdge: terms.e,
      uWidth: terms.w || 0.06,
      uChar: terms.char,
      uWarm: colour(p, ["color", "colour"], WARM_WHITE),
      uHot: colour(p, ["edge_color", "glow"], HOT_GLOW),
      uGlowStrength: num(p, "glow_strength", 0.9),
      uRoughness: Math.min(Math.max(num(p, "roughness", 0.3), 0), 1),
      uCells: num(p, "cells", 3),
      uOctaves: Math.min(Math.max(Math.trunc(num(p, "octaves", 4)), 1), 8),
      uGradient: GRADIENTS[text(p, "direction", "right")] ?? 0,
    };
  },
  glsl: /* glsl */ `
void main() {
  if (uPhase > 0.5) {
    vec3 b = texture(uB, vUV).rgb;
    finalColor = vec4(mix(uWarm, b, uWeight), 1.0);
    return;
  }
  float u = vUV.x;
  float v = vUV.y;
  float g = uGradient == 0 ? u : uGradient == 1 ? 1.0 - u : uGradient == 2 ? v : 1.0 - v;
  float noise = fbm(vUV, uSize.x / uSize.y, uCells, uOctaves, uSeed);
  float field = (1.0 - uRoughness) * g + uRoughness * noise;
  float z = (uEdge - field) / uWidth;
  vec3 a = texture(uA, vUV).rgb;
  vec3 o = mix(a, uWarm, smoothstep(0.0, 1.0, z));
  if (uChar > 0.0) {
    float r = (z + 1.6) * 1.25;
    float ring = max(1.0 - r * r, 0.0);
    ring *= ring;
    o *= 1.0 - uChar * ring;
  }
  float glow = max(1.0 - z * z, 0.0);
  glow *= glow;
  finalColor = vec4(clamp(o + glow * uGlowStrength * uHot, 0.0, 1.0), 1.0);
}
`,
  fallback: "dissolve",
  exactInFallback: false,
};

export default filmBurn;
