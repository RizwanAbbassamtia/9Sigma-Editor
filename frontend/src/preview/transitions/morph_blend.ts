/**
 * `morph_blend` (render/transitions/warps.py): the luma difference of the two pictures pushes
 * pixels along the local structure while they crossfade (a soft morph without optical flow).
 *   LA, LB = luma of A and B on a quarter-size copy, Gaussian-smoothed by 12 s px (full-size px)
 *   g = Sobel(LA + LB) / 8;  V = max_px s (LB - LA) g / sqrt(|g|^2 + 0.02^2)  (scaled up bilinearly)
 *   out = mix(A(p + V t), B(p - V (1 - t)), t)
 * The engine builds V on the quarter-size grid (gpu/passes.ts) and binds it as `uField`, encoded
 * as `V / (2 amount) + 0.5`.
 */
import { num, px, type TransitionFilter, type TransitionFrame } from "./common";

export interface MorphTerms {
  small: [number, number];
  sigmaSmall: number;
  amount: number;
  eps: number;
}

export function morphTerms(frame: TransitionFrame): MorphTerms {
  const factor = 4;
  const small: [number, number] = [Math.max(16, Math.floor(frame.width / factor)), Math.max(9, Math.floor(frame.height / factor))];
  return {
    small,
    sigmaSmall: (px(frame, num(frame.params, "smooth_px", 12)) * small[0]) / frame.width,
    amount: px(frame, num(frame.params, ["max_displacement_px", "max_px"], 18)),
    eps: num(frame.params, "edge_eps", 0.02),
  };
}

export const morphBlend: TransitionFilter = {
  id: "morph_blend",
  uniforms: { uAmount: { type: "f32" } },
  values(frame) {
    return { uAmount: morphTerms(frame).amount };
  },
  morph(frame) {
    return morphTerms(frame);
  },
  glsl: /* glsl */ `
uniform sampler2D uField;
void main() {
  vec2 p = pixel();
  vec2 V = (texture(uField, vUV).rg - 0.5) * (2.0 * uAmount);
  vec3 a = sampleA(p + V * uT);
  vec3 b = sampleB(p - V * (1.0 - uT));
  finalColor = vec4(mix(a, b, uT), 1.0);
}
`,
  fallback: "dissolve",
  exactInFallback: false,
};

export default morphBlend;
