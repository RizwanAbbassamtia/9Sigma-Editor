/**
 * The editor's document: pure functions over the timeline (docs/design/EDITOR-DESIGN.md
 * sections 4.6.4 to 4.6.8 and 10.2). Every edit returns a new timeline; the workspace keeps
 * the history for undo and saves the whole document (`PUT /api/edits/{id}/timeline`).
 *
 * Times are seconds on the timeline. The narration is the clock: a cut moves only to a
 * sentence start (Shift: a word boundary, Alt: any frame), scenes never swap time with the
 * voice, and the one edit that moves everything after it (shortening a pause) goes through the
 * timing route on the server, not through here.
 */
import { MOTION_PRESETS, coverRegion, rectInPixels, type Rect } from "../../preview/motion";
import type { BundlePicture, BundleSentence } from "../../types/preview";
import { defaultAdjust, defaultFrame, type AudioClip, type AudioTrack, type CaptionCue, type ClipSource, type ColourAdjust, type FrameAdjust, type Layer, type LayerClip, type PixelRect, type Timeline, type TimelineMarker, type TimelinePopup, type TimelineScene } from "../../types/timeline";
import type { TransitionCatalogue, TransitionCatalogueEntry, TransitionPack } from "../../types/storyboard";
import { transitionDuration, transitionsInPack } from "../../types/storyboard";
import type { ProjectFormat } from "../../types/project";

export const MIN_SCENE_S = 2.0;
export const MAX_SHARE_OF_SHORTER_SCENE = 0.25;
export const MAX_TRANSITION_S = 3;
export const MIN_POPUP_S = 0.6;
export const LONG_POPUP_S = 4;
export const PAUSE_BREAK_S = 0.35;
export const UNDO_LIMIT = 20;
export const SNAP_PX = 8;
/** v0.2: the shortest layer or sound clip, and the shortest scene in an edit without narration. */
export const MIN_CLIP_S = 0.1;
export const MIN_RIPPLE_SCENE_S = 0.5;
export const DEFAULT_LAYER_PICTURE_S = 5;

// ---- selection -----------------------------------------------------------------------------

export type Selection =
  | { kind: "none" }
  | { kind: "scene"; index: number }
  | { kind: "cut"; index: number }
  | { kind: "popup"; id: string }
  | { kind: "caption"; id: string }
  | { kind: "sentence"; id: string }
  | { kind: "pause"; after: string }
  | { kind: "music" }
  | { kind: "marker"; id: string }
  | { kind: "layerclip"; id: string }
  | { kind: "audioclip"; id: string }
  | { kind: "layer"; id: string }
  | { kind: "track"; id: string };

export const NO_SELECTION: Selection = { kind: "none" };

export function sameSelection(a: Selection, b: Selection): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ---- time formats ----------------------------------------------------------------------------

/** `00:01:05:18` at `fps` (the monitor and the timeline header). */
export function timecode(seconds: number, fps: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const totalFrames = Math.round(safe * fps);
  const frames = totalFrames % fps;
  const total = Math.floor(totalFrames / fps);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(h)}:${two(m)}:${two(s)}:${two(frames)}`;
}

/** `1:05.6` (inspector fields and tooltips). */
export function clock(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const tenths = Math.round(safe * 10);
  const m = Math.floor(tenths / 600);
  const rest = tenths - m * 600;
  return `${m}:${String(Math.floor(rest / 10)).padStart(2, "0")}.${rest % 10}`;
}

/** `10.4 s` */
export function seconds(value: number): string {
  return `${(Math.round(value * 10) / 10).toFixed(1)} s`;
}

/** `m:ss` for the ruler (frames added at 240 px/s and above). */
export function rulerLabel(seconds: number, pxPerSecond: number, fps: number): string {
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  const base = `${m}:${String(s).padStart(2, "0")}`;
  if (pxPerSecond < 240) return base;
  const frames = Math.round((seconds - total) * fps);
  return `${base}:${String(frames).padStart(2, "0")}`;
}

// ---- derived facts ------------------------------------------------------------------------

export function sentenceStarts(sentences: readonly BundleSentence[]): number[] {
  return sentences.map((sentence) => sentence.start_s);
}

export function wordBoundaries(sentences: readonly BundleSentence[]): number[] {
  const out: number[] = [];
  for (const sentence of sentences) {
    for (const word of sentence.words) out.push(word.start_s, word.end_s);
    if (sentence.words.length === 0) out.push(sentence.start_s, sentence.end_s);
  }
  return out;
}

export interface Pause {
  /** the sentence the pause follows */
  after: string;
  /** 0-based position of that sentence */
  index: number;
  start_s: number;
  end_s: number;
  length_s: number;
}

/** The pauses between sentences longer than `PAUSE_BREAK_S`. */
export function pausesOf(sentences: readonly BundleSentence[]): Pause[] {
  const out: Pause[] = [];
  for (let i = 0; i < sentences.length - 1; i += 1) {
    const sentence = sentences[i]!;
    const next = sentences[i + 1]!;
    const length = next.start_s - sentence.end_s;
    if (length > PAUSE_BREAK_S) out.push({ after: sentence.id, index: i, start_s: sentence.end_s, end_s: next.start_s, length_s: length });
  }
  return out;
}

export function cutTimes(timeline: Timeline): number[] {
  return timeline.scenes.slice(0, -1).map((scene) => scene.end_s);
}

export function sceneIndexAt(timeline: Timeline, t: number): number {
  const scenes = timeline.scenes;
  if (scenes.length === 0) return -1;
  for (let i = 0; i < scenes.length; i += 1) {
    const scene = scenes[i]!;
    if (t >= scene.start_s && t < scene.end_s) return i;
  }
  return t >= scenes[scenes.length - 1]!.end_s ? scenes.length - 1 : 0;
}

export function sentenceAt(sentences: readonly BundleSentence[], t: number): BundleSentence | null {
  return sentences.find((sentence) => t >= sentence.start_s && t < sentence.end_s) ?? null;
}

/** The sentences a scene covers: those that start inside it (the builder's own rule). */
export function sentenceIdsBetween(sentences: readonly BundleSentence[], start: number, end: number): string[] {
  return sentences.filter((sentence) => sentence.start_s >= start - 1e-6 && sentence.start_s < end - 1e-6).map((sentence) => sentence.id);
}

/** "7 · Why she paid": the scene number and the first words of its narration. */
export function sceneTitle(scene: TimelineScene, sentences: readonly BundleSentence[]): string {
  const first = scene.sentence_ids.map((id) => sentences.find((sentence) => sentence.id === id)).find(Boolean) ?? sentenceAt(sentences, scene.start_s + 0.01);
  const words = (first?.text ?? "").split(/\s+/).filter(Boolean).slice(0, 3).join(" ");
  const name = words ? words.replace(/[.,;:!?]+$/, "") : (scene.image.split("/").at(-1) ?? "Scene");
  return `${scene.index + 1} · ${name}`;
}

// ---- snapping ---------------------------------------------------------------------------------

export interface SnapTarget {
  t: number;
  label: string;
}

export function snapTargets(timeline: Timeline, sentences: readonly BundleSentence[], playhead: number, mode: "sentence" | "word" | "free"): SnapTarget[] {
  const out: SnapTarget[] = [{ t: playhead, label: "Playhead" }];
  if (mode === "free") return out;
  sentences.forEach((sentence, i) => {
    out.push({ t: sentence.start_s, label: `Start of sentence ${i + 1}` });
    out.push({ t: sentence.end_s, label: `End of sentence ${i + 1}` });
  });
  if (mode === "word") {
    for (const sentence of sentences) for (const word of sentence.words) out.push({ t: word.start_s, label: `"${word.w}"` }, { t: word.end_s, label: `after "${word.w}"` });
  }
  for (const scene of timeline.scenes) out.push({ t: scene.end_s, label: `Cut after scene ${scene.index + 1}` });
  for (const popup of timeline.popups) out.push({ t: popup.start_s, label: "Popup start" }, { t: popup.end_s, label: "Popup end" });
  for (const marker of timeline.markers) out.push({ t: marker.t, label: marker.label || "Marker" });
  return out;
}

/** The nearest target within `threshold` seconds, or null. */
export function snapTo(t: number, targets: readonly SnapTarget[], threshold: number): SnapTarget | null {
  let best: SnapTarget | null = null;
  let distance = threshold;
  for (const target of targets) {
    const d = Math.abs(target.t - t);
    if (d <= distance) {
      best = target;
      distance = d;
    }
  }
  return best;
}

// ---- transitions ----------------------------------------------------------------------------

/** The longest the cut out of scene `index` may run (a quarter of the shorter neighbour). */
export function cutLimit(timeline: Timeline, index: number): number {
  const scene = timeline.scenes[index];
  if (!scene) return 0;
  const length = scene.end_s - scene.start_s;
  const next = timeline.scenes[index + 1];
  const shorter = next ? Math.min(length, next.end_s - next.start_s) : length;
  return Math.max(0, Math.min(MAX_TRANSITION_S, Math.floor(shorter * MAX_SHARE_OF_SHORTER_SCENE * 100) / 100));
}

function clampedTransitions(scenes: TimelineScene[]): TimelineScene[] {
  return scenes.map((scene, index) => {
    const length = scene.end_s - scene.start_s;
    const next = scenes[index + 1];
    const shorter = next ? Math.min(length, next.end_s - next.start_s) : length;
    const limit = Math.max(0, Math.min(MAX_TRANSITION_S, Math.floor(shorter * MAX_SHARE_OF_SHORTER_SCENE * 100) / 100));
    if (scene.transition_out.duration_s <= limit) return scene;
    return { ...scene, transition_out: { ...scene.transition_out, duration_s: limit } };
  });
}

export function setTransition(timeline: Timeline, index: number, type: string, duration_s: number): Timeline {
  const scenes = timeline.scenes.map((scene, i) => (i === index ? { ...scene, transition_out: { type, duration_s: Math.max(0, duration_s) }, edited: withField(scene.edited, "transition_out") } : scene));
  return { ...timeline, scenes: clampedTransitions(scenes) };
}

export function applyTransitionToAll(timeline: Timeline, type: string, duration_s: number): Timeline {
  const scenes = timeline.scenes.map((scene) => ({ ...scene, transition_out: { type, duration_s }, edited: withField(scene.edited, "transition_out") }));
  return { ...timeline, scenes: clampedTransitions(scenes) };
}

/** A mix with no repeats from the pack's entries (the last cut keeps the pack's closer when it has one). */
export function varyTransitions(timeline: Timeline, catalogue: TransitionCatalogue, pack: TransitionPack, format: ProjectFormat): Timeline {
  const entries = transitionsInPack(catalogue, pack).filter((entry) => entry.id !== catalogue.last_scene);
  if (entries.length === 0) return timeline;
  let previous = "";
  const scenes = timeline.scenes.map((scene, index) => {
    const last = index === timeline.scenes.length - 1;
    if (last) {
      const closer = catalogue.transitions.find((entry) => entry.id === catalogue.last_scene);
      if (closer) return { ...scene, transition_out: { type: closer.id, duration_s: transitionDuration(catalogue, closer, pack, format) }, edited: withField(scene.edited, "transition_out") };
    }
    const choices = entries.filter((entry) => entry.id !== previous);
    const entry = choices[(index * 7 + timeline.scenes.length) % choices.length]!;
    previous = entry.id;
    return { ...scene, transition_out: { type: entry.id, duration_s: transitionDuration(catalogue, entry, pack, format) }, edited: withField(scene.edited, "transition_out") };
  });
  return { ...timeline, scenes: clampedTransitions(scenes) };
}

export function transitionEntry(catalogue: TransitionCatalogue | null, type: string): TransitionCatalogueEntry | null {
  if (!catalogue) return null;
  return catalogue.transitions.find((entry) => entry.id === type) ?? (catalogue.legacy[type] ? (catalogue.transitions.find((entry) => entry.id === catalogue.legacy[type]) ?? null) : null);
}

// ---- cuts, splits and merges -----------------------------------------------------------------

function withField(edited: string[], field: string): string[] {
  return edited.includes(field) ? edited : [...edited, field];
}

function reindexed(scenes: TimelineScene[]): TimelineScene[] {
  return scenes.map((scene, index) => ({ ...scene, index }));
}

/** Popups and captions follow the scenes they sit in; a popup squeezed below 0.6 s is clamped. */
function clampPopups(timeline: Timeline): TimelinePopup[] {
  return timeline.popups.map((popup) => {
    const scene = timeline.scenes[popup.scene];
    if (!scene) return popup;
    const start = Math.max(scene.start_s, Math.min(popup.start_s, scene.end_s - MIN_POPUP_S));
    const end = Math.max(start + MIN_POPUP_S, Math.min(popup.end_s, scene.end_s));
    if (start === popup.start_s && end === popup.end_s) return popup;
    return { ...popup, start_s: round3(start), end_s: round3(end) };
  });
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

export interface CutMove {
  timeline: Timeline;
  /** why the move stopped short, in plain English (or null) */
  note: string | null;
}

/**
 * A rolling edit: the end of scene `index` and the start of the next one move together to
 * `t`. Nothing else moves. The sentences follow their new scenes.
 */
export function moveCut(timeline: Timeline, index: number, t: number, sentences: readonly BundleSentence[]): CutMove {
  const left = timeline.scenes[index];
  const right = timeline.scenes[index + 1];
  if (!left || !right) return { timeline, note: null };
  const low = left.start_s + MIN_SCENE_S;
  const high = right.end_s - MIN_SCENE_S;
  let note: string | null = null;
  let target = round3(t);
  if (high < low) return { timeline, note: "Both scenes are already as short as they can be (2 s)." };
  if (target < low) {
    target = round3(low);
    note = `Scene ${left.index + 1} stays at least 2 s long.`;
  }
  if (target > high) {
    target = round3(high);
    note = `Scene ${right.index + 1} stays at least 2 s long.`;
  }
  const scenes = timeline.scenes.map((scene, i) => {
    if (i === index) return { ...scene, end_s: target, sentence_ids: sentenceIdsBetween(sentences, scene.start_s, target), edited: withField(scene.edited, "times") };
    if (i === index + 1) return { ...scene, start_s: target, sentence_ids: sentenceIdsBetween(sentences, target, scene.end_s), edited: withField(scene.edited, "times") };
    return scene;
  });
  const next: Timeline = { ...timeline, scenes: clampedTransitions(scenes) };
  return { timeline: { ...next, popups: clampPopups(next) }, note };
}

function lerpRect(a: PixelRect, b: PixelRect, t: number): PixelRect {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, a[3] + (b[3] - a[3]) * t];
}

/** Split scene `index` at `t`: the right part keeps the picture, the move carries on without a jump. */
export function splitScene(timeline: Timeline, index: number, t: number, sentences: readonly BundleSentence[]): CutMove {
  const scene = timeline.scenes[index];
  if (!scene) return { timeline, note: null };
  const at = round3(t);
  if (at - scene.start_s < MIN_SCENE_S || scene.end_s - at < MIN_SCENE_S) {
    return { timeline, note: "Both parts need at least 2 s. Move the playhead further from the edges." };
  }
  const progress = (at - scene.start_s) / (scene.end_s - scene.start_s);
  const middle = lerpRect(scene.motion.start_rect, scene.motion.end_rect, progress);
  const left: TimelineScene = {
    ...scene,
    end_s: at,
    sentence_ids: sentenceIdsBetween(sentences, scene.start_s, at),
    motion: { ...scene.motion, end_rect: middle },
    transition_out: { ...scene.transition_out, duration_s: 0 },
    edited: withField(withField(scene.edited, "times"), "motion"),
  };
  const right: TimelineScene = {
    ...scene,
    start_s: at,
    sentence_ids: sentenceIdsBetween(sentences, at, scene.end_s),
    motion: { ...scene.motion, start_rect: middle },
    edited: withField(withField(scene.edited, "times"), "motion"),
  };
  const scenes = reindexed([...timeline.scenes.slice(0, index), left, right, ...timeline.scenes.slice(index + 1)]);
  const popups = timeline.popups.map((popup) => (popup.scene > index ? { ...popup, scene: popup.scene + 1 } : popup.scene === index && popup.start_s >= at ? { ...popup, scene: index + 1 } : popup));
  const next: Timeline = { ...timeline, scenes: clampedTransitions(scenes), popups };
  return { timeline: { ...next, popups: clampPopups(next) }, note: null };
}

/** Merge scene `index` into its previous (or next) neighbour; the neighbour takes its time. */
export function mergeScene(timeline: Timeline, index: number, into: "previous" | "next", sentences: readonly BundleSentence[]): CutMove {
  const scene = timeline.scenes[index];
  const target = into === "previous" ? index - 1 : index + 1;
  const neighbour = timeline.scenes[target];
  if (!scene || !neighbour) return { timeline, note: into === "previous" ? "This is the first scene." : "This is the last scene." };
  const start = Math.min(scene.start_s, neighbour.start_s);
  const end = Math.max(scene.end_s, neighbour.end_s);
  const merged: TimelineScene = {
    ...neighbour,
    start_s: start,
    end_s: end,
    sentence_ids: sentenceIdsBetween(sentences, start, end),
    transition_out: into === "previous" ? scene.transition_out : neighbour.transition_out,
    edited: withField(neighbour.edited, "times"),
  };
  const scenes = reindexed(timeline.scenes.filter((_s, i) => i !== index).map((s) => (s.index === neighbour.index ? merged : s)));
  const mergedIndex = into === "previous" ? index - 1 : index;
  const popups = timeline.popups
    .map((popup) => {
      if (popup.scene === index) return { ...popup, scene: mergedIndex };
      if (popup.scene > index) return { ...popup, scene: popup.scene - 1 };
      return popup;
    })
    .filter((popup) => popup.end_s - popup.start_s >= MIN_POPUP_S);
  const next: Timeline = { ...timeline, scenes: clampedTransitions(scenes), popups };
  return { timeline: { ...next, popups: clampPopups(next) }, note: null };
}

/** Q / W: the selected scene's start or end cut goes to the playhead (snapped by the caller). */
export function moveSceneEdge(timeline: Timeline, index: number, edge: "start" | "end", t: number, sentences: readonly BundleSentence[]): CutMove {
  if (edge === "start") {
    if (index === 0) return { timeline, note: "The first scene starts with the video." };
    return moveCut(timeline, index - 1, t, sentences);
  }
  if (index >= timeline.scenes.length - 1) return { timeline, note: "The last scene ends with the video." };
  return moveCut(timeline, index, t, sentences);
}

// ---- motion ----------------------------------------------------------------------------------

export interface MotionView {
  /** 100 = the whole cover region */
  startScale: number;
  endScale: number;
  /** centre as % of the picture */
  startCentre: [number, number];
  endCentre: [number, number];
}

function frameSize(timeline: Timeline): [number, number] {
  return [timeline.width, timeline.height];
}

/** Scale and centre fields of the inspector, from the rectangles (source-picture pixels). */
export function motionView(scene: TimelineScene, picture: BundlePicture | undefined, timeline: Timeline): MotionView {
  const [frameW, frameH] = frameSize(timeline);
  const imageW = picture?.width || frameW;
  const imageH = picture?.height || frameH;
  const cover = coverRegion(imageW, imageH, frameW, frameH);
  const view = (rect: PixelRect): { scale: number; centre: [number, number] } => ({
    scale: rect[2] > 0 ? Math.round((cover[2] / rect[2]) * 1000) / 10 : 100,
    centre: [Math.round(((rect[0] + rect[2] / 2) / imageW) * 1000) / 10, Math.round(((rect[1] + rect[3] / 2) / imageH) * 1000) / 10],
  });
  const start = view(scene.motion.start_rect);
  const end = view(scene.motion.end_rect);
  return { startScale: start.scale, endScale: end.scale, startCentre: start.centre, endCentre: end.centre };
}

/** Rectangles from scale and centre, kept inside the picture. */
export function rectFromView(scale: number, centre: [number, number], picture: BundlePicture | undefined, timeline: Timeline): PixelRect {
  const [frameW, frameH] = frameSize(timeline);
  const imageW = picture?.width || frameW;
  const imageH = picture?.height || frameH;
  const cover = coverRegion(imageW, imageH, frameW, frameH);
  const factor = Math.max(1, Math.min(400, scale)) / 100;
  const w = cover[2] / factor;
  const h = cover[3] / factor;
  const cx = (Math.max(0, Math.min(100, centre[0])) / 100) * imageW;
  const cy = (Math.max(0, Math.min(100, centre[1])) / 100) * imageH;
  const x = Math.max(0, Math.min(imageW - w, cx - w / 2));
  const y = Math.max(0, Math.min(imageH - h, cy - h / 2));
  return [round3(x), round3(y), round3(w), round3(h)];
}

export function setMotion(timeline: Timeline, index: number, patch: Partial<TimelineScene["motion"]>): Timeline {
  const scenes = timeline.scenes.map((scene, i) => (i === index ? { ...scene, motion: { ...scene.motion, ...patch }, edited: withField(scene.edited, "motion") } : scene));
  return { ...timeline, scenes };
}

/** The preset's rectangles for this picture (the same maths as the builder). */
export function presetRects(preset: string, picture: BundlePicture | undefined, timeline: Timeline): { start_rect: PixelRect; end_rect: PixelRect } | null {
  const spec = MOTION_PRESETS[preset];
  if (!spec) return null;
  const [frameW, frameH] = frameSize(timeline);
  const imageW = picture?.width || frameW;
  const imageH = picture?.height || frameH;
  const toRect = (frac: readonly number[]): PixelRect => {
    const r: Rect = rectInPixels(frac, imageW, imageH, frameW, frameH);
    return [round3(r[0]), round3(r[1]), round3(r[2]), round3(r[3])];
  };
  return { start_rect: toRect(spec.start), end_rect: toRect(spec.end) };
}

export function applyPreset(timeline: Timeline, index: number, preset: string, picture: BundlePicture | undefined): Timeline {
  const rects = presetRects(preset, picture, timeline);
  if (!rects) return timeline;
  return setMotion(timeline, index, { preset, ...rects, ease: null });
}

export function swapMotion(timeline: Timeline, index: number): Timeline {
  const scene = timeline.scenes[index];
  if (!scene) return timeline;
  return setMotion(timeline, index, { start_rect: scene.motion.end_rect, end_rect: scene.motion.start_rect });
}

// ---- popups ------------------------------------------------------------------------------------

let nextLocalId = 1;

export function freshId(prefix: string): string {
  nextLocalId += 1;
  return `${prefix}-${Date.now().toString(36)}${nextLocalId.toString(36)}`;
}

export function addPopup(timeline: Timeline, t: number, text: string, sentences: readonly BundleSentence[]): { timeline: Timeline; id: string } {
  const index = sceneIndexAt(timeline, t);
  const scene = timeline.scenes[index];
  if (!scene) return { timeline, id: "" };
  const start = round3(Math.max(scene.start_s, Math.min(t, scene.end_s - MIN_POPUP_S)));
  const end = round3(Math.min(scene.end_s, start + Math.max(MIN_POPUP_S, Math.min(3.5, scene.end_s - start))));
  const sentence = sentenceAt(sentences, start);
  const id = freshId("p");
  const popup: TimelinePopup = {
    id,
    scene: index,
    text,
    style: "Rounded box, brand colour, slide-in from left",
    position: "top-left",
    start_s: start,
    end_s: end,
    anim: "slide_left",
    png: "",
    anchor: sentence ? { sentence_id: sentence.id, offset_s: round3(start - sentence.start_s) } : null,
    edited: true,
    locked: false,
  };
  return { timeline: { ...timeline, popups: [...timeline.popups, popup].sort((a, b) => a.start_s - b.start_s) }, id };
}

export function updatePopup(timeline: Timeline, id: string, patch: Partial<TimelinePopup>): Timeline {
  const popups = timeline.popups.map((popup) => (popup.id === id ? { ...popup, ...patch, edited: true } : popup));
  return { ...timeline, popups: clampPopups({ ...timeline, popups }) };
}

export function removePopup(timeline: Timeline, id: string): Timeline {
  return { ...timeline, popups: timeline.popups.filter((popup) => popup.id !== id) };
}

// ---- captions -----------------------------------------------------------------------------------

export function updateCue(timeline: Timeline, id: string, patch: Partial<CaptionCue>): Timeline {
  const cues = timeline.captions.cues.map((cue) => (cue.id === id ? { ...cue, ...patch, edited: true } : cue));
  return { ...timeline, captions: { ...timeline.captions, cues: cues.filter((cue) => cue.end_s > cue.start_s) } };
}

export function removeCue(timeline: Timeline, id: string): Timeline {
  return { ...timeline, captions: { ...timeline.captions, cues: timeline.captions.cues.filter((cue) => cue.id !== id) } };
}

export function splitCue(timeline: Timeline, id: string, at: number): Timeline {
  const cue = timeline.captions.cues.find((c) => c.id === id);
  if (!cue) return timeline;
  const words = cue.text.replace(/\\N/g, " ").split(/\s+/).filter(Boolean);
  const cut = Math.max(1, Math.min(words.length - 1, at));
  if (words.length < 2) return timeline;
  const share = cut / words.length;
  const middle = round3(cue.start_s + (cue.end_s - cue.start_s) * share);
  const first: CaptionCue = { ...cue, text: words.slice(0, cut).join(" "), end_s: middle, edited: true };
  const second: CaptionCue = { ...cue, id: freshId("c"), text: words.slice(cut).join(" "), start_s: middle, edited: true };
  const cues = timeline.captions.cues.flatMap((c) => (c.id === id ? [first, second] : [c]));
  return { ...timeline, captions: { ...timeline.captions, cues } };
}

export function mergeCueWithNext(timeline: Timeline, id: string): Timeline {
  const cues = timeline.captions.cues;
  const index = cues.findIndex((c) => c.id === id);
  const next = cues[index + 1];
  if (index === -1 || !next) return timeline;
  const merged: CaptionCue = { ...cues[index]!, text: `${cues[index]!.text.replace(/\\N/g, " ")} ${next.text.replace(/\\N/g, " ")}`, end_s: next.end_s, edited: true };
  return { ...timeline, captions: { ...timeline.captions, cues: [...cues.slice(0, index), merged, ...cues.slice(index + 2)] } };
}

// ---- markers and music -----------------------------------------------------------------------

export function addMarker(timeline: Timeline, t: number, label = ""): { timeline: Timeline; id: string } {
  const id = freshId("m");
  const marker: TimelineMarker = { id, t: round3(t), label, colour: "#60A5FA", chapter: false };
  return { timeline: { ...timeline, markers: [...timeline.markers, marker].sort((a, b) => a.t - b.t) }, id };
}

export function updateMarker(timeline: Timeline, id: string, patch: Partial<TimelineMarker>): Timeline {
  return { ...timeline, markers: timeline.markers.map((marker) => (marker.id === id ? { ...marker, ...patch } : marker)).sort((a, b) => a.t - b.t) };
}

export function removeMarker(timeline: Timeline, id: string): Timeline {
  return { ...timeline, markers: timeline.markers.filter((marker) => marker.id !== id) };
}

export function setMusic(timeline: Timeline, patch: Partial<Timeline["music"]>): Timeline {
  return { ...timeline, music: { ...timeline.music, ...patch } };
}

export function setCaptionsEnabled(timeline: Timeline, enabled: boolean): Timeline {
  return { ...timeline, captions: { ...timeline.captions, enabled } };
}

export function setLocked(timeline: Timeline, index: number, locked: boolean): Timeline {
  return { ...timeline, scenes: timeline.scenes.map((scene, i) => (i === index ? { ...scene, locked } : scene)) };
}

// ---- history -------------------------------------------------------------------------------------

export interface HistoryEntry {
  timeline: Timeline;
  /** "move cut 7 to 8", for the undo tooltip */
  label: string;
}

/** The undo and redo stacks: 20 steps of the whole document. */
export class History {
  past: HistoryEntry[] = [];
  future: HistoryEntry[] = [];

  push(before: Timeline, label: string): void {
    this.past.push({ timeline: before, label });
    if (this.past.length > UNDO_LIMIT) this.past.shift();
    this.future = [];
  }

  undo(current: Timeline): { timeline: Timeline; label: string } | null {
    const entry = this.past.pop();
    if (!entry) return null;
    this.future.push({ timeline: current, label: entry.label });
    return entry;
  }

  redo(current: Timeline): { timeline: Timeline; label: string } | null {
    const entry = this.future.pop();
    if (!entry) return null;
    this.past.push({ timeline: current, label: entry.label });
    return entry;
  }

  get undoLabel(): string | null {
    return this.past.at(-1)?.label ?? null;
  }

  get redoLabel(): string | null {
    return this.future.at(-1)?.label ?? null;
  }

  clear(): void {
    this.past = [];
    this.future = [];
  }
}

// ---- ruler ticks -------------------------------------------------------------------------------

const TICK_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];

/** The major tick spacing (seconds) so that labels sit at least 64 px apart. */
export function majorStep(pxPerSecond: number): number {
  for (const step of TICK_STEPS) if (step * pxPerSecond >= 64) return step;
  return 300;
}

export function minorStep(major: number): number {
  return major >= 1 ? major / (major % 5 === 0 || major === 15 ? 5 : 4) : major / 5;
}

export function clampZoom(pxPerSecond: number): number {
  return Math.max(2, Math.min(600, pxPerSecond));
}

// ---- v0.2: layers, sound tracks, frames, adjust, speed, picture time ---------------------------
// (docs/CONTRACT-v0.2.md sections 3 and 4)

/** Where the video ends: the end of the last scene (layers and sounds are cut off there). */
export function videoEnd(timeline: Timeline): number {
  return timeline.scenes.length > 0 ? timeline.scenes[timeline.scenes.length - 1]!.end_s : 0;
}

export interface ClipSourceInfo {
  path: string;
  kind: "picture" | "clip" | "audio" | "captions";
  name: string;
  duration_s: number | null;
}

interface Timed {
  id: string;
  start_s: number;
  end_s: number;
}

/** `clip` with its start and end kept inside `[0, end]` and off its neighbours on the lane. */
function fitInLane<T extends Timed>(clips: readonly T[], clip: T, end: number): T {
  const others = clips.filter((item) => item.id !== clip.id);
  const before = others.filter((item) => item.start_s < clip.start_s + 1e-6 && item.end_s > clip.start_s - 1e-6);
  const previous = others.filter((item) => item.end_s <= clip.start_s + 1e-6).sort((a, b) => b.end_s - a.end_s)[0];
  const next = others.filter((item) => item.start_s >= clip.end_s - 1e-6 || (item.start_s > clip.start_s && item.start_s < clip.end_s)).sort((a, b) => a.start_s - b.start_s)[0];
  let start = Math.max(0, clip.start_s);
  if (before.length > 0) start = Math.max(start, ...before.map((item) => item.end_s));
  if (previous) start = Math.max(start, previous.end_s);
  let stop = Math.min(end > 0 ? end : clip.end_s, clip.end_s);
  if (next) stop = Math.min(stop, next.start_s);
  if (stop - start < MIN_CLIP_S) stop = start + MIN_CLIP_S;
  return { ...clip, start_s: round3(start), end_s: round3(stop) };
}

function roomAt<T extends Timed>(clips: readonly T[], t: number, wanted: number, end: number): { start: number; stop: number } | null {
  const start = Math.max(0, t);
  const next = clips.filter((item) => item.end_s > start + 1e-6).sort((a, b) => a.start_s - b.start_s)[0];
  if (next && next.start_s < start + MIN_CLIP_S) return null;
  const limit = Math.min(next ? next.start_s : Infinity, end > 0 ? end : Infinity);
  const stop = Math.min(start + wanted, limit);
  if (!Number.isFinite(stop) || stop - start < MIN_CLIP_S) return null;
  return { start: round3(start), stop: round3(stop) };
}

function sorted<T extends Timed>(clips: readonly T[]): T[] {
  return [...clips].sort((a, b) => a.start_s - b.start_s);
}

// layers

export function addLayer(timeline: Timeline, name = ""): { timeline: Timeline; id: string } {
  const id = freshId("layer");
  const layer: Layer = { id, name: name || `Layer ${timeline.layers.length + 1}`, visible: true, locked: false, clips: [] };
  return { timeline: { ...timeline, layers: [...timeline.layers, layer] }, id };
}

export function updateLayer(timeline: Timeline, id: string, patch: Partial<Omit<Layer, "clips" | "id">>): Timeline {
  return { ...timeline, layers: timeline.layers.map((layer) => (layer.id === id ? { ...layer, ...patch } : layer)) };
}

export function removeLayer(timeline: Timeline, id: string): Timeline {
  return { ...timeline, layers: timeline.layers.filter((layer) => layer.id !== id) };
}

export function findLayerClip(timeline: Timeline, id: string): { layer: Layer; clip: LayerClip } | null {
  for (const layer of timeline.layers) {
    const clip = layer.clips.find((item) => item.id === id);
    if (clip) return { layer, clip };
  }
  return null;
}

function withLayer(timeline: Timeline, layerId: string, fn: (layer: Layer) => Layer): Timeline {
  return { ...timeline, layers: timeline.layers.map((layer) => (layer.id === layerId ? fn(layer) : layer)) };
}

/**
 * A picture or clip on a layer at `t` (the top layer, or `layerId`; a layer is made when there
 * is none). A clip keeps its own length at its speed; a picture gets 5 s. The clip is cut to
 * the room on the lane and to the end of the video.
 */
export function addLayerClip(timeline: Timeline, layerId: string | null, source: ClipSourceInfo, t: number): { timeline: Timeline; id: string; note: string | null } {
  if (videoEnd(timeline) <= 0) return { timeline, id: "", note: "Add a picture or clip first; layers go over the scenes." };
  let doc = timeline;
  let target = layerId ? doc.layers.find((layer) => layer.id === layerId) : doc.layers[doc.layers.length - 1];
  if (!target) {
    const made = addLayer(doc);
    doc = made.timeline;
    target = doc.layers.find((layer) => layer.id === made.id);
  }
  if (!target) return { timeline, id: "", note: null };
  const kind = source.kind === "clip" ? "clip" : "picture";
  const wanted = kind === "clip" && source.duration_s ? source.duration_s : DEFAULT_LAYER_PICTURE_S;
  const room = roomAt(target.clips, t, wanted, videoEnd(doc));
  if (!room) return { timeline, id: "", note: `No room on ${target.name} at the playhead. Move the playhead to a free spot.` };
  const id = freshId("lc");
  const clip: LayerClip = {
    id,
    source: source.path,
    kind,
    name: source.name,
    start_s: room.start,
    end_s: room.stop,
    in_s: 0,
    speed: 1,
    keep_sound: false,
    gain_db: 0,
    duration_s: source.duration_s,
    frame: { ...defaultFrame("contain"), scale: 0.45, x: 0.24, y: 0.22 },
    adjust: defaultAdjust(),
  };
  const layerIdFinal = target.id;
  return { timeline: withLayer(doc, layerIdFinal, (layer) => ({ ...layer, clips: sorted([...layer.clips, clip]) })), id, note: null };
}

export function updateLayerClip(timeline: Timeline, id: string, patch: Partial<Omit<LayerClip, "id">>): Timeline {
  const found = findLayerClip(timeline, id);
  if (!found) return timeline;
  const next = fitInLane(found.layer.clips, { ...found.clip, ...patch }, videoEnd(timeline));
  return withLayer(timeline, found.layer.id, (layer) => ({ ...layer, clips: sorted(layer.clips.map((clip) => (clip.id === id ? next : clip))) }));
}

/** Moves the whole clip so it starts at `start` (its length kept; stopped by its neighbours). */
export function moveLayerClip(timeline: Timeline, id: string, start: number): Timeline {
  const found = findLayerClip(timeline, id);
  if (!found) return timeline;
  const length = found.clip.end_s - found.clip.start_s;
  const others = found.layer.clips.filter((item) => item.id !== id);
  let from = Math.max(0, start);
  const end = videoEnd(timeline);
  if (end > 0) from = Math.min(from, Math.max(0, end - length));
  for (const other of sorted(others)) {
    const overlaps = from < other.end_s && from + length > other.start_s;
    if (!overlaps) continue;
    // pushed to the nearer side of the clip in the way
    from = Math.abs(from - (other.end_s)) < Math.abs(from + length - other.start_s) ? other.end_s : other.start_s - length;
  }
  if (from < 0) return timeline;
  const moved: LayerClip = { ...found.clip, start_s: round3(from), end_s: round3(from + length) };
  if (others.some((other) => moved.start_s < other.end_s - 1e-6 && moved.end_s > other.start_s + 1e-6)) return timeline;
  return withLayer(timeline, found.layer.id, (layer) => ({ ...layer, clips: sorted(layer.clips.map((clip) => (clip.id === id ? moved : clip))) }));
}

/** Trims one edge to `t`; trimming the start of a clip advances where the file starts. */
export function trimLayerClip(timeline: Timeline, id: string, edge: "start" | "end", t: number): Timeline {
  const found = findLayerClip(timeline, id);
  if (!found) return timeline;
  const clip = found.clip;
  if (edge === "start") {
    const start = Math.min(round3(Math.max(0, t)), clip.end_s - MIN_CLIP_S);
    const delta = start - clip.start_s;
    const inS = clip.kind === "clip" ? Math.max(0, round3(clip.in_s + delta * clip.speed)) : clip.in_s;
    return updateLayerClip(timeline, id, { start_s: start, in_s: inS });
  }
  return updateLayerClip(timeline, id, { end_s: Math.max(round3(t), clip.start_s + MIN_CLIP_S) });
}

export function removeLayerClip(timeline: Timeline, id: string): Timeline {
  const found = findLayerClip(timeline, id);
  if (!found) return timeline;
  return withLayer(timeline, found.layer.id, (layer) => ({ ...layer, clips: layer.clips.filter((clip) => clip.id !== id) }));
}

export function splitLayerClip(timeline: Timeline, id: string, t: number): CutMove {
  const found = findLayerClip(timeline, id);
  if (!found) return { timeline, note: null };
  const clip = found.clip;
  const at = round3(t);
  if (at - clip.start_s < MIN_CLIP_S || clip.end_s - at < MIN_CLIP_S) return { timeline, note: "Move the playhead inside the clip, away from its edges." };
  const first: LayerClip = { ...clip, end_s: at };
  const second: LayerClip = { ...clip, id: freshId("lc"), start_s: at, in_s: clip.kind === "clip" ? round3(clip.in_s + (at - clip.start_s) * clip.speed) : clip.in_s };
  return { timeline: withLayer(timeline, found.layer.id, (layer) => ({ ...layer, clips: sorted(layer.clips.flatMap((item) => (item.id === id ? [first, second] : [item]))) })), note: null };
}

// sound tracks

export function addAudioTrack(timeline: Timeline, name = "", kind: AudioTrack["kind"] = "sound"): { timeline: Timeline; id: string } {
  const id = freshId("track");
  const track: AudioTrack = { id, name: name || `Sound ${timeline.audio_tracks.length + 1}`, kind, muted: false, gain_db: 0, clips: [] };
  return { timeline: { ...timeline, audio_tracks: [...timeline.audio_tracks, track] }, id };
}

export function updateAudioTrack(timeline: Timeline, id: string, patch: Partial<Omit<AudioTrack, "clips" | "id">>): Timeline {
  return { ...timeline, audio_tracks: timeline.audio_tracks.map((track) => (track.id === id ? { ...track, ...patch } : track)) };
}

export function removeAudioTrack(timeline: Timeline, id: string): Timeline {
  return { ...timeline, audio_tracks: timeline.audio_tracks.filter((track) => track.id !== id) };
}

export function findAudioClip(timeline: Timeline, id: string): { track: AudioTrack; clip: AudioClip } | null {
  for (const track of timeline.audio_tracks) {
    const clip = track.clips.find((item) => item.id === id);
    if (clip) return { track, clip };
  }
  return null;
}

function withTrack(timeline: Timeline, trackId: string, fn: (track: AudioTrack) => AudioTrack): Timeline {
  return { ...timeline, audio_tracks: timeline.audio_tracks.map((track) => (track.id === trackId ? fn(track) : track)) };
}

/** A sound file on a track at `t` (the first track with room, else a new one), its own length. */
export function addAudioClip(timeline: Timeline, trackId: string | null, source: ClipSourceInfo & { text?: string; voice?: string }, t: number): { timeline: Timeline; id: string; note: string | null } {
  if (videoEnd(timeline) <= 0) return { timeline, id: "", note: "Add a picture or clip first; sounds play under the scenes." };
  let doc = timeline;
  const wanted = source.duration_s && source.duration_s > 0 ? source.duration_s : DEFAULT_LAYER_PICTURE_S;
  const end = videoEnd(doc);
  let target: AudioTrack | undefined;
  let room: { start: number; stop: number } | null = null;
  if (trackId) {
    target = doc.audio_tracks.find((track) => track.id === trackId);
    room = target ? roomAt(target.clips, t, wanted, end) : null;
    if (target && !room) return { timeline, id: "", note: `No room on ${target.name} at the playhead. Move the playhead to a free spot.` };
  } else {
    for (const track of doc.audio_tracks) {
      room = roomAt(track.clips, t, wanted, end);
      if (room) {
        target = track;
        break;
      }
    }
  }
  if (!target) {
    const made = addAudioTrack(doc);
    doc = made.timeline;
    target = doc.audio_tracks.find((track) => track.id === made.id);
    room = target ? roomAt(target.clips, t, wanted, end) : null;
  }
  if (!target || !room) return { timeline, id: "", note: end <= 0 ? "Add a picture or clip first; sounds play under the scenes." : "No room at the playhead." };
  const id = freshId("ac");
  const clip: AudioClip = {
    id,
    source: source.path,
    name: source.name,
    start_s: room.start,
    end_s: room.stop,
    in_s: 0,
    speed: 1,
    gain_db: 0,
    fade_in_s: 0,
    fade_out_s: 0,
    duration_s: source.duration_s,
    text: source.text ?? "",
    voice: source.voice ?? "",
  };
  return { timeline: withTrack(doc, target.id, (track) => ({ ...track, clips: sorted([...track.clips, clip]) })), id, note: null };
}

export function updateAudioClip(timeline: Timeline, id: string, patch: Partial<Omit<AudioClip, "id">>): Timeline {
  const found = findAudioClip(timeline, id);
  if (!found) return timeline;
  const next = fitInLane(found.track.clips, { ...found.clip, ...patch }, videoEnd(timeline));
  return withTrack(timeline, found.track.id, (track) => ({ ...track, clips: sorted(track.clips.map((clip) => (clip.id === id ? next : clip))) }));
}

export function moveAudioClip(timeline: Timeline, id: string, start: number): Timeline {
  const found = findAudioClip(timeline, id);
  if (!found) return timeline;
  const length = found.clip.end_s - found.clip.start_s;
  const others = found.track.clips.filter((item) => item.id !== id);
  let from = Math.max(0, start);
  const end = videoEnd(timeline);
  if (end > 0) from = Math.min(from, Math.max(0, end - length));
  for (const other of sorted(others)) {
    const overlaps = from < other.end_s && from + length > other.start_s;
    if (!overlaps) continue;
    from = Math.abs(from - other.end_s) < Math.abs(from + length - other.start_s) ? other.end_s : other.start_s - length;
  }
  if (from < 0) return timeline;
  const moved: AudioClip = { ...found.clip, start_s: round3(from), end_s: round3(from + length) };
  if (others.some((other) => moved.start_s < other.end_s - 1e-6 && moved.end_s > other.start_s + 1e-6)) return timeline;
  return withTrack(timeline, found.track.id, (track) => ({ ...track, clips: sorted(track.clips.map((clip) => (clip.id === id ? moved : clip))) }));
}

export function trimAudioClip(timeline: Timeline, id: string, edge: "start" | "end", t: number): Timeline {
  const found = findAudioClip(timeline, id);
  if (!found) return timeline;
  const clip = found.clip;
  if (edge === "start") {
    const start = Math.min(round3(Math.max(0, t)), clip.end_s - MIN_CLIP_S);
    const delta = start - clip.start_s;
    return updateAudioClip(timeline, id, { start_s: start, in_s: Math.max(0, round3(clip.in_s + delta * clip.speed)) });
  }
  return updateAudioClip(timeline, id, { end_s: Math.max(round3(t), clip.start_s + MIN_CLIP_S) });
}

export function removeAudioClip(timeline: Timeline, id: string): Timeline {
  const found = findAudioClip(timeline, id);
  if (!found) return timeline;
  return withTrack(timeline, found.track.id, (track) => ({ ...track, clips: track.clips.filter((clip) => clip.id !== id) }));
}

export function splitAudioClip(timeline: Timeline, id: string, t: number): CutMove {
  const found = findAudioClip(timeline, id);
  if (!found) return { timeline, note: null };
  const clip = found.clip;
  const at = round3(t);
  if (at - clip.start_s < MIN_CLIP_S || clip.end_s - at < MIN_CLIP_S) return { timeline, note: "Move the playhead inside the clip, away from its edges." };
  const first: AudioClip = { ...clip, end_s: at, fade_out_s: 0 };
  const second: AudioClip = { ...clip, id: freshId("ac"), start_s: at, in_s: round3(clip.in_s + (at - clip.start_s) * clip.speed), fade_in_s: 0 };
  return { timeline: withTrack(timeline, found.track.id, (track) => ({ ...track, clips: sorted(track.clips.flatMap((item) => (item.id === id ? [first, second] : [item]))) })), note: null };
}

/** The seconds of the file a clip shows, at its speed (for the inspector's "uses ... of the file"). */
export function sourceSpan(clip: { in_s: number; speed: number; start_s: number; end_s: number }): [number, number] {
  return [clip.in_s, round3(clip.in_s + (clip.end_s - clip.start_s) * clip.speed)];
}

// scenes: frame, adjust, clip, length

function withScene(timeline: Timeline, index: number, fn: (scene: TimelineScene) => TimelineScene): Timeline {
  return { ...timeline, scenes: timeline.scenes.map((scene, i) => (i === index ? fn(scene) : scene)) };
}

export function setSceneFrame(timeline: Timeline, index: number, patch: Partial<FrameAdjust> | null): Timeline {
  return withScene(timeline, index, (scene) => ({ ...scene, frame: patch === null ? null : { ...(scene.frame ?? defaultFrame("cover")), ...patch } }));
}

export function setSceneAdjust(timeline: Timeline, index: number, patch: Partial<ColourAdjust> | null): Timeline {
  return withScene(timeline, index, (scene) => ({ ...scene, adjust: patch === null ? null : { ...(scene.adjust ?? defaultAdjust()), ...patch } }));
}

export function setSceneClip(timeline: Timeline, index: number, patch: Partial<ClipSource>): Timeline {
  return withScene(timeline, index, (scene) => (scene.clip ? { ...scene, clip: { ...scene.clip, ...patch } } : scene));
}

/** Everything after `from` (seconds) moved by `delta`: later scenes, popups, captions, markers, layer and sound clips. */
export function rippleAfter(timeline: Timeline, from: number, delta: number): Timeline {
  const shift = (t: number) => (t >= from - 1e-6 ? round3(Math.max(0, t + delta)) : t);
  const scenes = timeline.scenes.map((scene) => (scene.start_s >= from - 1e-6 ? { ...scene, start_s: shift(scene.start_s), end_s: shift(scene.end_s) } : scene));
  const end = scenes.length > 0 ? scenes[scenes.length - 1]!.end_s : 0;
  const clipTimes = <T extends Timed>(clip: T): T => {
    if (clip.start_s >= from - 1e-6) return { ...clip, start_s: shift(clip.start_s), end_s: shift(clip.end_s) };
    return clip.end_s > from ? { ...clip, end_s: Math.max(clip.start_s + MIN_CLIP_S, Math.min(end, round3(clip.end_s + delta))) } : clip;
  };
  return {
    ...timeline,
    scenes,
    duration_s: end,
    popups: timeline.popups.map((popup) => (popup.start_s >= from - 1e-6 ? { ...popup, start_s: shift(popup.start_s), end_s: shift(popup.end_s) } : popup)),
    captions: { ...timeline.captions, cues: timeline.captions.cues.map((cue) => (cue.start_s >= from - 1e-6 ? { ...cue, start_s: shift(cue.start_s), end_s: shift(cue.end_s) } : cue)) },
    markers: timeline.markers.map((marker) => ({ ...marker, t: shift(marker.t) })),
    layers: timeline.layers.map((layer) => ({ ...layer, clips: layer.clips.map(clipTimes).filter((clip) => clip.end_s - clip.start_s >= MIN_CLIP_S && clip.start_s < end) })),
    audio_tracks: timeline.audio_tracks.map((track) => ({ ...track, clips: track.clips.map(clipTimes).filter((clip) => clip.end_s - clip.start_s >= MIN_CLIP_S && clip.start_s < end) })),
  };
}

/**
 * The length of scene `index`: with a narration it is a rolling edit (the next cut moves, the
 * v0.1 rules); without one (`ripple`) everything after the scene moves with its end.
 */
export function setSceneLength(timeline: Timeline, index: number, lengthS: number, sentences: readonly BundleSentence[], mode: "roll" | "ripple"): CutMove {
  const scene = timeline.scenes[index];
  if (!scene) return { timeline, note: null };
  const current = scene.end_s - scene.start_s;
  if (mode === "roll") {
    if (index >= timeline.scenes.length - 1) return { timeline, note: "The last scene ends with the narration. Shorten a pause or move the cut before it instead." };
    return moveCut(timeline, index, scene.start_s + lengthS, sentences);
  }
  const wanted = Math.max(MIN_RIPPLE_SCENE_S, round3(lengthS));
  const delta = round3(wanted - current);
  if (Math.abs(delta) < 0.001) return { timeline, note: null };
  const widened = withScene(timeline, index, (item) => ({ ...item, end_s: round3(item.end_s + delta), edited: withField(item.edited, "times") }));
  const after = scene.end_s;
  const rest = rippleAfter({ ...widened, scenes: widened.scenes.map((item, i) => (i > index ? { ...item, start_s: round3(item.start_s + delta), end_s: round3(item.end_s + delta) } : item)) }, after + 1e-6, 0);
  const moved: Timeline = {
    ...rest,
    popups: timeline.popups.map((popup) => (popup.scene > index ? { ...popup, start_s: round3(popup.start_s + delta), end_s: round3(popup.end_s + delta) } : popup)),
    captions: { ...timeline.captions, cues: timeline.captions.cues.map((cue) => (cue.start_s >= after - 1e-6 ? { ...cue, start_s: round3(cue.start_s + delta), end_s: round3(cue.end_s + delta) } : cue)) },
    markers: timeline.markers.map((marker) => (marker.t >= after - 1e-6 ? { ...marker, t: round3(marker.t + delta) } : marker)),
    layers: timeline.layers.map((layer) => ({ ...layer, clips: layer.clips.map((clip) => (clip.start_s >= after - 1e-6 ? { ...clip, start_s: round3(clip.start_s + delta), end_s: round3(clip.end_s + delta) } : clip)) })),
    audio_tracks: timeline.audio_tracks.map((track) => ({ ...track, clips: track.clips.map((clip) => (clip.start_s >= after - 1e-6 ? { ...clip, start_s: round3(clip.start_s + delta), end_s: round3(clip.end_s + delta) } : clip)) })),
  };
  const end = videoEnd(moved);
  const trimmed: Timeline = {
    ...moved,
    duration_s: end,
    scenes: clampedTransitions(moved.scenes),
    layers: moved.layers.map((layer) => ({ ...layer, clips: layer.clips.filter((clip) => clip.start_s < end).map((clip) => ({ ...clip, end_s: Math.min(clip.end_s, end) })) })),
    audio_tracks: moved.audio_tracks.map((track) => ({ ...track, clips: track.clips.filter((clip) => clip.start_s < end).map((clip) => ({ ...clip, end_s: Math.min(clip.end_s, end) })) })),
  };
  return { timeline: { ...trimmed, popups: clampPopups(trimmed) }, note: null };
}

/** The layer and sound clips a time shows (for the monitor's selection and the inspector). */
export function layerClipsAt(timeline: Timeline, t: number): LayerClip[] {
  return timeline.layers.flatMap((layer) => (layer.visible ? layer.clips.filter((clip) => t >= clip.start_s && t < clip.end_s) : []));
}
