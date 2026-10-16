/**
 * The two parts of the timeline builder the preview engine does not port, for the storyboard
 * animatic (docs/M5-CONTRACT.md section 5): the camera-move continuity rules and the caption
 * chunking. Scene bounds, transition lengths, popup windows, presets and rectangles come from the
 * engine's own ports (`src/preview/bounds.ts`, `src/preview/motion.ts`), so there is one set of
 * maths. Each function here is a line-by-line port of the Python named in its comment:
 *
 * - `applyContinuity` and its helpers: render/motion.py
 * - `chunkCues`, `wrapLines`, `captionRulesFor`: render/captions.py and config/captions.yaml
 */
import { MOTION_PRESETS, presetSpec, pyRound, type Rect } from "../../preview/motion";

/** A rectangle as shares of the picture, `[x, y, w, h]` in 0-1. */
export type FracRect = Rect;

// ---- render/motion.py: continuity ------------------------------------------------------------

type Axis = "x" | "y" | "z";

/** `MotionPreset.flips`: the preset that moves the other way along an axis. */
const PRESET_FLIPS: Record<string, ReadonlyArray<readonly [Axis, string]>> = {
  zoom_in: [["z", "zoom_out"]],
  zoom_out: [["z", "zoom_in"]],
  pan_left: [["x", "pan_right"]],
  pan_right: [["x", "pan_left"]],
  pan_up: [["y", "pan_down"]],
  pan_down: [["y", "pan_up"]],
  drift: [["x", "drift"]],
  tilt_up: [["y", "tilt_down"]],
  tilt_down: [["y", "tilt_up"]],
  dolly_in: [["z", "dolly_out"]],
  dolly_out: [["z", "dolly_in"]],
};

function flipTo(presetId: string, axis: Axis): string | null {
  return PRESET_FLIPS[presetId]?.find(([name]) => name === axis)?.[1] ?? null;
}

/** `ScenePlan`: a scene's move while the builder decides it. */
export interface ScenePlan {
  preset: string;
  start: FracRect;
  end: FracRect;
  transition: string;
  motionLocked: boolean;
  transitionLocked: boolean;
  notes: string[];
}

const MOVE_MIN = 0.02;
const PAN_MIN = 0.05;

function centre(rect: Readonly<FracRect>): [number, number] {
  return [rect[0] + rect[2] / 2, rect[1] + rect[3] / 2];
}

function components(start: Readonly<FracRect>, end: Readonly<FracRect>): [number, number, number] {
  const [cx0, cy0] = centre(start);
  const [cx1, cy1] = centre(end);
  const meanW = Math.max((start[2] + end[2]) / 2, 1e-9);
  const meanH = Math.max((start[3] + end[3]) / 2, 1e-9);
  const dz = Math.log(Math.max(start[2], 1e-9) / Math.max(end[2], 1e-9));
  return [(cx1 - cx0) / meanW, (cy1 - cy0) / meanH, dz];
}

/** Python `max(items, key=abs)`: the first of the largest. */
function largest(items: ReadonlyArray<readonly [Axis, number]>): readonly [Axis, number] {
  let best = items[0]!;
  for (const item of items.slice(1)) if (Math.abs(item[1]) > Math.abs(best[1])) best = item;
  return best;
}

export type Direction = readonly [Axis, 1 | -1];

/** `move_direction`: ["x", 1] = the window travels right, ["z", 1] = zooms in; null = still. */
export function moveDirection(start: Readonly<FracRect>, end: Readonly<FracRect>): Direction | null {
  const [dx, dy, dz] = components(start, end);
  const [axis, value] = largest([
    ["x", dx],
    ["y", dy],
    ["z", dz],
  ]);
  if (Math.abs(value) < MOVE_MIN) return null;
  return [axis, value > 0 ? 1 : -1];
}

/** `camera_pan`: the sideways direction of a move (zoom ignored), when it pans at least 5 %. */
export function cameraPan(start: Readonly<FracRect>, end: Readonly<FracRect>): Direction | null {
  const [dx, dy] = components(start, end);
  const [axis, value] = largest([
    ["x", dx],
    ["y", dy],
  ]);
  if (Math.abs(value) < PAN_MIN) return null;
  return [axis, value > 0 ? 1 : -1];
}

function zoomsIn(start: Readonly<FracRect>, end: Readonly<FracRect>): boolean {
  return end[2] < start[2] * 0.97;
}

function startsWide(start: Readonly<FracRect>): boolean {
  return start[2] >= 0.93 && start[3] >= 0.93;
}

const WHIP_DIRECTIONS = ["left", "right", "up", "down"] as const;
const WHIP_FOR_PAN: Record<string, string> = { "x:1": "left", "x:-1": "right", "y:1": "up", "y:-1": "down" };
const PAN_FOR_WHIP: Record<string, string> = { left: "pan_right", right: "pan_left", up: "pan_down", down: "pan_up" };

function transitionKey(name: string | null | undefined): string {
  return String(name ?? "")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_")
    .replace(/ /g, "_");
}

function isZoomThrough(name: string): boolean {
  const key = transitionKey(name);
  return key === "zoom_through" || key === "zoomin";
}

function whipDirection(name: string): string | null {
  const key = transitionKey(name);
  if (key.startsWith("whip_") && (WHIP_DIRECTIONS as readonly string[]).includes(key.slice(5))) return key.slice(5);
  return null;
}

/** `mirror_rect`. */
export function mirrorRect(rect: Readonly<FracRect>, axis: Axis): FracRect {
  const [x, y, w, h] = rect;
  if (axis === "x") return [pyRound(1 - x - w, 6), y, w, h];
  if (axis === "y") return [x, pyRound(1 - y - h, 6), w, h];
  return [x, y, w, h];
}

function setPlan(plan: ScenePlan, preset: string, start: FracRect, end: FracRect, note: string) {
  plan.preset = preset;
  plan.start = start;
  plan.end = end;
  plan.notes.push(note);
}

function usePreset(plan: ScenePlan, preset: string, note: string): boolean {
  const spec = MOTION_PRESETS[preset];
  if (!spec) return false;
  setPlan(plan, preset, [...spec.start] as FracRect, [...spec.end] as FracRect, note);
  return true;
}

function flip(plan: ScenePlan, axis: Axis, note: string): boolean {
  const spec = presetSpec(plan.preset);
  const target = spec ? flipTo(spec.id, axis) : null;
  if (target === null) return false;
  const [start, end]: [FracRect, FracRect] = axis === "z" ? [plan.end, plan.start] : [mirrorRect(plan.start, axis), mirrorRect(plan.end, axis)];
  setPlan(plan, target, start, end, note);
  return true;
}

function zoomInChoice(plan: ScenePlan): string {
  const spec = presetSpec(plan.preset);
  const flipped = spec ? flipTo(spec.id, "z") : null;
  if (flipped && zoomsIn(MOTION_PRESETS[flipped]!.start, MOTION_PRESETS[flipped]!.end)) return flipped;
  return spec !== null && spec.energy === "calm" ? "slow_push" : "zoom_in";
}

function wideStartChoice(plan: ScenePlan): string {
  const spec = presetSpec(plan.preset);
  const flipped = spec ? flipTo(spec.id, "z") : null;
  if (flipped && startsWide(MOTION_PRESETS[flipped]!.start)) return flipped;
  return spec !== null && spec.energy === "calm" ? "slow_push" : "zoom_in";
}

function sameDirection(a: Direction | null, b: Direction | null): boolean {
  return a !== null && b !== null && a[0] === b[0] && a[1] === b[1];
}

/**
 * `apply_continuity`: a zoom-through cut leaves a scene that ends zoomed in and lands on one that
 * starts wide; a whip follows the outgoing pan; neighbouring moves alternate direction. Locked
 * moves and locked transitions are never changed. Changes the plans in place; returns the notes.
 */
export function applyContinuity(plans: ScenePlan[]): string[] {
  const pinned = new Set<number>();
  const count = plans.length;
  for (let i = 0; i < count - 1; i += 1) {
    if (!isZoomThrough(plans[i]!.transition)) continue;
    const before = plans[i]!;
    const after = plans[i + 1]!;
    if (!zoomsIn(before.start, before.end) && !before.motionLocked) {
      const choice = zoomInChoice(before);
      usePreset(before, choice, `Scene ${i + 1} now ends zoomed in (${choice}) so the zoom-through cut carries its push on.`);
    }
    pinned.add(i);
    if (!startsWide(after.start) && !after.motionLocked) {
      const choice = wideStartChoice(after);
      usePreset(after, choice, `Scene ${i + 2} now starts on the whole picture (${choice}) so the zoom-through cut lands wide.`);
    }
    pinned.add(i + 1);
  }

  plans.forEach((plan, i) => {
    const wanted = whipDirection(plan.transition);
    if (wanted === null) return;
    const pan = cameraPan(plan.start, plan.end);
    if (pan === null) return;
    const follows = WHIP_FOR_PAN[`${pan[0]}:${pan[1]}`]!;
    if (follows === wanted) {
      pinned.add(i);
      return;
    }
    if (!plan.transitionLocked) {
      plan.transition = `whip_${follows}`;
      plan.notes.push(`The cut after scene ${i + 1} now whips ${follows} to follow the camera.`);
      pinned.add(i);
    } else if (!plan.motionLocked && !pinned.has(i)) {
      const target = PAN_FOR_WHIP[wanted]!;
      const targetAxis: Axis = wanted === "left" || wanted === "right" ? "x" : "y";
      const note = `Scene ${i + 1} now pans the way the locked whip after it goes.`;
      if (pan[0] !== targetAxis || !flip(plan, targetAxis, note)) usePreset(plan, target, note);
      pinned.add(i);
    }
  });

  let previous: Direction | null = null;
  plans.forEach((plan, i) => {
    let direction = moveDirection(plan.start, plan.end);
    if (i > 0 && direction !== null && sameDirection(direction, previous)) {
      const noteHere = `Scene ${i + 1} now moves the other way to scene ${i}.`;
      if (!plan.motionLocked && !pinned.has(i)) {
        if (flip(plan, direction[0], noteHere)) direction = moveDirection(plan.start, plan.end);
      } else {
        const earlier = plans[i - 1]!;
        const beforeThat = i >= 2 ? moveDirection(plans[i - 2]!.start, plans[i - 2]!.end) : null;
        const flipped: Direction = [direction[0], direction[1] === 1 ? -1 : 1];
        if (!earlier.motionLocked && !pinned.has(i - 1) && !sameDirection(beforeThat, flipped)) {
          flip(earlier, direction[0], `Scene ${i} now moves the other way to scene ${i + 1}.`);
        }
      }
    }
    previous = direction;
  });

  return plans.flatMap((plan) => plan.notes);
}

// ---- render/captions.py and config/captions.yaml ---------------------------------------------

export interface CaptionRules {
  font: string;
  popupFont: string;
  rtl: boolean;
  maxCharsPerLine: number;
  maxLines: number;
  spaceDependent: boolean;
  size: number;
}

const DEFAULT_CAPTION_RULES: CaptionRules = {
  font: "Noto Sans",
  popupFont: "Noto Sans",
  rtl: false,
  maxCharsPerLine: 42,
  maxLines: 2,
  spaceDependent: true,
  size: 48,
};

/** `by_language` of config/captions.yaml. */
const LANGUAGE_CAPTION_RULES: Record<string, Partial<CaptionRules>> = {
  Arabic: { font: "Noto Naskh Arabic", popupFont: "Noto Sans Arabic", rtl: true, maxCharsPerLine: 36 },
  Urdu: { font: "Noto Naskh Arabic", popupFont: "Noto Sans Arabic", rtl: true, maxCharsPerLine: 36 },
  Hindi: { font: "Noto Sans Devanagari", popupFont: "Noto Sans Devanagari", maxCharsPerLine: 36 },
  Japanese: { font: "Noto Sans JP", popupFont: "Noto Sans JP", maxCharsPerLine: 13, spaceDependent: false },
  Korean: { font: "Noto Sans KR", popupFont: "Noto Sans KR", maxCharsPerLine: 20 },
};

/** `rules_for`: the language's rules (exact name, as config/captions.yaml lists it), else the defaults. */
export function captionRulesFor(language: string | null | undefined): CaptionRules {
  const own = language ? LANGUAGE_CAPTION_RULES[language] : undefined;
  return own ? { ...DEFAULT_CAPTION_RULES, ...own } : { ...DEFAULT_CAPTION_RULES };
}

export const CAPTION_PAUSE_S = 0.35;
export const MIN_CUE_S = 0.4;

export interface TimedWordLike {
  text: string;
  start_s: number;
  end_s: number;
}

export interface TimedSentenceLike {
  id: string;
  text: string;
  start_s: number;
  end_s: number;
  words: ReadonlyArray<TimedWordLike>;
}

/** Python `len()`: code points, not UTF-16 units. */
function len(text: string): number {
  return Array.from(text).length;
}

function join(words: ReadonlyArray<TimedWordLike>, spaceDependent: boolean): string {
  return words.map((word) => word.text).join(spaceDependent ? " " : "");
}

function spread(text: string, start: number, end: number, spaceDependent: boolean): TimedWordLike[] {
  const pieces = spaceDependent ? text.split(" ") : Array.from(text).filter((char) => !/\s/u.test(char));
  const total = pieces.reduce((sum, piece) => sum + len(piece), 0) || 1;
  const span = Math.max(end - start, 0);
  let clock = start;
  return pieces.map((piece) => {
    const length = (span * len(piece)) / total;
    const word = { text: piece, start_s: pyRound(clock, 3), end_s: pyRound(clock + length, 3) };
    clock += length;
    return word;
  });
}

function wordsOf(sentence: TimedSentenceLike, spaceDependent: boolean): TimedWordLike[] {
  const words = sentence.words.filter((word) => word.text.trim()).map((word) => ({ text: word.text, start_s: word.start_s, end_s: word.end_s }));
  if (words.length > 0 && !spaceDependent) {
    return words.flatMap((word) =>
      len(word.text.trim()) <= 1 ? [{ text: word.text.trim(), start_s: word.start_s, end_s: word.end_s }] : spread(word.text, word.start_s, word.end_s, false),
    );
  }
  if (words.length > 0) return words;
  const text = sentence.text.split(/\s+/u).filter(Boolean).join(" ");
  if (!text) return [];
  return spread(text, sentence.start_s, sentence.end_s, spaceDependent);
}

function splitBalanced(words: ReadonlyArray<TimedWordLike>, budget: number, spaceDependent: boolean): TimedWordLike[][] {
  if (words.length === 0) return [];
  const textLen = len(join(words, spaceDependent));
  if (textLen <= budget) return [[...words]];
  let count = Math.max(2, Math.ceil(textLen / budget));
  for (;;) {
    const target = textLen / count;
    const chunks: TimedWordLike[][] = [];
    let current: TimedWordLike[] = [];
    words.forEach((word, index) => {
      const remainingChunks = count - chunks.length;
      const remainingWords = words.length - index;
      if (current.length > 0 && remainingChunks < remainingWords) {
        const withWord = len(join([...current, word], spaceDependent));
        const without = len(join(current, spaceDependent));
        if (withWord > budget || (Math.abs(withWord - target) > Math.abs(without - target) && remainingChunks > 1)) {
          chunks.push(current);
          current = [];
        }
      }
      current.push(word);
    });
    if (current.length > 0) chunks.push(current);
    if (chunks.every((chunk) => len(join(chunk, spaceDependent)) <= budget) || count >= words.length) return chunks;
    count += 1;
  }
}

function splitUnits(units: string[], count: number, glue: string): string[][] {
  if (count <= 1 || units.length <= 1) return [units];
  const total = len(units.join(glue));
  const target = total / count;
  const chunks: string[][] = [];
  let current: string[] = [];
  units.forEach((unit, index) => {
    const remainingChunks = count - chunks.length;
    const remainingUnits = units.length - index;
    if (current.length > 0 && remainingChunks > 1 && remainingChunks < remainingUnits + 1) {
      const withUnit = len([...current, unit].join(glue));
      const without = len(current.join(glue));
      if (Math.abs(withUnit - target) > Math.abs(without - target)) {
        chunks.push(current);
        current = [];
      }
    }
    current.push(unit);
  });
  if (current.length > 0) chunks.push(current);
  return chunks;
}

/** `wrap_lines`: at most `maxLines` lines of about `maxChars` characters, balanced. */
export function wrapLines(text: string, maxChars: number, maxLines: number, spaceDependent = true): string[] {
  const clean = spaceDependent ? text.split(/\s+/u).filter(Boolean).join(" ") : text.trim();
  if (!clean) return [""];
  if (len(clean) <= maxChars) return [clean];
  const units = spaceDependent ? clean.split(" ") : Array.from(clean);
  const glue = spaceDependent ? " " : "";
  const linesWanted = Math.min(maxLines, Math.max(2, Math.ceil(len(clean) / maxChars)));
  return splitUnits(units, linesWanted, glue).map((chunk) => chunk.join(glue));
}

export interface CueLike {
  start_s: number;
  end_s: number;
  /** lines joined with the ASS line break (backslash N) */
  text: string;
}

/**
 * `chunk_cues`: words -> cues of at most `maxCharsPerLine x maxLines` characters, broken at
 * sentence ends and at silences longer than `pauseS`; lines joined with the ASS line break.
 */
export function chunkCues(
  sentences: ReadonlyArray<TimedSentenceLike>,
  { maxCharsPerLine = 42, maxLines = 2, pauseS = CAPTION_PAUSE_S, spaceDependent = true }: { maxCharsPerLine?: number; maxLines?: number; pauseS?: number; spaceDependent?: boolean } = {},
): CueLike[] {
  const budget = Math.max(4, Math.trunc(maxCharsPerLine)) * Math.max(1, Math.trunc(maxLines));
  const cues: CueLike[] = [];
  for (const sentence of sentences) {
    const words = wordsOf(sentence, spaceDependent);
    if (words.length === 0) continue;
    const runs: TimedWordLike[][] = [[words[0]!]];
    for (let i = 1; i < words.length; i += 1) {
      const word = words[i]!;
      if (word.start_s - words[i - 1]!.end_s > pauseS + 1e-9) runs.push([word]);
      else runs[runs.length - 1]!.push(word);
    }
    for (const run of runs) {
      for (const chunk of splitBalanced(run, budget, spaceDependent)) {
        const text = wrapLines(join(chunk, spaceDependent), maxCharsPerLine, maxLines, spaceDependent).join("\\N");
        const start = chunk[0]!.start_s;
        const end = Math.max(chunk[chunk.length - 1]!.end_s, start + MIN_CUE_S);
        cues.push({ start_s: pyRound(start, 3), end_s: pyRound(end, 3), text });
      }
    }
  }
  for (let i = 0; i < cues.length - 1; i += 1) {
    const current = cues[i]!;
    const following = cues[i + 1]!;
    if (current.end_s > following.start_s) current.end_s = pyRound(Math.max(following.start_s, current.start_s + 0.1), 3);
  }
  return cues;
}
