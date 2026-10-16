/**
 * The two parallax layers (render/parallax/warp.py `analyse_depth`, `prepare_layers`,
 * `front_depth`, `fill_behind`, `unmix_front`; MOTION-PARITY.md section 6), worked out once per
 * scene on a small depth grid over the working picture:
 *
 * 1. envelope: the depth at half size, opened (erode, then dilate) by a square of side
 *    `2 round(0.25 short) + 1` after a point-reflection pad, smoothed but never below the opening,
 *    scaled back up, never above the depth;
 * 2. front alpha: `smoothstep(0.04, 0.12, depth - envelope)` where `depth - envelope > 0.08` holds
 *    over a 5 x 5 patch, kept for the connected parts that touch a sharp depth edge (local range
 *    over 1 % of the short side above 0.1);
 * 3. back depth `depth - alpha (depth - envelope)` (blurred by 1), front depth = the object's own
 *    depth carried 8 % of the short side past its edge;
 * 4. the back plate is the picture with the objects filled from the background around them, the
 *    front layer the picture un-mixed from that plate (`picture = a front + (1 - a) plate`).
 *
 * The preview analyses a grid with a short side of 192 (the renderer: 384), fills with a push-pull
 * pyramid instead of OpenCV's TELEA inpainting and does not snap the alpha to the picture's edges
 * with a guided filter; the thresholds and the shapes are the renderer's.
 */
import type { Rect } from "./motion";
import type { DepthMap } from "./pictures";

export interface Grid {
  values: Float32Array;
  width: number;
  height: number;
}

export const GRID_SHORT_SIDE = 192;
export const OBJECT_SCALE = 0.25;
export const LAYER_THRESHOLD = 0.08;
export const EDGE_THRESHOLD = 0.1;
export const FRONT_REACH = 0.08;

export function makeGrid(width: number, height: number, fill = 0): Grid {
  const values = new Float32Array(width * height);
  if (fill) values.fill(fill);
  return { values, width, height };
}

/** Bilinear read at grid position (gx, gy) in index space (pixel centres on integers), edges held. */
export function sampleGrid(grid: Grid, gx: number, gy: number): number {
  const x = Math.min(Math.max(gx, 0), grid.width - 1);
  const y = Math.min(Math.max(gy, 0), grid.height - 1);
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const x1 = Math.min(x0 + 1, grid.width - 1);
  const y1 = Math.min(y0 + 1, grid.height - 1);
  const fx = x - x0;
  const fy = y - y0;
  const v = grid.values;
  const w = grid.width;
  const top = v[y0 * w + x0]! * (1 - fx) + v[y0 * w + x1]! * fx;
  const bottom = v[y1 * w + x0]! * (1 - fx) + v[y1 * w + x1]! * fx;
  return top * (1 - fy) + bottom * fy;
}

/** `cv2.resize(INTER_LINEAR)`: source = (dest + 0.5) scale - 0.5, edges held. */
export function resizeLinear(grid: Grid, width: number, height: number): Grid {
  const out = makeGrid(width, height);
  const sx = grid.width / width;
  const sy = grid.height / height;
  for (let y = 0; y < height; y++) {
    const gy = (y + 0.5) * sy - 0.5;
    for (let x = 0; x < width; x++) out.values[y * width + x] = sampleGrid(grid, (x + 0.5) * sx - 0.5, gy);
  }
  return out;
}

/** `cv2.resize(INTER_AREA)` for shrinking: the mean over each destination pixel's footprint. */
export function resizeArea(grid: Grid, width: number, height: number): Grid {
  const across = makeGrid(width, grid.height);
  const sx = grid.width / width;
  for (let x = 0; x < width; x++) {
    const from = x * sx;
    const to = (x + 1) * sx;
    for (let y = 0; y < grid.height; y++) {
      let total = 0;
      for (let i = Math.floor(from); i < Math.ceil(to); i++) {
        const cover = Math.min(to, i + 1) - Math.max(from, i);
        total += grid.values[y * grid.width + Math.min(i, grid.width - 1)]! * cover;
      }
      across.values[y * width + x] = total / sx;
    }
  }
  const out = makeGrid(width, height);
  const sy = grid.height / height;
  for (let y = 0; y < height; y++) {
    const from = y * sy;
    const to = (y + 1) * sy;
    for (let x = 0; x < width; x++) {
      let total = 0;
      for (let j = Math.floor(from); j < Math.ceil(to); j++) {
        const cover = Math.min(to, j + 1) - Math.max(from, j);
        total += across.values[Math.min(j, grid.height - 1) * width + x]! * cover;
      }
      out.values[y * width + x] = total / sy;
    }
  }
  return out;
}

/** Erode (min) or dilate (max) with a square of side 2 radius + 1; pixels outside are ignored. */
export function squareFilter(grid: Grid, radius: number, mode: "min" | "max"): Grid {
  const { width, height } = grid;
  const pick = mode === "min" ? Math.min : Math.max;
  const across = makeGrid(width, height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let best = grid.values[y * width + x]!;
      for (let i = Math.max(0, x - radius); i <= Math.min(width - 1, x + radius); i++) best = pick(best, grid.values[y * width + i]!);
      across.values[y * width + x] = best;
    }
  }
  const out = makeGrid(width, height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let best = across.values[y * width + x]!;
      for (let j = Math.max(0, y - radius); j <= Math.min(height - 1, y + radius); j++) best = pick(best, across.values[j * width + x]!);
      out.values[y * width + x] = best;
    }
  }
  return out;
}

function reflect101(index: number, size: number): number {
  if (size <= 1) return 0;
  const period = 2 * size - 2;
  let i = index % period;
  if (i < 0) i += period;
  return i < size ? i : period - i;
}

/** `odd_reflect_pad`: pad by point reflection (`2 edge - mirror`), so a ramp stays a ramp. */
export function oddReflectPad(grid: Grid, radius: number): Grid {
  const width = grid.width + 2 * radius;
  const height = grid.height + 2 * radius;
  const out = makeGrid(width, height);
  for (let y = 0; y < height; y++) {
    const sy = y - radius;
    const my = reflect101(sy, grid.height);
    const ey = Math.min(Math.max(sy, 0), grid.height - 1);
    for (let x = 0; x < width; x++) {
      const sx = x - radius;
      const mx = reflect101(sx, grid.width);
      const ex = Math.min(Math.max(sx, 0), grid.width - 1);
      out.values[y * width + x] = 2 * grid.values[ey * grid.width + ex]! - grid.values[my * grid.width + mx]!;
    }
  }
  return out;
}

/** `cv2.GaussianBlur` of a float grid (kernel size `round(8 sigma + 1) | 1`, BORDER_REFLECT_101). */
export function gaussianGrid(grid: Grid, sigma: number): Grid {
  const size = Math.round(sigma * 8 + 1) | 1;
  const radius = (size - 1) / 2;
  const kernel: number[] = [];
  let total = 0;
  for (let i = -radius; i <= radius; i++) {
    const w = Math.exp(-(i * i) / (2 * sigma * sigma));
    kernel.push(w);
    total += w;
  }
  for (let i = 0; i < kernel.length; i++) kernel[i]! /= total;
  const { width, height } = grid;
  const across = makeGrid(width, height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let sum = 0;
      for (let i = -radius; i <= radius; i++) sum += kernel[i + radius]! * grid.values[y * width + reflect101(x + i, width)]!;
      across.values[y * width + x] = sum;
    }
  }
  const out = makeGrid(width, height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let sum = 0;
      for (let j = -radius; j <= radius; j++) sum += kernel[j + radius]! * across.values[reflect101(y + j, height) * width + x]!;
      out.values[y * width + x] = sum;
    }
  }
  return out;
}

/** 8-connected labels of a 0/1 mask (0 = background). */
export function labelComponents(mask: Uint8Array, width: number, height: number): Int32Array {
  const labels = new Int32Array(width * height);
  const stack: number[] = [];
  let next = 0;
  for (let start = 0; start < mask.length; start++) {
    if (!mask[start] || labels[start]) continue;
    next += 1;
    labels[start] = next;
    stack.push(start);
    while (stack.length) {
      const index = stack.pop()!;
      const x = index % width;
      const y = (index - x) / width;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx;
          const ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
          const neighbour = ny * width + nx;
          if (mask[neighbour] && !labels[neighbour]) {
            labels[neighbour] = next;
            stack.push(neighbour);
          }
        }
      }
    }
  }
  return labels;
}

function smoothstep(edge0: number, edge1: number, x: number): number {
  const u = Math.min(Math.max((x - edge0) / Math.max(edge1 - edge0, 1e-9), 0), 1);
  return u * u * (3 - 2 * u);
}

/** The depth over the working crop as a grid with a short side of `short` cells. */
export function cropGrid(depth: DepthMap, crop: Rect, image: [number, number], short = GRID_SHORT_SIDE): Grid {
  const [cx, cy, cw, ch] = crop;
  const scale = Math.min(1e9, short / Math.max(Math.min(cw, ch), 1));
  const width = Math.max(8, Math.round(cw * scale));
  const height = Math.max(8, Math.round(ch * scale));
  const out = makeGrid(width, height);
  const source: Grid = { values: depth.values, width: depth.width, height: depth.height };
  const fx = depth.width / image[0];
  const fy = depth.height / image[1];
  for (let y = 0; y < height; y++) {
    const py = cy + ((y + 0.5) * ch) / height;
    for (let x = 0; x < width; x++) {
      const px = cx + ((x + 0.5) * cw) / width;
      out.values[y * width + x] = Math.min(Math.max(sampleGrid(source, px * fx - 0.5, py * fy - 0.5), 0), 1);
    }
  }
  return out;
}

export interface LayerAnalysis {
  envelope: Grid;
  /** front coverage 0..1 on the grid; null = one layer */
  alpha: Grid | null;
  backDepth: Grid;
  frontDepth: Grid | null;
}

/** `analyse_depth` + the depths of `prepare_layers` on a grid of the working crop. */
export function analyseDepth(low: Grid): LayerAnalysis {
  const { width, height } = low;
  const short = Math.min(width, height);
  const half = resizeArea(low, Math.max(Math.floor(width / 2), 4), Math.max(Math.floor(height / 2), 4));
  const radius = Math.max(2, Math.round(OBJECT_SCALE * Math.min(half.width, half.height)));
  const padded = oddReflectPad(half, radius);
  const openedPadded = squareFilter(squareFilter(padded, radius, "min"), radius, "max");
  const opened = makeGrid(half.width, half.height);
  for (let y = 0; y < half.height; y++) {
    for (let x = 0; x < half.width; x++) opened.values[y * half.width + x] = openedPadded.values[(y + radius) * padded.width + x + radius]!;
  }
  const smooth = gaussianGrid(opened, Math.max(radius / 4, 1));
  const upper = makeGrid(half.width, half.height);
  for (let i = 0; i < upper.values.length; i++) upper.values[i] = Math.max(opened.values[i]!, smooth.values[i]!);
  const envelope = resizeLinear(upper, width, height);
  for (let i = 0; i < envelope.values.length; i++) envelope.values[i] = Math.min(envelope.values[i]!, low.values[i]!);
  const edgeRadius = Math.max(1, Math.round(0.01 * short));
  const high = squareFilter(low, edgeRadius, "max");
  const lowest = squareFilter(low, edgeRadius, "min");
  const candidate = new Uint8Array(width * height);
  const tophat = new Float32Array(width * height);
  for (let i = 0; i < candidate.length; i++) {
    tophat[i] = low.values[i]! - envelope.values[i]!;
    candidate[i] = tophat[i]! > LAYER_THRESHOLD ? 1 : 0;
  }
  // thin slivers are not objects: open the mask with a 5 x 5 square
  const opening = squareFilter(squareFilter({ values: Float32Array.from(candidate), width, height }, 2, "min"), 2, "max");
  let any = false;
  for (let i = 0; i < candidate.length; i++) {
    candidate[i] = opening.values[i]! > 0.5 ? 1 : 0;
    if (candidate[i]) any = true;
  }
  let alpha: Grid | null = null;
  if (any) {
    const labels = labelComponents(candidate, width, height);
    const keepIds = new Set<number>();
    for (let i = 0; i < labels.length; i++) {
      if (labels[i] && high.values[i]! - lowest.values[i]! > EDGE_THRESHOLD) keepIds.add(labels[i]!);
    }
    if (keepIds.size) {
      const keep = makeGrid(width, height);
      for (let i = 0; i < labels.length; i++) keep.values[i] = keepIds.has(labels[i]!) ? 1 : 0;
      const grown = squareFilter(keep, edgeRadius + 1, "max");
      alpha = makeGrid(width, height);
      let peak = 0;
      for (let i = 0; i < alpha.values.length; i++) {
        alpha.values[i] = smoothstep(0.5 * LAYER_THRESHOLD, 1.5 * LAYER_THRESHOLD, tophat[i]!) * grown.values[i]!;
        peak = Math.max(peak, alpha.values[i]!);
      }
      if (peak < 0.5) alpha = null;
    }
  }
  if (!alpha) return { envelope, alpha: null, backDepth: gaussianGrid(low, 1), frontDepth: null };
  const back = makeGrid(width, height);
  for (let i = 0; i < back.values.length; i++) back.values[i] = low.values[i]! - alpha.values[i]! * (low.values[i]! - envelope.values[i]!);
  return { envelope, alpha, backDepth: gaussianGrid(back, 1), frontDepth: frontDepth(low, alpha) };
}

/** `front_depth`: the object's own depth inside it, carried 8 % of the short side past its edge. */
export function frontDepth(low: Grid, alpha: Grid): Grid {
  const short = Math.min(low.width, low.height);
  const rim = Math.max(1, Math.round(0.015 * short));
  const flat = squareFilter(low, rim, "max");
  const inside = makeGrid(low.width, low.height);
  for (let i = 0; i < inside.values.length; i++) inside.values[i] = alpha.values[i]! > 0.5 ? flat.values[i]! : 0;
  const carried = squareFilter(inside, Math.max(rim + 1, Math.round(FRONT_REACH * short)), "max");
  const out = makeGrid(low.width, low.height);
  for (let i = 0; i < out.values.length; i++) out.values[i] = alpha.values[i]! > 0.5 ? flat.values[i]! : carried.values[i]!;
  return out;
}

// The plates (colours of the two layers) ---------------------------------------------------------

/** Fills the masked pixels from the ones around them (a push-pull pyramid, standing in for TELEA). */
export function pushPullFill(rgb: Float32Array, known: Float32Array, width: number, height: number): void {
  const levels: { c: Float32Array; w: Float32Array; width: number; height: number }[] = [{ c: rgb, w: known, width, height }];
  while (levels[levels.length - 1]!.width > 1 || levels[levels.length - 1]!.height > 1) {
    const fine = levels[levels.length - 1]!;
    const cw = Math.max(1, Math.ceil(fine.width / 2));
    const chh = Math.max(1, Math.ceil(fine.height / 2));
    const c = new Float32Array(cw * chh * 3);
    const w = new Float32Array(cw * chh);
    for (let y = 0; y < chh; y++) {
      for (let x = 0; x < cw; x++) {
        let sum = 0;
        const acc = [0, 0, 0];
        for (let dy = 0; dy < 2; dy++) {
          for (let dx = 0; dx < 2; dx++) {
            const fx = Math.min(2 * x + dx, fine.width - 1);
            const fy = Math.min(2 * y + dy, fine.height - 1);
            const k = fy * fine.width + fx;
            const weight = fine.w[k]!;
            sum += weight;
            acc[0]! += weight * fine.c[k * 3]!;
            acc[1]! += weight * fine.c[k * 3 + 1]!;
            acc[2]! += weight * fine.c[k * 3 + 2]!;
          }
        }
        const k = y * cw + x;
        if (sum > 1e-6) {
          c[k * 3] = acc[0]! / sum;
          c[k * 3 + 1] = acc[1]! / sum;
          c[k * 3 + 2] = acc[2]! / sum;
        }
        w[k] = Math.min(1, sum);
      }
    }
    levels.push({ c, w, width: cw, height: chh });
  }
  for (let level = levels.length - 2; level >= 0; level--) {
    const fine = levels[level]!;
    const coarse = levels[level + 1]!;
    for (let y = 0; y < fine.height; y++) {
      for (let x = 0; x < fine.width; x++) {
        const k = y * fine.width + x;
        const weight = fine.w[k]!;
        if (weight >= 1) continue;
        const gx = Math.min(Math.max((x + 0.5) / 2 - 0.5, 0), coarse.width - 1);
        const gy = Math.min(Math.max((y + 0.5) / 2 - 0.5, 0), coarse.height - 1);
        const x0 = Math.floor(gx);
        const y0 = Math.floor(gy);
        const x1 = Math.min(x0 + 1, coarse.width - 1);
        const y1 = Math.min(y0 + 1, coarse.height - 1);
        const ax = gx - x0;
        const ay = gy - y0;
        for (let ch = 0; ch < 3; ch++) {
          const top = coarse.c[(y0 * coarse.width + x0) * 3 + ch]! * (1 - ax) + coarse.c[(y0 * coarse.width + x1) * 3 + ch]! * ax;
          const bottom = coarse.c[(y1 * coarse.width + x0) * 3 + ch]! * (1 - ax) + coarse.c[(y1 * coarse.width + x1) * 3 + ch]! * ax;
          const filled = top * (1 - ay) + bottom * ay;
          fine.c[k * 3 + ch] = weight * fine.c[k * 3 + ch]! + (1 - weight) * filled;
        }
        fine.w[k] = 1;
      }
    }
  }
}

export interface Plates {
  /** the picture over the working crop with the near objects filled in */
  back: HTMLCanvasElement | OffscreenCanvas;
  /** the near objects (un-mixed colours) with their coverage as alpha */
  front: HTMLCanvasElement | OffscreenCanvas;
}

export function canvasOf(width: number, height: number): HTMLCanvasElement | OffscreenCanvas {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(width, height);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

/** The plates' size in pixels: the working crop of the decoded picture, at most `maxWidth` wide. */
export function plateSize(bitmap: { width: number; height: number }, image: [number, number], crop: Rect, maxWidth = 1280): [number, number] {
  const [, , cw, ch] = crop;
  const bx = bitmap.width / image[0];
  const by = bitmap.height / image[1];
  const scale = Math.min(1, maxWidth / Math.max(cw * bx, 1));
  return [Math.max(2, Math.round(cw * bx * scale)), Math.max(2, Math.round(ch * by * scale))];
}

/** The working crop of the decoded picture as RGBA pixels at `size` (OffscreenCanvas in a worker). */
export function cropPixels(bitmap: ImageBitmap, image: [number, number], crop: Rect, size: [number, number]): Uint8ClampedArray | null {
  const [cx, cy, cw, ch] = crop;
  const [width, height] = size;
  const bx = bitmap.width / image[0];
  const by = bitmap.height / image[1];
  const source = canvasOf(width, height);
  // a canvas read back at once: kept in memory, not on the GPU, and freed right after
  const ctx = source.getContext("2d", { willReadFrequently: true }) as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
  if (!ctx) return null;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bitmap, cx * bx, cy * by, cw * bx, ch * by, 0, 0, width, height);
  const pixels = ctx.getImageData(0, 0, width, height).data;
  source.width = 0;
  source.height = 0;
  return pixels;
}

export interface PlatePixels {
  width: number;
  height: number;
  /** RGBA, not premultiplied (as `getImageData` gives and `putImageData` takes) */
  back: Uint8ClampedArray;
  front: Uint8ClampedArray;
}

/**
 * The colours of the two layers from the crop's pixels (`fill_behind` and `unmix_front`): plain
 * arithmetic on arrays, so it runs in the parallax worker, off the main thread.
 */
export function platePixels(pixels: Uint8ClampedArray, width: number, height: number, alpha: Grid): PlatePixels {
  const count = width * height;
  const a = new Float32Array(count);
  const sx = alpha.width / width;
  const sy = alpha.height / height;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) a[y * width + x] = sampleGrid(alpha, (x + 0.5) * sx - 0.5, (y + 0.5) * sy - 0.5);
  }
  // fill_behind: the mask is every pixel the objects touch, grown a little
  const grow = Math.max(2, Math.round(0.004 * Math.min(width, height)));
  const touched = squareFilter({ values: Float32Array.from(a, (v) => (v > 0.02 ? 1 : 0)), width, height }, grow, "max");
  const rgb = new Float32Array(count * 3);
  const known = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    rgb[i * 3] = pixels[i * 4]!;
    rgb[i * 3 + 1] = pixels[i * 4 + 1]!;
    rgb[i * 3 + 2] = pixels[i * 4 + 2]!;
    known[i] = touched.values[i]! > 0.5 ? 0 : 1;
  }
  pushPullFill(rgb, known, width, height);
  const back = new Uint8ClampedArray(count * 4);
  const front = new Uint8ClampedArray(count * 4);
  for (let i = 0; i < count; i++) {
    const coverage = a[i]!;
    const weight = smoothstep(0.02, 0.1, coverage);
    for (let c = 0; c < 3; c++) {
      const picture = pixels[i * 4 + c]!;
      const plate = weight * rgb[i * 3 + c]! + (1 - weight) * picture;
      back[i * 4 + c] = plate;
      // unmix_front: picture = a front + (1 - a) plate
      const solved = coverage > 0.05 ? (picture - (1 - coverage) * plate) / Math.max(coverage, 0.05) : picture;
      front[i * 4 + c] = Math.min(Math.max(solved + 0.5, 0), 255);
    }
    back[i * 4 + 3] = 255;
    front[i * 4 + 3] = Math.round(Math.min(Math.max(coverage, 0), 1) * 255);
  }
  return { width, height, back, front };
}

/** The plates as canvases (the compositor's textures) from their pixels. */
export function platesFromPixels(plates: PlatePixels): Plates | null {
  const { width, height } = plates;
  const back = canvasOf(width, height);
  const front = canvasOf(width, height);
  // the compositor uploads them to its own textures: the canvases need not be on the GPU too
  const backCtx = back.getContext("2d", { willReadFrequently: true }) as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
  const frontCtx = front.getContext("2d", { willReadFrequently: true }) as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
  if (!backCtx || !frontCtx) return null;
  const backPixels = backCtx.createImageData(width, height);
  const frontPixels = frontCtx.createImageData(width, height);
  backPixels.data.set(plates.back);
  frontPixels.data.set(plates.front);
  backCtx.putImageData(backPixels, 0, 0);
  frontCtx.putImageData(frontPixels, 0, 0);
  return { back, front };
}

/** Frees the plates' canvases at once (they are big; the garbage collector may take a while). */
export function freePlates(plates: Plates): void {
  for (const canvas of [plates.back, plates.front]) {
    canvas.width = 0;
    canvas.height = 0;
  }
}

/** The back plate and the front layer of the working crop, at most `maxWidth` wide. */
export function buildPlates(
  bitmap: ImageBitmap,
  image: [number, number],
  crop: Rect,
  alpha: Grid,
  maxWidth = 1280,
): Plates | null {
  const size = plateSize(bitmap, image, crop, maxWidth);
  const pixels = cropPixels(bitmap, image, crop, size);
  return pixels ? platesFromPixels(platePixels(pixels, size[0], size[1], alpha)) : null;
}
