/**
 * `zoom_through` (render/transitions/zoom.py): the camera flies into A and out of B, crossfaded
 * in the middle, with a radial blur that peaks halfway.
 *   kA = 1 + (a_scale_end - 1) t,  kB = b_scale_start + (1 - b_scale_start) t
 *   m = smoothstep(0.5 - w/2, 0.5 + w/2, t),  span = 0.12 sin(pi t)
 */
import { flag, num, type TransitionFilter, type TransitionFrame, type TransitionParams } from "./common";
import { smoothstep } from "./easing";
import { RADIAL_BLUR_GLSL, SHARP_ZOOM_GLSL, blurSamples, blurStep } from "./_zoomblur";

export const DEFAULT_BLUR_SPAN = 0.12;

/** The peak span of the radial blur: `radial_blur` true (0.12), false (none) or a number. */
export function blurSpan(params: TransitionParams): number {
  const raw = Object.prototype.hasOwnProperty.call(params, "radial_blur") ? params.radial_blur : Object.prototype.hasOwnProperty.call(params, "blur") ? params.blur : true;
  if (raw === null || raw === undefined) return DEFAULT_BLUR_SPAN;
  if (typeof raw === "boolean") return raw ? DEFAULT_BLUR_SPAN : 0;
  const value = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() !== "" ? Number(raw.trim()) : NaN;
  if (Number.isNaN(value)) return flag(params, ["radial_blur", "blur"], true) ? DEFAULT_BLUR_SPAN : 0;
  return Number.isFinite(value) ? Math.max(value, 0) : DEFAULT_BLUR_SPAN;
}

export interface ZoomThroughTerms {
  scaleA: number;
  scaleB: number;
  m: number;
  span: number;
  samples: number;
}

export function zoomThroughTerms(frame: TransitionFrame): ZoomThroughTerms {
  const t = frame.t;
  const p = frame.params;
  const endA = Math.max(num(p, ["a_scale_end"], 1 + num(p, "scale_a", 0.25)), 1);
  const startB = Math.max(num(p, ["b_scale_start", "scale_b"], 1.15), 1);
  const width = Math.min(Math.max(num(p, ["crossfade_window", "crossfade"], 0.4), 0), 1);
  const span = blurSpan(p) * Math.sin(Math.PI * t);
  return {
    scaleA: 1 + (endA - 1) * t,
    scaleB: startB + (1 - startB) * t,
    m: smoothstep(0.5 - width / 2, 0.5 + width / 2, t),
    span,
    samples: blurSamples(frame.width, frame.height, span, 0),
  };
}

export const zoomThrough: TransitionFilter = {
  id: "zoom_through",
  uniforms: {
    uScaleA: { type: "f32" },
    uDegA: { type: "f32" },
    uScaleB: { type: "f32" },
    uDegB: { type: "f32" },
    uMix: { type: "f32" },
    uSamples: { type: "i32" },
    uQ: { type: "f32" },
    uD: { type: "f32" },
  },
  values(frame) {
    const terms = zoomThroughTerms(frame);
    const [q] = blurStep(terms.samples, terms.span, 0);
    return { uScaleA: terms.scaleA, uDegA: 0, uScaleB: terms.scaleB, uDegB: 0, uMix: terms.m, uSamples: terms.samples, uQ: q, uD: 0 };
  },
  prePass: { glsl: SHARP_ZOOM_GLSL },
  glsl: RADIAL_BLUR_GLSL,
  fallback: "dissolve",
  exactInFallback: false,
};

export default zoomThrough;
