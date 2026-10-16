/**
 * The integer hash and value noise of backend/sigma_editor/render/transitions/noise.py
 * (TRANSITION-PARITY.md section 6). The same numbers come out of the GLSL version below on every
 * device (WebGL2 `uint` arithmetic), so the glitch slices and the burn edge match the render.
 */

const M1 = 0x8da6b343;
const M2 = 0xd8163841;
const M3 = 0x9e3779b1;
const M4 = 0x5bd1e995;

/** `h = x*M1 ^ y*M2 ^ seed*M3; h ^= h >> 13; h *= M4; h ^= h >> 15` (uint32, wrapping). */
export function hashU32(x: number, y: number, seed: number): number {
  let h = (Math.imul(x >>> 0, M1) ^ Math.imul(y >>> 0, M2) ^ Math.imul(seed >>> 0, M3)) >>> 0;
  h = (h ^ (h >>> 13)) >>> 0;
  h = Math.imul(h, M4) >>> 0;
  h = (h ^ (h >>> 15)) >>> 0;
  return h;
}

/** The hash as a number in [0, 1): the top 24 bits divided by 2^24. */
export function hash01(x: number, y: number, seed: number): number {
  return (hashU32(x, y, seed) >>> 8) / 16777216;
}

/** One deterministic number in [0, 1) for (step, slot, seed). */
export function rand(step: number, slot: number, seed: number): number {
  return hash01(step, slot, seed);
}

/** Value noise with `cells` lattice cells across the frame height (square cells), at pixel (x, y). */
export function valueNoise(x: number, y: number, width: number, height: number, cells: number, seed: number): number {
  const aspect = width / height;
  const px = ((x + 0.5) / width) * aspect * cells;
  const py = ((y + 0.5) / height) * cells;
  const ix = Math.floor(px);
  const iy = Math.floor(py);
  const fx = px - ix;
  const fy = py - iy;
  const sx = fx * fx * (3 - 2 * fx);
  const sy = fy * fy * (3 - 2 * fy);
  const top = hash01(ix, iy, seed) * (1 - sx) + hash01(ix + 1, iy, seed) * sx;
  const bottom = hash01(ix, iy + 1, seed) * (1 - sx) + hash01(ix + 1, iy + 1, seed) * sx;
  return top * (1 - sy) + bottom * sy;
}

/** Fractal value noise in [0, 1]: octave `o` has `cells * 2^o` cells, weight `0.5^o`, seed `seed + 1013 o`. */
export function fbm(x: number, y: number, width: number, height: number, cells = 3, octaves = 4, seed = 0): number {
  let total = 0;
  let weights = 0;
  for (let octave = 0; octave < Math.max(1, octaves); octave++) {
    const weight = 0.5 ** octave;
    total += weight * valueNoise(x, y, width, height, cells * 2 ** octave, seed + 1013 * octave);
    weights += weight;
  }
  return total / weights;
}

/** GLSL ES 3.00 versions (uint arithmetic wraps like uint32). */
export const NOISE_GLSL = /* glsl */ `
uint hashU32(uint x, uint y, uint seed) {
  uint h = (x * 0x8DA6B343u) ^ (y * 0xD8163841u) ^ (seed * 0x9E3779B1u);
  h ^= h >> 13u;
  h *= 0x5BD1E995u;
  h ^= h >> 15u;
  return h;
}
float hash01(uint x, uint y, uint seed) {
  return float(hashU32(x, y, seed) >> 8u) / 16777216.0;
}
float valueNoise(vec2 uv, float aspect, float cells, uint seed) {
  vec2 P = vec2(uv.x * aspect * cells, uv.y * cells);
  vec2 i = floor(P);
  vec2 f = P - i;
  vec2 w = f * f * (3.0 - 2.0 * f);
  uint ix = uint(i.x);
  uint iy = uint(i.y);
  float top = mix(hash01(ix, iy, seed), hash01(ix + 1u, iy, seed), w.x);
  float bottom = mix(hash01(ix, iy + 1u, seed), hash01(ix + 1u, iy + 1u, seed), w.x);
  return mix(top, bottom, w.y);
}
float fbm(vec2 uv, float aspect, float cells, int octaves, uint seed) {
  float total = 0.0;
  float weights = 0.0;
  float weight = 1.0;
  float c = cells;
  for (int o = 0; o < 8; o++) {
    if (o >= octaves) break;
    total += weight * valueNoise(uv, aspect, c, seed + 1013u * uint(o));
    weights += weight;
    weight *= 0.5;
    c *= 2.0;
  }
  return total / weights;
}
`;
