/**
 * The live preview engine (docs/M5-CONTRACT.md section 4; docs/design/PREVIEW-ENGINE.md).
 *
 *     const preview = createPreview(canvas, bundle, options);
 *     preview.on("time", (seconds) => ...);
 *     await preview.play();
 *
 * `createPreview(canvas, bundle, options)` returns `{play, pause, seek, setQuality, setLoop, skim,
 * destroy, on, update, ...}` at once and gets ready in the background (WebGL2 through PixiJS 8,
 * else Canvas 2D). It draws exactly the frames the renderer makes (`n = floor(t x fps + 1e-6)`,
 * the frame layout of render/assembly.py), plays the voice and the music stem on the audio clock,
 * and reports its state with the same events the React wrapper listens to (`time`, `play`,
 * `pause`, `ended`, `status`, `error`). Framework-free: no React in here.
 */
import { AudioEngine } from "./audio";
import { Canvas2DCompositor } from "./canvas2d/compositor";
import { simplifiedIn2D } from "./canvas2d/transitions";
import { AutoQuality, FramePacer, PlaybackClock, frameNumber, requestFrame } from "./clock";
import type { Compositor, DrawInput, Overlay, SceneSource, SlateImage } from "./compositor";
import { buildDescription, cueAt, defaultFileUrl, frameAt, scenePositionAt, setImageSize, type DescriptionOptions } from "./description";
import { GpuCompositor } from "./gpu/compositor";
import { ParallaxStore, type ParallaxCounts } from "./parallaxJobs";
import { PictureStore, decodeHeight, depthMapPath, fetchDepthMap } from "./pictures";
import {
  FontLibrary,
  PopupLibrary,
  captionEvents,
  captionLook,
  eventAt,
  fontUrl,
  paintCaption,
  paintSlate,
  popScale,
  popupFrame,
  popupReservedPx,
  type CaptionEvent,
  type LoadedFont,
} from "./text";
import type {
  ActiveQuality,
  CueDesc,
  FrameState,
  LoopRange,
  PreviewBundle,
  PreviewEventName,
  PreviewEvents,
  PreviewHandle,
  PreviewOptions,
  PreviewStats,
  PreviewStatus,
  Quality,
  RenderDescription,
  SceneDesc,
} from "./types";

export type { PreviewBundle, PreviewHandle, PreviewOptions, PreviewEvents, Quality, LoopRange } from "./types";
export { buildDescription, frameAt } from "./description";

/** Full quality is the monitor's size x devicePixelRatio, at most this many pixels. */
export const FULL_MAX: [number, number] = [1280, 720];
/** A lost WebGL context that does not come back within this time switches to Canvas 2D. */
export const RESTORE_WAIT_MS = 3000;
/**
 * While paused, a picture loaded ahead of the playhead goes to the GPU only after this long
 * without a seek or a skim: an upload (and its mipmaps) keeps a laptop GPU busy for up to about
 * 100 ms, and a frame someone has just asked for would wait for it.
 */
export const UPLOAD_QUIET_MS = 300;
/**
 * The longest a press of Play holds the picture on its frame while the sound under the playhead
 * is decoded, so the picture and the sound start together and no sentence loses its first words
 * (docs/M5-CONTRACT.md 8.6.2 and 8.7.4). A sound device that is not running yet (the preview was
 * idle for a minute) is waited for up to `DEVICE_WAIT_MS` (audio.ts).
 */
export const PLAY_HOLD_MS = 300;
/** A seek or a skim while paused loads the sound there once the playhead has rested this long. */
export const AUDIO_PRIME_MS = 80;
const STATS_EVERY_MS = 500;

type Handler = (...args: never[]) => void;

interface Slate {
  canvas: HTMLCanvasElement | OffscreenCanvas;
  /** the scene number drawn on it (-1: none yet) */
  number: number;
  /** the font it was drawn with ("" before the font is in) */
  font: string;
  version: number;
}

function makeCanvas(width: number, height: number): HTMLCanvasElement | OffscreenCanvas {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(Math.max(1, width), Math.max(1, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, width);
  canvas.height = Math.max(1, height);
  return canvas;
}

/** The drawing size for a quality level: the monitor in device pixels, the video's aspect, capped. */
export function drawingSize(
  monitor: [number, number],
  dpr: number,
  video: [number, number],
  level: ActiveQuality,
  cap: [number, number] = FULL_MAX,
): [number, number] {
  const aspect = video[0] / Math.max(video[1], 1);
  const portrait = aspect < 1;
  const [capLong, capShort] = [Math.max(cap[0], cap[1]), Math.min(cap[0], cap[1])];
  const maxW = portrait ? capShort : capLong;
  const maxH = portrait ? capLong : capShort;
  let width = monitor[0] > 0 ? monitor[0] * dpr : maxW;
  let height = monitor[1] > 0 ? monitor[1] * dpr : maxH;
  if (width / height > aspect) width = height * aspect;
  else height = width / aspect;
  if (width > maxW) {
    width = maxW;
    height = width / aspect;
  }
  if (height > maxH) {
    height = maxH;
    width = height * aspect;
  }
  if (level === "half") {
    width /= 2;
    height /= 2;
  }
  // even sizes keep the frame centre on a pixel boundary
  return [Math.max(2, Math.round(width / 2) * 2), Math.max(2, Math.round(height / 2) * 2)];
}

class PreviewEngine implements PreviewHandle {
  readonly ready: Promise<void>;
  /** resolves once a compositor is chosen (WebGL2 or Canvas 2D) */
  private readonly started: Promise<void>;
  private resolveStarted!: () => void;
  private desc: RenderDescription;
  private bundle: PreviewBundle;
  private options: PreviewOptions;
  private readonly handlers = new Map<PreviewEventName, Set<Handler>>();
  private compositor: Compositor | null = null;
  private canvas: HTMLCanvasElement;
  private replacement: HTMLCanvasElement | null = null;
  private readonly fetchFn: typeof fetch;
  private readonly pictures: PictureStore;
  /** the 2.5D parallax plans of the scenes around the playhead (worked out by a worker) */
  private readonly parallax: ParallaxStore;
  private readonly fonts: FontLibrary;
  private captionFont: LoadedFont | null = null;
  private popupFont: LoadedFont | null = null;
  private readonly popups: PopupLibrary;
  private readonly audio: AudioEngine | null;
  private readonly clock: PlaybackClock;
  private readonly pacer = new FramePacer();
  private readonly auto = new AutoQuality();
  private quality: Quality;
  private active: ActiveQuality = "full";
  private loop: LoopRange | null;
  private scrubAudio: boolean;
  private playhead = 0;
  private skimTime: number | null = null;
  private playing = false;
  private destroyed = false;
  private cancelFrame: (() => void) | null = null;
  private drawQueued = false;
  private lastDrawn: { frame: number; skim: boolean } | null = null;
  private lastTimeEvent = -1;
  private monitor: [number, number] = [0, 0];
  private observer: ResizeObserver | null = null;
  private caption: { canvas: HTMLCanvasElement | OffscreenCanvas; key: string; version: number; cueKey: string; events: CaptionEvent[]; offsetY: number } | null = null;
  /** one "Picture missing" slate per scene slot, redrawn when the slot's scene changes */
  private readonly slates: [Slate | null, Slate | null] = [null, null];
  private status: PreviewStatus;
  private restoreTimer: ReturnType<typeof setTimeout> | null = null;
  private seekStarted: number | null = null;
  private stat: PreviewStats;
  private lastStats = 0;
  private lastRaf = 0;
  private readonly created = typeof performance !== "undefined" ? performance.now() : Date.now();
  private firstFrameDone = false;
  private resolveReady!: () => void;
  /** transitions whose shaders are still to be compiled while the preview is idle */
  private warmQueue: string[] = [];
  private warmTimer: ReturnType<typeof setTimeout> | null = null;
  /** pictures (and parallax plates) whose textures go to the GPU one at a time */
  private uploadQueue: { key: string; run: () => void }[] = [];
  private uploadTimer: ReturnType<typeof setTimeout> | null = null;
  /** performance.now() of the last seek or skim (uploads wait while someone is seeking) */
  private lastSeekAt = 0;
  /** bumped by every play, pause and seek while playing: a held start that is no longer wanted stops */
  private playToken = 0;
  /** Play was pressed and the picture waits on its frame for the sound (at most PLAY_HOLD_MS) */
  private holding = false;
  private primeTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(canvas: HTMLCanvasElement, bundle: PreviewBundle, options: PreviewOptions) {
    this.canvas = canvas;
    this.bundle = bundle;
    this.options = options;
    this.fetchFn = options.fetch ?? ((input, init) => fetch(input, init));
    this.quality = options.quality ?? "auto";
    this.loop = options.loop ?? null;
    this.scrubAudio = options.scrubAudio ?? false;
    this.desc = buildDescription(bundle, this.descriptionOptions());
    this.status = {
      renderer: "webgl2",
      restarting: false,
      simplified: false,
      mockMedia: Boolean(options.mockMedia || this.desc.mock),
      activeQuality: "full",
    };
    this.stat = { dropped: 0, shown: 0, lastFrameMs: 0, lastSeekMs: 0, firstFrameMs: 0, renderer: "none", quality: "full", width: 0, height: 0, textures: 0 };
    const projectId = this.projectId();
    this.pictures = new PictureStore({
      pictureUrl: options.pictureUrl,
      fileUrl: (path) => defaultFileUrl(projectId, path),
      fetch: this.fetchFn,
      onLoad: (path) => this.pictureLoaded(path),
    });
    this.parallax = new ParallaxStore({
      fetchDepth: (url) => fetchDepthMap(url, this.fetchFn),
      context: () => ({ render: [this.desc.width, this.desc.height], fps: this.desc.fps, limit: this.desc.settings.maxOverscan }),
      canRun: () => !this.playing,
      onReady: (position, plan, plates) => {
        // the plates' textures go to the GPU ahead of the first frame that shows them
        if (plates) this.queueUpload({ key: `plates:${position}`, run: () => this.compositor?.preloadPlates?.(plan) });
        this.requestDraw(true);
      },
      release: (plan) => this.compositor?.releasePlates?.(plan),
    });
    this.fonts = new FontLibrary(this.fetchFn);
    this.popups = new PopupLibrary(options.popupPreview ?? null, this.fetchFn, () => this.requestDraw());
    this.audio = options.audio === false || typeof window === "undefined" ? null : new AudioEngine({ fetch: this.fetchFn, onError: (message) => this.emit("error", message) });
    this.audio?.setDescription(this.desc);
    this.clock = new PlaybackClock(this.audio);
    this.ready = new Promise<void>((resolve) => {
      this.resolveReady = resolve;
    });
    this.started = new Promise<void>((resolve) => {
      this.resolveStarted = resolve;
    });
    if (canvas.style) canvas.style.objectFit = "contain";
    this.observeSize();
    this.loadFonts();
    void this.start();
  }

  private projectId(): string {
    return this.options.projectId || this.bundle.timeline.project_id;
  }

  private descriptionOptions(): DescriptionOptions {
    const projectId = this.projectId();
    return {
      settings: this.options.settings,
      catalogue: this.options.catalogue ?? null,
      fileUrl: (_id, path) => defaultFileUrl(projectId, path),
      segmentUrl: this.options.voiceSegmentUrl ?? undefined,
      popupsEnabled: this.options.popupsEnabled,
    };
  }

  // Start-up ------------------------------------------------------------------------------------

  private async start(): Promise<void> {
    const [width, height] = this.size();
    const wanted = this.options.renderer ?? "auto";
    if (wanted !== "canvas2d") {
      try {
        this.compositor = await GpuCompositor.create(this.canvas, width, height, {
          onContextLost: () => this.contextLost(),
          onContextRestored: () => this.contextRestored(),
          onError: (message) => this.emit("error", message),
        });
      } catch {
        this.compositor = null;
      }
    }
    if (this.destroyed) {
      this.compositor?.destroy();
      return;
    }
    if (!this.compositor) this.useCanvas2D(false);
    this.updateStatus({ renderer: this.compositor?.kind ?? "canvas2d", simplified: this.isSimplified() });
    // the whole status once, so a listener knows where it stands from the start
    this.emit("status", { ...this.status });
    this.resolveStarted();
    this.requestDraw();
    this.scheduleWarmUp();
    // the sound device opens once the first frame is up, not on the first press of Play, and
    // the sound under the playhead is decoded so the first Play starts with it
    void this.ready.then(() => {
      setTimeout(() => {
        if (this.destroyed || this.playing) return;
        this.audio?.prepare();
        this.audio?.preload(this.playhead);
      }, 200);
    });
  }

  /**
   * Compiles the shaders of the video's transitions one at a time while the preview is paused
   * (nearest to the playhead first), so playing over a cut never waits for a first link.
   */
  private scheduleWarmUp(): void {
    const at = this.frameFor(this.playhead);
    const seen = new Set<string>();
    const cuts = [...this.desc.cuts].sort((a, b) => Math.abs(a.startFrame - at) - Math.abs(b.startFrame - at));
    // "" first: the sprite batch shader (captions, popups and fades are sprites)
    this.warmQueue = [""];
    for (const cut of cuts) {
      const id = cut.transition?.id;
      if (id && !seen.has(id)) {
        seen.add(id);
        this.warmQueue.push(id);
      }
    }
    this.queueWarm(300);
  }

  private queueWarm(delayMs: number): void {
    if (this.warmTimer !== null || !this.warmQueue.length || this.destroyed) return;
    this.warmTimer = setTimeout(() => {
      this.warmTimer = null;
      if (this.destroyed) return;
      // never while playing: a link then would stall the picture
      if (this.playing) {
        this.queueWarm(1000);
        return;
      }
      const id = this.warmQueue.shift();
      if (id !== undefined) this.compositor?.warm?.(id);
      this.queueWarm(40);
    }, delayMs);
  }

  private useCanvas2D(fromLoss: boolean): void {
    const old = this.compositor;
    this.compositor = null;
    try {
      old?.destroy();
    } catch {
      /* the context is gone */
    }
    let target = this.replacement ?? this.canvas;
    let ctx: CanvasRenderingContext2D | null = null;
    if (!fromLoss && !this.replacement) {
      try {
        ctx = target.getContext("2d");
      } catch {
        ctx = null;
      }
    }
    if (!ctx) {
      // a canvas that had a WebGL context cannot draw in 2D: put a fresh one in its place
      const fresh = document.createElement("canvas");
      fresh.className = this.canvas.className;
      fresh.style.cssText = this.canvas.style.cssText;
      for (const name of ["aria-hidden", "role", "aria-label", "data-testid"]) {
        const value = this.canvas.getAttribute(name);
        if (value !== null) fresh.setAttribute(name, value);
      }
      if (this.canvas.parentNode) this.canvas.replaceWith(fresh);
      else this.replacement?.replaceWith(fresh);
      this.replacement?.remove();
      this.replacement = fresh;
      target = fresh;
      if (this.observer) {
        this.observer.disconnect();
        this.observer.observe(fresh);
      }
    }
    try {
      this.compositor = new Canvas2DCompositor(target);
    } catch {
      this.compositor = null;
      this.emit("error", "This computer cannot draw the live preview. Watch the last render instead.");
    }
    this.updateStatus({ renderer: "canvas2d", restarting: false, simplified: this.isSimplified() });
  }

  private contextLost(): void {
    this.updateStatus({ restarting: true });
    if (this.restoreTimer !== null) clearTimeout(this.restoreTimer);
    this.restoreTimer = setTimeout(() => {
      this.restoreTimer = null;
      if (this.compositor instanceof GpuCompositor && this.compositor.contextLost) {
        this.useCanvas2D(true);
        this.requestDraw(true);
      }
    }, RESTORE_WAIT_MS);
  }

  private contextRestored(): void {
    if (this.restoreTimer !== null) clearTimeout(this.restoreTimer);
    this.restoreTimer = null;
    this.updateStatus({ restarting: false });
    this.requestDraw(true);
  }

  private isSimplified(): boolean {
    if (!(this.compositor instanceof Canvas2DCompositor)) return false;
    return this.desc.cuts.some((cut) => cut.transition && simplifiedIn2D(cut.transition)) || this.desc.scenes.some((scene) => scene.style === "parallax");
  }

  private updateStatus(change: Partial<PreviewStatus>): void {
    let changed = false;
    for (const [key, value] of Object.entries(change) as [keyof PreviewStatus, never][]) {
      if (this.status[key] !== value) {
        (this.status as unknown as Record<string, unknown>)[key] = value;
        changed = true;
      }
    }
    if (changed) this.emit("status", { ...this.status });
  }

  // Sizes ---------------------------------------------------------------------------------------

  private observeSize(): void {
    const measure = () => {
      const target = this.replacement ?? this.canvas;
      const width = target.clientWidth;
      const height = target.clientHeight;
      if (width !== this.monitor[0] || height !== this.monitor[1]) {
        this.monitor = [width, height];
        this.requestDraw(true);
      }
    };
    measure();
    if (typeof ResizeObserver !== "undefined") {
      this.observer = new ResizeObserver(measure);
      this.observer.observe(this.canvas);
    }
  }

  private size(): [number, number] {
    const dpr = typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 3) : 1;
    const level: ActiveQuality = this.quality === "half" ? "half" : this.quality === "full" ? "full" : this.active;
    return drawingSize(this.monitor, dpr, [this.desc.width, this.desc.height], level, this.options.maxSize ?? FULL_MAX);
  }

  // Fonts -----------------------------------------------------------------------------------------

  private loadFonts(): void {
    const fonts = this.bundle.media?.fonts;
    const captionFamily = this.desc.captionStyle.font ?? "Noto Sans";
    const captionUrl = fontUrl(captionFamily, fonts);
    if (captionUrl) {
      void this.fonts.load(captionFamily, captionUrl).then((font) => {
        if (this.destroyed || !font) return;
        this.captionFont = font;
        this.caption = null;
        this.requestDraw(true);
      });
    }
    const popupUrl = fontUrl("Noto Sans", fonts);
    if (popupUrl) {
      void this.fonts.load("Noto Sans", popupUrl).then((font) => {
        if (this.destroyed || !font) return;
        this.popupFont = font;
        this.requestDraw(true);
      });
    }
  }

  // Pictures --------------------------------------------------------------------------------------

  private pictureLoaded(path: string): void {
    const picture = this.pictures.get(path);
    if (picture && picture !== "missing") {
      this.desc.scenes.forEach((scene, position) => {
        if (scene.image === path && !scene.imageSize) setImageSize(this.desc, position, picture.imageWidth, picture.imageHeight);
      });
      // its texture goes to the GPU before the first frame that shows it (one picture at a time:
      // after a seek the neighbours arrive together, and uploading them all at once would hold up
      // the frame that was asked for)
      this.queueUpload({
        key: path,
        run: () => {
          const loaded = this.pictures.get(path);
          if (loaded && loaded !== "missing") this.compositor?.preload?.(loaded);
        },
      });
    }
    this.requestDraw(true);
  }

  private queueUpload(item: { key: string; run: () => void }): void {
    if (!this.uploadQueue.some((queued) => queued.key === item.key)) this.uploadQueue.push(item);
    if (this.uploadTimer === null) this.uploadTimer = setTimeout(this.uploadNext, 60);
  }

  private readonly uploadNext = (): void => {
    this.uploadTimer = null;
    if (this.destroyed) return;
    // while someone is seeking, the frames they ask for come first
    const quiet = performance.now() - this.lastSeekAt;
    if (!this.playing && quiet < UPLOAD_QUIET_MS) {
      this.uploadTimer = setTimeout(this.uploadNext, UPLOAD_QUIET_MS - quiet);
      return;
    }
    this.uploadQueue.shift()?.run();
    if (this.uploadQueue.length) this.uploadTimer = setTimeout(this.uploadNext, 40);
  };

  /** Where the renderer reads a parallax scene's depth map: the bundle's depth_url, else beside the picture. */
  private depthUrl(scene: SceneDesc): string | null {
    const picture = this.bundle.pictures?.find((item) => item.scene === scene.index && item.depth_url);
    if (picture?.depth_url) return picture.depth_url;
    const besidePicture = depthMapPath(scene.image);
    return besidePicture ? defaultFileUrl(this.projectId(), besidePicture) : null;
  }

  private wantScene(scene: SceneDesc, position: number, height: number, priority: number): void {
    const cover = scene.path.bounds ? scene.path.bounds[3] : null;
    this.pictures.want(scene.image, decodeHeight(scene.imageSize, height, cover), scene.imageSize);
    if (scene.style !== "parallax") return;
    // the plan (and the plates of near objects) are worked out by the parallax worker, never here
    const state = this.pictures.get(scene.image);
    this.parallax.want(position, scene, this.depthUrl(scene), state && state !== "missing" ? state : null, priority);
  }

  /**
   * Pictures for the frame being drawn, then (once those are in) the next few scenes and the one
   * before; the rest may go. After a jump the frame's own pictures load alone: the neighbours would
   * share the connection and the decoder with them, and downloads for scenes far from the new
   * place stop. Parallax plans and their plates are kept for the same scenes only.
   */
  private prefetch(state: FrameState, height: number): void {
    const scenes = this.desc.scenes;
    if (!scenes.length) return;
    const position = state.kind === "empty" ? 0 : scenes.indexOf(state.a.scene);
    const keep = new Set<string>();
    const keepPositions = new Set<number>();
    const add = (index: number, load: boolean, priority: number) => {
      const scene = scenes[index];
      if (!scene) return;
      keep.add(scene.image);
      keepPositions.add(index);
      if (load) this.wantScene(scene, index, height, priority);
    };
    if (state.kind === "cut") add(scenes.indexOf(state.b.scene), true, 0);
    add(position, true, 0);
    const ready = this.frameComplete(state);
    for (let step = 1; step <= 3; step++) add(position + step, ready, step);
    add(position - 1, ready, 2);
    if (!ready) this.pictures.cancel(keep);
    const removed = this.pictures.trim(keep);
    if (removed.length) this.compositor?.release(removed);
    this.parallax.trim(keepPositions);
    // the next cut's shader is compiled ahead of time
    const next = this.desc.cuts.find((cut) => cut.from >= position && cut.transition);
    if (next?.transition) this.compositor?.prepare?.(next.transition.id);
  }

  private sceneSource(scene: SceneDesc): SceneSource {
    const state = this.pictures.get(scene.image);
    const picture = state && state !== "missing" ? state : null;
    // a parallax scene is its Ken Burns move until its plan (and plates) are ready
    const plan = scene.style === "parallax" ? this.parallax.get(this.desc.scenes.indexOf(scene)) : null;
    return { picture, missing: state === "missing" || !scene.image, parallax: plan };
  }

  /** The slate of scene `sceneNumber` in `slot`: one canvas per slot, drawn again when its scene changes. */
  private slate(sceneNumber: number, slot: 0 | 1): SlateImage {
    const [width, height] = this.desc.aspect === "9:16" ? [540, 960] : [960, 540];
    const font = this.popupFont?.cssFamily ?? "";
    let held = this.slates[slot];
    if (!held || held.canvas.width !== width || held.canvas.height !== height) {
      held = { canvas: makeCanvas(width, height), number: -1, font: "", version: 0 };
      this.slates[slot] = held;
    }
    if (held.number !== sceneNumber || held.font !== font) {
      const ctx = held.canvas.getContext("2d") as CanvasRenderingContext2D | null;
      if (ctx) paintSlate(ctx, width, height, sceneNumber, this.popupFont);
      held.number = sceneNumber;
      held.font = font;
      held.version += 1;
    }
    return { canvas: held.canvas, version: held.version };
  }

  /** How many parallax plans and plates are held (tests and the parity harness). */
  parallaxCounts(): ParallaxCounts {
    return this.parallax.counts();
  }

  // Overlays ----------------------------------------------------------------------------------------

  private overlays(n: number, width: number, height: number): Overlay[] {
    const out: Overlay[] = [];
    const desc = this.desc;
    const frame: [number, number] = [desc.width, desc.height];
    const scale = width / desc.width;
    const reserved = popupReservedPx(desc.captionStyle, desc.cues.length > 0, desc.captionsEnabled, frame, desc.settings);
    for (const popup of desc.popups) {
      const t = n / desc.fps;
      // the backend's PNG is asked for a few seconds before the popup shows
      if (t < popup.startS - 3 || t > popup.endS + 0.5) continue;
      const art = this.popups.get(popup, desc.aspect, frame, this.popupFont, reserved);
      if (!art) continue;
      const artScale = art.frameHeight > 0 ? desc.height / art.frameHeight : 1;
      const state = popupFrame(popup, n, desc.fps, { width: desc.width, height: desc.height }, { x: art.x * artScale, y: art.y * artScale }, desc.settings);
      if (!state.visible || state.alpha <= 0) continue;
      out.push({
        key: `popup:${popup.key}`,
        version: 0,
        image: art.image,
        x: state.xPx * scale,
        y: state.yPx * scale,
        w: art.w * artScale * scale,
        h: art.h * artScale * scale,
        alpha: state.alpha,
        premultiplied: !art.approximate,
      });
    }
    const caption = this.captionOverlay(n / desc.fps, width, height);
    if (caption) out.push(caption);
    return out;
  }

  private captionOverlay(t: number, width: number, height: number): Overlay | null {
    const desc = this.desc;
    const cue = cueAt(desc, t);
    if (!cue) return null;
    let caption = this.caption;
    if (!caption || caption.cueKey !== cue.key) {
      const canvas = caption && caption.canvas.width === width && caption.canvas.height === height ? caption.canvas : makeCanvas(width, height);
      caption = { canvas, key: "", version: (caption?.version ?? 0) + 1, cueKey: cue.key, events: captionEvents(cue, desc.cueWords.get(cue.key), desc.captionStyle), offsetY: 0 };
      this.caption = caption;
    }
    const event = eventAt(caption.events, t);
    if (!event) return null;
    const look = captionLook(desc.captionStyle);
    const pop = popScale(event, t, look);
    const key = `${width}x${height}:${event.kind}:${event.active ?? -1}:${event.startS}:${pop.toFixed(4)}:${this.captionFont?.cssFamily ?? ""}`;
    if (caption.key !== key) {
      if (caption.canvas.width !== width || caption.canvas.height !== height) {
        caption.canvas = makeCanvas(width, height);
      }
      const ctx = caption.canvas.getContext("2d") as CanvasRenderingContext2D | null;
      if (!ctx) return null;
      ctx.clearRect(0, 0, width, height);
      // drawn with its first line on a whole pixel; the overlay moves it to its exact place
      caption.offsetY = paintCaption(ctx, width, height, { cue: cue as CueDesc, event, t, style: desc.captionStyle, aspect: desc.aspect, font: this.captionFont, settings: desc.settings }).offsetY;
      caption.key = key;
      caption.version += 1;
    }
    return { key: "caption", version: caption.version, image: caption.canvas, x: 0, y: caption.offsetY, w: width, h: height, alpha: 1, premultiplied: false };
  }

  // Drawing ---------------------------------------------------------------------------------------

  /** Draws one video frame now. */
  private drawFrame(n: number, skim: boolean): void {
    const compositor = this.compositor;
    if (!compositor || this.destroyed) return;
    const started = performance.now();
    const [width, height] = this.size();
    const state = frameAt(this.desc, n);
    this.prefetch(state, height);
    const input: DrawInput = {
      state,
      width,
      height,
      source: (scene) => this.sceneSource(scene),
      slate: (number, slot) => this.slate(number, slot),
      overlays: state.kind === "empty" ? [] : this.overlays(state.n, width, height),
    };
    try {
      compositor.draw(input);
    } catch (error) {
      if (compositor instanceof GpuCompositor) {
        this.useCanvas2D(true);
        this.requestDraw(true);
        return;
      }
      this.emit("error", `The preview could not draw this frame (${error instanceof Error ? error.message : "unknown problem"}).`);
      return;
    }
    const ms = performance.now() - started;
    this.lastDrawn = { frame: state.n, skim };
    this.stat.lastFrameMs = ms;
    this.stat.width = width;
    this.stat.height = height;
    this.stat.renderer = compositor.kind;
    this.stat.quality = this.status.activeQuality;
    this.stat.textures = this.pictures.heldCount();
    if (!this.firstFrameDone && state.kind !== "empty") {
      const ready = this.frameComplete(state);
      if (ready) {
        this.firstFrameDone = true;
        this.stat.firstFrameMs = performance.now() - this.created;
        this.resolveReady();
      }
    } else if (state.kind === "empty") {
      this.resolveReady();
    }
    if (this.seekStarted !== null && this.frameComplete(state)) {
      this.stat.lastSeekMs = performance.now() - this.seekStarted;
      this.seekStarted = null;
    }
    this.emit("frame", { frame: state.n, seconds: state.n / this.desc.fps, kind: state.kind });
    if (!skim && state.n !== this.lastTimeEvent) {
      this.lastTimeEvent = state.n;
      this.emit("time", state.n / this.desc.fps);
    }
  }

  /** True when the frame's parallax scenes have their plans and plates (or will have none). */
  private parallaxSettled(state: FrameState): boolean {
    if (state.kind === "empty") return true;
    const settled = (scene: SceneDesc) => {
      if (scene.style !== "parallax" || !scene.image || this.pictures.get(scene.image) === "missing") return true;
      return this.parallax.settled(this.desc.scenes.indexOf(scene));
    };
    return settled(state.a.scene) && (state.kind !== "cut" || settled(state.b.scene));
  }

  /** True when every picture the frame needs was there (not still loading). */
  private frameComplete(state: FrameState): boolean {
    if (state.kind === "empty") return true;
    // a scene without a picture yet (an animatic before the pictures stage) shows its slate at once
    const ready = (scene: SceneDesc) => !scene.image || this.pictures.get(scene.image) !== null;
    if (state.kind === "cut") return ready(state.a.scene) && ready(state.b.scene);
    return ready(state.a.scene);
  }

  /** Draws the current frame on the next animation frame (paused, skimming or after a change). */
  private requestDraw(force = false): void {
    if (this.destroyed) return;
    if (force) this.lastDrawn = null;
    if (this.playing || this.drawQueued) return;
    this.drawQueued = true;
    this.cancelFrame = requestFrame(() => {
      this.drawQueued = false;
      this.cancelFrame = null;
      if (this.playing || this.destroyed) return;
      const skim = this.skimTime !== null;
      const t = skim ? this.skimTime! : this.playhead;
      const n = this.frameFor(t);
      if (this.lastDrawn && this.lastDrawn.frame === n && this.lastDrawn.skim === skim) return;
      this.drawFrame(n, skim);
    });
  }

  private frameFor(t: number): number {
    const last = Math.max(this.desc.totalFrames - 1, 0);
    return Math.min(Math.max(frameNumber(t, this.desc.fps), 0), last);
  }

  private tick = (rafTime: number): void => {
    this.cancelFrame = null;
    if (!this.playing || this.holding || this.destroyed) return;
    // the playhead at this animation frame's own time, followed smoothly (even frame pacing)
    const t = this.clock.frameTime(rafTime);
    const end = this.desc.durationS;
    const loop = this.loop;
    if (loop && loop.out_s > loop.in_s && (t >= loop.out_s || t >= end)) {
      this.restartAt(loop.in_s);
      return;
    }
    if (t >= end) {
      this.playhead = Math.max(end - 1 / this.desc.fps, 0);
      this.stopPlaying();
      this.drawFrame(this.frameFor(this.playhead), false);
      this.emit("pause");
      this.emit("ended");
      return;
    }
    this.playhead = t;
    const n = this.pacer.next(t, this.desc.fps);
    if (n !== null && this.skimTime === null) {
      const before = performance.now();
      this.drawFrame(Math.min(n, Math.max(this.desc.totalFrames - 1, 0)), false);
      const cost = performance.now() - before;
      const interval = this.lastRaf ? rafTime - this.lastRaf : 0;
      if (this.quality === "auto") {
        const level = this.auto.report(Math.max(cost, interval > 45 ? interval : 0), before);
        if (level !== this.active) {
          this.active = level;
          this.updateStatus({ activeQuality: level });
        }
      }
    }
    this.lastRaf = rafTime;
    this.stat.dropped = this.pacer.dropped;
    this.stat.shown = this.pacer.shown;
    if (rafTime - this.lastStats > STATS_EVERY_MS) {
      this.lastStats = rafTime;
      this.emit("stats", { ...this.stat });
    }
    this.cancelFrame = requestFrame(this.tick);
  };

  /** A jump while playing (a seek, the loop): the old sound stops and the new place starts like a press of Play. */
  private restartAt(t: number): void {
    this.cancelFrame?.();
    this.cancelFrame = null;
    this.clock.stop();
    this.audio?.pause(t);
    void this.startAt(t);
  }

  /**
   * Starts playing at `t`: the picture holds on the frame at `t` until the sound under it is
   * decoded (at most PLAY_HOLD_MS) and the sound device runs (at most DEVICE_WAIT_MS; it is kept
   * running while the preview is in use), then the picture and the sound start together (the
   * audio clock leads from its first sample), so nothing of the sentence under the playhead is
   * skipped. A pause, a seek or another play meanwhile wins.
   */
  private async startAt(t: number): Promise<void> {
    const token = ++this.playToken;
    this.holding = true;
    this.playhead = t;
    this.pacer.reset();
    this.lastRaf = 0;
    const n = this.frameFor(t);
    if (!this.lastDrawn || this.lastDrawn.frame !== n || this.lastDrawn.skim) this.drawFrame(n, false);
    if (this.audio) {
      try {
        await this.audio.ready(t, PLAY_HOLD_MS);
      } catch {
        /* the picture starts without waiting longer */
      }
    }
    if (token !== this.playToken || !this.playing || this.destroyed) return;
    this.holding = false;
    this.clock.start(t);
    this.cancelFrame = requestFrame(this.tick);
    if (this.audio) {
      try {
        await this.audio.play(() => this.clock.time());
      } catch {
        /* the picture keeps the performance clock */
      }
    }
  }

  private stopPlaying(): void {
    this.playing = false;
    this.holding = false;
    this.clock.stop();
    this.audio?.pause(this.playhead);
    this.cancelFrame?.();
    this.cancelFrame = null;
  }

  /** Decodes the sound at `t` once the playhead has rested there (a seek or a skim while paused). */
  private primeAudio(t: number): void {
    if (!this.audio) return;
    if (this.primeTimer !== null) clearTimeout(this.primeTimer);
    this.primeTimer = setTimeout(() => {
      this.primeTimer = null;
      if (!this.destroyed && !this.playing) this.audio?.preload(t);
    }, AUDIO_PRIME_MS);
  }

  // The API ---------------------------------------------------------------------------------------

  async play(): Promise<void> {
    if (this.destroyed || this.playing) return;
    if (this.desc.totalFrames <= 0) return;
    if (this.playhead >= this.desc.durationS - 1 / this.desc.fps) this.playhead = 0;
    this.skimTime = null;
    this.playing = true;
    this.cancelFrame?.();
    this.cancelFrame = null;
    this.drawQueued = false;
    this.emit("play");
    await this.startAt(this.playhead);
  }

  pause(): void {
    if (!this.playing) return;
    // a start still held on its frame has not moved the playhead
    if (!this.holding) this.playhead = this.clock.time();
    this.playToken += 1;
    this.stopPlaying();
    this.emit("pause");
    this.requestDraw(true);
  }

  seek(seconds: number): void {
    if (this.destroyed || !Number.isFinite(seconds)) return;
    const end = Math.max(this.desc.durationS - 1 / this.desc.fps, 0);
    const t = Math.min(Math.max(seconds, 0), end);
    this.seekStarted = performance.now();
    this.lastSeekAt = this.seekStarted;
    this.playhead = t;
    if (this.playing) {
      this.restartAt(t);
      return;
    }
    // dragging the playhead: a 60 ms grain of the voice when "Scrub audio" is on
    if (this.scrubAudio) this.audio?.grain(t);
    // the sound there is decoded while paused, so a press of Play starts with it
    this.primeAudio(t);
    this.requestDraw();
  }

  setQuality(quality: Quality): void {
    if (quality !== "auto" && quality !== "full" && quality !== "half") return;
    this.quality = quality;
    this.auto.reset();
    this.active = quality === "half" ? "half" : "full";
    this.updateStatus({ activeQuality: this.active });
    this.requestDraw(true);
  }

  setLoop(range: LoopRange | null): void {
    this.loop = range && Number.isFinite(range.in_s) && Number.isFinite(range.out_s) && range.out_s > range.in_s ? { ...range } : null;
  }

  setScrubAudio(on: boolean): void {
    this.scrubAudio = on;
  }

  skim(seconds: number | null): void {
    if (this.destroyed) return;
    this.lastSeekAt = performance.now();
    if (seconds === null || !Number.isFinite(seconds)) {
      this.skimTime = null;
      this.requestDraw(true);
      return;
    }
    this.skimTime = Math.min(Math.max(seconds, 0), this.desc.durationS);
    if (this.scrubAudio && !this.playing) this.audio?.grain(this.skimTime);
    if (!this.playing) this.primeAudio(this.skimTime);
    if (this.playing) {
      this.drawFrame(this.frameFor(this.skimTime), true);
      return;
    }
    this.requestDraw();
  }

  on<E extends PreviewEventName>(event: E, handler: PreviewEvents[E]): () => void {
    let set = this.handlers.get(event);
    if (!set) {
      set = new Set();
      this.handlers.set(event, set);
    }
    set.add(handler as unknown as Handler);
    return () => set!.delete(handler as unknown as Handler);
  }

  private emit<E extends PreviewEventName>(event: E, ...args: Parameters<PreviewEvents[E]>): void {
    const set = this.handlers.get(event);
    if (!set) return;
    for (const handler of [...set]) {
      try {
        (handler as (...values: unknown[]) => void)(...args);
      } catch {
        /* one listener must not stop the others */
      }
    }
  }

  update(bundle: PreviewBundle): void {
    if (this.destroyed) return;
    const previousFonts = JSON.stringify(this.bundle.media?.fonts ?? []);
    const previousFont = this.desc.captionStyle.font;
    const previousRevision = this.desc.revision;
    this.bundle = bundle;
    const sizes = new Map(this.desc.scenes.map((scene) => [scene.image, scene.imageSize]));
    this.desc = buildDescription(bundle, this.descriptionOptions());
    this.desc.scenes.forEach((scene, position) => {
      const known = sizes.get(scene.image);
      if (!scene.imageSize && known) setImageSize(this.desc, position, known[0], known[1]);
    });
    // a new revision of the timeline starts the parallax over; the same one patched (a cut or a
    // popup changed in the review) keeps each scene's depth analysis and plates and fits its plan
    // again. Either way a depth map that was not there is asked for again (parallax switched on).
    if (this.desc.revision !== previousRevision) this.parallax.reset();
    else this.parallax.rebase();
    this.caption = null;
    this.audio?.setDescription(this.desc);
    this.scheduleWarmUp();
    this.updateStatus({ mockMedia: Boolean(this.options.mockMedia || this.desc.mock), simplified: this.isSimplified() });
    if (JSON.stringify(bundle.media?.fonts ?? []) !== previousFonts || this.desc.captionStyle.font !== previousFont) this.loadFonts();
    const end = Math.max(this.desc.durationS - 1 / this.desc.fps, 0);
    if (this.playhead > end) this.seek(end);
    this.requestDraw(true);
  }

  currentTime(): number {
    return this.playing && !this.holding ? this.clock.time() : this.playhead;
  }

  duration(): number {
    return this.desc.durationS;
  }

  isPlaying(): boolean {
    return this.playing;
  }

  stats(): PreviewStats {
    return { ...this.stat, dropped: this.pacer.dropped, shown: this.pacer.shown };
  }

  description(): RenderDescription {
    return this.desc;
  }

  /** Draws the frame at `seconds` once its pictures and fonts are in (the parity harness). */
  async renderFrame(seconds: number): Promise<void> {
    await this.started;
    const n = this.frameFor(seconds);
    const deadline = performance.now() + 10000;
    for (;;) {
      const state = frameAt(this.desc, n);
      const [, height] = this.size();
      this.prefetch(state, height);
      const fontsReady = this.captionFont !== null || !this.bundle.media?.fonts?.length;
      const settled = this.frameComplete(state) && fontsReady && !this.popups.isDrawing() && this.parallaxSettled(state);
      if (settled || performance.now() > deadline) break;
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    this.playhead = n / this.desc.fps;
    this.drawFrame(n, false);
  }

  /** The pixels on screen (the parity harness reads frames with it). */
  snapshot(): ImageData | null {
    const compositor = this.compositor as (Compositor & { snapshot?: () => ImageData | null }) | null;
    return compositor?.snapshot?.() ?? null;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.playing = false;
    this.cancelFrame?.();
    this.cancelFrame = null;
    if (this.restoreTimer !== null) clearTimeout(this.restoreTimer);
    if (this.warmTimer !== null) clearTimeout(this.warmTimer);
    this.warmTimer = null;
    if (this.uploadTimer !== null) clearTimeout(this.uploadTimer);
    this.uploadTimer = null;
    if (this.primeTimer !== null) clearTimeout(this.primeTimer);
    this.primeTimer = null;
    this.observer?.disconnect();
    this.audio?.destroy();
    this.popups.destroy();
    this.parallax.destroy();
    this.pictures.destroy();
    try {
      this.compositor?.destroy();
    } catch {
      /* gone */
    }
    this.compositor = null;
    this.replacement?.remove();
    this.handlers.clear();
  }
}

/**
 * Creates a live preview on `canvas` for a timeline bundle (`GET /api/projects/{id}/timeline`).
 * The handle works at once; `ready` resolves when the first frame is on screen.
 */
export function createPreview(canvas: HTMLCanvasElement, bundle: PreviewBundle, options: PreviewOptions = {}): PreviewHandle & { snapshot(): ImageData | null } {
  return new PreviewEngine(canvas, bundle, options);
}

export { scenePositionAt };
export default { createPreview };
