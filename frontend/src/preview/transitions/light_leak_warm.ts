/**
 * `light_leak_warm` (render/transitions/leaks.py): two soft warm blobs of light drift across a
 * linear-light crossfade, screen-blended, strongest at `peak_t`.
 *   t' = t^(ln 0.5 / ln peak_t);  I = strength sin(pi t')^2
 *   c1 = (-0.15 + 1.1 t, 0.30 + 0.10 sin(2 pi t)), r1 = 0.45;  c2 = (1.10 - 0.9 t, 0.85 - 0.35 t), r2 = 0.35
 *   (frame heights; odd seeds mirror the x of both centres)
 *   L = clamp(I (g1 C1 + 0.8 g2 C2 + wash C1)),  out = base + L (1 - base)
 * The reference draws L on a quarter-size grid; the per-pixel value here differs by under one code value.
 */
import { colour, num, type RGB, type TransitionFilter, type TransitionFrame } from "./common";

export const LEAK_COLOUR_1: RGB = [1, 176 / 255, 96 / 255];
export const LEAK_COLOUR_2: RGB = [1, 0.36, 0.12];

export interface LeakTerms {
  shaped: number;
  intensity: number;
  c1: [number, number];
  c2: [number, number];
  r1: number;
  r2: number;
  colour1: RGB;
  colour2: RGB;
  wash: number;
  /** below 1/1024 the reference draws the plain crossfade */
  skip: boolean;
}

export function leakTerms(frame: TransitionFrame): LeakTerms {
  const t = frame.t;
  const p = frame.params;
  const peak = Math.min(Math.max(num(p, "peak_t", 0.5), 0.05), 0.95);
  const shaped = t ** (Math.log(0.5) / Math.log(peak));
  const strength = Math.min(Math.max(num(p, ["strength", "intensity"], 0.85), 0), 1);
  const intensity = strength * Math.sin(Math.PI * shaped) ** 2;
  const mirror = Math.trunc(frame.seed) % 2 !== 0;
  const c1: [number, number] = [-0.15 + 1.1 * t, 0.3 + 0.1 * Math.sin(2 * Math.PI * t)];
  const c2: [number, number] = [1.1 - 0.9 * t, 0.85 - 0.35 * t];
  if (mirror) {
    c1[0] = 1 - c1[0];
    c2[0] = 1 - c2[0];
  }
  return {
    shaped,
    intensity,
    c1,
    c2,
    r1: num(p, "radius_1", 0.45),
    r2: num(p, "radius_2", 0.35),
    colour1: colour(p, ["color", "colour_1"], LEAK_COLOUR_1),
    colour2: colour(p, "colour_2", LEAK_COLOUR_2),
    wash: num(p, "wash", 0.06),
    skip: intensity < 1 / 1024,
  };
}

export const lightLeakWarm: TransitionFilter = {
  id: "light_leak_warm",
  uniforms: {
    uIntensity: { type: "f32" },
    uC1: { type: "vec2<f32>" },
    uC2: { type: "vec2<f32>" },
    uRadii: { type: "vec2<f32>" },
    uColour1: { type: "vec3<f32>" },
    uColour2: { type: "vec3<f32>" },
    uWash: { type: "f32" },
  },
  values(frame) {
    const terms = leakTerms(frame);
    return {
      uIntensity: terms.skip ? 0 : terms.intensity,
      uC1: terms.c1,
      uC2: terms.c2,
      uRadii: [terms.r1, terms.r2],
      uColour1: terms.colour1,
      uColour2: terms.colour2,
      uWash: terms.wash,
    };
  },
  glsl: /* glsl */ `
float blob(vec2 P, vec2 centre, float aspect, float radius) {
  vec2 d = P - vec2(centre.x * aspect, centre.y);
  return exp(-dot(d, d) / (2.0 * radius * radius));
}
void main() {
  vec3 a = texture(uA, vUV).rgb;
  vec3 b = texture(uB, vUV).rgb;
  vec3 base = mixLinear(a, b, uT, 1.0);
  if (uIntensity <= 0.0) {
    finalColor = vec4(base, 1.0);
    return;
  }
  float aspect = uSize.x / uSize.y;
  vec2 P = vec2(vUV.x * aspect, vUV.y);
  float g1 = blob(P, uC1, aspect, uRadii.x);
  float g2 = blob(P, uC2, aspect, uRadii.y);
  vec3 light = clamp(uIntensity * (g1 * uColour1 + 0.8 * g2 * uColour2 + uWash * uColour1), 0.0, 1.0);
  finalColor = vec4(base + light * (1.0 - base), 1.0);
}
`,
  fallback: "linear-dissolve",
  exactInFallback: false,
};

export default lightLeakWarm;
