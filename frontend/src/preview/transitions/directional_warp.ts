/**
 * `directional_warp` (render/transitions/warps.py, after GL Transitions "directionalwarp" by
 * pschroen, MIT). `direction` is in GL axes (x right, y up), default (-1, 1):
 *   d0 = normalize(direction); d0 /= |d0.x| + |d0.y|; k = d0 . (0.5, 0.5)
 *   m = 1 - smoothstep(-s, 0, d0.x u + d0.y (1 - v) - (k - 0.5 + t (1 + s)))
 *   out = mix(A(c + (p - c)(1 - m)), B(c + (p - c) m), m)
 */
import { num, type TransitionFilter, type TransitionFrame } from "./common";
import { smoothstep } from "./easing";

/** Python's `float(value)` for JSON values: null when it would raise. */
function pythonFloat(value: unknown): number | null {
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value.trim());
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

export function warpDirection(frame: TransitionFrame): [number, number] {
  const raw = frame.params.direction;
  let dx = -1;
  let dy = 1;
  if (Array.isArray(raw) && raw.length >= 2) {
    const x = pythonFloat(raw[0]);
    const y = pythonFloat(raw[1]);
    if (x !== null && y !== null) {
      dx = x;
      dy = y;
    }
  }
  let length = Math.hypot(dx, dy);
  if (length < 1e-9) {
    dx = -1;
    dy = 1;
    length = Math.sqrt(2);
  }
  dx /= length;
  dy /= length;
  const norm = Math.abs(dx) + Math.abs(dy);
  return [dx / norm, dy / norm];
}

export interface DirectionalWarpTerms {
  v: [number, number];
  smoothness: number;
  /** `k - 0.5 + t (1 + s)` */
  offset: number;
}

export function directionalWarpTerms(frame: TransitionFrame): DirectionalWarpTerms {
  const smooth = Math.max(num(frame.params, "smoothness", 0.5), 1e-3);
  const [vx, vy] = warpDirection(frame);
  const d = vx * 0.5 + vy * 0.5;
  return { v: [vx, vy], smoothness: smooth, offset: d - 0.5 + frame.t * (1 + smooth) };
}

/** The mix weight at pixel centre (u, v). */
export function directionalWarpMix(terms: DirectionalWarpTerms, u: number, v: number): number {
  const front = terms.v[0] * u + terms.v[1] * (1 - v) - terms.offset;
  return 1 - smoothstep(-terms.smoothness, 0, front);
}

export const directionalWarp: TransitionFilter = {
  id: "directional_warp",
  uniforms: { uDirection: { type: "vec2<f32>" }, uSmooth: { type: "f32" }, uOffset: { type: "f32" }, uGrid: { type: "vec2<f32>" } },
  values(frame) {
    const terms = directionalWarpTerms(frame);
    // the reference works the mix out on a grid a quarter of the frame (at least 8 x 8)
    const grid = [Math.max(8, Math.floor(frame.width / 4)), Math.max(8, Math.floor(frame.height / 4))];
    return { uDirection: terms.v, uSmooth: terms.smoothness, uOffset: terms.offset, uGrid: grid };
  },
  glsl: /* glsl */ `
float warpMix(vec2 uv) {
  float front = uDirection.x * uv.x + uDirection.y * (1.0 - uv.y) - uOffset;
  return 1.0 - smoothstep(-uSmooth, 0.0, front);
}
/* m on the reference's quarter-size grid, scaled up like cv2.resize INTER_LINEAR (edges held). */
float gridMix(vec2 p) {
  vec2 grid = uGrid;
  vec2 g = p * grid / uSize - 0.5;
  vec2 g0 = floor(g);
  vec2 f = g - g0;
  if (g0.x < 0.0) { g0.x = 0.0; f.x = 0.0; }
  if (g0.y < 0.0) { g0.y = 0.0; f.y = 0.0; }
  if (g0.x >= grid.x - 1.0) { g0.x = grid.x - 1.0; f.x = 0.0; }
  if (g0.y >= grid.y - 1.0) { g0.y = grid.y - 1.0; f.y = 0.0; }
  vec2 g1 = min(g0 + 1.0, grid - 1.0);
  float m00 = warpMix((g0 + 0.5) / grid);
  float m10 = warpMix((vec2(g1.x, g0.y) + 0.5) / grid);
  float m01 = warpMix((vec2(g0.x, g1.y) + 0.5) / grid);
  float m11 = warpMix((g1 + 0.5) / grid);
  return mix(mix(m00, m10, f.x), mix(m01, m11, f.x), f.y);
}
void main() {
  vec2 p = pixel();
  vec2 c = frameCentre();
  float m = gridMix(p);
  vec3 a = sampleA(c + (p - c) * (1.0 - m));
  vec3 b = sampleB(c + (p - c) * m);
  finalColor = vec4(mix(a, b, m), 1.0);
}
`,
  fallback: "dissolve",
  exactInFallback: false,
};

export default directionalWarp;
