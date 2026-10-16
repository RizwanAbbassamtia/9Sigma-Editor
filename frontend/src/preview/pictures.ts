/**
 * The scene pictures (design 5.6): decoded with `createImageBitmap` at most 2x the preview size and
 * at most 1920 wide, 8 to 12 kept (the current scene +/- 3 and the next cut), loaded ahead of the
 * playhead. A picture that cannot be loaded is "missing" and the scene shows the slate. Depth maps
 * (2.5D parallax) are read the same way when the bundle names one. A load the playhead has left
 * behind (a jump far away) can be stopped while it is still downloading (`cancel`).
 */

export const MAX_DECODE_WIDTH = 1920;
/** The tallest picture the backend's thumbnail route makes (`/media/thumb?h=` is at most 2160). */
export const MAX_THUMB_HEIGHT = 2160;
export const MIN_CACHED = 8;
export const MAX_CACHED = 12;
/**
 * The heights pictures are decoded at (and the backend's sized JPEGs made at): a picture is asked
 * for at the step at or above twice the preview, so Half quality and nearby window sizes share
 * the same files, which the backend makes ahead of time (render/thumbs.py `PREVIEW_STEPS`).
 */
export const PICTURE_STEPS = [540, 720, 1080, 1440, 1920, 2160] as const;
/** A height up to 2 % over a step still uses that step (render/thumbs.py `STEP_TOLERANCE`). */
export const STEP_TOLERANCE = 1.02;

export interface Picture {
  path: string;
  bitmap: ImageBitmap;
  /** the picture's own size (the camera rectangles are in these pixels) */
  imageWidth: number;
  imageHeight: number;
  /** a version that changes when a new bitmap replaces the old one */
  version: number;
}

export interface DepthMap {
  /** depth 0..1 (1 = nearest), row-major */
  values: Float32Array;
  width: number;
  height: number;
}

export type PictureState = Picture | "missing" | null;

export interface PictureStoreOptions {
  /** URL of the picture at `height` pixels (the backend's cached JPEG), else the full file */
  pictureUrl?: (path: string, height: number) => string;
  fileUrl: (path: string) => string;
  fetch: typeof fetch;
  onLoad: (path: string) => void;
}

interface Entry {
  path: string;
  state: PictureState;
  /** decoded height asked for */
  height: number;
  loading: Promise<void> | null;
  /** stops the download of the load in progress (null once its bytes are in) */
  abort: AbortController | null;
  lastUsed: number;
  sizeHint: [number, number] | null;
}

/**
 * Where to read a picture needed `height` pixels tall, in order: the backend's sized JPEG when that
 * is tall enough (it goes up to 2160), else the file itself (decoded down to `height`), with the
 * tallest sized copy as the last resort.
 */
export function pictureUrls(
  path: string,
  height: number,
  pictureUrl: ((path: string, height: number) => string) | undefined,
  fileUrl: (path: string) => string,
): string[] {
  const wanted = Math.max(16, Math.round(height));
  const urls: string[] = [];
  if (pictureUrl && wanted <= MAX_THUMB_HEIGHT) urls.push(pictureUrl(path, wanted));
  urls.push(fileUrl(path));
  if (pictureUrl && wanted > MAX_THUMB_HEIGHT) urls.push(pictureUrl(path, MAX_THUMB_HEIGHT));
  return [...new Set(urls)];
}

/** `height` rounded up to the next of {@link PICTURE_STEPS}; over the last step it stays (the file is read). */
export function stepHeight(height: number): number {
  for (const step of PICTURE_STEPS) if (height <= step * STEP_TOLERANCE) return step;
  return height;
}

/**
 * How tall to decode a picture so the camera window never needs more than 2x the preview: twice
 * the preview over the camera's cover region, rounded up to a step, never taller than the picture
 * or wider than 1920 (render/thumbs.py `decode_height` makes the same sums to prepare the JPEGs).
 */
export function decodeHeight(imageSize: [number, number] | null, previewHeight: number, coverHeight: number | null): number {
  const [imageW, imageH] = imageSize ?? [0, 0];
  let height = previewHeight * 2;
  if (imageH > 0 && coverHeight && coverHeight > 0) height = (2 * previewHeight * imageH) / coverHeight;
  height = stepHeight(height);
  if (imageH > 0) height = Math.min(height, imageH);
  if (imageW > 0 && imageH > 0) height = Math.min(height, (MAX_DECODE_WIDTH * imageH) / imageW);
  return Math.max(16, Math.round(height));
}

export class PictureStore {
  private readonly entries = new Map<string, Entry>();
  private clock = 0;
  private version = 0;
  private destroyed = false;

  constructor(private readonly options: PictureStoreOptions) {}

  get(path: string): PictureState {
    const entry = this.entries.get(path);
    if (!entry) return null;
    entry.lastUsed = ++this.clock;
    return entry.state;
  }

  /** Asks for a picture decoded at about `height` pixels (a bigger one already held is kept). */
  want(path: string, height: number, sizeHint: [number, number] | null): void {
    if (this.destroyed || !path) return;
    let entry = this.entries.get(path);
    if (!entry) {
      entry = { path, state: null, height: 0, loading: null, abort: null, lastUsed: ++this.clock, sizeHint };
      this.entries.set(path, entry);
    }
    entry.lastUsed = ++this.clock;
    entry.sizeHint = sizeHint ?? entry.sizeHint;
    if (entry.loading) return;
    if (entry.state === "missing") return;
    if (entry.state && entry.height >= height / 1.25) return;
    entry.loading = this.load(entry, height);
  }

  /**
   * Stops the downloads of pictures that are not in `keep` and forgets them (asked for again, they
   * load from the start). A picture already downloaded is left to finish decoding.
   */
  cancel(keep: ReadonlySet<string>): void {
    for (const entry of [...this.entries.values()]) {
      if (!entry.abort || keep.has(entry.path)) continue;
      entry.abort.abort();
      entry.abort = null;
      if (entry.state === null) this.entries.delete(entry.path);
    }
  }

  /** The picture's bytes, from the first of `pictureUrls` that answers. */
  private async fetchPicture(path: string, height: number, signal: AbortSignal | undefined): Promise<Blob> {
    const urls = pictureUrls(path, height, this.options.pictureUrl, this.options.fileUrl);
    let problem: unknown = null;
    for (const url of urls) {
      try {
        const response = await this.options.fetch(url, { credentials: "same-origin", signal });
        if (response.ok) return await response.blob();
        problem = new Error(String(response.status));
      } catch (error) {
        problem = error;
      }
      if (signal?.aborted) break;
    }
    throw problem ?? new Error("not found");
  }

  private async load(entry: Entry, height: number): Promise<void> {
    const abort = typeof AbortController !== "undefined" ? new AbortController() : null;
    entry.abort = abort;
    const signal = abort?.signal;
    try {
      const blob = await this.fetchPicture(entry.path, height, signal);
      if (entry.abort === abort) entry.abort = null;
      let bitmap = await createImageBitmap(blob);
      const [hintW, hintH] = entry.sizeHint ?? [bitmap.width, bitmap.height];
      // a full-size file: decode it again at the size the preview needs
      if (bitmap.height > height * 1.1 || bitmap.width > MAX_DECODE_WIDTH) {
        const scale = Math.min(height / bitmap.height, MAX_DECODE_WIDTH / bitmap.width, 1);
        const resized = await createImageBitmap(bitmap, {
          resizeWidth: Math.max(1, Math.round(bitmap.width * scale)),
          resizeHeight: Math.max(1, Math.round(bitmap.height * scale)),
          resizeQuality: "high",
        });
        bitmap.close();
        bitmap = resized;
      }
      if (this.destroyed) {
        bitmap.close();
        return;
      }
      const old = entry.state;
      entry.state = {
        path: entry.path,
        bitmap,
        imageWidth: hintW > 0 ? hintW : bitmap.width,
        imageHeight: hintH > 0 ? hintH : bitmap.height,
        version: ++this.version,
      };
      entry.height = height;
      if (old && old !== "missing") old.bitmap.close();
    } catch {
      // a stopped download is not a missing picture: it is simply not asked for any more
      if (signal?.aborted) return;
      if (!this.destroyed && !entry.state) entry.state = "missing";
    } finally {
      entry.loading = null;
      if (entry.abort === abort) entry.abort = null;
    }
    if (!this.destroyed) this.options.onLoad(entry.path);
  }

  /** Keeps the pictures in `keep` (and the most recently used up to 12); closes the rest. */
  trim(keep: ReadonlySet<string>): string[] {
    const held = [...this.entries.values()].filter((entry) => entry.state !== null && entry.state !== "missing");
    const kept = held.filter((entry) => keep.has(entry.path)).length;
    const others = held.filter((entry) => !keep.has(entry.path)).sort((a, b) => b.lastUsed - a.lastUsed);
    const room = Math.max(MAX_CACHED - kept, 0);
    const removed: string[] = [];
    for (const entry of others.slice(room)) {
      if (entry.state && entry.state !== "missing") entry.state.bitmap.close();
      this.entries.delete(entry.path);
      removed.push(entry.path);
    }
    return removed;
  }

  /** Forgets everything about one picture (it changed on disk). */
  invalidate(path: string): void {
    const entry = this.entries.get(path);
    if (!entry) return;
    if (entry.state && entry.state !== "missing") entry.state.bitmap.close();
    this.entries.delete(path);
  }

  heldCount(): number {
    let count = 0;
    for (const entry of this.entries.values()) if (entry.state && entry.state !== "missing") count += 1;
    return count;
  }

  destroy(): void {
    this.destroyed = true;
    for (const entry of this.entries.values()) {
      entry.abort?.abort();
      if (entry.state && entry.state !== "missing") entry.state.bitmap.close();
    }
    this.entries.clear();
  }
}

/**
 * Where the renderer keeps a picture's depth map: `06_images/scene_03.png` ->
 * `06_images/scene_03.depth.png` (render/parallax/depth.py `depth_map_path`); null for a URL.
 */
export function depthMapPath(image: string): string | null {
  if (!image || /^(https?:|blob:|data:)/i.test(image)) return null;
  const slash = Math.max(image.lastIndexOf("/"), image.lastIndexOf("\\"));
  const name = image.slice(slash + 1);
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  return stem ? `${image.slice(0, slash + 1)}${stem}.depth.png` : null;
}

/** The depth map's bytes, or null when there is none (the scene stays a Ken Burns move). */
export async function fetchDepthMap(url: string, fetchFn: typeof fetch): Promise<Blob | null> {
  try {
    const response = await fetchFn(url, { credentials: "same-origin" });
    if (!response.ok) return null;
    return await response.blob();
  } catch {
    return null;
  }
}

/**
 * A depth map (16-bit PNG, white = near) as a small grid of 0..1 values. Works in a worker too
 * (OffscreenCanvas), where the parallax analysis runs.
 */
export async function decodeDepthMap(blob: Blob, width = 512): Promise<DepthMap | null> {
  try {
    const bitmap = await createImageBitmap(blob);
    const height = Math.max(2, Math.round((width * bitmap.height) / Math.max(bitmap.width, 1)));
    const canvas = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(width, height) : Object.assign(document.createElement("canvas"), { width, height });
    const ctx = canvas.getContext("2d", { willReadFrequently: true }) as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
    if (!ctx) {
      bitmap.close();
      return null;
    }
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bitmap, 0, 0, width, height);
    bitmap.close();
    const pixels = ctx.getImageData(0, 0, width, height).data;
    canvas.width = 0;
    canvas.height = 0;
    const values = new Float32Array(width * height);
    for (let i = 0; i < values.length; i++) values[i] = pixels[i * 4]! / 255;
    return { values, width, height };
  } catch {
    return null;
  }
}

/** Reads a depth map (16-bit PNG, white = near) into a small grid of 0..1 values. */
export async function loadDepthMap(url: string, fetchFn: typeof fetch, width = 512): Promise<DepthMap | null> {
  const blob = await fetchDepthMap(url, fetchFn);
  return blob ? decodeDepthMap(blob, width) : null;
}
