/**
 * Time helpers for the live preview: tenths-of-a-second clock text (the owner chose tenths over
 * frames, docs/M5-CONTRACT.md), the frame grid the renderer uses (`n = floor(t x fps + 1e-6)`),
 * cuts, the scene under the playhead and the monitor's text alternative.
 */
import type { Timeline } from "../../types/timeline";

/** "Scene 3 of 12 at 0:21.4. Popup: ... Caption: ..." for the monitor's text alternative. */
export function describeFrame(bundle: { timeline: Pick<Timeline, "scenes" | "popups" | "captions"> } | null, seconds: number): string {
  const timeline = bundle?.timeline;
  if (!timeline || timeline.scenes.length === 0) return "Preview";
  const index = sceneAt(timeline, seconds);
  const parts = [`Scene ${index + 1} of ${timeline.scenes.length} at ${formatTenths(seconds)}.`];
  const popup = timeline.popups.find((item) => seconds >= item.start_s && seconds <= item.end_s);
  if (popup) parts.push(`Popup: ${popup.text}.`);
  if (timeline.captions.enabled) {
    const cue = timeline.captions.cues.find((item) => seconds >= item.start_s && seconds < item.end_s);
    if (cue) parts.push(`Caption: ${cue.text.split("\\N").join(" ")}`);
  }
  return parts.join(" ");
}

/** Seconds -> "1:05.6" (tenths are cut, never rounded up, so 0.96 s reads 0:00.9); hours as "1:02:03.4". */
export function formatTenths(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const tenths = Math.floor(safe * 10 + 1e-6);
  const totalSeconds = Math.floor(tenths / 10);
  const tenth = tenths % 10;
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const sec = `${String(s).padStart(2, "0")}.${tenth}`;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

/** "1:00.8 to 1:11.2" for a range. */
export function formatRange(from: number, to: number): string {
  return `${formatTenths(from)} to ${formatTenths(to)}`;
}

/** The frame on screen at `t` (the renderer's rule: `floor(t x fps + 1e-6)`). */
export function frameIndex(t: number, fps: number): number {
  const rate = fps > 0 ? fps : 30;
  return Math.max(0, Math.floor(Math.max(0, t) * rate + 1e-6));
}

/** The last frame's time of a video `duration` seconds long. */
export function lastFrameTime(duration: number, fps: number): number {
  const rate = fps > 0 ? fps : 30;
  const frames = Math.ceil(Math.max(0, duration) * rate - 1e-6);
  return Math.max(0, (frames - 1) / rate);
}

/** One or more frames back (negative) or forward from `t`, kept inside the video. */
export function stepFrames(t: number, fps: number, delta: number, duration: number): number {
  const rate = fps > 0 ? fps : 30;
  const next = (frameIndex(t, rate) + delta) / rate;
  return clampTime(next, Math.max(lastFrameTime(duration, rate), 0));
}

export function clampTime(t: number, duration: number): number {
  if (!Number.isFinite(t)) return 0;
  return Math.min(Math.max(t, 0), Math.max(duration, 0));
}

/** The cut times (every scene start after the first), in order. */
export function cutTimes(timeline: Pick<Timeline, "scenes"> | null): number[] {
  return (timeline?.scenes ?? []).slice(1).map((scene) => scene.start_s);
}

/** The last cut before `t` (half a frame of slack), else the start. */
export function previousCut(cuts: readonly number[], t: number, fps: number): number {
  const slack = 0.5 / (fps > 0 ? fps : 30);
  let best = 0;
  for (const cut of cuts) if (cut < t - slack && cut > best) best = cut;
  return best;
}

/** The first cut after `t` (half a frame of slack), else the end. */
export function nextCut(cuts: readonly number[], t: number, fps: number, duration: number): number {
  const slack = 0.5 / (fps > 0 ? fps : 30);
  for (const cut of cuts) if (cut > t + slack) return cut;
  return Math.max(duration, 0);
}

/** The scene playing at `t` (0-based), the last one after the end, -1 without scenes. */
export function sceneAt(timeline: Pick<Timeline, "scenes"> | null, t: number): number {
  const scenes = timeline?.scenes ?? [];
  if (scenes.length === 0) return -1;
  for (let i = scenes.length - 1; i >= 0; i -= 1) if (t >= scenes[i]!.start_s) return i;
  return 0;
}
