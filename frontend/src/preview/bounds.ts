/**
 * Scene bounds and transition lengths: the rules of backend/sigma_editor/render/timeline_builder.py
 * (`scene_bounds`, `clip_transitions`, `popup_window`, `popup_anim_for`), ported once and tested
 * against the Python reference (src/preview/__tests__/bounds.test.ts). The storyboard animatic
 * uses them to lay a virtual timeline over the pictures before `timeline.json` exists.
 */
import { pyRound } from "./motion";

export const MIN_SCENE_S = 0.2;

export interface BoundsScene {
  sentence_ids: readonly string[];
  est_duration_s?: number | null;
}

export interface BoundsSentence {
  id: string;
  start_s: number;
  end_s: number;
}

export interface BoundsTiming {
  duration_s: number;
  sentences: readonly BoundsSentence[];
}

/**
 * `(start_s, end_s)` per scene on the voice clock: first sentence start to the next scene's start;
 * the first scene starts at 0; the last ends at the voice end plus `tailS`. A scene whose sentences
 * are not in the timing keeps its estimated length.
 */
export function sceneBounds(scenes: readonly BoundsScene[], timing: BoundsTiming, tailS = 0.8): [number, number][] {
  if (!scenes.length) return [];
  const byId = new Map<string, BoundsSentence>();
  for (const sentence of timing.sentences) byId.set(sentence.id, sentence);
  const bounds: [number, number][] = [];
  let clock = 0;
  for (const scene of scenes) {
    const covered = scene.sentence_ids.filter((id) => byId.has(id)).map((id) => byId.get(id)!);
    let start: number;
    let end: number;
    if (covered.length) {
      start = Math.min(...covered.map((s) => s.start_s));
      end = Math.max(...covered.map((s) => s.end_s));
    } else {
      start = clock;
      end = clock + Math.max(Number(scene.est_duration_s || 0), MIN_SCENE_S);
    }
    start = Math.max(start, clock);
    end = Math.max(end, start + MIN_SCENE_S);
    bounds.push([start, end]);
    clock = end;
  }
  for (let i = 0; i + 1 < bounds.length; i++) {
    const current = bounds[i]!;
    const following = bounds[i + 1]!;
    current[1] = Math.max(following[0], current[0] + MIN_SCENE_S);
    following[0] = current[1];
  }
  bounds[0]![0] = 0;
  const last = bounds[bounds.length - 1]!;
  last[1] = Math.max(last[1], timing.duration_s) + Math.max(tailS, 0);
  return bounds.map(([s, e]) => [pyRound(s, 3), pyRound(e, 3)]);
}

/**
 * Transition lengths clipped to `maxShare` of the shorter neighbouring scene (the last scene's fade
 * to its own length); anything under `minS` becomes a plain cut (0).
 */
export function clipTransitions(
  bounds: readonly (readonly [number, number])[],
  durations: readonly number[],
  options: { maxShare?: number; minS?: number } = {},
): number[] {
  const maxShare = options.maxShare ?? 0.25;
  const minS = options.minS ?? 0;
  const result: number[] = [];
  const count = bounds.length;
  durations.slice(0, count).forEach((wanted, index) => {
    const here = bounds[index]![1] - bounds[index]![0];
    let shorter = here;
    if (index < count - 1) {
      const following = bounds[index + 1]![1] - bounds[index + 1]![0];
      shorter = Math.min(here, following);
    }
    const limit = Math.max(0, maxShare * shorter);
    let duration = Math.min(Math.max(Number(wanted), 0), limit);
    if (duration < minS) duration = 0;
    result.push(pyRound(duration, 3));
  });
  return result;
}

export interface PopupWindowOptions {
  lead_s?: number;
  max_show_s?: number;
  end_margin_s?: number;
  min_show_s?: number;
}

/**
 * When a popup is on screen: `lead_s` after its key sentence starts until at most `max_show_s`
 * later and `end_margin_s` before the scene ends; null when the scene is too short.
 */
export function popupWindow(
  keyStartS: number,
  sceneStartS: number,
  sceneEndS: number,
  options: PopupWindowOptions = {},
): [number, number] | null {
  const lead = options.lead_s ?? 0.3;
  const maxShow = options.max_show_s ?? 4.0;
  const endMargin = options.end_margin_s ?? 0.2;
  const minShow = options.min_show_s ?? 0.6;
  let start = Math.max(sceneStartS, keyStartS + lead);
  const end = Math.min(start + maxShow, sceneEndS - endMargin);
  if (end - start < minShow) {
    start = Math.max(sceneStartS, end - minShow);
    if (end - start < minShow - 1e-9) return null;
  }
  return [pyRound(start, 3), pyRound(end, 3)];
}

/** `pop`, `fade` or `slide_left` from a storyboard popup style. */
export function popupAnimFor(style: string | null | undefined): "pop" | "fade" | "slide_left" {
  const text = (style ?? "").toLowerCase();
  if (text.includes("pop")) return "pop";
  if (text.includes("fade") && !text.includes("slide")) return "fade";
  return "slide_left";
}
