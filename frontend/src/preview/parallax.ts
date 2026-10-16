/**
 * 2.5D parallax in the preview (MOTION-PARITY.md section 6): the picture is drawn on a 64 x 36 mesh
 * whose vertices move with the camera by `gain (depth - mean) (n zoom - (px, py))` (the forward
 * version of render/parallax/warp.py's backward map), with `gain = strength W / 0.18`. The window
 * is tightened and the strength softened exactly as `warp.fit_overscan` does, so the preview moves
 * as far as the render. Near objects get their own front mesh (layers.ts: the renderer's
 * `analyse_depth`), drawn over a back plate where they are filled in.
 * Without a depth map a parallax scene is the eased Ken Burns move, as in the renderer.
 */
import { analyseDepth, cropGrid, sampleGrid, type Grid, type LayerAnalysis, type Plates } from "./layers";
import { MotionPath, parallaxGain, type CameraTerms, type Rect } from "./motion";
import type { DepthMap } from "./pictures";

export const MESH_COLS = 64;
export const MESH_ROWS = 36;
export const TAP_MARGIN_PX = 1;
export const MAX_DOWNSAMPLE = 1.5;
export const WORK_MARGIN = 0.04;

/** Bilinear depth at picture position (x, y) in picture pixels (edge replicated). */
export function sampleDepth(depth: DepthMap, imageW: number, imageH: number, x: number, y: number): number {
  const gx = Math.min(Math.max((x / imageW) * depth.width - 0.5, 0), depth.width - 1);
  const gy = Math.min(Math.max((y / imageH) * depth.height - 0.5, 0), depth.height - 1);
  const x0 = Math.floor(gx);
  const y0 = Math.floor(gy);
  const x1 = Math.min(x0 + 1, depth.width - 1);
  const y1 = Math.min(y0 + 1, depth.height - 1);
  const fx = gx - x0;
  const fy = gy - y0;
  const v = depth.values;
  const top = v[y0 * depth.width + x0]! * (1 - fx) + v[y0 * depth.width + x1]! * fx;
  const bottom = v[y1 * depth.width + x0]! * (1 - fx) + v[y1 * depth.width + x1]! * fx;
  return top * (1 - fy) + bottom * fy;
}

/** The working picture of `warp.working_image`: the cover region plus 4 % of real picture around it. */
export function workingCrop(region: Rect, imageW: number, imageH: number): Rect {
  const [rx, ry, rw, rh] = region;
  const x0 = Math.max(Math.floor(rx - rw * WORK_MARGIN), 0);
  const y0 = Math.max(Math.floor(ry - rh * WORK_MARGIN), 0);
  const x1 = Math.min(Math.ceil(rx + rw + rw * WORK_MARGIN), imageW);
  const y1 = Math.min(Math.ceil(ry + rh + rh * WORK_MARGIN), imageH);
  return [x0, y0, Math.max(x1 - x0, 1), Math.max(y1 - y0, 1)];
}

export interface DepthStats {
  mean: number;
  low: number;
  high: number;
}

/** Mean, lowest and highest depth over a part of the picture. */
export function depthStats(depth: DepthMap, crop: Rect, imageW: number, imageH: number): DepthStats {
  const [cx, cy, cw, ch] = crop;
  const gx0 = Math.max(0, Math.floor((cx / imageW) * depth.width));
  const gy0 = Math.max(0, Math.floor((cy / imageH) * depth.height));
  const gx1 = Math.min(depth.width, Math.ceil(((cx + cw) / imageW) * depth.width));
  const gy1 = Math.min(depth.height, Math.ceil(((cy + ch) / imageH) * depth.height));
  let total = 0;
  let count = 0;
  let low = 1;
  let high = 0;
  for (let y = gy0; y < gy1; y++) {
    for (let x = gx0; x < gx1; x++) {
      const value = depth.values[y * depth.width + x]!;
      total += value;
      count += 1;
      if (value < low) low = value;
      if (value > high) high = value;
    }
  }
  return count ? { mean: total / count, low, high } : { mean: 0.5, low: 0.5, high: 0.5 };
}

interface FrameNeed {
  ax: number;
  bx: number;
  ay: number;
  by: number;
}

function needShare(need: FrameNeed, strength: number): number {
  return Math.max(need.ax * strength - need.bx, need.ay * strength - need.by);
}

function needStrength(need: FrameNeed, share: number): number {
  let best = Infinity;
  for (const [a, b] of [
    [need.ax, need.bx],
    [need.ay, need.by],
  ] as const) {
    if (a > 1e-12) best = Math.min(best, (share + b) / a);
  }
  return best;
}

/**
 * `warp.fit_overscan`: how much to tighten every window (a share of its width and height on every
 * side) so the parallax never pulls pixels from outside the picture, and the strength to draw with
 * (lowered when the move needs more than `limit`).
 */
export function fitOverscan(
  path: MotionPath,
  stats: DepthStats,
  outW: number,
  outH: number,
  options: { strength: number; frames: number; fps: number; limit: number; picture: Rect; tapMarginPx?: number },
): { share: number; strength: number } {
  let strength = Math.max(options.strength, 0);
  if (options.limit <= 0 || strength <= 0) return { share: 0, strength };
  const spread = Math.max(stats.high - stats.mean, stats.mean - stats.low, 0);
  const perStrength = (parallaxGain(1, outW) * spread) / Math.max(outW, 1);
  const aspect = outH / Math.max(outW, 1);
  const [bx, by, bw, bh] = options.picture;
  const tap = options.tapMarginPx ?? TAP_MARGIN_PX;
  const needs: FrameNeed[] = [];
  const count = Math.max(Math.trunc(options.frames), 2);
  for (let k = 0; k < count; k++) {
    const camera = path.frameCamera(k, count, options.fps);
    const reachX = perStrength * (Math.abs(camera.zoom) + Math.abs(camera.px));
    const reachY = perStrength * (aspect * Math.abs(camera.zoom) + Math.abs(camera.py));
    if (reachX <= 0 && reachY <= 0) continue;
    const [x, y, wRaw, hRaw] = path.frameRect(k, count, options.fps);
    const w = Math.max(wRaw, 1e-9);
    const h = Math.max(hRaw, 1e-9);
    const gapX = Math.min(x - bx, bx + bw - (x + w)) - tap;
    const gapY = Math.min(y - by, by + bh - (y + h)) - tap;
    needs.push({
      ax: reachX > 0 ? reachX : 0,
      bx: reachX > 0 ? gapX / w : Infinity,
      ay: reachY > 0 ? (reachY * w) / h : 0,
      by: reachY > 0 ? gapY / h : Infinity,
    });
  }
  let needed = needs.reduce((most, need) => Math.max(most, needShare(need, strength)), 0);
  if (needed <= options.limit) return { share: Math.max(needed, 0), strength };
  const softer = Math.max(Math.min(strength, ...needs.map((need) => needStrength(need, options.limit))), 0);
  needed = needs.reduce((most, need) => Math.max(most, needShare(need, softer)), 0);
  strength = softer;
  return { share: Math.min(Math.max(needed, 0), options.limit), strength };
}

/** `warp.tighten`: a window shrunk by `share` of its width and height on every side. */
export function tighten(rect: Rect, share: number): Rect {
  const [x, y, w, h] = rect;
  if (share <= 0) return [x, y, w, h];
  return [x + w * share, y + h * share, w * (1 - 2 * share), h * (1 - 2 * share)];
}

export interface ParallaxPlan {
  stats: DepthStats;
  share: number;
  strength: number;
  /** the working picture (cover region plus 4 %) the displacement may reach into */
  crop: Rect;
  /** the depth over the crop and its layers (render/parallax/warp.py prepare_layers) */
  analysis: LayerAnalysis;
  /**
   * the colours of the two layers, made by the parallax worker once the picture is in (null: one
   * layer, or not made yet: the scene is drawn as its Ken Burns move until they are)
   */
  plates: Plates | null;
}

/** Depth statistics of a grid (mean, lowest, highest). */
export function gridStats(grid: Grid): DepthStats {
  let total = 0;
  let low = 1;
  let high = 0;
  for (const value of grid.values) {
    total += value;
    if (value < low) low = value;
    if (value > high) high = value;
  }
  return grid.values.length ? { mean: total / grid.values.length, low, high } : { mean: 0.5, low: 0.5, high: 0.5 };
}

export interface PlanOptions {
  strength: number;
  frames: number;
  fps: number;
  limit: number;
}

/** The depth over a working crop: its statistics and its two layers. */
export interface CropAnalysis {
  stats: DepthStats;
  analysis: LayerAnalysis;
}

/** The working picture of a scene (null until the picture's size bounds the camera path). */
export function sceneCrop(path: MotionPath, image: [number, number]): Rect | null {
  return path.bounds ? workingCrop(path.bounds, image[0], image[1]) : null;
}

/**
 * The depth over the working crop and its layers: the costly part of a plan (tens of
 * milliseconds), run by the parallax worker (parallaxJobs.ts), never inside a frame.
 */
export function analyseCrop(depth: DepthMap, crop: Rect, image: [number, number]): CropAnalysis {
  const grid = cropGrid(depth, crop, image);
  return { stats: gridStats(grid), analysis: analyseDepth(grid) };
}

/**
 * A scene's plan from its crop's analysis: the overscan and the softened strength (cheap). The
 * windows stay clamped to the cover region (the path's bounds); only the margin each frame has
 * left is measured against the working picture.
 */
export function planFromAnalysis(path: MotionPath, found: CropAnalysis, crop: Rect, render: [number, number], options: PlanOptions): ParallaxPlan | null {
  if (!path.bounds || options.strength <= 0) return null;
  const [wMin, wMax] = path.widthRange();
  // render/parallax/warp.py working_image: about one working pixel per output pixel
  const factor = Math.min(1, render[0] / Math.max(wMin, 1e-6), (MAX_DOWNSAMPLE * render[0]) / Math.max(wMax, 1e-6));
  const fit = fitOverscan(path, found.stats, render[0], render[1], { ...options, picture: crop, tapMarginPx: TAP_MARGIN_PX / Math.max(factor, 1e-6) });
  return { stats: found.stats, share: fit.share, strength: fit.strength, crop, analysis: found.analysis, plates: null };
}

/**
 * Works out a scene's parallax at once: the depth over the working picture and its layers, the
 * overscan and the softened strength (tests and tools; the engine splits it, see parallaxJobs.ts).
 */
export function planParallax(path: MotionPath, depth: DepthMap, image: [number, number], render: [number, number], options: PlanOptions): ParallaxPlan | null {
  const crop = sceneCrop(path, image);
  if (!crop || options.strength <= 0) return null;
  return planFromAnalysis(path, analyseCrop(depth, crop, image), crop, render, options);
}

export interface MeshFrame {
  /** vertex positions in output pixels (x, y pairs), (cols + 3) x (rows + 3) vertices */
  positions: Float32Array;
  /** texture coordinates 0..1 of the layer's texture */
  uvs: Float32Array;
  indices: Uint32Array;
  cols: number;
  rows: number;
}

/**
 * The parallax mesh of one layer for one frame: a grid over the output frame (one cell beyond each
 * edge, so the moved border never opens a gap), each vertex showing the picture where the camera
 * puts it and moved by the parallax offset of that layer's depth there. `textureRect` is the part
 * of the picture the layer's texture covers (the picture itself, or the working crop of the plates).
 */
export function parallaxMesh(
  plan: ParallaxPlan,
  rect: Rect,
  camera: CameraTerms,
  layer: "back" | "front",
  textureRect: Rect,
  out: [number, number],
  cols = MESH_COLS,
  rows = MESH_ROWS,
  reuse?: MeshFrame,
): MeshFrame {
  const [outW, outH] = out;
  const [x, y, w, h] = tighten(rect, plan.share);
  const gain = parallaxGain(plan.strength, outW);
  const depth = layer === "front" && plan.analysis.frontDepth ? plan.analysis.frontDepth : plan.analysis.backDepth;
  const [cx, cy, cw, ch] = plan.crop;
  const [tx, ty, tw, th] = textureRect;
  const half = outW / 2;
  const nx = cols + 3;
  const ny = rows + 3;
  const count = nx * ny;
  const positions = reuse && reuse.positions.length === count * 2 ? reuse.positions : new Float32Array(count * 2);
  const uvs = reuse && reuse.uvs.length === count * 2 ? reuse.uvs : new Float32Array(count * 2);
  let indices = reuse && reuse.cols === cols && reuse.rows === rows ? reuse.indices : null;
  const cellW = outW / cols;
  const cellH = outH / rows;
  for (let j = 0; j < ny; j++) {
    const py = (j - 1) * cellH;
    for (let i = 0; i < nx; i++) {
      const px = (i - 1) * cellW;
      const sx = x + (px * w) / outW;
      const sy = y + (py * h) / outH;
      const d = sampleGrid(depth, ((sx - cx) / cw) * depth.width - 0.5, ((sy - cy) / ch) * depth.height - 0.5) - plan.stats.mean;
      const vx = ((px - outW / 2) / half) * camera.zoom - camera.px;
      const vy = ((py - outH / 2) / half) * camera.zoom - camera.py;
      const at = (j * nx + i) * 2;
      positions[at] = px + gain * d * vx;
      positions[at + 1] = py + gain * d * vy;
      uvs[at] = (sx - tx) / tw;
      uvs[at + 1] = (sy - ty) / th;
    }
  }
  if (!indices) {
    indices = new Uint32Array((nx - 1) * (ny - 1) * 6);
    let k = 0;
    for (let j = 0; j < ny - 1; j++) {
      for (let i = 0; i < nx - 1; i++) {
        const a = j * nx + i;
        indices[k++] = a;
        indices[k++] = a + 1;
        indices[k++] = a + nx;
        indices[k++] = a + 1;
        indices[k++] = a + nx + 1;
        indices[k++] = a + nx;
      }
    }
  }
  return { positions, uvs, indices, cols, rows };
}
