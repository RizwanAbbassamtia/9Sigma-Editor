/**
 * `dip_black` / `dip_white` (render/transitions/dips.py): A fades into a colour (ease-in),
 * the colour holds for `hold` of the cut, then B comes out of it (ease-out); display-space blends.
 */
import { colour, num, type RGB, type TransitionFilter, type TransitionFrame } from "./common";
import { TRANSITION_EASINGS } from "./easing";

export interface DipTerms {
  /** 0 = A into the colour, 1 = the colour holds, 2 = the colour into B */
  phase: 0 | 1 | 2;
  weight: number;
  colour: RGB;
}

export function dipTerms(frame: TransitionFrame, fallback: RGB): DipTerms {
  const t = frame.t;
  const tint = colour(frame.params, ["color", "colour"], fallback);
  const hold = Math.min(Math.max(num(frame.params, "hold", 0.08), 0), 0.9);
  const h0 = 0.5 - hold / 2;
  const h1 = 0.5 + hold / 2;
  if (t < h0) return { phase: 0, weight: TRANSITION_EASINGS.ease_in_quad!(t / h0), colour: tint };
  if (t <= h1) return { phase: 1, weight: 1, colour: tint };
  return { phase: 2, weight: TRANSITION_EASINGS.ease_out_quad!((t - h1) / (1 - h1)), colour: tint };
}

export function dipFilter(id: "dip_black" | "dip_white", fallback: RGB): TransitionFilter {
  return {
    id,
    uniforms: { uPhase: { type: "f32" }, uWeight: { type: "f32" }, uColour: { type: "vec3<f32>" } },
    values(frame) {
      const terms = dipTerms(frame, fallback);
      return { uPhase: terms.phase, uWeight: terms.weight, uColour: terms.colour };
    },
    glsl: /* glsl */ `
void main() {
  vec3 a = texture(uA, vUV).rgb;
  vec3 b = texture(uB, vUV).rgb;
  vec3 c = uColour;
  vec3 o = uPhase < 0.5 ? mix(a, c, uWeight) : (uPhase < 1.5 ? c : mix(c, b, uWeight));
  finalColor = vec4(o, 1.0);
}
`,
    fallback: "dip",
    exactInFallback: true,
  };
}
