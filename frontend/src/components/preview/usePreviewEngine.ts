/**
 * Runs one preview engine inside a host element: creates the canvas and the engine when a
 * bundle arrives, hands a changed bundle to `engine.update` (or starts a fresh engine on the
 * same spot when the engine has no `update`), listens to its events, and destroys it with the
 * component. The canvas is made here, not by React, because the engine may swap it for a
 * Canvas 2D one after a lost WebGL context.
 *
 * Two kinds of trouble are kept apart: `error` means the preview could not start at all (the
 * monitor says so and offers the last render), `notice` is a problem the engine reported while
 * drawing ("The picture for scene 3 could not be loaded.") and is shown under the picture
 * without hiding it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  DEFAULT_ENGINE_STATUS,
  type PreviewEngine,
  type PreviewEngineFactory,
  type PreviewEngineOptions,
  type PreviewEngineStatus,
  type PreviewLoopRange,
  type PreviewQuality,
  type TimelineBundle,
} from "../../types/preview";
import { loadPreviewEngine } from "./engineLoader";
import { clampTime } from "./previewTime";
import type { TimeStore } from "./timeStore";

export interface PreviewController {
  play(): void;
  pause(): void;
  toggle(): void;
  seek(seconds: number): void;
  /** show the frame at `seconds` without moving the playhead; null goes back */
  skim(seconds: number | null): void;
  /** play from `from` to `to` once, then pause (play around a cut) */
  playRange(from: number, to: number): void;
}

export interface PreviewEngineState {
  /** an engine is drawing (its first frame is on screen, or it never said when) */
  ready: boolean;
  playing: boolean;
  status: PreviewEngineStatus;
  /** plain English, when the preview could not start */
  error: string | null;
  /** plain English, the last problem the engine reported while drawing (null when none) */
  notice: string | null;
  dismissNotice(): void;
  controller: PreviewController;
}

export type EngineOptionsBase = Omit<PreviewEngineOptions, "quality" | "loop">;

export interface UsePreviewEngineArgs {
  host: HTMLElement | null;
  bundle: TimelineBundle | null;
  quality: PreviewQuality;
  loop: PreviewLoopRange | null;
  options: EngineOptionsBase;
  store: TimeStore;
  /** true while something the engine is made with is still loading (it starts after) */
  hold?: boolean;
  /** replaces the engine loader (tests) */
  factory?: PreviewEngineFactory;
}

interface Running {
  engine: PreviewEngine;
  canvas: HTMLCanvasElement;
  host: HTMLElement;
  bundle: TimelineBundle;
  offs: Array<() => void>;
}

export const START_MESSAGE = "The live preview could not start. Watch the last render instead, or reload the page.";
/** An engine that never says its first frame is on screen is taken as ready after this long. */
export const READY_TIMEOUT_MS = 6000;

/**
 * Stops listening, destroys the engine and takes its canvas out of the host. The engine may
 * have put a Canvas 2D canvas in place of ours (after a lost WebGL context), so everything in
 * the host goes, except `keep` (the canvas of the engine that replaces this one).
 */
function dispose(running: Running, keep: Element | null = null) {
  for (const off of running.offs) {
    try {
      off();
    } catch {
      /* already gone */
    }
  }
  try {
    running.engine.destroy();
  } catch {
    /* the engine is going away anyway */
  }
  running.canvas.remove();
  for (const child of [...running.host.children]) if (child !== keep) child.remove();
}

function durationOf(bundle: TimelineBundle | null): number {
  const timeline = bundle?.timeline;
  if (!timeline) return 0;
  const lastEnd = timeline.scenes.length > 0 ? timeline.scenes[timeline.scenes.length - 1]!.end_s : 0;
  return Math.max(timeline.duration_s, lastEnd);
}

function statusFrom(raw: unknown): Partial<PreviewEngineStatus> {
  if (typeof raw !== "object" || raw === null) return {};
  const value = raw as Record<string, unknown>;
  const out: Partial<PreviewEngineStatus> = {};
  if (value.renderer === "webgl2" || value.renderer === "canvas2d") out.renderer = value.renderer;
  if (typeof value.restarting === "boolean") out.restarting = value.restarting;
  if (typeof value.simplified === "boolean") out.simplified = value.simplified;
  if (typeof value.mockMedia === "boolean") out.mockMedia = value.mockMedia;
  if (value.activeQuality === "full" || value.activeQuality === "half") out.activeQuality = value.activeQuality;
  return out;
}

/** The engine's `ready` promise (resolved when its first frame is on screen), when it has one. */
function readyOf(engine: PreviewEngine): PromiseLike<unknown> | null {
  const ready = (engine as { ready?: unknown }).ready;
  return typeof ready === "object" && ready !== null && typeof (ready as { then?: unknown }).then === "function" ? (ready as PromiseLike<unknown>) : null;
}

export function usePreviewEngine({ host, bundle, quality, loop, options, store, hold = false, factory }: UsePreviewEngineArgs): PreviewEngineState {
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [status, setStatus] = useState<PreviewEngineStatus>(DEFAULT_ENGINE_STATUS);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNoticeState] = useState<string | null>(null);
  const noticeRef = useRef<string | null>(null);
  const setNotice = useCallback((value: string | null) => {
    noticeRef.current = value;
    setNoticeState(value);
  }, []);

  const runningRef = useRef<Running | null>(null);
  const playingRef = useRef(false);
  const stopAtRef = useRef<number | null>(null);
  const lastTimeEventRef = useRef(0);
  const clockRef = useRef<{ from: number; at: number } | null>(null);
  const bundleRef = useRef(bundle);
  bundleRef.current = bundle;
  const qualityRef = useRef(quality);
  qualityRef.current = quality;
  const loopRef = useRef(loop);
  loopRef.current = loop;
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const setPlayingBoth = useCallback(
    (value: boolean) => {
      playingRef.current = value;
      setPlaying(value);
      clockRef.current = value ? { from: store.get(), at: performance.now() } : null;
    },
    [store],
  );

  const pauseEngine = useCallback(() => {
    stopAtRef.current = null;
    try {
      runningRef.current?.engine.pause();
    } catch {
      /* nothing to pause */
    }
    setPlayingBoth(false);
  }, [setPlayingBoth]);

  const onTime = useCallback(
    (seconds: number) => {
      if (!Number.isFinite(seconds)) return;
      lastTimeEventRef.current = performance.now();
      store.set(seconds);
      const stopAt = stopAtRef.current;
      if (stopAt !== null && seconds >= stopAt) pauseEngine();
    },
    [store, pauseEngine],
  );

  // Create (or update) the engine for the current bundle.
  useEffect(() => {
    if (!host || !bundle || hold) return;
    const current = runningRef.current;
    if (current && current.host !== host) {
      // The monitor came back (after "Watch the last render"): move the canvas, keep the engine.
      while (current.host.firstChild) host.appendChild(current.host.firstChild);
      current.host = host;
    }
    if (current && current.bundle === bundle) return;
    if (current && typeof current.engine.update === "function") {
      try {
        current.engine.update(bundle);
        current.bundle = bundle;
        setNotice(null);
        const duration = durationOf(bundle);
        if (store.get() > duration) current.engine.seek(duration);
        return;
      } catch {
        /* fall through: start a fresh engine with the new bundle */
      }
    }
    let cancelled = false;
    let readyTimer: ReturnType<typeof setTimeout> | null = null;
    const resumeAt = store.get();
    const resume = playingRef.current;
    const make = factory ? Promise.resolve(factory) : loadPreviewEngine();
    make
      .then((create) => {
        if (cancelled) return;
        const canvas = document.createElement("canvas");
        canvas.className = "absolute inset-0 block size-full";
        canvas.setAttribute("aria-hidden", "true");
        host.appendChild(canvas);
        let engine: PreviewEngine;
        try {
          engine = create(canvas, bundle, { ...optionsRef.current, quality: qualityRef.current, loop: loopRef.current });
        } catch {
          canvas.remove();
          setError(START_MESSAGE);
          return;
        }
        const offs: Array<() => void> = [];
        const listen = (event: string, handler: (...args: never[]) => void) => {
          try {
            const off = (engine.on as unknown as (name: string, fn: (...args: never[]) => void) => unknown)(event, handler);
            if (typeof off === "function") offs.push(off as () => void);
          } catch {
            /* an engine without this event */
          }
        };
        listen("time", (seconds: number) => onTime(Number(seconds)));
        listen("play", () => setPlayingBoth(true));
        listen("pause", () => {
          stopAtRef.current = null;
          setPlayingBoth(false);
        });
        listen("ended", () => {
          stopAtRef.current = null;
          setPlayingBoth(false);
        });
        listen("status", (raw: unknown) => setStatus((previous) => ({ ...previous, ...statusFrom(raw) })));
        listen("error", (message: unknown) => {
          if (typeof message === "string" && message.trim()) setNotice(message.trim());
        });

        const previous = runningRef.current;
        runningRef.current = { engine, canvas, host, bundle, offs };
        if (previous) dispose(previous, canvas);
        setError(null);
        setNotice(null);

        // Ready once the engine says its first frame is on screen. An engine that has not drawn
        // anything after a while is taken as ready, unless it said what went wrong meanwhile
        // ("This computer cannot draw the live preview."): then the preview did not start.
        const isCurrent = () => runningRef.current?.engine === engine;
        const ready = readyOf(engine);
        if (ready) {
          setReady(false);
          readyTimer = setTimeout(() => {
            if (!isCurrent()) return;
            if (noticeRef.current) setError(noticeRef.current);
            else setReady(true);
          }, READY_TIMEOUT_MS);
          ready.then(
            () => {
              if (readyTimer !== null) clearTimeout(readyTimer);
              if (!isCurrent()) return;
              setError(null);
              setReady(true);
            },
            () => {
              if (readyTimer !== null) clearTimeout(readyTimer);
              if (isCurrent()) setError(START_MESSAGE);
            },
          );
        } else {
          setReady(true);
        }
        try {
          engine.seek(clampTime(resumeAt, durationOf(bundle)));
          if (resume) void Promise.resolve(engine.play()).catch(() => setPlayingBoth(false));
        } catch {
          /* the first frame comes with the next seek */
        }
      })
      .catch(() => {
        if (!cancelled) setError(START_MESSAGE);
      });
    return () => {
      cancelled = true;
    };
  }, [host, bundle, hold, factory, store, onTime, setPlayingBoth, setNotice]);

  // Destroy with the component.
  useEffect(
    () => () => {
      if (runningRef.current) dispose(runningRef.current);
      runningRef.current = null;
    },
    [],
  );

  useEffect(() => {
    try {
      runningRef.current?.engine.setQuality(quality);
    } catch {
      /* applied with the next engine */
    }
  }, [quality, ready]);

  useEffect(() => {
    try {
      runningRef.current?.engine.setLoop(loop);
    } catch {
      /* applied with the next engine */
    }
  }, [loop, ready]);

  // A clock of last resort: an engine that never reports its time still moves the display.
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      const clock = clockRef.current;
      if (!clock || performance.now() - lastTimeEventRef.current < 500) return;
      const duration = durationOf(bundleRef.current);
      const estimate = clock.from + (performance.now() - clock.at) / 1000;
      store.set(clampTime(estimate, duration));
      if (estimate >= duration && !loopRef.current) setPlayingBoth(false);
    }, 200);
    return () => window.clearInterval(timer);
  }, [playing, store, setPlayingBoth]);

  const controller = useMemo<PreviewController>(() => {
    const engine = () => runningRef.current?.engine ?? null;
    const play = () => {
      const current = engine();
      if (!current) return;
      setPlayingBoth(true);
      try {
        void Promise.resolve(current.play()).catch(() => setPlayingBoth(false));
      } catch {
        setPlayingBoth(false);
      }
    };
    const seek = (seconds: number) => {
      stopAtRef.current = null;
      const target = clampTime(seconds, durationOf(bundleRef.current));
      store.set(target);
      if (clockRef.current) clockRef.current = { from: target, at: performance.now() };
      try {
        engine()?.seek(target);
      } catch {
        /* no engine yet: the time waits in the store */
      }
    };
    return {
      play,
      pause: pauseEngine,
      toggle: () => (playingRef.current ? pauseEngine() : play()),
      seek,
      skim(seconds) {
        try {
          engine()?.skim(seconds);
        } catch {
          /* skimming is optional */
        }
      },
      playRange(from, to) {
        seek(from);
        play();
        stopAtRef.current = to;
      },
    };
  }, [pauseEngine, setPlayingBoth, store]);

  const dismissNotice = useCallback(() => setNotice(null), [setNotice]);

  return { ready, playing, status, error, notice, dismissNotice, controller };
}
