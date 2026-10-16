/**
 * The preview's clock (design 5.2): while playing, the master clock is the audio clock
 * (`t = t0 + (audioTime - c0) x rate`, falling back to the performance clock when the audio
 * context cannot run); pictures are drawn on `requestAnimationFrame`. Every drawn picture is the
 * frame the renderer would output, `n = floor(t x fps + 1e-6)`; a frame that comes too late is
 * dropped (the next animation frame shows the frame for its own time), audio never is. While
 * playing, the playhead of a drawn frame is the audio clock read at the animation frame's own
 * time and followed smoothly (`PlaybackClock.frameTime`), so every video frame stays on screen
 * for the same number of refreshes. While paused or scrubbing, frames are drawn on demand, at
 * most one per animation frame.
 */

/** `n = floor(t x fps + 1e-6)`. */
export function frameNumber(t: number, fps: number): number {
  return Math.floor(t * fps + 1e-6);
}

export interface TimeSource {
  /** seconds on the video clock while running */
  now(): number;
  /** the same clock at `perfMs` on the performance clock (an animation frame's time) */
  nowAt?(perfMs: number): number;
  readonly running: boolean;
}

/** How much of the difference to the audio clock a drawn frame's playhead takes up per frame. */
export const FRAME_CLOCK_FOLLOW = 0.1;
/** A difference bigger than this (seconds) is a jump (a seek, a loop, a stall), taken at once. */
export const FRAME_CLOCK_SNAP_S = 0.04;

/** Keeps the playhead: the audio clock when it runs, else the performance clock. */
export class PlaybackClock {
  private playing = false;
  private t0 = 0;
  private p0 = 0;
  private rate = 1;

  constructor(
    private readonly audio: TimeSource | null,
    private readonly perfNow: () => number = () => (typeof performance !== "undefined" ? performance.now() : Date.now()),
  ) {}

  get isPlaying(): boolean {
    return this.playing;
  }

  /** the last time handed out while playing: the playhead never runs backwards */
  private last = 0;
  /** drawn frames: the playhead minus the animation frame's time (seconds), followed smoothly */
  private offset: number | null = null;

  start(t: number, rate = 1): void {
    this.playing = true;
    this.t0 = t;
    this.rate = rate;
    this.p0 = this.perfNow();
    this.last = t;
    this.offset = null;
  }

  stop(): number {
    const t = this.time();
    this.playing = false;
    this.t0 = t;
    this.offset = null;
    return t;
  }

  set(t: number): void {
    this.t0 = t;
    this.p0 = this.perfNow();
    this.last = t;
    this.offset = null;
  }

  /**
   * The playhead in seconds. When the audio clock takes over it may be a few milliseconds behind
   * the performance clock (the sound leaves the speakers a little later): the picture then holds
   * until the sound catches up instead of stepping back.
   */
  time(): number {
    if (!this.playing) return this.t0;
    const t =
      this.audio && this.audio.running && this.rate === 1 ? this.audio.now() : this.t0 + ((this.perfNow() - this.p0) / 1000) * this.rate;
    if (t > this.last) this.last = t;
    return this.last;
  }

  /**
   * The playhead of the animation frame stamped `frameMs` (requestAnimationFrame's time, on the
   * performance clock). Its callback runs a little after that moment, later or sooner each time;
   * reading the clock when it runs would make a frame number that changes right at a frame's
   * start come one refresh early or late (one picture held three refreshes, the next one), a
   * visible stutter. So the audio clock (or the performance clock) is read at the frame's own
   * time and followed smoothly; a jump (a seek, a loop, a stall) is taken at once.
   */
  frameTime(frameMs: number): number {
    if (!this.playing) return this.t0;
    const audio = this.audio;
    const raw =
      audio && audio.running && this.rate === 1
        ? audio.nowAt
          ? audio.nowAt(frameMs)
          : audio.now()
        : this.t0 + ((frameMs - this.p0) / 1000) * this.rate;
    const frameS = frameMs / 1000;
    const error = this.offset === null ? Infinity : raw - (this.offset + frameS);
    if (Math.abs(error) > FRAME_CLOCK_SNAP_S) this.offset = raw - frameS;
    else this.offset = this.offset! + error * FRAME_CLOCK_FOLLOW;
    const t = this.offset + frameS;
    if (t > this.last) this.last = t;
    return this.last;
  }

  /** True when the audio clock drives the picture (for diagnostics). */
  get audioDriven(): boolean {
    return this.playing && !!this.audio && this.audio.running && this.rate === 1;
  }
}

/** Decides which video frame to draw at each animation frame and counts the dropped ones. */
export class FramePacer {
  private last: number | null = null;
  dropped = 0;
  shown = 0;

  reset(): void {
    this.last = null;
  }

  /**
   * The frame to draw for playhead time `t`, or null when it is already on screen. Frames of
   * video time skipped since the last one drawn count as dropped.
   */
  next(t: number, fps: number): number | null {
    const n = frameNumber(t, fps);
    if (this.last !== null && n === this.last) return null;
    if (this.last !== null && n > this.last + 1) this.dropped += n - this.last - 1;
    this.last = n;
    this.shown += 1;
    return n;
  }

  /** The share of frames dropped so far (0..1). */
  get dropRate(): number {
    const total = this.shown + this.dropped;
    return total ? this.dropped / total : 0;
  }
}

export interface AutoQualityOptions {
  /** a frame slower than this counts against Full (ms) */
  slowMs?: number;
  /** this many slow frames drop to Half */
  slowCount?: number;
  /** back to Full after this long without a slow frame (ms) */
  recoverMs?: number;
  /** a frame under this share of `slowMs` counts as headroom */
  headroomShare?: number;
}

/** Auto quality: drops to Half after 3 frames over 33 ms, returns after 5 s of headroom. */
export class AutoQuality {
  private readonly slowMs: number;
  private readonly slowCount: number;
  private readonly baseRecoverMs: number;
  private readonly headroomShare: number;
  private slow = 0;
  private level: "full" | "half" = "full";
  private calmSince: number | null = null;
  private recoverMs: number;

  constructor(options: AutoQualityOptions = {}) {
    this.slowMs = options.slowMs ?? 33;
    this.slowCount = options.slowCount ?? 3;
    this.baseRecoverMs = options.recoverMs ?? 5000;
    this.recoverMs = this.baseRecoverMs;
    this.headroomShare = options.headroomShare ?? 0.6;
  }

  get active(): "full" | "half" {
    return this.level;
  }

  reset(): void {
    this.slow = 0;
    this.level = "full";
    this.calmSince = null;
    this.recoverMs = this.baseRecoverMs;
  }

  /** Reports one played frame's cost; returns the level to use from now on. */
  report(frameMs: number, nowMs: number): "full" | "half" {
    if (this.level === "full") {
      if (frameMs > this.slowMs) {
        this.slow += 1;
        if (this.slow >= this.slowCount) {
          this.level = "half";
          this.slow = 0;
          this.calmSince = nowMs;
        }
      }
      return this.level;
    }
    if (frameMs > this.slowMs * this.headroomShare) {
      this.calmSince = nowMs;
    } else if (this.calmSince !== null && nowMs - this.calmSince >= this.recoverMs) {
      this.level = "full";
      this.calmSince = null;
      // if Full is too slow again, wait longer before the next try
      this.recoverMs = Math.min(this.recoverMs * 2, 60000);
    }
    return this.level;
  }
}

/**
 * An animation frame that has not come after this long comes from a timer instead: some views
 * hold animation frames back while they still say they are visible (an embedded browser view
 * that is not on screen), and the preview would stop answering seeks there.
 */
export const FRAME_FALLBACK_MS = 100;

/**
 * One callback per animation frame. A hidden page gets no animation frames, so a timer stands in
 * (the frame still reaches the canvas, and a paused preview keeps answering seeks); so does a
 * timer when an animation frame is held back for {@link FRAME_FALLBACK_MS}.
 */
export function requestFrame(callback: (time: number) => void): () => void {
  const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());
  const hidden = typeof document !== "undefined" && document.visibilityState === "hidden";
  if (typeof requestAnimationFrame !== "function" || hidden) {
    const id = setTimeout(() => callback(now()), 16);
    return () => clearTimeout(id);
  }
  let done = false;
  const stop = () => {
    done = true;
    cancelAnimationFrame(frame);
    clearTimeout(timer);
  };
  const run = (time: number) => {
    if (done) return;
    stop();
    callback(time);
  };
  const frame = requestAnimationFrame(run);
  const timer = setTimeout(() => run(now()), FRAME_FALLBACK_MS);
  return stop;
}
