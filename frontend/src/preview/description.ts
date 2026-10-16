/**
 * The render description: everything the engine draws, worked out once from the timeline bundle
 * with the renderer's own bookkeeping (backend/sigma_editor/render/assembly.py `plan_layout`,
 * TRANSITION-PARITY.md section 2):
 *
 * - whole frames at the timeline's fps, the first scene starting at frame 0;
 * - scene k runs from its start to the next kept scene's start; a scene shorter than a frame is skipped;
 * - a cut is `n = round(duration x fps)` frames (0 below min_frames), capped at a quarter of the
 *   shorter scene, 3 s and half the shorter scene's frames, CENTRED on the boundary C:
 *   frames [C - h, C - h + n) with h = n // 2;
 * - scene clip k holds h_in frames before the scene, the scene and n_out - h_out after it, and its
 *   camera path spans the whole clip (`t = k / (frames - 1)`);
 * - cut frame k is drawn at linear progress (k + 0.5) / n, eased by the transition's curve;
 * - the last scene ends with a fade to black (white for dip_white) over its last n_f frames,
 *   frame k weighted (k + 1) / n_f.
 *
 * `frameAt(desc, n)` says what output frame n shows; tested frame by frame against the Python
 * reference (src/preview/__tests__/description.test.ts).
 */
import { voiceClips } from "./audio";
import { MotionPath, coverRegion, floorDiv, pyRound } from "./motion";
import { frameTime, transitionEase } from "./transitions/easing";
import { fadeOutColour, resolveTransition, type TransitionCatalogue } from "./transitions/registry";
import {
  DEFAULT_SETTINGS,
  type AudioDesc,
  type CueDesc,
  type CutDesc,
  type FrameState,
  type GainCurve,
  type PopupAnim,
  type PopupDesc,
  type PreviewBundle,
  type PreviewTimeline,
  type PreviewWord,
  type RenderDescription,
  type RenderSettings,
  type SceneDesc,
  type SceneRef,
  type VoiceClip,
} from "./types";

/** The longest transition anywhere (models/transitions.py MAX_TRANSITION_S). */
export const MAX_TRANSITION_S = 3.0;
/** Upper bound for transitions.max_share_of_shorter_scene (assembly.py MAX_SHARE_OF_SCENE). */
export const MAX_SHARE_OF_SCENE = 0.5;

export interface DescriptionOptions {
  settings?: Partial<RenderSettings>;
  catalogue?: TransitionCatalogue | null;
  /** URL of a file inside the project folder (default: the project files route) */
  fileUrl?: (projectId: string, path: string) => string;
  /** URL of one sentence's audio (default: the M5 media route) */
  segmentUrl?: (sentenceId: string) => string;
  popupsEnabled?: boolean;
}

export function defaultFileUrl(projectId: string, path: string): string {
  if (/^(https?:)?\/\//i.test(path) || path.startsWith("/") || path.startsWith("data:") || path.startsWith("blob:")) return path;
  const safe = path
    .split(/[\\/]+/)
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  return `/api/projects/${encodeURIComponent(projectId)}/files/${safe}`;
}

/** A path on this PC rather than one inside the project (`F:\\music\\a.wav`, `\\\\server\\x`, `/home/a`). */
export function isAbsolutePath(path: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(path) || /^[\\/]{2}/.test(path) || path.startsWith("/");
}

export function defaultSegmentUrl(projectId: string, sentenceId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/media/voice/segments/${encodeURIComponent(sentenceId)}.wav`;
}

// Cut lengths (assembly.py cut_limit / cut_frames) -----------------------------------------

/** The longest cut next to a scene of `shorterS` seconds and `shorterFrames` frames. */
export function cutLimit(shorterS: number, shorterFrames: number, fps: number, maxShare: number): number {
  const share = Math.min(Math.max(maxShare, 0), MAX_SHARE_OF_SCENE);
  const seconds = Math.min(Math.max(shorterS, 0) * share, MAX_TRANSITION_S);
  const byShare = Math.floor(seconds * fps + 0.6);
  return Math.max(0, Math.min(byShare, floorDiv(Math.max(Math.trunc(shorterFrames), 0), 2)));
}

export function cutFrames(durationS: number, fps: number, minFrames: number, longest: number): number {
  let frames = pyRound(Math.max(durationS, 0) * fps);
  frames = Math.min(frames, Math.max(longest, 0));
  return frames >= Math.max(minFrames, 1) ? frames : 0;
}

// The description ------------------------------------------------------------------------------

function transitionOf(timeline: PreviewTimeline, index: number): { type: string; duration_s: number } {
  const transition = timeline.scenes[index]?.transition_out;
  return {
    type: transition?.type ?? "film_dissolve",
    duration_s: Math.min(Number(transition?.duration_s ?? 0.6), MAX_TRANSITION_S),
  };
}

function popupAnim(value: string | undefined): PopupAnim {
  return value === "fade" || value === "pop" ? value : "slide_left";
}

/** Cue text -> lines, as the ASS writer passes them (`\N` breaks, braces made safe). */
export function cueLines(text: string): string[] {
  const cleaned = text.replace(/\r/g, "").replace(/\n/g, "\\N").replace(/\{/g, "(").replace(/\}/g, ")");
  return cleaned.split("\\N");
}

function describeAudio(bundle: PreviewBundle, projectId: string, totalFrames: number, fps: number, options: DescriptionOptions): AudioDesc {
  const timeline = bundle.timeline;
  const fileUrl = options.fileUrl ?? defaultFileUrl;
  const segmentUrl = options.segmentUrl ?? ((sentenceId: string) => defaultSegmentUrl(projectId, sentenceId));
  const voiceUrl = bundle.media?.voice_url ?? (timeline.voice?.path ? fileUrl(projectId, timeline.voice.path) : null);
  const clips: VoiceClip[] = totalFrames > 0 ? voiceClips(bundle, totalFrames, fps, segmentUrl, voiceUrl) : [];
  const music = timeline.music;
  const stemUrl = bundle.media?.stem_url ?? null;
  const envelopeUrl = bundle.media?.envelope_url ?? (music?.envelope ? fileUrl(projectId, music.envelope) : null);
  // Without a current stem the raw track plays: the bundle's `music_url` (the track sits in the
  // channel's music folder, an absolute path the project files route refuses), or a track kept
  // inside the project folder. Anything else plays no music until the stem is ready.
  const rawUrl = music?.path ? (bundle.media?.music_url ?? (isAbsolutePath(music.path) ? null : fileUrl(projectId, music.path))) : null;
  const raw =
    !stemUrl && rawUrl
      ? {
          url: rawUrl,
          startS: music?.start_s ?? 0,
          gainDb: music?.gain_db ?? -18,
          fadeInS: music?.fade_in_s ?? 1,
          fadeOutS: music?.fade_out_s ?? 2,
        }
      : null;
  const gain = bundle.preview_gain_db ?? timeline.render?.preview_gain_db ?? 0;
  return { clips, stemUrl, envelopeUrl, music: raw, gainDb: Number.isFinite(gain) ? Number(gain) : 0, gainCurve: describeGainCurve(bundle) };
}

/** loudnorm's measured gain when the bundle has it for the stem it plays (never for the raw track). */
function describeGainCurve(bundle: PreviewBundle): GainCurve | null {
  const curve = bundle.preview_gain_curve;
  if (!curve || !(curve.step_s > 0) || !curve.gain_db.length) return null;
  if (!bundle.media?.stem_url && Boolean(bundle.timeline.music?.path)) return null;
  if (!curve.gain_db.every((value) => Number.isFinite(value))) return null;
  return { stepS: curve.step_s, gainDb: curve.gain_db };
}

function describeCueWords(bundle: PreviewBundle): Map<string, PreviewWord[]> {
  const words = new Map<string, PreviewWord[]>();
  for (const [id, list] of Object.entries(bundle.cue_words ?? {})) {
    words.set(
      id,
      list.map((word) => ({ w: word.w, start_s: word.start_s, end_s: word.end_s })),
    );
  }
  return words;
}

/** The whole render description of a bundle (pure). */
export function buildDescription(bundle: PreviewBundle, options: DescriptionOptions = {}): RenderDescription {
  const timeline = bundle.timeline;
  const settings: RenderSettings = { ...DEFAULT_SETTINGS, ...(options.settings ?? {}) };
  const catalogue = options.catalogue ?? null;
  const projectId = timeline.project_id;
  const fps = Math.max(1, Math.trunc(timeline.fps || 30));
  const scenesIn = timeline.scenes ?? [];
  const pictures = new Map<number, [number, number]>();
  for (const picture of bundle.pictures ?? []) {
    if (picture.width > 0 && picture.height > 0) pictures.set(picture.scene, [picture.width, picture.height]);
  }
  const base: RenderDescription = {
    projectId,
    revision: bundle.revision ?? timeline.revision ?? 1,
    fps,
    width: timeline.width,
    height: timeline.height,
    aspect: timeline.aspect,
    firstFrame: 0,
    totalFrames: 0,
    durationS: 0,
    scenes: [],
    cuts: [],
    popups: [],
    cues: [],
    captionsEnabled: timeline.captions?.enabled !== false,
    captionStyle: timeline.captions?.style ?? {},
    audio: { clips: [], stemUrl: null, envelopeUrl: null, music: null, gainDb: 0, gainCurve: null },
    settings,
    catalogue,
    mock: Boolean(bundle.media?.mock),
    estimatedTiming: Boolean(bundle.timing_estimated),
    virtual: Boolean(bundle.virtual),
    cueWords: describeCueWords(bundle),
  };
  base.popups = options.popupsEnabled === false ? [] : describePopups(timeline);
  base.cues = describeCues(timeline);
  if (!scenesIn.length) {
    base.audio = describeAudio(bundle, projectId, 0, fps, options);
    return base;
  }

  const first = pyRound(scenesIn[0]!.start_s * fps);
  const starts = scenesIn.map((scene) => pyRound(scene.start_s * fps) - first);
  const ends = [...starts.slice(1), pyRound(scenesIn[scenesIn.length - 1]!.end_s * fps) - first];
  const lengths = starts.map((start, i) => Math.max(0, ends[i]! - start));
  const kept = lengths.map((length, i) => (length > 0 ? i : -1)).filter((i) => i >= 0);
  base.firstFrame = first;
  if (!kept.length) {
    base.audio = describeAudio(bundle, projectId, 0, fps, options);
    return base;
  }

  const minFrames = settings.minFrames;
  const share = settings.maxShare;
  const seconds = scenesIn.map((scene) => Math.max(scene.end_s - scene.start_s, 0));
  const cutN = new Map<number, number>();
  for (let i = 0; i + 1 < kept.length; i++) {
    const left = kept[i]!;
    const right = kept[i + 1]!;
    const longest = cutLimit(Math.min(seconds[left]!, seconds[right]!), Math.min(lengths[left]!, lengths[right]!), fps, share);
    cutN.set(left, cutFrames(transitionOf(timeline, left).duration_s, fps, minFrames, longest));
  }
  const last = kept[kept.length - 1]!;
  let fadeN = cutFrames(transitionOf(timeline, last).duration_s, fps, minFrames, cutLimit(seconds[last]!, lengths[last]!, fps, share));

  const consumed = (index: number, before: number | null): number => {
    let incoming = 0;
    if (before !== null && (cutN.get(before) ?? 0)) {
      const n = cutN.get(before)!;
      incoming = n - floorDiv(n, 2);
    }
    const outgoing = index !== last ? floorDiv(cutN.get(index) ?? 0, 2) : fadeN;
    return incoming + outgoing;
  };
  let previous: number | null = null;
  for (const index of kept) {
    // a safety net; the caps above already keep every body >= 0
    while (consumed(index, previous) > lengths[index]!) {
      if (index !== last && (cutN.get(index) ?? 0)) cutN.set(index, 0);
      else if (index === last && fadeN) fadeN = 0;
      else if (previous !== null) cutN.set(previous, 0);
      else break;
    }
    previous = index;
  }

  const scenes: SceneDesc[] = [];
  const cuts: CutDesc[] = [];
  previous = null;
  let outFrame = 0;
  for (const index of kept) {
    const scene = scenesIn[index]!;
    const nIn = previous !== null ? (cutN.get(previous) ?? 0) : 0;
    const hIn = floorDiv(nIn, 2);
    const isLast = index === last;
    const nOut = isLast ? 0 : (cutN.get(index) ?? 0);
    const tailOut = nOut - floorDiv(nOut, 2);
    const clipFrames = Math.max(lengths[index]! + hIn + tailOut, 1);
    const tail = isLast ? fadeN : nOut;
    const imageSize = pictures.get(scene.index) ?? pictures.get(index) ?? null;
    const bounds = imageSize ? coverRegion(imageSize[0], imageSize[1], timeline.width, timeline.height) : null;
    const path = MotionPath.forPreset(scene.motion.preset, scene.motion.start_rect, scene.motion.end_rect, {
      seed: index,
      bounds,
      easing: scene.motion.ease ?? null,
      handheldAmplitude: settings.handheldAmplitude,
    });
    const body = clipFrames - nIn - tail;
    const desc: SceneDesc = {
      number: index + 1,
      index,
      image: scene.image,
      imageSize,
      startFrame: starts[index]!,
      length: lengths[index]!,
      leadIn: hIn,
      tailOut,
      clipFrames,
      bodyStart: outFrame,
      body,
      path,
      style: scene.motion.style === "parallax" ? "parallax" : "ken_burns",
      strength: Number(scene.motion.strength ?? 0.035),
    };
    scenes.push(desc);
    outFrame += body;
    let cut: CutDesc | null = null;
    if (isLast && fadeN) {
      cut = {
        number: index + 1,
        from: scenes.length - 1,
        to: null,
        startFrame: outFrame,
        frames: fadeN,
        durationS: fadeN / fps,
        transition: null,
        fadeColour: fadeOutColour(transitionOf(timeline, index).type, catalogue),
        seed: index + 1,
      };
    } else if (!isLast && tail) {
      cut = {
        number: index + 1,
        from: scenes.length - 1,
        to: scenes.length,
        startFrame: outFrame,
        frames: tail,
        durationS: tail / fps,
        transition: resolveTransition(transitionOf(timeline, index).type, catalogue),
        fadeColour: null,
        seed: index + 1,
      };
    }
    if (cut) {
      cuts.push(cut);
      outFrame += cut.frames;
    }
    previous = index;
  }
  base.scenes = scenes;
  base.cuts = cuts;
  base.totalFrames = outFrame;
  base.durationS = outFrame / fps;
  base.audio = describeAudio(bundle, projectId, outFrame, fps, options);
  return base;
}

function describePopups(timeline: PreviewTimeline): PopupDesc[] {
  return (timeline.popups ?? []).map((popup, index) => ({
    key: popup.id || `popup-${index}`,
    index,
    scene: popup.scene,
    text: String(popup.text ?? "")
      .split(/\s+/)
      .filter(Boolean)
      .join(" "),
    style: popup.style ?? "",
    position: popup.position ?? "bottom-left",
    anim: popupAnim(popup.anim),
    startS: popup.start_s,
    endS: popup.end_s,
    png: popup.png ?? "",
  }));
}

function describeCues(timeline: PreviewTimeline): CueDesc[] {
  return (timeline.captions?.cues ?? [])
    .map((cue, index) => ({
      key: cue.id || `cue-${index}`,
      index,
      startS: cue.start_s,
      endS: cue.end_s,
      lines: cueLines(cue.text ?? ""),
      text: cue.text ?? "",
    }))
    .filter((cue) => cue.lines.join("").trim() !== "");
}

/** Records a picture's size once it is known (the cover region bounds the camera path). */
export function setImageSize(desc: RenderDescription, position: number, width: number, height: number): void {
  const scene = desc.scenes[position];
  if (!scene || width <= 0 || height <= 0) return;
  if (scene.imageSize && scene.imageSize[0] === width && scene.imageSize[1] === height) return;
  scene.imageSize = [width, height];
  scene.path = scene.path.withBounds(coverRegion(width, height, desc.width, desc.height));
}

// Frames ---------------------------------------------------------------------------------------

/** The video frame shown at `t` seconds: `n = floor(t x fps + 1e-6)`, clamped to the video. */
export function frameForTime(desc: Pick<RenderDescription, "fps" | "totalFrames">, t: number): number {
  const n = Math.floor(t * desc.fps + 1e-6);
  return Math.min(Math.max(n, 0), Math.max(desc.totalFrames - 1, 0));
}

export function timeOfFrame(desc: Pick<RenderDescription, "fps">, n: number): number {
  return n / desc.fps;
}

/** The scene whose body or outgoing cut holds output frame `n` (binary search). */
export function scenePositionAt(desc: RenderDescription, n: number): number {
  const scenes = desc.scenes;
  let lo = 0;
  let hi = scenes.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (scenes[mid]!.bodyStart <= n) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

function sceneRef(desc: RenderDescription, scene: SceneDesc, clipFrame: number): SceneRef {
  const frame = Math.min(Math.max(clipFrame, 0), scene.clipFrames - 1);
  return {
    scene,
    clipFrame: frame,
    rect: scene.path.frameRect(frame, scene.clipFrames, desc.fps),
    camera: scene.style === "parallax" ? scene.path.frameCamera(frame, scene.clipFrames, desc.fps) : null,
  };
}

/** What output frame `n` shows: a scene frame, a cut frame or a frame of the closing fade. */
export function frameAt(desc: RenderDescription, n: number): FrameState {
  if (!desc.scenes.length || desc.totalFrames <= 0) return { kind: "empty", n };
  const frame = Math.min(Math.max(Math.trunc(n), 0), desc.totalFrames - 1);
  const position = scenePositionAt(desc, frame);
  const scene = desc.scenes[position]!;
  const inBody = frame - scene.bodyStart;
  // clip frame of scene S at output frame m: h_in + (m - S.startFrame)
  if (inBody < scene.body) return { kind: "scene", n: frame, a: sceneRef(desc, scene, scene.leadIn + (frame - scene.startFrame)) };
  const cut = desc.cuts.find((item) => item.from === position);
  if (!cut) return { kind: "scene", n: frame, a: sceneRef(desc, scene, scene.leadIn + (frame - scene.startFrame)) };
  const k = frame - cut.startFrame;
  const a = sceneRef(desc, scene, scene.clipFrames - cut.frames + k);
  if (cut.to === null || !cut.transition) {
    return { kind: "fade", n: frame, cut, a, k, weight: (k + 1) / Math.max(cut.frames, 1), colour: cut.fadeColour ?? [0, 0, 0] };
  }
  const next = desc.scenes[cut.to]!;
  const tRaw = frameTime(k, cut.frames);
  return {
    kind: "cut",
    n: frame,
    cut,
    a,
    b: sceneRef(desc, next, k),
    k,
    tRaw,
    t: transitionEase(cut.transition.easing, tRaw),
  };
}

/**
 * Linear progress of a cut at video time `T` for a display that is not on the frame grid
 * (TRANSITION-PARITY.md section 2): `clamp((T - T0 + 0.5 / fps) / D)`.
 */
export function cutProgressAt(cut: CutDesc, fps: number, timeS: number): number {
  const t0 = cut.startFrame / fps;
  return Math.min(Math.max((timeS - t0 + 0.5 / fps) / Math.max(cut.durationS, 1e-9), 0), 1);
}

/** The cut boundaries in seconds (for "previous/next cut" in the transport). */
export function cutTimes(desc: RenderDescription): number[] {
  return desc.scenes.slice(1).map((scene) => scene.startFrame / desc.fps);
}

/** The caption cue on screen at `t` (libass shows a cue from its start up to, not including, its end). */
export function cueAt(desc: RenderDescription, t: number): CueDesc | null {
  if (!desc.captionsEnabled) return null;
  let found: CueDesc | null = null;
  for (const cue of desc.cues) {
    if (assTime(cue.startS) <= t + 1e-9 && t < assTime(cue.endS) - 1e-9) found = cue;
  }
  return found;
}

/** ASS stores times in centiseconds (`ass_time`). */
export function assTime(seconds: number): number {
  const total = Math.max(0, seconds);
  const hours = Math.trunc(total / 3600);
  const minutes = Math.trunc((total % 3600) / 60);
  const secs = total % 60;
  let centis = pyRound(secs * 100);
  if (centis >= 6000) centis = 5999;
  return hours * 3600 + minutes * 60 + centis / 100;
}
