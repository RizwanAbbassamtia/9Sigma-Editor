/**
 * `film_dissolve` (render/transitions/dissolve.py): a crossfade in linear light with a gentle
 * luma lift at the middle: `out = mix_linear(A, B, t, 1 + luma_lift sin(pi t))`.
 */
import { num, type TransitionFilter } from "./common";

export const filmDissolve: TransitionFilter = {
  id: "film_dissolve",
  uniforms: { uGain: { type: "f32" } },
  values(frame) {
    return { uGain: 1 + num(frame.params, ["luma_lift", "lift"], 0.03) * Math.sin(Math.PI * frame.t) };
  },
  glsl: /* glsl */ `
void main() {
  vec3 a = texture(uA, vUV).rgb;
  vec3 b = texture(uB, vUV).rgb;
  finalColor = vec4(mixLinear(a, b, uT, uGain), 1.0);
}
`,
  fallback: "linear-dissolve",
  exactInFallback: true,
};

export default filmDissolve;
