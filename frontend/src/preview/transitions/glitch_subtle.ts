/**
 * `glitch_subtle` (render/transitions/glitch.py): a few frames of digital break-up on A, then a
 * clean cut to B. Timing counts frames of a 30 fps reference clock on linear progress; the strength
 * follows the eased `t`. The colour split and the slice bands come from the integer hash, so every
 * glitch frame matches the render's (positions in whole pixels of the frame being drawn).
 */
import { num, px, span, type TransitionFilter, type TransitionFrame } from "./common";
import { transitionEase } from "./easing";
import { pyRound } from "../motion";
import { rand } from "./noise";

export const REFERENCE_FPS = 30;
export const MAX_BANDS = 8;

export interface GlitchWindow {
  /** glitched reference frames */
  count: number;
  /** reference frames in the cut */
  total: number;
  /** linear progress of the clean cut */
  cut: number;
}

export function glitchWindow(frame: TransitionFrame): GlitchWindow {
  const [lo, hi] = span(frame.params, "glitch_frames", [6, 8]);
  const loI = Math.max(1, pyRound(lo));
  const hiI = Math.max(1, pyRound(hi));
  const count = loI + Math.min(Math.trunc(rand(0, 99, frame.seed) * (hiI - loI + 1)), hiI - loI);
  const total = Math.max(frame.durationS * REFERENCE_FPS, 1);
  return { count, total, cut: Math.min(1, count / total) };
}

export interface GlitchTerms extends GlitchWindow {
  clean: boolean;
  env: number;
  step: number;
  /** red right, blue left by this many pixels */
  split: number;
  /** [top, height, shift] in pixels */
  bands: [number, number, number][];
}

export function glitchTerms(frame: TransitionFrame): GlitchTerms {
  const window = glitchWindow(frame);
  if (frame.tRaw >= window.cut) return { ...window, clean: true, env: 0, step: 0, split: 0, bands: [] };
  const easedCut = Math.max(transitionEase(frame.easing, window.cut), 1e-6);
  const env = Math.sqrt(Math.min(1, frame.t / easedCut));
  const step = Math.min(Math.trunc(frame.tRaw * window.total), window.count - 1);
  const seed = Math.trunc(frame.seed);
  const height = frame.height;
  const maxShift = px(frame, num(frame.params, ["max_shift_px", "split_px"], 6));
  const split = pyRound(maxShift * env * (0.6 + 0.4 * rand(step, 0, seed)));
  const [lo, hi] = span(frame.params, "slices", [3, 5]);
  const loI = Math.max(0, pyRound(lo));
  const hiI = Math.max(0, pyRound(hi));
  const count = loI + Math.min(Math.trunc(rand(step, 1, seed) * (hiI - loI + 1)), hiI - loI);
  const reach = px(frame, num(frame.params, "slice_px", 40)) * env;
  const bands: [number, number, number][] = [];
  for (let band = 0; band < count; band++) {
    const top = Math.trunc(rand(step, 2 + 3 * band, seed) * height);
    const size = Math.max(2, Math.trunc((0.02 + 0.08 * rand(step, 3 + 3 * band, seed)) * height));
    const shift = pyRound((2 * rand(step, 4 + 3 * band, seed) - 1) * reach);
    bands.push([top, size, shift]);
  }
  return { ...window, clean: false, env, step, split, bands };
}

export const glitchSubtle: TransitionFilter = {
  id: "glitch_subtle",
  uniforms: {
    uClean: { type: "f32" },
    uSplit: { type: "f32" },
    uBandCount: { type: "i32" },
    uBands: { type: "vec4<f32>", size: MAX_BANDS },
  },
  values(frame) {
    const terms = glitchTerms(frame);
    const flat: number[] = [];
    const used = terms.bands.filter(([top, , shift]) => shift !== 0 && top < frame.height).slice(0, MAX_BANDS);
    for (let i = 0; i < MAX_BANDS; i++) {
      const band = used[i];
      flat.push(band ? band[0] : 0, band ? band[1] : 0, band ? band[2] : 0, 0);
    }
    return {
      uClean: terms.clean ? 1 : 0,
      uSplit: terms.split >= 1 ? terms.split : 0,
      uBandCount: used.length,
      uBands: flat,
    };
  },
  glsl: /* glsl */ `
/* np.roll's wrap in whole pixels (integers: a float mod can return the width itself on a GPU) */
int wrapColumn(int x, int width) {
  return (x + width * 256) % width;
}
void main() {
  if (uClean > 0.5) {
    finalColor = vec4(texture(uB, vUV).rgb, 1.0);
    return;
  }
  int width = int(uSize.x + 0.5);
  int x = int(floor(vUV.x * uSize.x));
  float y = floor(vUV.y * uSize.y);
  int shift = 0;
  for (int i = 0; i < ${MAX_BANDS}; i++) {
    if (i >= uBandCount) break;
    vec4 band = uBands[i];
    if (y >= band.x && y < min(uSize.y, band.x + band.y)) shift += int(band.z);
  }
  int split = int(uSplit);
  int base = x - shift;
  float r = sampleA(vec2(float(wrapColumn(base - split, width)) + 0.5, y + 0.5)).r;
  float g = sampleA(vec2(float(wrapColumn(base, width)) + 0.5, y + 0.5)).g;
  float b = sampleA(vec2(float(wrapColumn(base + split, width)) + 0.5, y + 0.5)).b;
  finalColor = vec4(r, g, b, 1.0);
}
`,
  fallback: "dissolve",
  exactInFallback: false,
};

export default glitchSubtle;
