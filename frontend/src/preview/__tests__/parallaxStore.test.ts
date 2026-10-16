import { describe, expect, it, vi } from "vitest";

import type { Grid, PlatePixels, Plates } from "../layers";
import { MotionPath, coverRegion } from "../motion";
import type { CropAnalysis, ParallaxPlan } from "../parallax";
import { JobQueue, ParallaxStore, WORKER_IDLE_MS, WorkerJobs, type AnalyseJob, type ParallaxJobs, type PlatesJob } from "../parallaxJobs";
import type { Picture } from "../pictures";
import type { SceneDesc } from "../types";

/** Lets the promise chains of the store and the fake jobs run (no timers involved). */
async function flush(turns = 40): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

const IMAGE: [number, number] = [1920, 1080];

function grid(fill: number, width = 32, height = 18): Grid {
  return { values: new Float32Array(width * height).fill(fill), width, height };
}

function scene(index: number): SceneDesc {
  const bounds = coverRegion(IMAGE[0], IMAGE[1], 1920, 1080);
  return {
    number: index + 1,
    index,
    image: `06_images/scene_${index + 1}.png`,
    imageSize: IMAGE,
    startFrame: index * 150,
    length: 150,
    leadIn: 0,
    tailOut: 0,
    clipFrames: 150,
    bodyStart: index * 150,
    body: 150,
    path: MotionPath.forPreset("zoom_in", [0, 0, 1, 1], [0.1, 0.1, 0.8, 0.8], { seed: index, bounds }),
    style: "parallax",
    clip: null,
    frame: null,
    adjust: null,
    strength: 0.035,
  };
}

function picture(index: number, version = 1): Picture {
  return {
    path: `06_images/scene_${index + 1}.png`,
    bitmap: { width: IMAGE[0], height: IMAGE[1], close: vi.fn() } as unknown as ImageBitmap,
    imageWidth: IMAGE[0],
    imageHeight: IMAGE[1],
    version,
  };
}

/** A job runner answering at once: near objects everywhere (two layers), tiny plates. */
function fakeJobs(layers = true) {
  const done = { analyse: 0, plates: 0, closed: 0 };
  const jobs: ParallaxJobs = {
    analyse: async (_job: AnalyseJob, _priority: () => number, cancelled: () => boolean): Promise<CropAnalysis | null> => {
      await Promise.resolve();
      if (cancelled()) return null;
      done.analyse += 1;
      return { stats: { mean: 0.5, low: 0.2, high: 0.9 }, analysis: { envelope: grid(0.4), alpha: layers ? grid(1) : null, backDepth: grid(0.4), frontDepth: layers ? grid(0.9) : null } };
    },
    plates: async (job: PlatesJob, _priority: () => number, cancelled: () => boolean): Promise<PlatePixels | null> => {
      job.bitmap.close();
      done.closed += 1;
      await Promise.resolve();
      if (cancelled()) return null;
      done.plates += 1;
      return { width: 4, height: 2, back: new Uint8ClampedArray(32), front: new Uint8ClampedArray(32) };
    },
    destroy: () => undefined,
  };
  return { jobs, done };
}

function makeStore(layers = true) {
  const { jobs, done } = fakeJobs(layers);
  const live = new Set<ParallaxPlan>();
  const released: ParallaxPlan[] = [];
  let platesMade = 0;
  const depthFetches: string[] = [];
  const store = new ParallaxStore({
    fetchDepth: async (url) => {
      depthFetches.push(url);
      return url.includes("missing") ? null : new Blob([new Uint8Array(4)]);
    },
    context: () => ({ render: [1920, 1080], fps: 30, limit: 0.03 }),
    onReady: (_position, plan, plates) => {
      if (!plates) return;
      platesMade += 1;
      live.add(plan);
    },
    release: (plan) => {
      released.push(plan);
      live.delete(plan);
    },
    jobs: () => jobs,
    cloneBitmap: async () => ({ width: IMAGE[0], height: IMAGE[1], close: vi.fn() }) as unknown as ImageBitmap,
    toPlates: (pixels) => ({ back: { width: pixels.width, height: pixels.height }, front: { width: pixels.width, height: pixels.height } }) as unknown as Plates,
  });
  return { store, done, live, released, depthFetches, platesMade: () => platesMade };
}

/** The scenes the engine keeps around position `p` (prefetch: the scene, 3 ahead, 1 behind). */
function keepAround(p: number, count: number): number[] {
  return [p - 1, p, p + 1, p + 2, p + 3].filter((k) => k >= 0 && k < count);
}

describe("the parallax plans around the playhead (parallaxJobs.ts ParallaxStore)", () => {
  it("draws a scene as its Ken Burns move until its plates are made", async () => {
    const { store } = makeStore();
    const first = scene(0);
    store.want(0, first, "/depth/1.png", null, 0);
    expect(store.get(0)).toBeNull();
    expect(store.settled(0)).toBe(false);
    await flush();
    // the plan is in, but near objects need their plates: still Ken Burns
    expect(store.get(0)).toBeNull();
    expect(store.settled(0)).toBe(false);
    store.want(0, first, "/depth/1.png", picture(0), 0);
    await flush();
    const plan = store.get(0);
    expect(plan?.plates).not.toBeNull();
    expect(store.settled(0)).toBe(true);
    // a sharper decode of the picture makes the plates again; the old ones are freed
    const old = plan!.plates!;
    store.want(0, first, "/depth/1.png", picture(0, 2), 0);
    await flush();
    expect(store.get(0)!.plates).not.toBe(old);
    expect(old.back.width).toBe(0);
  });

  it("keeps a scene's analysis and plates when the same timeline is patched, and fits its plan again", async () => {
    const { store, done, released } = makeStore();
    store.want(2, scene(2), "/depth/3.png", picture(2), 0);
    await flush();
    store.want(2, scene(2), "/depth/3.png", picture(2), 0);
    await flush();
    const plan = store.get(2)!;
    const plates = plan.plates;
    expect(plates).not.toBeNull();
    // a cut changed in the review: new scene objects, a gentler strength
    store.rebase();
    const patched = { ...scene(2), strength: 0.01 };
    store.want(2, patched, "/depth/3.png", picture(2), 0);
    expect(store.get(2)).toBe(plan);
    expect(plan.plates).toBe(plates);
    expect(plan.strength).toBeLessThanOrEqual(0.01);
    expect(done.analyse).toBe(1);
    expect(done.plates).toBe(1);
    expect(released).toEqual([]);
    // parallax switched off for the scene (strength 0): its plates go
    store.rebase();
    store.want(2, { ...scene(2), strength: 0 }, "/depth/3.png", picture(2), 0);
    expect(store.get(2)).toBeNull();
    expect(released).toEqual([plan]);
  });

  it("draws a one-layer plan as soon as its depth is analysed", async () => {
    const { store, done } = makeStore(false);
    store.want(3, scene(3), "/depth/4.png", null, 0);
    await flush();
    expect(store.get(3)?.plates).toBeNull();
    expect(store.settled(3)).toBe(true);
    expect(done.plates).toBe(0);
  });

  it("asks for a missing depth map once per bundle", async () => {
    const { store, depthFetches } = makeStore();
    store.want(0, scene(0), "/depth/missing.png", null, 0);
    await flush();
    store.want(0, scene(0), "/depth/missing.png", picture(0), 0);
    store.trim(new Set());
    store.want(0, scene(0), "/depth/missing.png", picture(0), 0);
    await flush();
    expect(depthFetches).toEqual(["/depth/missing.png"]);
    expect(store.get(0)).toBeNull();
    expect(store.settled(0)).toBe(true);
    store.reset(); // a new bundle: parallax may have been switched on since
    store.want(0, scene(0), "/depth/missing.png", picture(0), 0);
    await flush();
    expect(depthFetches).toHaveLength(2);
  });

  it("keeps plans, plates and their textures only for the scenes around the playhead (60-scene soak)", async () => {
    const { store, live, released, platesMade, done } = makeStore();
    const count = 60;
    const scenes = Array.from({ length: count }, (_, i) => scene(i));
    let mostPlans = 0;
    let mostPlates = 0;
    for (let lap = 0; lap < 2; lap++) {
      for (let p = 0; p < count; p++) {
        const keep = keepAround(p, count);
        for (let pass = 0; pass < 2; pass++) {
          for (const k of keep) store.want(k, scenes[k]!, `/depth/${k + 1}.png`, picture(k), k === p ? 0 : Math.abs(k - p));
          await flush();
        }
        store.trim(new Set(keep));
        const counts = store.counts();
        mostPlans = Math.max(mostPlans, counts.plans);
        mostPlates = Math.max(mostPlates, counts.plates);
        expect(counts.plans).toBeLessThanOrEqual(keep.length);
        expect(counts.plates).toBeLessThanOrEqual(keep.length);
        // the compositor holds textures only for plates still in use
        expect(live.size).toBe(counts.plates);
      }
    }
    expect(mostPlans).toBeLessThanOrEqual(5);
    expect(mostPlates).toBeLessThanOrEqual(5);
    // every plate made was either released or is one of the few still kept
    expect(platesMade()).toBe(released.length + live.size);
    expect(platesMade()).toBeGreaterThanOrEqual(count);
    // every picture copy sent for plates was closed
    expect(done.closed).toBeGreaterThanOrEqual(done.plates);
    store.destroy();
    expect(store.counts()).toEqual({ plans: 0, plates: 0, working: 0 });
    expect(live.size).toBe(0);
  });

  it("drops the work of a scene the playhead has left before it runs", async () => {
    const { store, done } = makeStore();
    const far = scene(40);
    store.want(40, far, "/depth/41.png", picture(40), 5);
    store.trim(new Set([0, 1]));
    await flush();
    expect(done.analyse).toBe(0);
    expect(store.counts()).toEqual({ plans: 0, plates: 0, working: 0 });
  });
});

describe("the parallax worker (parallaxJobs.ts WorkerJobs)", () => {
  class FakeWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: (() => void) | null = null;
    terminated = false;
    readonly posted: { id: number; kind: string }[] = [];
    postMessage(message: { id: number; kind: string }): void {
      this.posted.push(message);
      const analysis: CropAnalysis = { stats: { mean: 0.5, low: 0, high: 1 }, analysis: { envelope: grid(0.5), alpha: null, backDepth: grid(0.5), frontDepth: null } };
      queueMicrotask(() => this.onmessage?.({ data: { id: message.id, ok: true, analysis } } as MessageEvent));
    }
    terminate(): void {
      this.terminated = true;
    }
  }

  it("answers each job from the worker and closes the worker when it has been idle a while", async () => {
    vi.useFakeTimers();
    try {
      const made: FakeWorker[] = [];
      const jobs = new WorkerJobs(() => {
        const worker = new FakeWorker();
        made.push(worker);
        return worker as unknown as Worker;
      }, () => true);
      const job = { depth: new Blob([new Uint8Array(2)]), crop: [0, 0, 10, 10] as [number, number, number, number], image: [10, 10] as [number, number] };
      const [first, second] = await Promise.all([jobs.analyse(job, () => 0, () => false), jobs.analyse(job, () => 1, () => false)]);
      expect(first?.stats.mean).toBe(0.5);
      expect(second?.stats.mean).toBe(0.5);
      expect(made).toHaveLength(1);
      expect(made[0]!.posted.map((message) => message.kind)).toEqual(["analyse", "analyse"]);
      expect(jobs.open).toBe(true);
      await vi.advanceTimersByTimeAsync(WORKER_IDLE_MS + 10);
      expect(jobs.open).toBe(false);
      expect(made[0]!.terminated).toBe(true);
      // the next job starts a new worker
      expect((await jobs.analyse(job, () => 0, () => false))?.stats.mean).toBe(0.5);
      expect(made).toHaveLength(2);
      jobs.destroy();
      expect(made[1]!.terminated).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the parallax job queue", () => {
  it("runs one job at a time, the most urgent first, and drops cancelled ones", async () => {
    const queue = new JobQueue();
    const ran: string[] = [];
    const dropped: string[] = [];
    let release: () => void = () => undefined;
    const first = queue.add(() => new Promise<string>((resolve) => (release = () => resolve("a"))), () => 5, () => false);
    const urgency = { b: 3, c: 1 };
    let cancelB = false;
    const b = queue.add(async () => (ran.push("b"), "b"), () => urgency.b, () => cancelB, () => dropped.push("b"));
    const c = queue.add(async () => (ran.push("c"), "c"), () => urgency.c, () => false);
    const d = queue.add(async () => (ran.push("d"), "d"), () => 2, () => false);
    cancelB = true;
    await flush();
    release();
    expect(await first).toBe("a");
    expect(await Promise.all([b, c, d])).toEqual([null, "c", "d"]);
    expect(ran).toEqual(["c", "d"]);
    expect(dropped).toEqual(["b"]);
    queue.close();
    expect(await queue.add(async () => "late", () => 0, () => false)).toBeNull();
  });

  it("waits while the page is busy playing when it runs on the page", async () => {
    vi.useFakeTimers();
    try {
      let playing = true;
      const queue = new JobQueue(() => !playing, true);
      let ran = false;
      const job = queue.add(async () => (ran = true), () => 0, () => false);
      await vi.advanceTimersByTimeAsync(1000);
      expect(ran).toBe(false);
      playing = false;
      await vi.advanceTimersByTimeAsync(300);
      expect(await job).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
