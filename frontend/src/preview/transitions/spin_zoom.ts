/**
 * `spin_zoom` (render/transitions/spin.py): A spins (up to 12 degrees) and zooms away with a
 * rotational/radial blur while B unspins from -8 degrees; every sample keeps the frame covered.
 */
import { flag, num, type TransitionFilter, type TransitionFrame } from "./common";
import { smoothstep } from "./easing";
import { RADIAL_BLUR_GLSL, SHARP_ZOOM_GLSL, blurSamples, blurStep } from "./_zoomblur";

/** The smallest magnification that keeps a frame turned by `degrees` covering it. */
export function coverScale(width: number, height: number, degrees: number): number {
  const theta = (Math.min(Math.abs(degrees), 89) * Math.PI) / 180;
  const ratio = Math.max(width / height, height / width);
  return Math.cos(theta) + ratio * Math.sin(theta);
}

export interface SpinTerms {
  scaleA: number;
  degreesA: number;
  scaleB: number;
  degreesB: number;
  blurScale: number;
  blurDegrees: number;
  mix: number;
  samples: number;
}

export function spinTerms(frame: TransitionFrame): SpinTerms {
  const t = frame.t;
  const p = frame.params;
  const turn = flag(p, "clockwise", true) ? 1 : -1;
  const { width, height } = frame;
  const degA = num(p, ["max_angle_deg", "degrees_a"], 12) * t * turn;
  const degB = num(p, ["b_angle_start_deg"], -num(p, "degrees_b", 8)) * (1 - t) * turn;
  const endA = num(p, ["a_scale_end"], 1.4);
  const startB = num(p, ["b_scale_start"], 1.3);
  const bump = Math.sin(Math.PI * t);
  const blurDeg = num(p, "blur_degrees", 3) * bump * turn;
  const blurScale = Math.max(num(p, "blur_scale", 0.05), 0) * bump;
  const scaleA = Math.max(1 + (endA - 1) * t, coverScale(width, height, degA), coverScale(width, height, degA + blurDeg));
  const scaleB = Math.max(1 + (startB - 1) * (1 - t), coverScale(width, height, degB), coverScale(width, height, degB + blurDeg));
  const window = Math.min(Math.max(num(p, ["crossfade_window", "crossfade"], 0.3), 0), 1);
  return {
    scaleA,
    degreesA: degA,
    scaleB,
    degreesB: degB,
    blurScale,
    blurDegrees: blurDeg,
    mix: smoothstep(0.5 - window / 2, 0.5 + window / 2, t),
    samples: blurSamples(width, height, blurScale, blurDeg),
  };
}

export const spinZoom: TransitionFilter = {
  id: "spin_zoom",
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
    const s = spinTerms(frame);
    const [q, d] = blurStep(s.samples, s.blurScale, s.blurDegrees);
    return {
      uScaleA: s.scaleA,
      uDegA: s.degreesA,
      uScaleB: s.scaleB,
      uDegB: s.degreesB,
      uMix: s.mix,
      uSamples: s.samples,
      uQ: q,
      uD: d,
    };
  },
  prePass: { glsl: SHARP_ZOOM_GLSL },
  glsl: RADIAL_BLUR_GLSL,
  fallback: "dissolve",
  exactInFallback: false,
};

export default spinZoom;
