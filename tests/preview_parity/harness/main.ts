// The preview parity harness (dev only, docs/M5-CONTRACT.md section 6). Served by the app under
// /parity/ and driven by tests/preview_parity/parity_harness.py through Playwright: it opens a
// project's timeline bundle from the real API with the same engine options as the app's
// LivePreview, draws exact frames for the comparison with the FFmpeg render, reports the
// engine's plan (frame layout, cues, voice clips, music stem, loudness) and measures playback,
// seeks, the first frame and memory.
import { VOICE_UPMIX_GAIN } from "@preview/audio";
import { frameAt } from "@preview/description";
import { createPreview } from "@preview/engine";
import { MAX_THUMB_HEIGHT } from "@preview/pictures";
import type { PopupPreviewRequest, PopupPreviewResult, PreviewBundle } from "@preview/types";

type Handle = ReturnType<typeof createPreview>;

interface OpenOptions {
  width: number;
  height: number;
  renderer?: "auto" | "webgl2" | "canvas2d";
  /** every picture replaced by flat grey and no popups: the captions alone (caption boxes) */
  grey?: boolean;
  /** the timeline's captions switched off */
  noCaptions?: boolean;
  /** read every picture from its own file, never the sized JPEG (diagnostics) */
  fullPictures?: boolean;
  audio?: boolean;
  quality?: "full" | "half" | "auto";
}

interface Current {
  handle: Handle;
  bundle: PreviewBundle;
  projectId: string;
  opened: number;
}

const log = document.getElementById("log")!;
const stage = document.getElementById("stage")!;
let current: Current | null = null;
let catalogue: unknown = null;
const greyUrls = new Map<string, string>();
const scheduled: { when: number; offset: number; duration: number; length: number; at: number }[] = [];

// Recording what the engine plays ------------------------------------------------------------------
//
// Every AudioContext the engine makes is a TappedContext: its `destination` is a gain node in
// front of the real one, so the engine's whole mix can be recorded by an AudioWorklet (the
// browser is muted by Playwright, the graph still runs).

const RECORDER = `
class ParityRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.on = false; this.l = []; this.r = []; this.first = -1; this.count = 0;
    this.port.onmessage = (event) => {
      if (event.data === "start") this.on = true;
      if (event.data === "stop") { this.on = false; this.flush(); this.port.postMessage({ done: true }); }
    };
  }
  flush() {
    if (!this.count) return;
    const l = new Float32Array(this.count), r = new Float32Array(this.count);
    let at = 0;
    for (let i = 0; i < this.l.length; i++) { l.set(this.l[i], at); r.set(this.r[i], at); at += this.l[i].length; }
    this.port.postMessage({ first: this.first, l, r }, [l.buffer, r.buffer]);
    this.l = []; this.r = []; this.count = 0; this.first = -1;
  }
  process(inputs) {
    if (this.on) {
      const input = inputs[0];
      const left = input && input[0] ? input[0] : new Float32Array(128);
      const right = input && input[1] ? input[1] : left;
      if (this.first < 0) this.first = currentFrame;
      this.l.push(left.slice()); this.r.push(right.slice()); this.count += left.length;
      if (this.count >= 24000) this.flush();
    }
    return true;
  }
}
registerProcessor("parity-recorder", ParityRecorder);
`;

interface Chunk {
  first: number;
  l: Float32Array;
  r: Float32Array;
}

const NativeContext = window.AudioContext;

class TappedContext extends NativeContext {
  readonly tap: GainNode;
  readonly speakers: AudioDestinationNode;
  recorder: AudioWorkletNode | null = null;
  chunks: Chunk[] = [];
  stopped: (() => void) | null = null;

  constructor(options?: AudioContextOptions) {
    super(options);
    this.speakers = super.destination;
    this.tap = super.createGain();
    this.tap.connect(this.speakers);
  }

  override get destination(): AudioDestinationNode {
    return (this.tap ?? super.destination) as unknown as AudioDestinationNode;
  }
}
(window as unknown as { AudioContext: typeof AudioContext }).AudioContext = TappedContext;

async function recorderFor(ctx: TappedContext): Promise<AudioWorkletNode> {
  if (ctx.recorder) return ctx.recorder;
  const url = URL.createObjectURL(new Blob([RECORDER], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  const node = new AudioWorkletNode(ctx, "parity-recorder", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2] });
  const silent = ctx.createGain();
  silent.gain.value = 0;
  ctx.tap.connect(node);
  node.connect(silent);
  silent.connect(ctx.speakers);
  node.port.onmessage = (event: MessageEvent) => {
    if (event.data?.done) ctx.stopped?.();
    else ctx.chunks.push(event.data as Chunk);
  };
  ctx.recorder = node;
  return node;
}

function floatsToBase64(values: Float32Array): string {
  const bytes = new Uint8Array(values.buffer, values.byteOffset, values.byteLength);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/**
 * Plays `seconds` from `from` with the sound recorded (after one short warm-up play so the voice
 * and music pieces are decoded), and returns the recording with the video time of its first
 * sample.
 */
async function record(from: number, seconds: number) {
  const handle = current!.handle;
  const audio = engineField<{ ensureContext(): AudioContext | null; t0: number; c0: number; context: AudioContext | null }>("audio");
  const ctx = audio.ensureContext() as TappedContext | null;
  if (!ctx) throw new Error("No audio context.");
  const recorder = await recorderFor(ctx);
  // warm-up: decode what plays around `from`
  handle.seek(from);
  await settleSeek();
  await handle.play();
  await sleep(1500);
  handle.pause();
  handle.seek(from);
  await settleSeek();
  ctx.chunks = [];
  recorder.port.postMessage("start");
  await handle.play();
  const clockStart = { t0: audio.t0, c0: audio.c0 };
  await sleep(300);
  const audioDriven = engineField<{ audioDriven: boolean }>("clock").audioDriven;
  await sleep(Math.max(seconds * 1000 - 300, 0));
  handle.pause();
  const done = new Promise<void>((resolve) => {
    ctx.stopped = resolve;
  });
  recorder.port.postMessage("stop");
  await done;
  const total = ctx.chunks.reduce((sum, chunk) => sum + chunk.l.length, 0);
  const left = new Float32Array(total);
  const right = new Float32Array(total);
  let at = 0;
  for (const chunk of ctx.chunks) {
    left.set(chunk.l, at);
    right.set(chunk.r, at);
    at += chunk.l.length;
  }
  const first = ctx.chunks[0]?.first ?? 0;
  ctx.chunks = [];
  return {
    sampleRate: ctx.sampleRate,
    firstFrame: first,
    t0: clockStart.t0,
    c0: clockStart.c0,
    // the video time of the first recorded sample: t0 + (context time - c0)
    startVideoS: clockStart.t0 + (first / ctx.sampleRate - clockStart.c0),
    left: floatsToBase64(left),
    right: floatsToBase64(right),
    audioDriven,
  };
}

function say(text: string): void {
  log.textContent = text;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${path}: ${response.status} ${await response.text()}`);
  return (await response.json()) as T;
}

async function signIn(username: string, password: string): Promise<string> {
  const status = await api<{ needs_first_run?: boolean }>("/api/auth/status");
  if (status.needs_first_run) throw new Error("The test account is not made yet.");
  await api("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, remember: false, remember_username: false }),
  });
  return "signed in";
}

/** As the app's `mediaThumbUrl` (api/preview.ts): the sized JPEG, 16 to 2160 pixels tall. */
function thumbUrl(projectId: string, path: string, height: number): string {
  const h = Math.min(MAX_THUMB_HEIGHT, Math.max(16, Math.round(height)));
  return `/api/projects/${encodeURIComponent(projectId)}/media/thumb?${new URLSearchParams({ path, h: String(h) })}`;
}

function fileUrl(projectId: string, path: string): string {
  const safe = path.split(/[\\/]+/).filter(Boolean).map(encodeURIComponent).join("/");
  return `/api/projects/${encodeURIComponent(projectId)}/files/${safe}`;
}

/** A flat grey picture (as a blob URL) per picture path, the picture's own aspect. */
async function makeGrey(bundle: PreviewBundle): Promise<void> {
  for (const picture of bundle.pictures ?? []) {
    if (greyUrls.has(picture.path)) continue;
    const ratio = picture.width > 0 && picture.height > 0 ? picture.width / picture.height : 16 / 9;
    const height = 540;
    const canvas = new OffscreenCanvas(Math.max(2, Math.round(height * ratio)), height);
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "rgb(128,128,128)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    greyUrls.set(picture.path, URL.createObjectURL(await canvas.convertToBlob({ type: "image/png" })));
  }
}

/** The picture a request is for (the sized JPEG route or the project files route), if any. */
function pictureOf(url: string, projectId: string): string | null {
  try {
    const parsed = new URL(url, location.href);
    if (parsed.pathname.endsWith("/media/thumb")) return parsed.searchParams.get("path");
    const files = `/api/projects/${encodeURIComponent(projectId)}/files/`;
    if (parsed.pathname.startsWith(files)) {
      const path = decodeURIComponent(parsed.pathname.slice(files.length));
      return /\.(png|jpe?g|webp)$/i.test(path) && !/\.depth\.png$/i.test(path) ? path : null;
    }
  } catch {
    return null;
  }
  return null;
}

/** In grey mode every picture the engine reads (sized JPEG or the file itself) is flat grey. */
function greyFetch(projectId: string): typeof fetch {
  return (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = pictureOf(url, projectId);
    const grey = path ? (greyUrls.get(path) ?? greyUrls.values().next().value) : undefined;
    return grey ? fetch(grey) : fetch(input, init);
  };
}

async function loadCatalogue(): Promise<unknown> {
  if (catalogue) return catalogue;
  try {
    catalogue = await api("/transitions/catalogue.json");
  } catch {
    catalogue = null;
  }
  return catalogue;
}

function close(): void {
  current?.handle.destroy();
  current = null;
  stage.innerHTML = "";
}

async function open(projectId: string, options: OpenOptions) {
  close();
  const started = performance.now();
  const [raw] = await Promise.all([api<PreviewBundle>(`/api/projects/${encodeURIComponent(projectId)}/timeline`), loadCatalogue()]);
  const bundleMs = performance.now() - started;
  let bundle = raw;
  if (options.noCaptions || options.grey) {
    bundle = JSON.parse(JSON.stringify(raw)) as PreviewBundle;
    if (options.noCaptions && bundle.timeline.captions) bundle.timeline.captions = { ...bundle.timeline.captions, enabled: false };
    if (options.grey) {
      // flat grey pictures, no popups and plain cuts: only the captions differ from grey
      await makeGrey(bundle);
      bundle.timeline = {
        ...bundle.timeline,
        scenes: bundle.timeline.scenes.map((scene) => ({ ...scene, transition_out: scene.transition_out ? { ...scene.transition_out, duration_s: 0 } : scene.transition_out })),
      };
    }
  }
  stage.style.width = `${options.width}px`;
  stage.style.height = `${options.height}px`;
  const canvas = document.createElement("canvas");
  stage.appendChild(canvas);
  const sized = new Set((bundle.pictures ?? []).filter((p) => p.width > 0 && p.height > 0).map((p) => p.path));
  const handle = createPreview(canvas, bundle, {
    projectId,
    quality: options.quality ?? "full",
    renderer: options.renderer ?? "auto",
    maxSize: [Math.max(options.width, options.height), Math.min(options.width, options.height)],
    audio: options.audio ?? false,
    popupsEnabled: !options.grey,
    fetch: options.grey ? greyFetch(projectId) : undefined,
    catalogue: catalogue as never,
    pictureUrl: (path, height) =>
      options.grey ? greyUrls.get(path) ?? "" : sized.has(path) && !options.fullPictures ? thumbUrl(projectId, path, height) : fileUrl(projectId, path),
    voiceSegmentUrl: (sid) => `/api/projects/${encodeURIComponent(projectId)}/media/voice/segments/${encodeURIComponent(sid)}.wav`,
    popupPreview: (_key: string, request: PopupPreviewRequest) =>
      api<PopupPreviewResult>(`/api/projects/${encodeURIComponent(projectId)}/popups/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      }),
  });
  current = { handle, bundle, projectId, opened: started };
  let status: unknown = null;
  handle.on("status", (value) => {
    status = { ...(status as object), ...value };
  });
  const errors: string[] = [];
  handle.on("error", (message) => errors.push(message));
  await handle.ready;
  const readyMs = performance.now() - started;
  say(`${projectId}: ready in ${readyMs.toFixed(0)} ms`);
  return { bundleMs, readyMs, status, errors, voiceGain: VOICE_UPMIX_GAIN, description: describe(handle) };
}

function describe(handle: Handle) {
  const d = handle.description();
  return {
    fps: d.fps,
    width: d.width,
    height: d.height,
    aspect: d.aspect,
    firstFrame: d.firstFrame,
    totalFrames: d.totalFrames,
    durationS: d.durationS,
    scenes: d.scenes.map((s) => ({
      number: s.number,
      index: s.index,
      image: s.image,
      startFrame: s.startFrame,
      length: s.length,
      leadIn: s.leadIn,
      tailOut: s.tailOut,
      clipFrames: s.clipFrames,
      bodyStart: s.bodyStart,
      body: s.body,
      style: s.style,
    })),
    cuts: d.cuts.map((c) => ({
      number: c.number,
      from: c.from,
      to: c.to,
      startFrame: c.startFrame,
      frames: c.frames,
      transition: c.transition?.id ?? null,
      fadeColour: c.fadeColour,
    })),
    cues: d.cues.map((c) => ({ key: c.key, startS: c.startS, endS: c.endS, text: c.text })),
    popups: d.popups.map((p) => ({ key: p.key, startS: p.startS, endS: p.endS, anim: p.anim, text: p.text })),
    captionsEnabled: d.captionsEnabled,
    captionStyle: d.captionStyle,
    audio: d.audio,
  };
}

async function toBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** Frame `n` as the engine draws it, as a base64 PNG. */
async function frame(n: number): Promise<string> {
  if (!current) throw new Error("No project is open.");
  const { handle } = current;
  await handle.renderFrame(n / handle.description().fps);
  const image = handle.snapshot();
  if (!image) throw new Error("The engine gave no picture.");
  const canvas = new OffscreenCanvas(image.width, image.height);
  canvas.getContext("2d")!.putImageData(image, 0, 0);
  return toBase64(await canvas.convertToBlob({ type: "image/png" }));
}

// Performance ------------------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function engineField<T>(name: string): T {
  return (current!.handle as unknown as Record<string, T>)[name];
}

/** Waits until the frame of the last seek is on screen with all its pictures; its time in ms. */
async function settleSeek(limitMs = 5000): Promise<number | null> {
  const deadline = performance.now() + limitMs;
  while (performance.now() < deadline) {
    if (engineField<number | null>("seekStarted") === null) return current!.handle.stats().lastSeekMs;
    await sleep(2);
  }
  return null;
}

/**
 * Waits until the pictures the frames at `times` need are loaded (or found missing) and the
 * engine has sent the loaded ones to the GPU (it does so one at a time in the background after
 * they load: a cached seek is one to a picture that is ready in both places).
 */
async function settlePictures(times: number[], limitMs = 8000): Promise<boolean> {
  const handle = current!.handle;
  const desc = handle.description();
  const store = engineField<{ get(path: string): unknown }>("pictures");
  const images = new Set<string>();
  for (const t of times) {
    const state = frameAt(desc, Math.max(0, Math.min(Math.floor(t * desc.fps + 1e-6), desc.totalFrames - 1)));
    if (state.kind === "empty") continue;
    images.add(state.a.scene.image);
    if (state.kind === "cut") images.add(state.b.scene.image);
  }
  const deadline = performance.now() + limitMs;
  while (performance.now() < deadline) {
    const uploaded = engineField<string[]>("uploadQueue").length === 0 && engineField<unknown>("uploadTimer") === null;
    if (uploaded && [...images].every((image) => store.get(image) !== null)) return true;
    await sleep(20);
  }
  return false;
}

/** Waits until the engine has compiled the transitions' shaders it warms up while idle. */
async function warmedUp(limitMs = 20000): Promise<number> {
  const started = performance.now();
  while (performance.now() - started < limitMs) {
    const queue = engineField<string[]>("warmQueue");
    if ((!queue || queue.length === 0) && engineField<unknown>("warmTimer") === null) break;
    await sleep(50);
  }
  return performance.now() - started;
}

/**
 * After the warm-up: redraws the frame on screen until three redraws in a row are on screen
 * within 50 ms. The GPU driver finishes the warmed-up shaders in the background, and a present
 * in the next few hundred milliseconds can wait 0.2 to 0.5 s for it (a trace shows
 * `SkiaOutputSurfaceImplOnGpu::SwapBuffers`); seeks are timed after that. Its time in ms.
 */
async function settleGpu(limitMs = 4000): Promise<number> {
  const started = performance.now();
  const engine = current!.handle as unknown as { requestDraw(force: boolean): void };
  let fast = 0;
  while (fast < 3 && performance.now() - started < limitMs) {
    const t0 = performance.now();
    engine.requestDraw(true);
    // the redraw, then the frame after it (by then the redraw has been presented)
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    fast = performance.now() - t0 < 50 ? fast + 1 : 0;
    await sleep(100);
  }
  return performance.now() - started;
}

async function seek(seconds: number): Promise<number | null> {
  current!.handle.seek(seconds);
  return settleSeek();
}

/**
 * The time of the animation frame being run (requestAnimationFrame's timestamp, the screen
 * refresh it is drawn for): a frame's place on screen, without how late its callback ran.
 */
let refreshNow = 0;
function watchRefreshes(): void {
  const marked = window as unknown as { __parityRaf?: boolean };
  if (marked.__parityRaf) return;
  const original = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (callback: FrameRequestCallback) =>
    original((time) => {
      refreshNow = time;
      callback(time);
    });
  marked.__parityRaf = true;
}

/**
 * Plays `seconds` from `from` and reports the frames shown and dropped, the drawing cost and the
 * gaps between the screen refreshes the frames were drawn for (two refreshes, 33 ms at 60 Hz, is
 * even pacing; a gap over 45 ms is a picture held a refresh longer).
 */
async function play(from: number, seconds: number) {
  watchRefreshes();
  const handle = current!.handle;
  handle.seek(from);
  await settleSeek();
  const before = handle.stats();
  const costs: number[] = [];
  const gaps: number[] = [];
  let lastFrame = 0;
  const stalls: { gapMs: number; frame: number; kind: string }[] = [];
  const off = handle.on("frame", (info) => {
    costs.push(handle.stats().lastFrameMs);
    const now = refreshNow || performance.now();
    if (lastFrame) {
      gaps.push(now - lastFrame);
      if (now - lastFrame > 45) stalls.push({ gapMs: Math.round(now - lastFrame), frame: info.frame, kind: info.kind });
    }
    lastFrame = now;
  });
  const longTasks: number[] = [];
  let observer: PerformanceObserver | null = null;
  try {
    observer = new PerformanceObserver((list) => list.getEntries().forEach((entry) => longTasks.push(entry.duration)));
    observer.observe({ type: "longtask", buffered: false });
  } catch {
    observer = null;
  }
  const started = performance.now();
  await handle.play();
  const audioDriven = () => (engineField<{ audioDriven: boolean }>("clock")).audioDriven;
  await sleep(250);
  const drivenEarly = audioDriven();
  await sleep(Math.max(0, seconds * 1000 - 250));
  const drivenLate = audioDriven();
  handle.pause();
  off();
  observer?.disconnect();
  const after = handle.stats();
  costs.sort((a, b) => a - b);
  gaps.sort((a, b) => a - b);
  const pick = (q: number) => (costs.length ? costs[Math.min(costs.length - 1, Math.floor(q * costs.length))]! : 0);
  return {
    from,
    seconds: (performance.now() - started) / 1000,
    shown: after.shown - before.shown,
    dropped: after.dropped - before.dropped,
    frameMsMedian: pick(0.5),
    frameMsP95: pick(0.95),
    frameMsMax: costs.length ? costs[costs.length - 1]! : 0,
    quality: after.quality,
    renderer: after.renderer,
    size: [after.width, after.height],
    audioDriven: drivenEarly && drivenLate,
    frameGapMsP95: gaps.length ? gaps[Math.min(gaps.length - 1, Math.floor(0.95 * gaps.length))]! : 0,
    frameGapMsMax: gaps.length ? gaps[gaps.length - 1]! : 0,
    gapsOver45: gaps.filter((gap) => gap > 45).length,
    stalls: stalls.slice(0, 20),
    longTasks: longTasks.length,
    longTaskMsMax: longTasks.length ? Math.max(...longTasks) : 0,
    longTaskMsTotal: longTasks.reduce((sum, value) => sum + value, 0),
  };
}

function memory() {
  const perf = performance as unknown as { memory?: { usedJSHeapSize: number; totalJSHeapSize: number } };
  const engine = current?.handle as unknown as { parallaxCounts?: () => unknown } | undefined;
  return {
    usedJSHeapMB: perf.memory ? perf.memory.usedJSHeapSize / 1048576 : null,
    totalJSHeapMB: perf.memory ? perf.memory.totalJSHeapSize / 1048576 : null,
    textures: current?.handle.stats().textures ?? null,
    // parallax plans and plates held (kept for the scenes around the playhead only)
    parallax: engine?.parallaxCounts?.() ?? null,
  };
}

/** Logs every audio source the engine starts (context time, offset, length) for the sync check. */
function watchSources(): void {
  const proto = AudioBufferSourceNode.prototype as unknown as { start: (...args: number[]) => void; __parity?: boolean };
  if (proto.__parity) return;
  const original = proto.start;
  proto.start = function (this: AudioBufferSourceNode, when = 0, offset = 0, duration?: number) {
    const audio = current ? engineField<{ t0: number; c0: number } | null>("audio") : null;
    scheduled.push({
      when,
      offset,
      duration: duration ?? (this.buffer ? this.buffer.duration - offset : 0),
      length: this.buffer ? this.buffer.duration : 0,
      at: audio ? audio.t0 + (when - audio.c0) : Number.NaN,
    });
    return original.call(this, when, offset, duration as number);
  };
  proto.__parity = true;
}

function takeSources() {
  return scheduled.splice(0, scheduled.length);
}

/** Diagnostics: logs the engine's draws, picture loads and uploads with times (dev only). */
function traceEngine(): { events: [number, string][] } {
  const engine = current!.handle as unknown as Record<string, (...args: unknown[]) => unknown>;
  const log: { events: [number, string][] } = { events: [] };
  const wrap = (name: string, describe: (args: unknown[]) => string) => {
    const original = engine[name]!.bind(engine);
    engine[name] = (...args: unknown[]) => {
      const t0 = performance.now();
      const result = original(...args);
      log.events.push([Math.round(t0), `${name} ${describe(args)} ${Math.round(performance.now() - t0)}ms`]);
      return result;
    };
  };
  wrap("drawFrame", (args) => `n=${String(args[0])}`);
  wrap("pictureLoaded", (args) => String(args[0]).slice(-12));
  wrap("uploadNext", () => "");
  (window as unknown as { __trace: typeof log }).__trace = log;
  return log;
}

(window as unknown as Record<string, unknown>).parity = {
  traceEngine: () => {
    traceEngine();
    return true;
  },
  traceEvents: () => (window as unknown as { __trace?: { events: [number, string][] } }).__trace?.events ?? [],
  signIn,
  open,
  close,
  frame,
  seek,
  warmedUp,
  settleGpu,
  settlePictures,
  play,
  memory,
  watchSources,
  takeSources,
  record,
  stats: () => current?.handle.stats() ?? null,
  description: () => (current ? describe(current.handle) : null),
};
say("ready");
