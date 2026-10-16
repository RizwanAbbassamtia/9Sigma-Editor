/**
 * `blur_dissolve` (render/transitions/blur.py): A defocuses (Gaussian 0 -> blur_px) while B comes
 * into focus (blur_px -> 0), crossfaded in linear light. The engine blurs both pictures first
 * (`blur()`, the same small-copy Gaussian as `fast_gaussian`), then this pass mixes them.
 */
import { num, px, type TransitionFilter } from "./common";

export const blurDissolve: TransitionFilter = {
  id: "blur_dissolve",
  uniforms: {},
  values() {
    return {};
  },
  blur(frame) {
    const sigma = px(frame, num(frame.params, ["max_blur_px", "blur_px"], 24));
    return { a: sigma * frame.t, b: sigma * (1 - frame.t) };
  },
  glsl: /* glsl */ `
void main() {
  vec3 a = texture(uA, vUV).rgb;
  vec3 b = texture(uB, vUV).rgb;
  finalColor = vec4(mixLinear(a, b, uT, 1.0), 1.0);
}
`,
  fallback: "linear-dissolve",
  exactInFallback: false,
};

export default blurDissolve;
