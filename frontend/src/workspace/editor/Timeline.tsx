import { Bookmark, Captions, Eye, EyeOff, Flag, Image as ImageIcon, Layers, Link2, Lock, Magnet, Merge, MessageSquare, Mic, Minus, MousePointer2, Music, Plus, Scissors, Trash2, Type, Unlock, Video, Volume2, VolumeX, Waves } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent as ReactDragEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import { projectMediaUrl, sceneImageFile } from "../../api/edit";
import { useStoreTime } from "../../components/preview/timeStore";
import { cn } from "../../lib/cn";
import { MEDIA_DRAG_TYPE, readMediaDrag, type MediaDrag } from "../../types/media";
import type { BundleSentence } from "../../types/preview";
import type { AudioClip, AudioTrack, Layer, LayerClip, PixelRect, Timeline as TimelineDoc, TimelineScene } from "../../types/timeline";
import { useEditor } from "./context";
import {
  SNAP_PX,
  type Pause,
  type Selection,
  type SnapTarget,
  addAudioClip,
  addAudioTrack,
  addLayer,
  addLayerClip,
  addMarker,
  addPopup,
  clampZoom,
  clock,
  majorStep,
  mergeScene,
  minorStep,
  moveAudioClip,
  moveCut,
  moveLayerClip,
  pausesOf,
  removeAudioClip,
  removeAudioTrack,
  removeCue,
  removeLayer,
  removeLayerClip,
  removeMarker,
  removePopup,
  rulerLabel,
  sameSelection,
  sceneIndexAt,
  sceneTitle,
  seconds,
  snapTargets,
  snapTo,
  splitAudioClip,
  splitLayerClip,
  splitScene,
  timecode,
  transitionEntry,
  trimAudioClip,
  trimLayerClip,
  updateAudioTrack,
  updateCue,
  updateLayer,
  updateMarker,
  updatePopup,
} from "./document";

const HEADER_W = 120;
const OVERVIEW_H = 28;
const RULER_H = 24;
const GAP = 4;
const LANES = { markers: 16, popups: 24, captions: 24, layer: 36, scenes: 64, voice: 52, sound: 32, music: 36 } as const;

type DragMode = "sentence" | "word" | "free";

function modeOf(event: { shiftKey: boolean; altKey: boolean }): DragMode {
  return event.altKey ? "free" : event.shiftKey ? "word" : "sentence";
}

interface Drag {
  kind: "cut" | "popup" | "popup-edge" | "caption-edge" | "marker" | "music" | "scrub" | "overview" | "overview-edge" | "layerclip" | "layerclip-edge" | "audioclip" | "audioclip-edge";
  index?: number;
  id?: string;
  edge?: "start" | "end";
  startX: number;
  startT: number;
  base: TimelineDoc;
  targets: SnapTarget[];
  startScroll?: number;
  startPps?: number;
}

function Hatch({ className }: { className?: string }) {
  return <span className={cn("pointer-events-none absolute inset-0 bg-[repeating-linear-gradient(45deg,rgba(255,255,255,0.07)_0_3px,transparent_3px_8px)]", className)} aria-hidden />;
}

/** The thumbnails along the camera move: crops of the picture at 0 to 100% of the move. */
function SceneThumbs({ scene, width, height, thumb, picture }: { scene: TimelineScene; width: number; height: number; thumb: string | null; picture: { width: number; height: number } | undefined }) {
  if (!thumb || !picture) return <div className="flex size-full items-center justify-center text-ink-faint"><ImageIcon className="size-4" aria-hidden /></div>;
  const tileW = Math.round(height * (scene.motion.start_rect[2] / Math.max(1, scene.motion.start_rect[3])));
  const count = Math.max(1, Math.min(7, Math.floor(width / Math.max(24, tileW))));
  const tiles: ReactNode[] = [];
  for (let k = 0; k < count; k += 1) {
    const t = count === 1 ? 0 : k / (count - 1);
    const a = scene.motion.start_rect;
    const b = scene.motion.end_rect;
    const rect: PixelRect = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, a[3] + (b[3] - a[3]) * t];
    const scale = tileW / Math.max(1, rect[2]);
    tiles.push(
      <span
        key={k}
        className="block h-full shrink-0 border-r border-black/40"
        style={{ width: tileW, backgroundImage: `url("${thumb}")`, backgroundSize: `${picture.width * scale}px ${picture.height * scale}px`, backgroundPosition: `-${rect[0] * scale}px -${rect[1] * scale}px`, backgroundRepeat: "no-repeat" }}
      />,
    );
  }
  return <div className="flex size-full overflow-hidden">{tiles}</div>;
}

function Waveform({ peaks, gainCurve, startS, endS, width, height, colour, fill, mirror }: { peaks: { step_s: number; min: number[]; max: number[] } | null; gainCurve?: { step_s: number; gain_db: number[] } | null; startS: number; endS: number; width: number; height: number; colour: string; fill: number; mirror: boolean }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);
    if (!peaks || peaks.max.length === 0 || endS <= startS) return;
    const span = endS - startS;
    const mid = height / 2;
    ctx.fillStyle = colour;
    ctx.globalAlpha = fill;
    for (let x = 0; x < width; x += 1) {
      const t0 = startS + (x / width) * span;
      const t1 = startS + ((x + 1) / width) * span;
      const i0 = Math.max(0, Math.floor(t0 / peaks.step_s));
      const i1 = Math.min(peaks.max.length - 1, Math.max(i0, Math.floor(t1 / peaks.step_s)));
      if (i0 >= peaks.max.length) break;
      let hi = -1;
      let lo = 1;
      for (let i = i0; i <= i1; i += 1) {
        hi = Math.max(hi, peaks.max[i] ?? 0);
        lo = Math.min(lo, peaks.min[i] ?? 0);
      }
      if (mirror) {
        const top = mid - Math.max(0, hi) * mid;
        const bottom = mid - Math.min(0, lo) * mid;
        ctx.fillRect(x, top, 1, Math.max(1, bottom - top));
      } else {
        const amp = Math.max(Math.abs(hi), Math.abs(lo));
        ctx.fillRect(x, height - amp * height, 1, Math.max(1, amp * height));
      }
    }
    ctx.globalAlpha = 1;
    if (gainCurve && gainCurve.gain_db.length > 0) {
      ctx.strokeStyle = "#E8EDF7";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (let x = 0; x < width; x += 1) {
        const t = startS + (x / width) * span;
        const i = Math.min(gainCurve.gain_db.length - 1, Math.max(0, Math.floor(t / gainCurve.step_s)));
        const db = gainCurve.gain_db[i] ?? 0;
        const y = 4 + Math.max(0, Math.min(1, -db / 30)) * (height - 8);
        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }, [peaks, gainCurve, startS, endS, width, height, colour, fill, mirror]);
  return <canvas ref={ref} style={{ width, height }} className="absolute inset-0" aria-hidden />;
}

function LaneHeader({ icon, label, height, children, onClick, selected }: { icon: ReactNode; label: string; height: number; children?: ReactNode; onClick?: () => void; selected?: boolean }) {
  return (
    <div className={cn("flex items-center gap-1.5 border-r border-ed-line bg-ed-panel-2 px-2 text-[11px] font-medium text-ink-muted", onClick && "cursor-pointer hover:text-ink", selected && "text-accent-text")} style={{ height }} onClick={onClick}>
      <span className="text-ink-faint [&_svg]:size-3.5">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {children}
    </div>
  );
}

function HeaderToggle({ title, pressed, onClick, children }: { title: string; pressed?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" title={title} aria-label={title} aria-pressed={pressed} onClick={(event) => { event.stopPropagation(); onClick(); }} className={cn("ws-focus grid size-5 place-items-center rounded text-ink-faint hover:bg-ed-raised hover:text-ink [&_svg]:size-3", pressed && "text-ink")}>
      {children}
    </button>
  );
}

function ToolButton({ title, pressed, onClick, disabled, children }: { title: string; pressed?: boolean; onClick: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <button type="button" title={title} aria-label={title} aria-pressed={pressed} disabled={disabled} onClick={onClick} className={cn("ws-focus grid size-7 place-items-center rounded text-ink-muted hover:bg-ed-raised hover:text-ink disabled:opacity-40 [&_svg]:size-4", pressed && "bg-ed-raised-hi text-ink")}>
      {children}
    </button>
  );
}

function hasMediaDrag(event: ReactDragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes(MEDIA_DRAG_TYPE);
}

/** The timeline (docs/design/EDITOR-DESIGN.md section 4.6, docs/CONTRACT-v0.2.md section 8). */
export function Timeline() {
  const { state, actions } = useEditor();
  const laneRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(800);
  const [pps, setPps] = useState(40);
  const [scrollS, setScrollS] = useState(0);
  const [, setDrag] = useState<Drag | null>(null);
  const [preview, setPreview] = useState<TimelineDoc | null>(null);
  const [snapLine, setSnapLine] = useState<{ t: number; label: string } | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [dropLane, setDropLane] = useState<string | null>(null);
  const fittedFor = useRef<number>(0);
  const time = useStoreTime(state.timeStore, 1 / state.fps);
  const timeline = preview ?? state.timeline;
  const duration = Math.max(state.duration, timeline.duration_s, 1);
  const noScenes = timeline.scenes.length === 0;

  // the lane area's width
  useEffect(() => {
    const node = laneRef.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) setWidth(Math.max(200, Math.floor(box.width)));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const fit = useCallback(() => {
    setPps(clampZoom((width - 24) / duration));
    setScrollS(0);
  }, [width, duration]);

  // fit once when the video is known (and again when its length changes a lot)
  useEffect(() => {
    if (fittedFor.current === 0 || Math.abs(fittedFor.current - duration) > 5) {
      fittedFor.current = duration;
      fit();
    }
  }, [duration, fit, width]);

  const xOf = useCallback((t: number) => (t - scrollS) * pps, [scrollS, pps]);
  const tOf = useCallback((x: number) => scrollS + x / pps, [scrollS, pps]);
  const visibleEnd = scrollS + width / pps;

  // zoom and fit from the keyboard and the toolbar
  const zoomBy = useCallback(
    (factor: number, aroundX?: number) => {
      const anchorX = aroundX ?? width / 2;
      const anchorT = tOf(anchorX);
      const next = clampZoom(pps * factor);
      setPps(next);
      setScrollS(Math.max(0, anchorT - anchorX / next));
    },
    [pps, tOf, width],
  );
  useEffect(() => {
    const onZoom = (event: Event) => {
      const detail = (event as CustomEvent<{ factor?: number; fit?: boolean }>).detail;
      if (detail?.fit) fit();
      else if (detail?.factor) zoomBy(detail.factor);
    };
    window.addEventListener("sge:timeline-zoom", onZoom);
    return () => window.removeEventListener("sge:timeline-zoom", onZoom);
  }, [fit, zoomBy]);

  // Ctrl+wheel zooms around the pointer; the wheel alone scrolls sideways
  useEffect(() => {
    const node = laneRef.current;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (event.ctrlKey) {
        const rect = node.getBoundingClientRect();
        zoomBy(event.deltaY < 0 ? 1.2 : 1 / 1.2, event.clientX - rect.left);
      } else {
        const delta = (event.deltaX || event.deltaY) / pps;
        setScrollS((current) => Math.max(0, Math.min(Math.max(0, duration - width / pps + 2), current + delta)));
      }
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [pps, zoomBy, duration, width]);

  // follow the playhead while playing
  useEffect(() => {
    if (!state.followPlayhead || !state.playing) return;
    const x = xOf(time);
    if (x > width * 0.85 || x < 0) setScrollS(Math.max(0, time - (width * 0.1) / pps));
  }, [time, state.playing, state.followPlayhead, xOf, width, pps]);

  // ---- drags ---------------------------------------------------------------------------------

  // The latest view and document, readable from the drag handlers without re-binding them.
  const viewRef = useRef({ pps, scrollS, width, duration, tOf });
  viewRef.current = { pps, scrollS, width, duration, tOf };
  const liveRef = useRef({ sentences: state.sentences, snapping: state.snapping, timeStore: state.timeStore, actions });
  liveRef.current = { sentences: state.sentences, snapping: state.snapping, timeStore: state.timeStore, actions };
  const previewRef = useRef<TimelineDoc | null>(null);
  const setPreviewDoc = (doc: TimelineDoc | null) => {
    previewRef.current = doc;
    setPreview(doc);
  };

  const beginDrag = (event: ReactPointerEvent, drag: Omit<Drag, "startX" | "startT" | "base" | "targets"> & { targets?: SnapTarget[] }) => {
    event.preventDefault();
    event.stopPropagation();
    const rect = laneRef.current?.getBoundingClientRect();
    const left = rect?.left ?? 0;
    const view = viewRef.current;
    const startX = event.clientX - left;
    const live = liveRef.current;
    const current: Drag = {
      ...drag,
      startX,
      startT: view.tOf(startX),
      base: state.timeline,
      targets: drag.targets ?? snapTargets(state.timeline, live.sentences, live.timeStore.get(), "sentence"),
      startScroll: view.scrollS,
      startPps: view.pps,
    };
    setDrag(current);

    const snapAt = (t: number, mode: DragMode, allow: boolean): number => {
      if (!allow) {
        setSnapLine(null);
        return t;
      }
      const targets = mode === "sentence" ? current.targets : snapTargets(current.base, live.sentences, live.timeStore.get(), mode);
      const hit = snapTo(t, targets, SNAP_PX / viewRef.current.pps);
      if (hit) {
        setSnapLine({ t: hit.t, label: hit.label });
        return hit.t;
      }
      setSnapLine(null);
      return t;
    };

    const onMove = (move: PointerEvent) => {
      const v = viewRef.current;
      const x = move.clientX - left;
      const rawT = v.tOf(x);
      const mode = modeOf(move);
      const snapping = liveRef.current.snapping;
      if (current.kind === "scrub") {
        liveRef.current.actions.seek(Math.max(0, Math.min(v.duration, rawT)));
        return;
      }
      if (current.kind === "overview") {
        const overviewPps = v.width / v.duration;
        const delta = (x - current.startX) / overviewPps;
        setScrollS(Math.max(0, Math.min(Math.max(0, v.duration - v.width / v.pps), (current.startScroll ?? 0) + delta)));
        return;
      }
      if (current.kind === "overview-edge") {
        const overviewPps = v.width / v.duration;
        const t = Math.max(0, Math.min(v.duration, x / overviewPps));
        const startScroll = current.startScroll ?? 0;
        const visible = current.edge === "end" ? Math.max(1, t - startScroll) : Math.max(1, startScroll + v.width / (current.startPps ?? v.pps) - t);
        setPps(clampZoom(v.width / visible));
        if (current.edge === "start") setScrollS(Math.max(0, t));
        return;
      }
      if (current.kind === "cut" && current.index !== undefined) {
        const snapMode: DragMode = snapping ? mode : "free";
        const t = snapAt(rawT, snapMode, snapMode !== "free");
        const result = moveCut(current.base, current.index, t, live.sentences);
        setPreviewDoc(result.timeline);
        const leftScene = result.timeline.scenes[current.index];
        const rightScene = result.timeline.scenes[current.index + 1];
        setHint(result.note ?? (leftScene && rightScene ? `Scene ${leftScene.index + 1}: ${seconds(leftScene.end_s - leftScene.start_s)}, scene ${rightScene.index + 1}: ${seconds(rightScene.end_s - rightScene.start_s)}` : null));
        return;
      }
      if (current.kind === "popup" && current.id) {
        const popup = current.base.popups.find((item) => item.id === current.id);
        if (!popup) return;
        const delta = rawT - current.startT;
        const length = popup.end_s - popup.start_s;
        const start = snapAt(popup.start_s + delta, snapping ? (mode === "sentence" ? "word" : mode) : "free", snapping);
        setPreviewDoc(updatePopup(current.base, popup.id, { start_s: start, end_s: start + length }));
        return;
      }
      if (current.kind === "popup-edge" && current.id) {
        const popup = current.base.popups.find((item) => item.id === current.id);
        if (!popup) return;
        const t = snapAt(rawT, snapping ? (mode === "sentence" ? "word" : mode) : "free", snapping);
        setPreviewDoc(updatePopup(current.base, popup.id, current.edge === "start" ? { start_s: Math.min(t, popup.end_s - 0.6) } : { end_s: Math.max(t, popup.start_s + 0.6) }));
        return;
      }
      if (current.kind === "caption-edge" && current.id) {
        const cue = current.base.captions.cues.find((item) => item.id === current.id);
        if (!cue) return;
        const t = snapAt(rawT, "word", true);
        setPreviewDoc(updateCue(current.base, cue.id, current.edge === "start" ? { start_s: Math.min(t, cue.end_s - 0.2) } : { end_s: Math.max(t, cue.start_s + 0.2) }));
        return;
      }
      if (current.kind === "marker" && current.id) {
        const t = snapAt(Math.max(0, Math.min(v.duration, rawT)), snapping ? mode : "free", snapping);
        setPreviewDoc(updateMarker(current.base, current.id, { t }));
        return;
      }
      if (current.kind === "music") {
        const delta = rawT - current.startT;
        const startAt = Math.max(0, Math.round((current.base.music.start_s - delta) * 10) / 10);
        setPreviewDoc({ ...current.base, music: { ...current.base.music, start_s: startAt } });
        setHint(`Track starts ${seconds(startAt)} in`);
        return;
      }
      if ((current.kind === "layerclip" || current.kind === "audioclip") && current.id) {
        const found = current.kind === "layerclip" ? current.base.layers.flatMap((layer) => layer.clips).find((item) => item.id === current.id) : current.base.audio_tracks.flatMap((track) => track.clips).find((item) => item.id === current.id);
        if (!found) return;
        const delta = rawT - current.startT;
        const start = snapAt(found.start_s + delta, snapping ? mode : "free", snapping);
        const moved = current.kind === "layerclip" ? moveLayerClip(current.base, found.id, start) : moveAudioClip(current.base, found.id, start);
        setPreviewDoc(moved);
        setHint(`Starts at ${clock(Math.max(0, start))}`);
        return;
      }
      if ((current.kind === "layerclip-edge" || current.kind === "audioclip-edge") && current.id && current.edge) {
        const t = snapAt(rawT, snapping ? mode : "free", snapping);
        setPreviewDoc(current.kind === "layerclip-edge" ? trimLayerClip(current.base, current.id, current.edge, t) : trimAudioClip(current.base, current.id, current.edge, t));
      }
    };

    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      const final = previewRef.current;
      setDrag(null);
      setSnapLine(null);
      setHint(null);
      setPreviewDoc(null);
      if (final && JSON.stringify(final) !== JSON.stringify(current.base)) {
        const label =
          current.kind === "cut" ? `move cut ${(current.index ?? 0) + 1} to ${(current.index ?? 0) + 2}`
          : current.kind === "music" ? "move the music"
          : current.kind === "marker" ? "move a marker"
          : current.kind.startsWith("popup") ? "re-time a popup"
          : current.kind === "layerclip" ? "move a layer clip"
          : current.kind === "layerclip-edge" ? "trim a layer clip"
          : current.kind === "audioclip" ? "move a sound clip"
          : current.kind === "audioclip-edge" ? "trim a sound clip"
          : "re-time a caption";
        liveRef.current.actions.edit(label, () => final);
      }
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  };

  // ---- drops from the library ------------------------------------------------------------------------

  const dropInfo = (drag: MediaDrag) => ({ path: drag.path, kind: drag.kind, name: drag.name, duration_s: drag.duration_s });
  const dropAt = (event: ReactDragEvent): number => {
    const rect = laneRef.current?.getBoundingClientRect();
    return Math.max(0, tOf(event.clientX - (rect?.left ?? 0)));
  };
  const dropProps = (laneKey: string, onDrop: (drag: MediaDrag, t: number) => void) => ({
    onDragOver: (event: ReactDragEvent) => {
      if (!hasMediaDrag(event) || state.locked) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      if (dropLane !== laneKey) setDropLane(laneKey);
    },
    onDragLeave: () => setDropLane((current) => (current === laneKey ? null : current)),
    onDrop: (event: ReactDragEvent) => {
      const drag = readMediaDrag(event.dataTransfer);
      setDropLane(null);
      if (!drag) return;
      event.preventDefault();
      event.stopPropagation();
      onDrop(drag, dropAt(event));
    },
  });
  const dropOnScenes = (drag: MediaDrag, t: number) => {
    if (drag.kind !== "picture" && drag.kind !== "clip") {
      actions.notify("info", "Sound goes on the Voice lane (the narration), the Music lane, or a sound lane.");
      return;
    }
    const under = sceneIndexAt(state.timeline, t);
    const after = under >= 0 && t < state.timeline.scenes[under]!.end_s ? under : undefined;
    void actions.addScene(drag.path, after, drag.kind === "clip");
  };
  const dropOnLayer = (layer: Layer) => (drag: MediaDrag, t: number) => {
    if (drag.kind !== "picture" && drag.kind !== "clip") {
      actions.notify("info", "Only pictures and clips go on a layer.");
      return;
    }
    let note: string | null = null;
    let id = "";
    const done = actions.edit(`add ${drag.name} to ${layer.name}`, (doc) => {
      const result = addLayerClip(doc, layer.id, dropInfo(drag), t);
      note = result.note;
      id = result.id;
      return result.timeline;
    });
    if (note) actions.notify("info", note);
    else if (done && id) actions.select({ kind: "layerclip", id });
  };
  const dropOnTrack = (track: AudioTrack) => (drag: MediaDrag, t: number) => {
    if (drag.kind !== "audio") {
      actions.notify("info", "Only sound files go on a sound track.");
      return;
    }
    let note: string | null = null;
    let id = "";
    const done = actions.edit(`add ${drag.name} to ${track.name}`, (doc) => {
      const result = addAudioClip(doc, track.id, dropInfo(drag), t);
      note = result.note;
      id = result.id;
      return result.timeline;
    });
    if (note) actions.notify("info", note);
    else if (done && id) actions.select({ kind: "audioclip", id });
  };
  const dropOnVoice = (drag: MediaDrag) => {
    if (drag.kind !== "audio") {
      actions.notify("info", "Drop a sound file here to make it the narration.");
      return;
    }
    void actions.setNarration({ media: drag.path });
  };
  const dropOnMusic = (drag: MediaDrag) => {
    if (drag.kind !== "audio") {
      actions.notify("info", "Drop a sound file here to make it the music.");
      return;
    }
    void actions.applyChoices({ music_path: drag.path });
  };

  // ---- derived ----------------------------------------------------------------------------------

  const pauses = useMemo(() => pausesOf(state.sentences), [state.sentences]);
  const selection = state.selection;
  const isSelected = (sel: Selection) => sameSelection(selection, sel);
  const major = majorStep(pps);
  const minor = minorStep(major);
  const ticks = useMemo(() => {
    const out: { t: number; major: boolean }[] = [];
    const first = Math.floor(scrollS / minor) * minor;
    for (let t = Math.max(0, first); t <= Math.min(duration, visibleEnd) + minor; t += minor) {
      const isMajor = Math.abs(t / major - Math.round(t / major)) < 1e-6;
      out.push({ t: Math.round(t * 1000) / 1000, major: isMajor });
    }
    return out;
  }, [scrollS, visibleEnd, major, minor, duration]);
  const words = pps >= 60;
  const wordTicks = pps >= 30 && !words;
  const overviewPps = width / duration;
  const laneTop = OVERVIEW_H + RULER_H;
  const layersTopFirst = [...timeline.layers].reverse();
  const lanesHeight =
    [LANES.markers, LANES.popups, LANES.captions, LANES.scenes, LANES.voice, LANES.music].reduce((sum, h) => sum + h + GAP, 0) +
    layersTopFirst.length * (LANES.layer + GAP) +
    timeline.audio_tracks.length * (LANES.sound + GAP);
  const playheadX = xOf(time);

  const onLaneClick = (t: number) => actions.seek(Math.max(0, Math.min(duration, t)));
  const selectAndSeek = (sel: Selection, t?: number) => {
    actions.select(sel);
    if (t !== undefined) actions.seek(t);
  };

  const run = (label: string, result: { timeline: TimelineDoc; note: string | null }) => {
    if (result.note) actions.notify("info", result.note);
    else actions.edit(label, () => result.timeline);
  };

  const splitAtPlayhead = () => {
    const t = state.timeStore.get();
    if (selection.kind === "layerclip") return run("split a layer clip", splitLayerClip(state.timeline, selection.id, t));
    if (selection.kind === "audioclip") return run("split a sound clip", splitAudioClip(state.timeline, selection.id, t));
    const index = selection.kind === "scene" ? selection.index : timeline.scenes.findIndex((scene) => t >= scene.start_s && t < scene.end_s);
    if (index < 0) return;
    run(`split scene ${index + 1}`, splitScene(state.timeline, index, t, state.sentences));
  };
  const deleteSelected = (forward: boolean) => {
    if (selection.kind === "scene") run(`merge scene ${selection.index + 1}`, mergeScene(state.timeline, selection.index, forward ? "next" : "previous", state.sentences));
    else if (selection.kind === "popup") actions.edit("remove a popup", (doc) => removePopup(doc, selection.id));
    else if (selection.kind === "marker") actions.edit("remove a marker", (doc) => removeMarker(doc, selection.id));
    else if (selection.kind === "caption") actions.edit("remove a caption", (doc) => removeCue(doc, selection.id));
    else if (selection.kind === "layerclip") actions.edit("remove a layer clip", (doc) => removeLayerClip(doc, selection.id));
    else if (selection.kind === "audioclip") actions.edit("remove a sound clip", (doc) => removeAudioClip(doc, selection.id));
    else if (selection.kind === "layer") actions.edit("remove a layer", (doc) => removeLayer(doc, selection.id));
    else if (selection.kind === "track") actions.edit("remove a sound track", (doc) => removeAudioTrack(doc, selection.id));
    if (selection.kind !== "scene") actions.select({ kind: "none" });
  };

  useEffect(() => {
    const onCommand = (event: Event) => {
      const detail = (event as CustomEvent<{ command: string }>).detail;
      if (detail.command === "split") splitAtPlayhead();
      if (detail.command === "delete") deleteSelected(false);
      if (detail.command === "delete-forward") deleteSelected(true);
      if (detail.command === "marker") {
        let id = "";
        if (actions.edit("add a marker", (doc) => { const result = addMarker(doc, state.timeStore.get()); id = result.id; return result.timeline; }) && id) actions.select({ kind: "marker", id });
      }
    };
    window.addEventListener("sge:timeline-command", onCommand);
    return () => window.removeEventListener("sge:timeline-command", onCommand);
  });

  const lockedUi = state.locked;
  const deletable = ["popup", "marker", "caption", "layerclip", "audioclip", "layer", "track"].includes(selection.kind);
  const lanePointer = (event: ReactPointerEvent) => {
    if (event.target === event.currentTarget) {
      const rect = laneRef.current?.getBoundingClientRect();
      onLaneClick(tOf(event.clientX - (rect?.left ?? 0)));
    }
  };

  const thumbOf = (source: string): string | null => state.sources.get(source)?.thumb_url ?? state.media?.items.find((item) => item.path === source)?.thumb_url ?? null;

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-ed-panel" aria-label="Timeline">
      {/* toolbar */}
      <div className="flex h-9 shrink-0 items-center gap-1 border-b border-ed-line bg-ed-panel-2 px-2">
        <ToolButton title="Select (A)" pressed onClick={() => undefined}>
          <MousePointer2 />
        </ToolButton>
        <span className="mx-1 h-5 w-px bg-ed-line" aria-hidden />
        <ToolButton title="Split the selected scene or clip at the playhead (S)" onClick={splitAtPlayhead} disabled={lockedUi || noScenes}>
          <Scissors />
        </ToolButton>
        <ToolButton title="Merge the selected scene into the previous one (Delete)" onClick={() => deleteSelected(false)} disabled={lockedUi || selection.kind !== "scene"}>
          <Merge />
        </ToolButton>
        <ToolButton title="Delete the selected popup, marker, caption, clip, layer or sound track (Delete)" onClick={() => deleteSelected(false)} disabled={lockedUi || !deletable}>
          <Trash2 />
        </ToolButton>
        <span className="mx-1 h-5 w-px bg-ed-line" aria-hidden />
        <ToolButton title="Add a marker at the playhead (M)" disabled={lockedUi || noScenes} onClick={() => { let id = ""; if (actions.edit("add a marker", (doc) => { const result = addMarker(doc, state.timeStore.get()); id = result.id; return result.timeline; }) && id) actions.select({ kind: "marker", id }); }}>
          <Flag />
        </ToolButton>
        <ToolButton title="Add a popup at the playhead (T)" disabled={lockedUi || noScenes} onClick={() => { let id = ""; if (actions.edit("add a popup", (doc) => { const result = addPopup(doc, state.timeStore.get(), "New popup", state.sentences); id = result.id; return result.timeline; }) && id) actions.select({ kind: "popup", id }); }}>
          <Type />
        </ToolButton>
        <span className="mx-1 h-5 w-px bg-ed-line" aria-hidden />
        <ToolButton title="Add a layer (pictures and clips over the scenes)" disabled={lockedUi} onClick={() => { let id = ""; if (actions.edit("add a layer", (doc) => { const result = addLayer(doc); id = result.id; return result.timeline; }) && id) actions.select({ kind: "layer", id }); }}>
          <Layers />
        </ToolButton>
        <ToolButton title="Add a sound track (under the narration)" disabled={lockedUi} onClick={() => { let id = ""; if (actions.edit("add a sound track", (doc) => { const result = addAudioTrack(doc); id = result.id; return result.timeline; }) && id) actions.select({ kind: "track", id }); }}>
          <Volume2 />
        </ToolButton>
        <div className="ml-auto flex items-center gap-1">
          <ToolButton title="Snapping (N)" pressed={state.snapping} onClick={actions.toggleSnapping}>
            <Magnet />
          </ToolButton>
          <ToolButton title="Link popups to their scenes" pressed={state.linkPopups} onClick={actions.toggleLinkPopups}>
            <Link2 />
          </ToolButton>
          <ToolButton title="Follow the playhead while playing" pressed={state.followPlayhead} onClick={actions.toggleFollowPlayhead}>
            <Waves />
          </ToolButton>
          <span className="mx-1 h-5 w-px bg-ed-line" aria-hidden />
          <ToolButton title="Zoom out (-)" onClick={() => zoomBy(1 / 1.25)}>
            <Minus />
          </ToolButton>
          <input type="range" min={0} max={100} value={Math.round((Math.log(pps / 2) / Math.log(300)) * 100)} onChange={(event) => { const next = 2 * Math.pow(300, Number(event.target.value) / 100); const anchorT = tOf(width / 2); setPps(clampZoom(next)); setScrollS(Math.max(0, anchorT - width / 2 / next)); }} className="w-24 accent-accent" aria-label="Zoom" />
          <ToolButton title="Zoom in (=)" onClick={() => zoomBy(1.25)}>
            <Plus />
          </ToolButton>
          <button type="button" onClick={fit} className="ws-focus h-7 rounded px-2 text-[11px] text-ink-muted hover:bg-ed-raised hover:text-ink" title="Fit the whole video (Shift+Z)">
            Fit
          </button>
        </div>
      </div>

      <div className="ws-scroll flex min-h-0 flex-1 overflow-y-auto">
        {/* header column */}
        <div className="shrink-0" style={{ width: HEADER_W }}>
          <button type="button" onClick={() => { const value = window.prompt("Go to time (m:ss or seconds)", clock(time)); if (value === null) return; const parts = value.split(":"); const t = parts.length === 2 ? Number(parts[0]) * 60 + Number(parts[1]) : Number(value); if (Number.isFinite(t)) actions.seek(t); }} className="ws-focus sticky top-0 z-20 flex w-full items-center border-b border-r border-ed-line bg-ed-panel-2 px-2 font-timecode text-[16px] font-semibold text-accent-text" style={{ height: laneTop }} title="Go to time">
            {timecode(time, state.fps)}
          </button>
          <div className="flex flex-col" style={{ gap: GAP, paddingTop: GAP }}>
            <LaneHeader icon={<Bookmark />} label="Markers" height={LANES.markers} />
            <LaneHeader icon={<MessageSquare />} label="Popups" height={LANES.popups} />
            <LaneHeader icon={<Captions />} label="Captions" height={LANES.captions} />
            {layersTopFirst.map((layer) => (
              <LaneHeader key={layer.id} icon={<Layers />} label={layer.name} height={LANES.layer} onClick={() => actions.select({ kind: "layer", id: layer.id })} selected={isSelected({ kind: "layer", id: layer.id })}>
                <HeaderToggle title={layer.visible ? "Hide this layer" : "Show this layer"} pressed={layer.visible} onClick={() => actions.edit(layer.visible ? `hide ${layer.name}` : `show ${layer.name}`, (doc) => updateLayer(doc, layer.id, { visible: !layer.visible }))}>
                  {layer.visible ? <Eye /> : <EyeOff />}
                </HeaderToggle>
                <HeaderToggle title={layer.locked ? "Unlock this layer" : "Lock this layer"} pressed={layer.locked} onClick={() => actions.edit(layer.locked ? `unlock ${layer.name}` : `lock ${layer.name}`, (doc) => updateLayer(doc, layer.id, { locked: !layer.locked }))}>
                  {layer.locked ? <Lock /> : <Unlock />}
                </HeaderToggle>
              </LaneHeader>
            ))}
            <LaneHeader icon={<ImageIcon />} label="Scenes" height={LANES.scenes} />
            <LaneHeader icon={<Mic />} label="Voice" height={LANES.voice} />
            {timeline.audio_tracks.map((track) => (
              <LaneHeader key={track.id} icon={<Volume2 />} label={track.name} height={LANES.sound} onClick={() => actions.select({ kind: "track", id: track.id })} selected={isSelected({ kind: "track", id: track.id })}>
                <HeaderToggle title={track.muted ? "Unmute this track" : "Mute this track"} pressed={!track.muted} onClick={() => actions.edit(track.muted ? `unmute ${track.name}` : `mute ${track.name}`, (doc) => updateAudioTrack(doc, track.id, { muted: !track.muted }))}>
                  {track.muted ? <VolumeX /> : <Volume2 />}
                </HeaderToggle>
              </LaneHeader>
            ))}
            <LaneHeader icon={<Music />} label="Music" height={LANES.music} />
          </div>
        </div>

        {/* lanes */}
        <div ref={laneRef} className="relative min-w-0 flex-1 select-none overflow-hidden bg-ed-canvas">
          <div className="sticky top-0 z-20">
          {/* overview strip */}
          <div className="relative border-b border-ed-line bg-ed-panel-2" style={{ height: OVERVIEW_H }} onPointerDown={(event) => { const rect = laneRef.current?.getBoundingClientRect(); const x = event.clientX - (rect?.left ?? 0); const t = x / overviewPps; const visible = width / pps; setScrollS(Math.max(0, Math.min(Math.max(0, duration - visible), t - visible / 2))); }}>
            {timeline.scenes.map((scene) => {
              const picture = state.pictures.get(scene.index);
              const thumb = picture?.thumb_url ?? projectMediaUrl(state.projectId, scene.image || sceneImageFile(scene.index));
              return (
                <div key={scene.index} className={cn("absolute top-1 bottom-1 overflow-hidden rounded-sm border-r border-ed-canvas bg-ed-track", selection.kind === "scene" && selection.index === scene.index && "border-b-2 border-b-accent")} style={{ left: scene.start_s * overviewPps, width: Math.max(2, (scene.end_s - scene.start_s) * overviewPps), backgroundImage: thumb ? `url("${thumb}")` : undefined, backgroundSize: "cover", backgroundPosition: "center" }}>
                  <span className="absolute left-0.5 top-0 text-[9px] font-semibold text-white [text-shadow:0_0_2px_#000]">{scene.index + 1}</span>
                </div>
              );
            })}
            <div className="absolute top-0 bottom-0 w-px bg-accent" style={{ left: time * overviewPps }} aria-hidden />
            <div
              className="absolute top-0 bottom-0 cursor-grab border-[1.5px] border-white/80 bg-white/[0.06]"
              style={{ left: scrollS * overviewPps, width: Math.max(8, (width / pps) * overviewPps) }}
              onPointerDown={(event) => beginDrag(event, { kind: "overview" })}
              title="The part on screen: drag to scroll, drag an edge to zoom"
            >
              <span className="absolute inset-y-0 -left-1 w-2 cursor-ew-resize" onPointerDown={(event) => beginDrag(event, { kind: "overview-edge", edge: "start" })} />
              <span className="absolute inset-y-0 -right-1 w-2 cursor-ew-resize" onPointerDown={(event) => beginDrag(event, { kind: "overview-edge", edge: "end" })} />
            </div>
          </div>

          {/* ruler */}
          <div className="relative border-b border-ed-line bg-ed-panel-2" style={{ height: RULER_H }} onPointerDown={(event) => { const rect = laneRef.current?.getBoundingClientRect(); onLaneClick(tOf(event.clientX - (rect?.left ?? 0))); beginDrag(event, { kind: "scrub" }); }}>
            {state.range.in_s !== null && state.range.out_s !== null ? <div className="absolute top-0 bottom-0 border-x border-white/50 bg-white/[0.08]" style={{ left: xOf(state.range.in_s), width: Math.max(1, (state.range.out_s - state.range.in_s) * pps) }} aria-hidden /> : null}
            {ticks.map((tick) => (
              <div key={tick.t} className="absolute bottom-0 w-px bg-ink-faint/60" style={{ left: xOf(tick.t), height: tick.major ? 8 : 4 }}>
                {tick.major ? <span className="absolute bottom-2 left-1 whitespace-nowrap font-timecode text-[10px] text-ink-faint">{rulerLabel(tick.t, pps, state.fps)}</span> : null}
              </div>
            ))}
            <div className="absolute top-0 -ml-1.5 size-0 border-x-[6px] border-t-[8px] border-x-transparent border-t-accent" style={{ left: playheadX }} aria-hidden />
          </div>
          </div>

          {/* lanes */}
          <div
            className={cn("relative", dropLane === "lanes" && "bg-accent/10")}
            style={{ height: lanesHeight, paddingTop: GAP }}
            onPointerDown={(event) => { if (event.target === event.currentTarget) { const rect = laneRef.current?.getBoundingClientRect(); onLaneClick(tOf(event.clientX - (rect?.left ?? 0))); actions.select({ kind: "none" }); } }}
            {...dropProps("lanes", (drag) => {
              if (drag.kind === "picture" || drag.kind === "clip") void actions.addScene(drag.path, undefined, drag.kind === "clip");
              else actions.notify("info", "Drop sound on the Voice lane, the Music lane or a sound lane.");
            })}
          >
            {noScenes ? (
              <div className="pointer-events-none absolute inset-x-0 top-[calc(50%-12px)] z-10 flex justify-center">
                <span className="rounded-md border border-dashed border-ed-line-strong bg-ed-panel px-4 py-2 text-[12px] text-ink-faint">Drag pictures, clips or sound here from the Media tab to start</span>
              </div>
            ) : null}
            {/* markers */}
            <div className="relative bg-ed-track" style={{ height: LANES.markers, marginBottom: GAP }} onPointerDown={lanePointer}>
              {state.sentences.map((sentence, i) => (
                <span key={sentence.id} className="absolute bottom-0 h-1.5 w-px bg-[#4A5878]" style={{ left: xOf(sentence.start_s) }} title={`Sentence ${i + 1}: ${sentence.text}`} />
              ))}
              {timeline.markers.map((marker) => (
                <button key={marker.id} type="button" className={cn("ws-focus absolute top-0.5 flex h-3 items-center gap-1 text-[9px] text-white", isSelected({ kind: "marker", id: marker.id }) && "outline outline-1 outline-accent")} style={{ left: xOf(marker.t) - 5 }} title={marker.label || "Marker"} onPointerDown={(event) => { selectAndSeek({ kind: "marker", id: marker.id }); beginDrag(event, { kind: "marker", id: marker.id }); }}>
                  <span className="block size-2.5 rotate-45" style={{ backgroundColor: marker.colour }} aria-hidden />
                  {pps > 20 ? <span className="whitespace-nowrap text-ink-muted">{marker.label}</span> : null}
                </button>
              ))}
            </div>

            {/* popups */}
            <div className="relative bg-ed-track-2" style={{ height: LANES.popups, marginBottom: GAP }} onPointerDown={lanePointer}>
              {timeline.popups.map((popup) => {
                const left = xOf(popup.start_s);
                const w = Math.max(8, (popup.end_s - popup.start_s) * pps);
                if (left + w < 0 || left > width) return null;
                const selected = isSelected({ kind: "popup", id: popup.id });
                return (
                  <div key={popup.id} className={cn("absolute top-0 h-full cursor-grab rounded-[4px] bg-lane-popup text-[11px] text-white", selected && "outline outline-2 outline-accent")} style={{ left, width: w }} title={`${popup.text} (${clock(popup.start_s)} to ${clock(popup.end_s)})`} onPointerDown={(event) => { selectAndSeek({ kind: "popup", id: popup.id }, popup.start_s); if (!lockedUi) beginDrag(event, { kind: "popup", id: popup.id }); }}>
                    <span className="pointer-events-none absolute inset-y-0 left-0 w-2 rounded-l-[4px] bg-black/20" aria-hidden />
                    <span className="pointer-events-none absolute inset-y-0 right-0 w-2 rounded-r-[4px] bg-black/20" aria-hidden />
                    <span className="block truncate px-2 leading-6">{popup.text}</span>
                    {state.linkPopups ? <Link2 className="absolute right-3 top-1.5 size-3 opacity-70" aria-hidden /> : null}
                    <span className="absolute inset-y-0 -left-1 w-2 cursor-ew-resize" onPointerDown={(event) => { if (!lockedUi) beginDrag(event, { kind: "popup-edge", id: popup.id, edge: "start" }); }} />
                    <span className="absolute inset-y-0 -right-1 w-2 cursor-ew-resize" onPointerDown={(event) => { if (!lockedUi) beginDrag(event, { kind: "popup-edge", id: popup.id, edge: "end" }); }} />
                  </div>
                );
              })}
            </div>

            {/* captions */}
            <div className="relative bg-ed-track" style={{ height: LANES.captions, marginBottom: GAP }} onPointerDown={lanePointer}>
              {timeline.captions.cues.map((cue) => {
                const left = xOf(cue.start_s);
                const w = Math.max(6, (cue.end_s - cue.start_s) * pps - 1);
                if (left + w < 0 || left > width) return null;
                const selected = isSelected({ kind: "caption", id: cue.id });
                return (
                  <div key={cue.id} className={cn("absolute top-0 h-full rounded-[4px] bg-lane-caption text-[11px] text-white", selected && "outline outline-2 outline-accent", !timeline.captions.enabled && "opacity-50")} style={{ left, width: w }} title={cue.text.replace(/\\N/g, " / ")} onPointerDown={(event) => { event.stopPropagation(); selectAndSeek({ kind: "caption", id: cue.id }, cue.start_s); }}>
                    <span className="block truncate px-2 leading-6">{cue.text.replace(/\\N/g, " / ")}</span>
                    <span className="absolute inset-y-0 -left-1 w-2 cursor-ew-resize" onPointerDown={(event) => { if (!lockedUi) beginDrag(event, { kind: "caption-edge", id: cue.id, edge: "start" }); }} />
                    <span className="absolute inset-y-0 -right-1 w-2 cursor-ew-resize" onPointerDown={(event) => { if (!lockedUi) beginDrag(event, { kind: "caption-edge", id: cue.id, edge: "end" }); }} />
                  </div>
                );
              })}
            </div>

            {/* layers, the top layer first */}
            {layersTopFirst.map((layer) => (
              <div key={layer.id} className={cn("relative bg-ed-track-2", !layer.visible && "opacity-50", dropLane === layer.id && "outline outline-2 outline-accent/70", isSelected({ kind: "layer", id: layer.id }) && "outline outline-1 outline-accent/60")} style={{ height: LANES.layer, marginBottom: GAP }} onPointerDown={lanePointer} {...dropProps(layer.id, dropOnLayer(layer))}>
                {layer.clips.map((clip: LayerClip) => {
                  const left = xOf(clip.start_s);
                  const w = Math.max(6, (clip.end_s - clip.start_s) * pps);
                  if (left + w < 0 || left > width) return null;
                  const selected = isSelected({ kind: "layerclip", id: clip.id });
                  const thumb = thumbOf(clip.source);
                  const canDrag = !lockedUi && !layer.locked;
                  return (
                    <div key={clip.id} className={cn("absolute top-0 flex h-full cursor-grab items-center gap-1 overflow-hidden rounded-[4px] bg-lane-layer text-[11px] text-white", selected && "outline outline-2 outline-accent")} style={{ left, width: w }} title={`${clip.name || clip.source}: ${clock(clip.start_s)} to ${clock(clip.end_s)}`} onPointerDown={(event) => { selectAndSeek({ kind: "layerclip", id: clip.id }, Math.max(clip.start_s, Math.min(time, clip.end_s - 0.05))); if (canDrag) beginDrag(event, { kind: "layerclip", id: clip.id }); }}>
                      {thumb ? <span className="ml-0.5 block h-[30px] w-[54px] shrink-0 rounded-sm bg-black bg-cover bg-center" style={{ backgroundImage: `url("${thumb}")` }} aria-hidden /> : <span className="ml-1 grid size-5 shrink-0 place-items-center">{clip.kind === "clip" ? <Video className="size-3.5" aria-hidden /> : <ImageIcon className="size-3.5" aria-hidden />}</span>}
                      <span className="truncate pr-2">{clip.name || clip.source.split("/").at(-1)}{clip.speed !== 1 ? ` · ${clip.speed}x` : ""}</span>
                      {layer.locked ? <Hatch /> : null}
                      <span className="absolute inset-y-0 -left-1 w-2 cursor-ew-resize" onPointerDown={(event) => { if (canDrag) beginDrag(event, { kind: "layerclip-edge", id: clip.id, edge: "start" }); }} />
                      <span className="absolute inset-y-0 -right-1 w-2 cursor-ew-resize" onPointerDown={(event) => { if (canDrag) beginDrag(event, { kind: "layerclip-edge", id: clip.id, edge: "end" }); }} />
                    </div>
                  );
                })}
                {layer.clips.length === 0 ? <span className="pointer-events-none absolute left-2 top-2.5 text-[10px] text-white/50">Empty layer: drop a picture or clip here, or press "Add as a layer" under Media.</span> : null}
              </div>
            ))}

            {/* scenes */}
            <div className={cn("relative bg-ed-track-2", dropLane === "scenes" && "outline outline-2 outline-accent/70")} style={{ height: LANES.scenes, marginBottom: GAP }} onPointerDown={lanePointer} {...dropProps("scenes", dropOnScenes)}>
              {timeline.scenes.map((scene, i) => {
                const left = xOf(scene.start_s);
                const w = Math.max(4, (scene.end_s - scene.start_s) * pps);
                if (left + w < 0 || left > width) return null;
                const selected = isSelected({ kind: "scene", index: i });
                const hovered = hover === i;
                const picture = state.pictures.get(scene.index);
                const thumb = picture?.thumb_url ?? projectMediaUrl(state.projectId, scene.image || sceneImageFile(scene.index));
                const entry = transitionEntry(state.catalogue, scene.transition_out.type);
                const bandW = scene.transition_out.duration_s * pps;
                const last = i === timeline.scenes.length - 1;
                const cutSelected = isSelected({ kind: "cut", index: i });
                return (
                  <div key={`${scene.index}-${scene.image}`} className="absolute top-0 h-full" style={{ left, width: w }} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover((current) => (current === i ? null : current))}>
                    <div className={cn("absolute inset-0 overflow-hidden rounded-[4px] border-r border-ed-canvas", selected ? "outline outline-2 outline-accent" : hovered ? "outline outline-1 outline-ed-line-strong" : "")} onPointerDown={(event) => { event.stopPropagation(); const rect = laneRef.current?.getBoundingClientRect(); selectAndSeek({ kind: "scene", index: i }, tOf(event.clientX - (rect?.left ?? 0))); }} title={`${sceneTitle(scene, state.sentences)}: ${clock(scene.start_s)} to ${clock(scene.end_s)}`}>
                      <div className="flex h-4 items-center gap-1 bg-lane-scene px-1.5 text-[11px] font-semibold text-white/85">
                        {scene.clip ? <Video className="size-3 shrink-0" aria-hidden /> : null}
                        <span className="truncate">{sceneTitle(scene, state.sentences)}</span>
                        {scene.clip && scene.clip.speed !== 1 ? <span className="shrink-0 font-normal text-white/70">{scene.clip.speed}x</span> : null}
                        <span className="ml-auto shrink-0 font-normal text-white/70">{seconds(scene.end_s - scene.start_s)}</span>
                        {scene.edited.length > 0 ? <span className="size-1 rounded-full bg-white" title="Changed by you, kept when the timeline is built again" aria-hidden /> : null}
                      </div>
                      <div className="relative h-12 bg-black">
                        <SceneThumbs scene={scene} width={w} height={48} thumb={thumb} picture={picture} />
                        {scene.locked ? <Hatch /> : null}
                      </div>
                    </div>
                    {/* trim handles */}
                    {(selected || hovered) && !lockedUi ? (
                      <>
                        {i > 0 ? <span className="absolute inset-y-0 -left-1 w-2 cursor-ew-resize" onPointerDown={(event) => { actions.select({ kind: "cut", index: i - 1 }); beginDrag(event, { kind: "cut", index: i - 1 }); }} title="Move this cut (snaps to a sentence; Shift: a word; Alt: any frame)"><span className="absolute inset-y-2 left-0.5 w-1.5 rounded bg-accent/80" /></span> : null}
                        {!last ? <span className="absolute inset-y-0 -right-1 w-2 cursor-ew-resize" onPointerDown={(event) => { actions.select({ kind: "cut", index: i }); beginDrag(event, { kind: "cut", index: i }); }} title="Move this cut (snaps to a sentence; Shift: a word; Alt: any frame)"><span className="absolute inset-y-2 right-0.5 w-1.5 rounded bg-accent/80" /></span> : null}
                      </>
                    ) : null}
                    {/* the transition band and badge on the cut */}
                    {bandW > 2 ? <span className={cn("pointer-events-none absolute top-4 h-12 bg-[repeating-linear-gradient(-45deg,rgba(255,255,255,0.18)_0_2px,rgba(255,255,255,0.06)_2px_6px)]", last ? "right-0 rounded-r" : "")} style={last ? { width: bandW } : { right: -bandW / 2, width: bandW }} aria-hidden /> : null}
                    <button type="button" className={cn("ws-focus absolute top-4 z-10 grid size-4 -translate-x-1/2 place-items-center rounded-full border bg-ed-panel text-[9px] text-ink", cutSelected ? "border-accent" : "border-ed-line-strong", last ? "right-0 translate-x-0" : "right-0 translate-x-1/2")} title={`${entry?.label ?? scene.transition_out.type}, ${scene.transition_out.duration_s.toFixed(1)} s: select this cut`} onPointerDown={(event) => { event.stopPropagation(); selectAndSeek({ kind: "cut", index: i }, Math.max(0, scene.end_s - 1)); }}>
                      <Scissors className="size-2.5" aria-hidden />
                    </button>
                  </div>
                );
              })}
            </div>

            {/* voice */}
            <div className={cn("relative overflow-hidden bg-lane-voice", dropLane === "voice" && "outline outline-2 outline-accent/70")} style={{ height: LANES.voice, marginBottom: GAP }} onPointerDown={(event) => { const rect = laneRef.current?.getBoundingClientRect(); const t = tOf(event.clientX - (rect?.left ?? 0)); const pause = pauses.find((item) => t >= item.start_s && t < item.end_s); const sentence = state.sentences.find((item) => t >= item.start_s && t < item.end_s); if (pause) selectAndSeek({ kind: "pause", after: pause.after }, pause.start_s); else if (sentence) selectAndSeek({ kind: "sentence", id: sentence.id }, t); else onLaneClick(t); }} {...dropProps("voice", dropOnVoice)}>
              <div className="absolute inset-x-0 top-[14px] bottom-0">
                <Waveform peaks={state.voicePeaks} startS={scrollS} endS={visibleEnd} width={width} height={LANES.voice - 14} colour="#7FD6E6" fill={0.9} mirror />
              </div>
              {state.timingSource === "scenes" ? <span className="pointer-events-none absolute left-2 top-2.5 text-[11px] text-white/60">No narration. Drop a sound file here, or press "Use as narration" under Media.</span> : null}
              {state.sentences.map((sentence, i) => (
                <span key={sentence.id} className={cn("absolute top-[14px] bottom-0 w-px bg-white/30", isSelected({ kind: "sentence", id: sentence.id }) && "bg-accent")} style={{ left: xOf(sentence.start_s) }} title={`Sentence ${i + 1}`} />
              ))}
              {isSelected({ kind: "sentence", id: (selection as { id?: string }).id ?? "" }) && selection.kind === "sentence" ? (() => { const s = state.sentences.find((item) => item.id === selection.id); return s ? <span className="pointer-events-none absolute top-[14px] bottom-0 rounded-sm outline outline-2 outline-accent" style={{ left: xOf(s.start_s), width: (s.end_s - s.start_s) * pps }} /> : null; })() : null}
              {pauses.map((pause: Pause) => {
                const left = xOf(pause.start_s);
                const w = pause.length_s * pps;
                if (left + w < 0 || left > width) return null;
                return (
                  <span key={pause.after} className={cn("absolute top-[14px] bottom-0 rounded-sm", isSelected({ kind: "pause", after: pause.after }) && "outline outline-2 outline-accent")} style={{ left, width: w }} title={`${seconds(pause.length_s)} pause after sentence ${pause.index + 1}: click to shorten it`}>
                    <Hatch className="rounded-sm bg-[repeating-linear-gradient(45deg,rgba(255,255,255,0.12)_0_3px,transparent_3px_8px)]" />
                    {w > 40 ? <span className="absolute inset-0 flex items-center justify-center text-[10px] text-white/80">{seconds(pause.length_s)} pause</span> : null}
                  </span>
                );
              })}
              {(words || wordTicks) ? (
                <div className="absolute inset-x-0 top-0 h-[14px]">
                  {state.sentences.flatMap((sentence) => sentence.words).map((word, k) => {
                    const left = xOf(word.start_s);
                    if (left < -40 || left > width) return null;
                    return words ? (
                      <span key={k} className="absolute top-0.5 max-w-[80px] truncate rounded-sm bg-black/30 px-0.5 text-[10px] leading-3 text-white/90" style={{ left }}>
                        {word.w}
                      </span>
                    ) : (
                      <span key={k} className="absolute top-2 h-1 w-px bg-white/50" style={{ left }} aria-hidden />
                    );
                  })}
                </div>
              ) : null}
            </div>

            {/* sound tracks */}
            {timeline.audio_tracks.map((track) => (
              <div key={track.id} className={cn("relative bg-ed-track-2", track.muted && "opacity-50", dropLane === track.id && "outline outline-2 outline-accent/70", isSelected({ kind: "track", id: track.id }) && "outline outline-1 outline-accent/60")} style={{ height: LANES.sound, marginBottom: GAP }} onPointerDown={lanePointer} {...dropProps(track.id, dropOnTrack(track))}>
                {track.clips.map((clip: AudioClip) => {
                  const left = xOf(clip.start_s);
                  const w = Math.max(6, (clip.end_s - clip.start_s) * pps);
                  if (left + w < 0 || left > width) return null;
                  const selected = isSelected({ kind: "audioclip", id: clip.id });
                  return (
                    <div key={clip.id} className={cn("absolute top-0 flex h-full cursor-grab items-center gap-1 overflow-hidden rounded-[4px] bg-lane-sound text-[11px] text-white", selected && "outline outline-2 outline-accent")} style={{ left, width: w }} title={`${clip.name || clip.source}: ${clock(clip.start_s)} to ${clock(clip.end_s)}`} onPointerDown={(event) => { selectAndSeek({ kind: "audioclip", id: clip.id }, Math.max(clip.start_s, Math.min(time, clip.end_s - 0.05))); if (!lockedUi) beginDrag(event, { kind: "audioclip", id: clip.id }); }}>
                      <span className="ml-1 grid size-5 shrink-0 place-items-center">{clip.voice ? <Mic className="size-3.5" aria-hidden /> : <Volume2 className="size-3.5" aria-hidden />}</span>
                      <span className="truncate pr-2">{clip.name || clip.source.split("/").at(-1)}{clip.speed !== 1 ? ` · ${clip.speed}x` : ""}{clip.gain_db !== 0 ? ` · ${clip.gain_db > 0 ? "+" : ""}${clip.gain_db} dB` : ""}</span>
                      {clip.fade_in_s > 0 ? <span className="pointer-events-none absolute inset-y-0 left-0 bg-[linear-gradient(90deg,rgba(0,0,0,0.5),transparent)]" style={{ width: clip.fade_in_s * pps }} aria-hidden /> : null}
                      {clip.fade_out_s > 0 ? <span className="pointer-events-none absolute inset-y-0 right-0 bg-[linear-gradient(270deg,rgba(0,0,0,0.5),transparent)]" style={{ width: clip.fade_out_s * pps }} aria-hidden /> : null}
                      <span className="absolute inset-y-0 -left-1 w-2 cursor-ew-resize" onPointerDown={(event) => { if (!lockedUi) beginDrag(event, { kind: "audioclip-edge", id: clip.id, edge: "start" }); }} />
                      <span className="absolute inset-y-0 -right-1 w-2 cursor-ew-resize" onPointerDown={(event) => { if (!lockedUi) beginDrag(event, { kind: "audioclip-edge", id: clip.id, edge: "end" }); }} />
                    </div>
                  );
                })}
                {track.clips.length === 0 ? <span className="pointer-events-none absolute left-2 top-2 text-[10px] text-white/50">Empty track: drop a sound file here, or press "Add as a sound" under Media.</span> : null}
              </div>
            ))}

            {/* music */}
            <div className={cn("relative overflow-hidden bg-lane-music", isSelected({ kind: "music" }) && "outline outline-2 outline-accent", dropLane === "music" && "outline outline-2 outline-accent/70")} style={{ height: LANES.music }} onPointerDown={(event) => { actions.select({ kind: "music" }); if (timeline.music.path && !lockedUi) beginDrag(event, { kind: "music" }); }} {...dropProps("music", dropOnMusic)}>
              {timeline.music.path ? (
                <>
                  <Waveform peaks={state.musicPeaks} gainCurve={state.envelope} startS={scrollS} endS={visibleEnd} width={width} height={LANES.music} colour="#9BE0B8" fill={0.55} mirror={false} />
                  <span className="pointer-events-none absolute left-2 top-1 text-[11px] text-white/90">
                    {timeline.music.path.split(/[\\/]/).at(-1)} · {timeline.music.gain_db} dB · dips {timeline.music.duck_db} dB under the voice
                    <span className={cn("ml-2 rounded-full px-1.5 text-[10px]", timeline.music.license_ok ? "bg-ok/30 text-white" : "bg-warn/30 text-white")}>{timeline.music.license_ok ? "Licence noted" : "No licence yet"}</span>
                  </span>
                </>
              ) : (
                <span className="absolute left-2 top-2.5 text-[11px] text-white/60">No music. Drop a sound file here, or press "Use as music" under Media.</span>
              )}
            </div>

            {/* playhead, snap line, drag hint */}
            <div className="pointer-events-none absolute top-0 bottom-0 w-0.5 bg-accent shadow-[0_0_0_1px_rgba(0,0,0,0.55)]" style={{ left: playheadX - 1 }} aria-hidden />
            {snapLine ? (
              <>
                <div className="pointer-events-none absolute top-0 bottom-0 w-px border-l border-dashed border-ink" style={{ left: xOf(snapLine.t) }} aria-hidden />
                <span className="pointer-events-none absolute top-1 rounded bg-ed-raised-hi px-1.5 py-0.5 text-[10px] text-ink" style={{ left: xOf(snapLine.t) + 6 }}>{snapLine.label}</span>
              </>
            ) : null}
            {hint ? <span className="pointer-events-none absolute bottom-1 left-2 rounded bg-ed-raised-hi px-1.5 py-0.5 text-[10px] text-ink">{hint}</span> : null}
          </div>
        </div>
      </div>
    </section>
  );
}

export type { BundleSentence };
