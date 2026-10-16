/**
 * `crosswarp` (render/transitions/warps.py, after GL Transitions "crosswarp" by Eke Peter, MIT):
 * per column `x = smoothstep(0, 1, 2t + u - 1)`, so the right side goes first;
 * `out = mix(A(c + (p - c)(1 - x)), B(c + (p - c) x), x)`.
 */
import type { TransitionFilter } from "./common";
import { smoothstep } from "./easing";

/** The per-column progress at pixel-centre column `u`. */
export function crosswarpProgress(t: number, u: number): number {
  return smoothstep(0, 1, 2 * t - 1 + u);
}

export const crosswarp: TransitionFilter = {
  id: "crosswarp",
  uniforms: {},
  values() {
    return {};
  },
  glsl: /* glsl */ `
void main() {
  vec2 p = pixel();
  vec2 c = frameCentre();
  float x = smoothstep(0.0, 1.0, 2.0 * uT - 1.0 + vUV.x);
  vec3 a = sampleA(c + (p - c) * (1.0 - x));
  vec3 b = sampleB(c + (p - c) * x);
  finalColor = vec4(mix(a, b, x), 1.0);
}
`,
  fallback: "dissolve",
  exactInFallback: false,
};

export default crosswarp;
