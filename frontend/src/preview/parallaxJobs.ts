/**
 * The costly parts of 2.5D parallax, kept off the main thread and out of the frame draw
 * (docs/M5-CONTRACT.md 8.7.2 and 8.7.3).
 *
 * - The depth analysis of a scene's working crop (`analyseCrop`, tens of milliseconds) and the
 *   colours of its two layers (`platePixels`, 250 to 500 ms for a 1280 pixel plate) run in a Web
 *   Worker (parallax.worker.ts), one job at a time, the frame's own scenes first. Without workers
 *   (tests) the same functions run on the main thread in idle slices: never while playing and
 *   never inside a frame.
 * - `ParallaxStore` keeps the scenes' plans the way the picture store keeps pictures: only for the
 *   scenes around the playhead (the engine's prefetch keep-set). A plan that leaves it drops its
 *   plates (the compositor destroys their textures, the canvases are freed) and its depth grids.
 *   A parallax scene is drawn as its Ken Burns move until its plan (and, with near objects, its
 *   plates) is ready.
 */
import { cropPixels, freePlates, platePixels, platesFromPixels, plateSize, type PlatePixels, type Plates } from "./layers";
import type { Rect } from "./motion";
import { analyseCrop, planFromAnalysis, sceneCrop, type CropAnalysis, type ParallaxPlan } from "./parallax";
import type { AnalyseJob, PlatesJob, WorkerReply, WorkerRequest } from "./parallaxMessages";
import { decodeDepthMap, type Picture } from "./pictures";
import type { SceneDesc } from "./types";

export type { AnalyseJob, PlatesJob } from "./parallaxMessages";

/** The widest plate (the renderer's working picture is about one pixel per output pixel). */
export const PLATE_MAX_WIDTH = 1280;
/** Without a worker, a job waits this long while the preview plays before it looks again. */
export const IDLE_RETRY_MS = 250;
/**
 * The worker closes after this long without a job and starts again for the next one: a plates
 * job works through about 45 MB of arrays, and a worker kept open holds on to that memory.
 */
export const WORKER_IDLE_MS = 10_000;

/** Runs the parallax jobs; `priority()` is read when a job is picked (lower goes first). */
export interface ParallaxJobs {
  analyse(job: AnalyseJob, priority: () => number, cancelled: () => boolean): Promise<CropAnalysis | null>;
  plates(job: PlatesJob, priority: () => number, cancelled: () => boolean): Promise<PlatePixels | null>;
  destroy(): void;
}

// One job at a time, the most urgent first -------------------------------------------------------

interface Queued {
  run: () => Promise<unknown>;
  priority: () => number;
  cancelled: () => boolean;
  settle: (value: unknown) => void;
  /** frees what the job holds when it is dropped unrun */
  drop?: () => void;
  order: number;
}

export class JobQueue {
  private queue: Queued[] = [];
  private busy = false;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private order = 0;
  private closed = false;

  constructor(
    private readonly canRun: () => boolean = () => true,
    /** run each job in a task of its own (the main-thread fallback yields to the page first) */
    private readonly yieldFirst = false,
  ) {}

  add<T>(run: () => Promise<T>, priority: () => number, cancelled: () => boolean, drop?: () => void): Promise<T | null> {
    if (this.closed) {
      drop?.();
      return Promise.resolve(null);
    }
    return new Promise<T | null>((resolve) => {
      this.queue.push({ run, priority, cancelled, settle: resolve as (value: unknown) => void, drop, order: this.order++ });
      this.pump();
    });
  }

  get size(): number {
    return this.queue.length + (this.busy ? 1 : 0);
  }

  private pump(): void {
    if (this.busy || this.closed) return;
    this.queue = this.queue.filter((job) => {
      if (!job.cancelled()) return true;
      job.drop?.();
      job.settle(null);
      return false;
    });
    if (!this.queue.length) return;
    if (!this.canRun()) {
      if (this.retry === null) {
        this.retry = setTimeout(() => {
          this.retry = null;
          this.pump();
        }, IDLE_RETRY_MS);
      }
      return;
    }
    this.queue.sort((a, b) => a.priority() - b.priority() || a.order - b.order);
    const job = this.queue.shift()!;
    this.busy = true;
    const start = this.yieldFirst ? new Promise<void>((resolve) => setTimeout(resolve, 0)) : Promise.resolve();
    const run = (): Promise<unknown> | null => {
      if (!job.cancelled()) return job.run();
      job.drop?.();
      return null;
    };
    void start
      .then(run)
      .then(
        (value) => job.settle(value),
        () => job.settle(null),
      )
      .finally(() => {
        this.busy = false;
        this.pump();
      });
  }

  close(): void {
    this.closed = true;
    if (this.retry !== null) clearTimeout(this.retry);
    this.retry = null;
    for (const job of this.queue) {
      job.drop?.();
      job.settle(null);
    }
    this.queue = [];
  }
}

// On the main thread (no workers) ------------------------------------------------------------------

export class InlineJobs implements ParallaxJobs {
  private readonly queue: JobQueue;

  constructor(canRun: () => boolean) {
    this.queue = new JobQueue(canRun, true);
  }

  analyse(job: AnalyseJob, priority: () => number, cancelled: () => boolean): Promise<CropAnalysis | null> {
    return this.queue.add(async () => {
      const depth = await decodeDepthMap(job.depth);
      return depth ? analyseCrop(depth, job.crop, job.image) : null;
    }, priority, cancelled);
  }

  plates(job: PlatesJob, priority: () => number, cancelled: () => boolean): Promise<PlatePixels | null> {
    return this.queue.add(
      async () => {
        try {
          const size = plateSize(job.bitmap, job.image, job.crop, job.maxWidth);
          const pixels = cropPixels(job.bitmap, job.image, job.crop, size);
          return pixels ? platePixels(pixels, size[0], size[1], job.alpha) : null;
        } finally {
          job.bitmap.close();
        }
      },
      priority,
      cancelled,
      () => job.bitmap.close(),
    );
  }

  destroy(): void {
    this.queue.close();
  }
}

// In a worker ---------------------------------------------------------------------------------------

export class WorkerJobs implements ParallaxJobs {
  private readonly queue = new JobQueue();
  private readonly pending = new Map<number, (reply: WorkerReply | null) => void>();
  private nextId = 1;
  private worker: Worker | null = null;
  private fallback: InlineJobs | null = null;
  private idle: ReturnType<typeof setTimeout> | null = null;
  private destroyed = false;

  constructor(
    private readonly makeWorker: () => Worker,
    private readonly canRun: () => boolean,
  ) {
    // made at once, so a browser that refuses module workers is found out here (and the page
    // does the work instead, see createParallaxJobs)
    this.start();
  }

  private start(): Worker {
    const worker = this.makeWorker();
    worker.onmessage = (event: MessageEvent<WorkerReply>) => {
      const reply = event.data;
      const settle = this.pending.get(reply.id);
      this.pending.delete(reply.id);
      settle?.(reply);
    };
    worker.onerror = () => this.broken();
    this.worker = worker;
    return worker;
  }

  /** The worker failed to start or crashed: what was sent is lost, the rest runs on the page. */
  private broken(): void {
    this.worker?.terminate();
    this.worker = null;
    this.fallback ??= new InlineJobs(this.canRun);
    for (const settle of this.pending.values()) settle(null);
    this.pending.clear();
  }

  /** After a job: the worker closes when nothing more comes for WORKER_IDLE_MS. */
  private rest(): void {
    if (this.destroyed) return;
    if (this.idle !== null) clearTimeout(this.idle);
    this.idle = setTimeout(() => {
      this.idle = null;
      if (this.pending.size || this.queue.size || !this.worker) return;
      this.worker.terminate();
      this.worker = null;
    }, WORKER_IDLE_MS);
  }

  private send(request: WorkerRequest, transfer: Transferable[]): Promise<WorkerReply | null> {
    if (this.idle !== null) clearTimeout(this.idle);
    this.idle = null;
    let worker = this.worker;
    if (!worker) {
      try {
        worker = this.start();
      } catch {
        this.broken();
        return Promise.resolve(null);
      }
    }
    const started = worker;
    return new Promise((resolve) => {
      this.pending.set(request.id, (reply) => {
        resolve(reply);
        this.rest();
      });
      try {
        started.postMessage(request, transfer);
      } catch {
        this.pending.delete(request.id);
        resolve(null);
      }
    });
  }

  analyse(job: AnalyseJob, priority: () => number, cancelled: () => boolean): Promise<CropAnalysis | null> {
    if (this.fallback) return this.fallback.analyse(job, priority, cancelled);
    return this.queue.add(async () => {
      if (this.fallback) return this.fallback.analyse(job, priority, cancelled);
      const reply = await this.send({ id: this.nextId++, kind: "analyse", ...job }, []);
      return reply && reply.ok ? (reply.analysis ?? null) : null;
    }, priority, cancelled);
  }

  plates(job: PlatesJob, priority: () => number, cancelled: () => boolean): Promise<PlatePixels | null> {
    if (this.fallback) return this.fallback.plates(job, priority, cancelled);
    return this.queue.add(
      async () => {
        if (this.fallback) return this.fallback.plates(job, priority, cancelled);
        const reply = await this.send({ id: this.nextId++, kind: "plates", ...job }, [job.bitmap, job.alpha.values.buffer as ArrayBuffer]);
        return reply && reply.ok ? (reply.plates ?? null) : null;
      },
      priority,
      cancelled,
      () => job.bitmap.close(),
    );
  }

  /** True while a worker is open (tests and diagnostics). */
  get open(): boolean {
    return this.worker !== null;
  }

  destroy(): void {
    this.destroyed = true;
    if (this.idle !== null) clearTimeout(this.idle);
    this.idle = null;
    this.queue.close();
    this.fallback?.destroy();
    this.worker?.terminate();
    this.worker = null;
    for (const settle of this.pending.values()) settle(null);
    this.pending.clear();
  }
}

/** A worker when the browser has them (and OffscreenCanvas), else idle slices on the page. */
export function createParallaxJobs(canRun: () => boolean): ParallaxJobs {
  if (typeof Worker !== "undefined" && typeof OffscreenCanvas !== "undefined") {
    try {
      return new WorkerJobs(() => new Worker(new URL("./parallax.worker.ts", import.meta.url), { type: "module", name: "sge-parallax" }), canRun);
    } catch {
      /* no module workers here: the page does the work when idle */
    }
  }
  return new InlineJobs(canRun);
}

// The plans of the scenes around the playhead ---------------------------------------------------------

export interface ParallaxContext {
  /** the timeline's frame size (the renderer's output) */
  render: [number, number];
  fps: number;
  /** parallax.max_overscan */
  limit: number;
}

export interface ParallaxStoreOptions {
  /** the depth map's bytes (null: there is none, the scene stays a Ken Burns move) */
  fetchDepth(url: string): Promise<Blob | null>;
  context(): ParallaxContext;
  /** a plan (`plates` false) or its plates (`plates` true) are ready: draw again */
  onReady(position: number, plan: ParallaxPlan, plates: boolean): void;
  /** a plan's plates go: the compositor frees what it holds for them */
  release(plan: ParallaxPlan): void;
  /** the job runner, made when the first parallax scene needs it (tests pass their own) */
  jobs?: () => ParallaxJobs;
  /** the main thread may run a job now (only without a worker) */
  canRun?: () => boolean;
  /** a copy of a decoded picture for the plates job (default createImageBitmap) */
  cloneBitmap?: (bitmap: ImageBitmap) => Promise<ImageBitmap>;
  /** the plates as canvases (default platesFromPixels) */
  toPlates?: (pixels: PlatePixels) => Plates | null;
}

interface Entry {
  /** what the analysis and the plates depend on: the depth map and the working crop */
  key: string;
  /** what the cheap rest of the plan depends on: the strength, the clip and the bundle (`rebase`) */
  fit: string;
  state: "loading" | "ready" | "none";
  /** the depth over the crop (kept so a new bundle only refits the plan) */
  found: CropAnalysis | null;
  plan: ParallaxPlan | null;
  plates: "none" | "loading" | "ready" | "failed";
  /** the picture version the plates were made from (or failed for) */
  platesVersion: number;
  priority: number;
  token: { cancelled: boolean };
}

export interface ParallaxCounts {
  /** scenes with a plan */
  plans: number;
  /** plans holding their two plates */
  plates: number;
  /** scenes whose depth or plates are still being worked out */
  working: number;
}

export class ParallaxStore {
  private readonly entries = new Map<number, Entry>();
  /** depth maps found missing (cleared by reset: one may have been made since) */
  private readonly missing = new Set<string>();
  private runner: ParallaxJobs | null = null;
  private destroyed = false;
  /** bumped by rebase(): every plan is fitted again to its scene of the new bundle */
  private bundle = 0;

  constructor(private readonly options: ParallaxStoreOptions) {}

  private jobs(): ParallaxJobs {
    this.runner ??= this.options.jobs ? this.options.jobs() : createParallaxJobs(this.options.canRun ?? (() => true));
    return this.runner;
  }

  /**
   * Asks for the parallax of the scene at `position` (cheap when it is in hand: the engine calls
   * it for the scenes around the playhead on every frame). `picture` is the decoded picture once
   * it is in; `priority` orders the work (0: the frame on screen).
   */
  want(position: number, scene: SceneDesc, depthUrl: string | null, picture: Picture | null, priority: number): void {
    if (this.destroyed || scene.style !== "parallax") return;
    let entry = this.entries.get(position);
    if (!depthUrl || this.missing.has(depthUrl)) {
      if (entry && entry.key) this.drop(position, entry);
      if (!entry || entry.key) this.entries.set(position, { key: "", fit: "", state: "none", found: null, plan: null, plates: "none", platesVersion: 0, priority, token: { cancelled: false } });
      return;
    }
    // the working crop needs the picture's size (the cover region bounds the camera path)
    if (!scene.imageSize) return;
    const crop = sceneCrop(scene.path, scene.imageSize);
    if (!crop) return;
    const key = `${depthUrl}|${crop.join(",")}`;
    const fit = `${scene.strength}|${scene.clipFrames}|${this.bundle}`;
    if (!entry || entry.key !== key) {
      if (entry) this.drop(position, entry);
      entry = { key, fit, state: "loading", found: null, plan: null, plates: "none", platesVersion: 0, priority, token: { cancelled: false } };
      this.entries.set(position, entry);
      this.analyse(position, entry, scene, depthUrl, crop, scene.imageSize);
      return;
    }
    entry.priority = priority;
    // the same depth and crop in a new bundle (a cut changed): only the cheap fit is redone, the
    // analysis and the plates stay
    if (entry.state === "ready" && entry.fit !== fit) this.refit(position, entry, scene, fit);
    const plan = entry.plan;
    if (entry.state !== "ready" || !plan?.analysis.alpha || !picture || entry.plates === "loading") return;
    // made (or failed) for this picture already; a sharper decode of it makes them again
    if ((entry.plates === "ready" || entry.plates === "failed") && entry.platesVersion >= picture.version) return;
    this.makePlates(position, entry, plan, picture);
  }

  private analyse(position: number, entry: Entry, scene: SceneDesc, url: string, crop: Rect, image: [number, number]): void {
    const token = entry.token;
    const work = async () => {
      const blob = await this.options.fetchDepth(url);
      if (token.cancelled) return;
      if (!blob) {
        this.missing.add(url);
        entry.state = "none";
        return;
      }
      const found = await this.jobs().analyse({ depth: blob, crop, image }, () => entry.priority, () => token.cancelled);
      if (token.cancelled) return;
      const plan = found ? this.fitPlan(scene, found, crop) : null;
      entry.found = found;
      entry.plan = plan;
      entry.state = plan ? "ready" : "none";
      if (plan) this.options.onReady(position, plan, false);
    };
    work().catch(() => {
      if (!token.cancelled) entry.state = "none";
    });
  }

  private fitPlan(scene: SceneDesc, found: CropAnalysis, crop: Rect): ParallaxPlan | null {
    const context = this.options.context();
    return planFromAnalysis(scene.path, found, crop, context.render, { strength: scene.strength, frames: scene.clipFrames, fps: context.fps, limit: context.limit });
  }

  /** Fits a ready plan to its scene in a new bundle; its analysis and plates are kept. */
  private refit(position: number, entry: Entry, scene: SceneDesc, fit: string): void {
    const plan = entry.plan;
    entry.fit = fit;
    if (!plan || !entry.found) return;
    const next = this.fitPlan(scene, entry.found, plan.crop);
    if (!next) {
      // no parallax any more (strength 0): the plates go
      this.freePlatesOf(plan);
      entry.plan = null;
      entry.state = "none";
      return;
    }
    plan.share = next.share;
    plan.strength = next.strength;
    this.options.onReady(position, plan, false);
  }

  private makePlates(position: number, entry: Entry, plan: ParallaxPlan, picture: Picture): void {
    const token = entry.token;
    const version = picture.version;
    const alpha = plan.analysis.alpha!;
    entry.plates = "loading";
    const failed = () => {
      if (token.cancelled) return;
      entry.plates = "failed";
      entry.platesVersion = version;
    };
    const work = async () => {
      const clone = this.options.cloneBitmap ?? ((bitmap: ImageBitmap) => createImageBitmap(bitmap));
      let bitmap: ImageBitmap;
      try {
        bitmap = await clone(picture.bitmap);
      } catch {
        failed();
        return;
      }
      if (token.cancelled) {
        bitmap.close();
        return;
      }
      const job: PlatesJob = {
        bitmap,
        image: [picture.imageWidth, picture.imageHeight],
        crop: plan.crop,
        alpha: { values: alpha.values.slice(), width: alpha.width, height: alpha.height },
        maxWidth: PLATE_MAX_WIDTH,
      };
      const pixels = await this.jobs().plates(job, () => entry.priority, () => token.cancelled);
      if (token.cancelled) return;
      const plates = pixels ? (this.options.toPlates ?? platesFromPixels)(pixels) : null;
      if (!plates) {
        failed();
        return;
      }
      if (plan.plates) this.freePlatesOf(plan);
      plan.plates = plates;
      entry.plates = "ready";
      entry.platesVersion = version;
      this.options.onReady(position, plan, true);
    };
    work().catch(failed);
  }

  private freePlatesOf(plan: ParallaxPlan): void {
    const plates = plan.plates;
    if (!plates) return;
    this.options.release(plan);
    plan.plates = null;
    freePlates(plates);
  }

  private drop(position: number, entry: Entry): void {
    entry.token.cancelled = true;
    if (entry.plan) this.freePlatesOf(entry.plan);
    entry.plan = null;
    this.entries.delete(position);
  }

  /** The plan to draw the scene at `position` with; null: draw its Ken Burns move. */
  get(position: number): ParallaxPlan | null {
    const entry = this.entries.get(position);
    if (!entry || entry.state !== "ready" || !entry.plan) return null;
    return !entry.plan.analysis.alpha || entry.plan.plates ? entry.plan : null;
  }

  /** True when nothing is left to work out for the scene at `position` (the parity harness). */
  settled(position: number): boolean {
    const entry = this.entries.get(position);
    if (!entry) return false;
    if (entry.state === "loading") return false;
    if (entry.state === "none" || !entry.plan?.analysis.alpha) return true;
    return entry.plates === "ready" || entry.plates === "failed";
  }

  /** Keeps the plans of the scenes in `keep`; the others go with their plates and depth grids. */
  trim(keep: ReadonlySet<number>): void {
    for (const [position, entry] of [...this.entries]) if (!keep.has(position)) this.drop(position, entry);
  }

  /** Forgets every plan (a new bundle): their plates go, and missing depth maps are asked again. */
  reset(): void {
    for (const [position, entry] of [...this.entries]) this.drop(position, entry);
    this.missing.clear();
  }

  /**
   * The same timeline patched (a cut or a popup changed in the review): each plan is fitted again
   * to its scene when it is next asked for, keeping its depth analysis and its plates when the
   * scene still shows the same picture and depth; missing depth maps are asked again.
   */
  rebase(): void {
    this.bundle += 1;
    this.missing.clear();
  }

  counts(): ParallaxCounts {
    let plans = 0;
    let plates = 0;
    let working = 0;
    for (const entry of this.entries.values()) {
      if (entry.plan) plans += 1;
      if (entry.plan?.plates) plates += 1;
      if (entry.state === "loading" || entry.plates === "loading") working += 1;
    }
    return { plans, plates, working };
  }

  destroy(): void {
    if (this.destroyed) return;
    this.reset();
    this.destroyed = true;
    this.runner?.destroy();
    this.runner = null;
  }
}
