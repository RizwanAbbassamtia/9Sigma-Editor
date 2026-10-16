import { Columns2, Film, LoaderCircle, MonitorPlay, TriangleAlert, X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";

import { createPopupPreviewer, mediaThumbUrl, voiceSegmentUrl } from "../../api/preview";
import { cn } from "../../lib/cn";
import type { PreviewAspect, PreviewEngineFactory, PreviewLoopRange, PreviewQuality, TimelineBundle } from "../../types/preview";
import { isPreviewQuality } from "../../types/preview";
import { ProxyPlayer } from "../edit/ProxyPlayer";
import { useTransitionCatalogue } from "../storyboard/transitionCatalogue";
import { projectFile } from "./bundle";
import { PreviewChip, PreviewChips } from "./PreviewChips";
import { clampTime, cutTimes, describeFrame, lastFrameTime, nextCut, previousCut, stepFrames } from "./previewTime";
import { QualityMenu } from "./QualityMenu";
import { createTimeStore, useStoreTime, type TimeStore } from "./timeStore";
import { Transport } from "./Transport";
import { usePreviewEngine, type EngineOptionsBase } from "./usePreviewEngine";

export type MonitorMode = "live" | "render";

/** What a review panel can ask of the monitor (`controllerRef`). */
export interface LivePreviewHandle {
  /** move the playhead (the live preview, or the last render while it is shown) */
  seek(seconds: number, options?: { play?: boolean }): void;
  play(): void;
  pause(): void;
  /** show a frame without moving the playhead; null goes back */
  skim(seconds: number | null): void;
  /** play from `before` seconds ahead of `seconds` to `after` seconds past it, once */
  playAround(seconds: number, before?: number, after?: number): void;
  /** the time under the playhead */
  time(): number;
  monitor(): MonitorMode;
  /** play when paused, pause when playing (the storyboard's animatic strip has its own button) */
  toggle(): void;
  /** true while the live preview plays */
  playing(): boolean;
}

export interface LivePreviewProps {
  projectId: string;
  /** what to draw: the timeline bundle, or the storyboard's animatic */
  bundle: TimelineBundle | null;
  /** the bundle is on its way */
  loading?: boolean;
  /** why there is nothing to draw (shown in the monitor when there is no bundle) */
  unavailable?: ReactNode;
  aspect: PreviewAspect;
  /** "Preview" by default */
  title?: string;
  /** the storyboard's animatic rather than timeline.json */
  animatic?: boolean;
  /** the animatic's times are estimates (no voice yet) */
  estimatedTiming?: boolean;
  /** placeholder pictures or a silent voice */
  mockMedia?: boolean;
  /** the last render (the proxy video); adds the "Watch the last render" toggle */
  proxySrc?: string | null;
  /** a line under the last render ("Low-resolution render ...") */
  proxyNote?: ReactNode;
  /** the monitor to start with (the live preview when it can draw) */
  defaultMonitor?: MonitorMode;
  /** the playhead, a few times a second (and at once after a seek or a pause) */
  onTimeChange?: (seconds: number) => void;
  controllerRef?: Ref<LivePreviewHandle>;
  /** replaces the engine (tests) */
  engineFactory?: PreviewEngineFactory;
  /** under the transport (notices, the animatic's notes) */
  footer?: ReactNode;
  /** something the page is doing for the preview ("Preparing the music..."), shown as a chip */
  activity?: string | null;
  /** the picture's box (its size and aspect) when the default does not fit, e.g. inside a dialog */
  frameClassName?: string;
  /** take the keyboard focus when it opens (Space plays at once) */
  autoFocus?: boolean;
  className?: string;
  /**
   * The playhead store to use, so the page can draw the same playhead elsewhere (the
   * storyboard's mini timeline); the monitor makes its own when this is left out.
   */
  timeStore?: TimeStore;
  /**
   * "full" (the default): title row, picture and transport. "monitor": the picture alone, for
   * a page that draws its own controls (the storyboard's animatic strip).
   */
  chrome?: "full" | "monitor";
  /** drawn over the picture, inside the frame (the start and end boxes of "Edit on picture") */
  frameOverlay?: ReactNode;
  /**
   * The range that plays over and over, when the page decides it (the selected scene); null
   * plays straight through. Left out, the monitor's own loop button and In / Out marks decide.
   */
  loopRange?: PreviewLoopRange | null;
  /** the live preview started or stopped playing */
  onPlayingChange?: (playing: boolean) => void;
}

const QUALITY_KEY = "sge-preview-quality";
const REPORT_EVERY_MS = 250;
/** The longest the monitor waits for the transition catalogue before it starts without it. */
const CATALOGUE_WAIT_MS = 1500;

function readQuality(): PreviewQuality {
  try {
    const saved = window.localStorage.getItem(QUALITY_KEY);
    return isPreviewQuality(saved) ? saved : "auto";
  } catch {
    return "auto";
  }
}

function saveQuality(quality: PreviewQuality) {
  try {
    window.localStorage.setItem(QUALITY_KEY, quality);
  } catch {
    /* private window: the choice lasts until the page closes */
  }
}

function durationOf(bundle: TimelineBundle | null): number {
  const timeline = bundle?.timeline;
  if (!timeline) return 0;
  const lastEnd = timeline.scenes.length > 0 ? timeline.scenes[timeline.scenes.length - 1]!.end_s : 0;
  return Math.max(timeline.duration_s, lastEnd);
}

/** The frame's text alternative, updated once a second on its own. */
function FrameDescription({ store, bundle }: { store: TimeStore; bundle: TimelineBundle | null }) {
  const second = useStoreTime(store, 1);
  return <div role="img" aria-label={describeFrame(bundle, second)} className="pointer-events-none absolute inset-0" />;
}

function Overlay({ children, tone = "plain" }: { children: ReactNode; tone?: "plain" | "dim" }) {
  return (
    <div className={cn("absolute inset-0 flex flex-col items-center justify-center gap-2 p-6 text-center text-[#E8EDF7]", tone === "dim" && "bg-black/55")}>{children}</div>
  );
}

/**
 * The monitor of the review screens (docs/M5-CONTRACT.md sections 4 and 5): the live preview
 * drawn by the engine from the timeline (or the storyboard's animatic), its transport, quality
 * menu, clock in tenths of a second and status chips, plus "Watch the last render" to switch to
 * the proxy video and a "Compare" placeholder. Keyboard: Space or K plays, Left and Right step a
 * frame (Shift: a second), Up and Down jump between cuts, Home and End, I and O mark the range,
 * Alt+X clears it, Ctrl+L loops.
 */
export function LivePreview({
  projectId,
  bundle,
  loading = false,
  unavailable,
  aspect,
  title = "Preview",
  animatic = false,
  estimatedTiming = false,
  mockMedia = false,
  proxySrc = null,
  proxyNote,
  defaultMonitor = "live",
  onTimeChange,
  controllerRef,
  engineFactory,
  footer,
  activity = null,
  frameClassName,
  autoFocus = false,
  className,
  timeStore,
  chrome = "full",
  frameOverlay,
  loopRange,
  onPlayingChange,
}: LivePreviewProps) {
  const ownStore = useMemo(() => createTimeStore(0), []);
  const store = timeStore ?? ownStore;
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const [quality, setQuality] = useState<PreviewQuality>(readQuality);
  const [monitor, setMonitor] = useState<MonitorMode>(defaultMonitor);
  const [inPoint, setInPoint] = useState<number | null>(null);
  const [outPoint, setOutPoint] = useState<number | null>(null);
  const [loopOn, setLoopOn] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const sectionRef = useRef<HTMLElement | null>(null);
  const pendingVideoSeek = useRef<{ time: number; play: boolean } | null>(null);

  const duration = durationOf(bundle);
  const fps = bundle?.timeline.fps && bundle.timeline.fps > 0 ? bundle.timeline.fps : 30;
  const cuts = useMemo(() => cutTimes(bundle?.timeline ?? null), [bundle]);
  const liveAvailable = bundle !== null && bundle.timeline.scenes.length > 0;
  // With nothing to draw and a render to show, the last render stands in until the bundle comes.
  const shownMonitor: MonitorMode = monitor === "live" && !liveAvailable && !loading && proxySrc ? "render" : monitor;

  const loop = useMemo<PreviewLoopRange | null>(() => {
    if (loopRange !== undefined) {
      if (loopRange === null) return null;
      const from = clampTime(loopRange.in_s, duration);
      const to = clampTime(loopRange.out_s, duration);
      return to - from > 1 / fps ? { in_s: from, out_s: to } : { in_s: 0, out_s: duration };
    }
    if (!loopOn) return null;
    const from = clampTime(inPoint ?? 0, duration);
    const to = clampTime(outPoint ?? duration, duration);
    return to - from > 1 / fps ? { in_s: from, out_s: to } : { in_s: 0, out_s: duration };
  }, [loopRange, loopOn, inPoint, outPoint, duration, fps]);

  // Each transition's curve and settings come from the catalogue: the engine starts once it is
  // in (it is a small local file, usually already loaded by the review), or once it failed.
  const catalogueQuery = useTransitionCatalogue();
  const catalogue = catalogueQuery.data ?? null;
  const [catalogueWaitOver, setCatalogueWaitOver] = useState(false);
  const catalogueLoading = catalogueQuery.isPending && catalogueQuery.failureCount === 0;
  useEffect(() => {
    if (!catalogueLoading) return;
    const timer = setTimeout(() => setCatalogueWaitOver(true), CATALOGUE_WAIT_MS);
    return () => clearTimeout(timer);
  }, [catalogueLoading]);
  // A picture whose size the bundle knows comes as the server's JPEG at the height the engine
  // asks for; one of unknown size comes whole, so the engine reads its real size from the file
  // (the camera rectangles are in the picture's own pixels).
  const sizedPaths = useRef<ReadonlySet<string>>(new Set());
  sizedPaths.current = useMemo(() => new Set((bundle?.pictures ?? []).filter((picture) => picture.width > 0 && picture.height > 0).map((picture) => picture.path)), [bundle]);
  const options = useMemo<EngineOptionsBase>(
    () => ({
      projectId,
      scrubAudio: false,
      animatic,
      mockMedia,
      popupPreview: createPopupPreviewer(projectId),
      pictureUrl: (path, height) =>
        sizedPaths.current.has(path) ? mediaThumbUrl(projectId, path, height) : (projectFile(projectId, path) ?? mediaThumbUrl(projectId, path, height)),
      voiceSegmentUrl: (sentenceId) => voiceSegmentUrl(projectId, sentenceId),
      catalogue,
    }),
    [projectId, animatic, mockMedia, catalogue],
  );

  const engine = usePreviewEngine({
    host: shownMonitor === "live" ? host : null,
    bundle: liveAvailable ? bundle : null,
    quality,
    loop,
    options,
    store,
    hold: catalogueLoading && !catalogueWaitOver,
    factory: engineFactory,
  });
  const { controller } = engine;

  const playingRef = useRef(engine.playing);
  playingRef.current = engine.playing;
  const onPlayingRef = useRef(onPlayingChange);
  onPlayingRef.current = onPlayingChange;
  useEffect(() => {
    onPlayingRef.current?.(engine.playing);
  }, [engine.playing]);

  // Report the playhead a few times a second, and at once after a jump.
  const onTimeRef = useRef(onTimeChange);
  onTimeRef.current = onTimeChange;
  useEffect(() => {
    let lastAt = 0;
    let lastValue = store.get();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const report = () => {
      timer = null;
      lastAt = performance.now();
      lastValue = store.get();
      onTimeRef.current?.(lastValue);
    };
    const unsubscribe = store.subscribe(() => {
      const now = performance.now();
      const jumped = Math.abs(store.get() - lastValue) > 1;
      if (jumped || now - lastAt >= REPORT_EVERY_MS) {
        if (timer !== null) clearTimeout(timer);
        report();
      } else if (timer === null) {
        timer = setTimeout(report, REPORT_EVERY_MS - (now - lastAt));
      }
    });
    return () => {
      unsubscribe();
      if (timer !== null) clearTimeout(timer);
    };
  }, [store]);

  // Pause the hidden monitor when switching; the other one picks up the same time.
  const switchMonitor = useCallback(
    (next: MonitorMode) => {
      if (next === shownMonitor) return;
      if (next === "render") {
        controller.pause();
        pendingVideoSeek.current = { time: store.get(), play: false };
      } else {
        videoRef.current?.pause();
      }
      setMonitor(next);
    },
    [controller, shownMonitor, store],
  );

  useEffect(() => {
    if (shownMonitor === "live" && engine.ready) controller.seek(store.get());
  }, [shownMonitor, engine.ready, controller, store]);

  useEffect(() => {
    if (autoFocus) sectionRef.current?.focus({ preventScroll: true });
  }, [autoFocus]);

  const seekVideo = useCallback((seconds: number, play: boolean) => {
    const video = videoRef.current;
    if (!video || video.readyState < 1) {
      pendingVideoSeek.current = { time: seconds, play };
      return;
    }
    video.currentTime = Math.max(0, seconds);
    if (play) void video.play().catch(() => undefined);
  }, []);

  const handle = useMemo<LivePreviewHandle>(
    () => ({
      seek(seconds, seekOptions) {
        const target = Math.max(0, seconds);
        store.set(target);
        if (shownMonitor === "render") {
          seekVideo(target, Boolean(seekOptions?.play));
          return;
        }
        controller.seek(target);
        if (seekOptions?.play) controller.play();
      },
      play() {
        if (shownMonitor === "render") void videoRef.current?.play().catch(() => undefined);
        else controller.play();
      },
      pause() {
        if (shownMonitor === "render") videoRef.current?.pause();
        else controller.pause();
      },
      skim(seconds) {
        if (shownMonitor === "live") controller.skim(seconds);
      },
      playAround(seconds, before = 2, after = 2) {
        if (shownMonitor !== "live") return;
        controller.playRange(Math.max(0, seconds - before), Math.min(duration, seconds + after));
      },
      time: () => store.get(),
      monitor: () => shownMonitor,
      toggle() {
        if (shownMonitor === "render") {
          const video = videoRef.current;
          if (video?.paused) void video.play().catch(() => undefined);
          else video?.pause();
        } else controller.toggle();
      },
      playing: () => (shownMonitor === "render" ? Boolean(videoRef.current && !videoRef.current.paused) : playingRef.current),
    }),
    [controller, duration, seekVideo, shownMonitor, store],
  );
  useImperativeHandle(controllerRef, () => handle, [handle]);

  const canDrive = shownMonitor === "live" && engine.ready && liveAvailable;
  const seekTo = (seconds: number) => controller.seek(clampTime(seconds, duration));
  const stepBy = (frames: number) => {
    controller.pause();
    seekTo(stepFrames(store.get(), fps, frames, duration));
  };
  const markIn = () => {
    const t = store.get();
    setInPoint(t);
    if (outPoint !== null && outPoint <= t) setOutPoint(null);
  };
  const markOut = () => {
    const t = store.get();
    setOutPoint(t);
    if (inPoint !== null && inPoint >= t) setInPoint(null);
  };
  const clearRange = () => {
    setInPoint(null);
    setOutPoint(null);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!canDrive || event.defaultPrevented) return;
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select, [contenteditable='true']")) return;
    const onButton = target.closest("button") !== null;
    const ctrl = event.ctrlKey || event.metaKey;
    const key = event.key;
    let handled = true;
    if ((key === " " && !onButton) || ((key === "k" || key === "K") && !ctrl && !event.altKey)) controller.toggle();
    else if (key === "ArrowLeft") event.shiftKey ? seekTo(store.get() - 1) : stepBy(-1);
    else if (key === "ArrowRight") event.shiftKey ? seekTo(store.get() + 1) : stepBy(1);
    else if (key === "ArrowUp") seekTo(previousCut(cuts, store.get(), fps));
    else if (key === "ArrowDown") seekTo(nextCut(cuts, store.get(), fps, duration));
    else if (key === "Home") seekTo(0);
    else if (key === "End") seekTo(lastFrameTime(duration, fps));
    else if ((key === "i" || key === "I") && !ctrl && !event.altKey) markIn();
    else if ((key === "o" || key === "O") && !ctrl && !event.altKey) markOut();
    else if ((key === "x" || key === "X") && event.altKey) clearRange();
    else if ((key === "l" || key === "L") && ctrl) setLoopOn((value) => !value);
    else handled = false;
    if (handled) {
      event.preventDefault();
      event.stopPropagation();
    }
  };

  const changeQuality = (next: PreviewQuality) => {
    setQuality(next);
    saveQuality(next);
  };

  const portrait = aspect === "9:16";
  const frameBox = frameClassName ?? (portrait ? "aspect-[9/16] h-[min(520px,70vh)] max-w-full" : "aspect-video w-full");

  let liveBody: ReactNode = null;
  if (!liveAvailable) {
    liveBody = loading ? (
      <Overlay>
        <LoaderCircle className="size-5 animate-spin text-[#96A3BC]" aria-hidden />
        <p className="text-[13px] text-[#96A3BC]">Getting the preview ready...</p>
      </Overlay>
    ) : (
      <Overlay>
        <MonitorPlay className="size-7 text-[#7C8BA3]" aria-hidden />
        <div className="max-w-sm text-[13px] text-[#96A3BC]">{unavailable ?? "Nothing to play yet."}</div>
      </Overlay>
    );
  } else if (engine.error) {
    liveBody = (
      <Overlay tone="dim">
        <TriangleAlert className="size-6 text-[#F59E0B]" aria-hidden />
        <p className="max-w-sm text-[13px]">{engine.error}</p>
      </Overlay>
    );
  } else if (engine.status.restarting) {
    liveBody = (
      <Overlay tone="dim">
        <LoaderCircle className="size-5 animate-spin text-[#E8EDF7]" aria-hidden />
        <p className="text-[13px]">Restarting the preview...</p>
      </Overlay>
    );
  } else if (!engine.ready) {
    liveBody = (
      <Overlay>
        <LoaderCircle className="size-5 animate-spin text-[#96A3BC]" aria-hidden />
        <p className="text-[13px] text-[#96A3BC]">Starting the preview...</p>
      </Overlay>
    );
  }

  const renderLabel = proxySrc ? "Watch the last render" : null;
  const showNotice = Boolean(engine.notice && liveAvailable && !engine.error);
  const noticeBar = showNotice ? (
    <div
      role="status"
      className={cn(
        "flex items-start gap-2 bg-[#F59E0B]/10 px-3 py-1.5 text-[12px] text-[#FCD34D]",
        chrome === "monitor" ? "absolute inset-x-0 bottom-0 bg-[#1A1508]/90" : "border-t border-[#232B3B]",
      )}
    >
      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <p className="min-w-0 flex-1">{engine.notice}</p>
      <button
        type="button"
        aria-label="Hide this message"
        title="Hide this message"
        onClick={engine.dismissNotice}
        className="grid size-5 shrink-0 place-items-center rounded text-[#FCD34D] hover:bg-[#F59E0B]/20 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[#E8EDF7]"
      >
        <X className="size-3.5" aria-hidden />
      </button>
    </div>
  ) : null;

  if (chrome === "monitor") {
    // The picture alone: the page around it draws the controls (the storyboard's animatic strip).
    return (
      <section
        ref={sectionRef}
        aria-label={title}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className={cn("flex min-h-0 min-w-0 flex-col overflow-hidden bg-black text-[#E8EDF7] outline-none", className)}
      >
        <div className="flex min-h-0 flex-1 items-center justify-center bg-black" onClick={() => sectionRef.current?.focus({ preventScroll: true })}>
          <div className={cn("relative overflow-hidden", frameClassName ?? (portrait ? "aspect-[9/16] h-full max-w-full" : "aspect-video h-full max-w-full"))}>
            <div ref={setHost} className="absolute inset-0" data-testid="preview-canvas-host" />
            {liveAvailable ? <FrameDescription store={store} bundle={bundle} /> : null}
            {liveBody}
            {frameOverlay}
            {noticeBar}
          </div>
        </div>
        {footer ? <div className="border-t border-[#232B3B] bg-[#141924] px-3 py-2 text-[12px] text-[#96A3BC]">{footer}</div> : null}
      </section>
    );
  }

  return (
    <section
      ref={sectionRef}
      aria-label={title}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      className={cn("flex min-w-0 flex-col overflow-hidden rounded-md border border-[#232B3B] bg-[#141924] text-[#E8EDF7] outline-none", className)}
    >
      <div className="flex min-h-9 flex-wrap items-center gap-2 border-b border-[#232B3B] bg-[#181E2A] px-3 py-1.5">
        <span className="text-[12px] font-semibold">{title}</span>
        {shownMonitor === "live" ? (
          <PreviewChips
            status={engine.status}
            quality={quality}

            animatic={animatic}
            estimatedTiming={estimatedTiming}
            mockMedia={mockMedia}
          />
        ) : (
          <PreviewChip tone="neutral" title="The proxy video from the last render. It does not show changes made since.">
            Last render
          </PreviewChip>
        )}
        {activity && shownMonitor === "live" ? (
          <PreviewChip tone="neutral" spin title={activity}>
            {activity}
          </PreviewChip>
        ) : null}
        <span className="ml-auto" />
        {shownMonitor === "live" && liveAvailable ? <QualityMenu value={quality} onChange={changeQuality} /> : null}
        {renderLabel ? (
          <button
            type="button"
            aria-pressed={shownMonitor === "render"}
            disabled={!liveAvailable && !loading && shownMonitor === "render"}
            title={shownMonitor === "render" ? "Back to the live preview" : "Play the proxy video of the last render"}
            onClick={() => switchMonitor(shownMonitor === "render" ? "live" : "render")}
            className={cn(
              "inline-flex h-6 items-center gap-1.5 rounded-[5px] px-2 text-[11.5px] font-medium transition-colors",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#E8EDF7] disabled:opacity-50",
              shownMonitor === "render" ? "bg-[#283044] text-[#E8EDF7]" : "text-[#96A3BC] hover:bg-[#1E2533] hover:text-[#E8EDF7]",
            )}
          >
            <Film className="size-3.5" aria-hidden />
            {renderLabel}
          </button>
        ) : null}
        <button
          type="button"
          disabled
          title="Side-by-side compare with the last render comes with the timeline editor."
          className="inline-flex h-6 items-center gap-1.5 rounded-[5px] px-2 text-[11.5px] font-medium text-[#7C8BA3] opacity-60"
        >
          <Columns2 className="size-3.5" aria-hidden />
          Compare
        </button>
      </div>

      {shownMonitor === "render" ? (
        <div className="flex flex-col bg-black">
          <ProxyPlayer
            src={proxySrc}
            aspect={aspect}
            videoRef={videoRef}
            className="rounded-none border-0 bg-black"
            onTimeUpdate={(seconds) => store.set(seconds)}
            onDurationChange={() => {
              const pending = pendingVideoSeek.current;
              const video = videoRef.current;
              if (!pending || !video) return;
              pendingVideoSeek.current = null;
              video.currentTime = Math.max(0, pending.time);
              if (pending.play) void video.play().catch(() => undefined);
            }}
          />
          {proxyNote ? <p className="border-t border-[#232B3B] bg-[#141924] px-3 py-1.5 text-[11.5px] text-[#96A3BC]">{proxyNote}</p> : null}
        </div>
      ) : (
        <>
          <div className="flex justify-center bg-black" onClick={() => sectionRef.current?.focus({ preventScroll: true })}>
            <div className={cn("relative", frameBox)}>
              <div ref={setHost} className="absolute inset-0" data-testid="preview-canvas-host" />
              {liveAvailable ? <FrameDescription store={store} bundle={bundle} /> : null}
              {liveBody}
              {frameOverlay}
            </div>
          </div>
          {noticeBar}
          <Transport
            store={store}
            duration={duration}
            playing={engine.playing}
            disabled={!canDrive}
            loop={loop !== null}
            inPoint={inPoint}
            outPoint={outPoint}
            onPlayPause={controller.toggle}
            onStep={stepBy}
            onPreviousCut={() => seekTo(previousCut(cuts, store.get(), fps))}
            onNextCut={() => seekTo(nextCut(cuts, store.get(), fps, duration))}
            onStart={() => seekTo(0)}
            onEnd={() => seekTo(lastFrameTime(duration, fps))}
            onMarkIn={markIn}
            onMarkOut={markOut}
            onClearRange={clearRange}
            onToggleLoop={() => setLoopOn((value) => !value)}
          />
        </>
      )}
      {footer ? <div className="border-t border-[#232B3B] bg-[#141924] px-3 py-2 text-[12px] text-[#96A3BC]">{footer}</div> : null}
    </section>
  );
}
