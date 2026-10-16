/**
 * Live preview shapes (docs/M5-CONTRACT.md sections 2 and 4).
 *
 * `GET /api/projects/{id}/timeline` returns a {@link TimelineBundle}: the timeline plus
 * everything the preview engine needs in one call (sentences with word times, story sections,
 * picture sizes, media URLs and the loudness offset). The backend builds it from
 * `07_edit/timeline.json`, `05_voice/timing.json` and the pictures; the Pydantic models of
 * `api/preview.py` are the source of truth and these types mirror them. The storyboard's
 * animatic builds the same bundle in the browser (components/preview/animatic.ts).
 *
 * The second half of the file is the engine's public API as the React wrapper uses it
 * (`createPreview(canvas, bundle, options)`, section 4).
 */
import type { TransitionCatalogue } from "./storyboard";
import { parseTimeline, type PopupAnim, type Timeline } from "./timeline";

// ---- the bundle --------------------------------------------------------------------------

export interface BundleWord {
  /** the word as spoken */
  w: string;
  start_s: number;
  end_s: number;
}

export interface BundleSentence {
  /** timing.json sentence id */
  id: string;
  text: string;
  start_s: number;
  end_s: number;
  words: BundleWord[];
}

/** A story section of the script: scenes `start_scene` to `end_scene` (0-based, inclusive). */
export interface BundleSection {
  id: string;
  name: string;
  start_scene: number;
  end_scene: number;
}

/** A scene picture and its size in pixels (the camera rectangles are pixels of this picture). */
export interface BundlePicture {
  scene: number;
  /** relative to the project folder, e.g. 06_images/scene_01.png */
  path: string;
  width: number;
  height: number;
  /** a 128 px tall JPEG for strips and lanes; null when the picture is missing */
  thumb_url: string | null;
}

export interface BundleFont {
  family: string;
  url: string;
}

export interface BundleMedia {
  /** the whole narration (`05_voice/voice.wav`); null before the voice exists */
  voice_url: string | null;
  /** `07_edit/music_ducked.wav`; null when there is no music or no stem yet */
  stem_url: string | null;
  /** `07_edit/music_envelope.json` */
  envelope_url: string | null;
  /** the Noto fonts the captions use (`/api/fonts/...`) */
  fonts: BundleFont[];
  /**
   * additive: the timeline's own music track (`/api/projects/{id}/media/music?v=...`), which the
   * preview plays with the envelope applied live while the stem is not current; null without
   * music or when the track is not on this PC
   */
  music_url?: string | null;
}

/**
 * The gain the renderer's `loudnorm` gives the mix, measured on that mix: `gain_db[i]` covers
 * `i * step_s` to `(i + 1) * step_s` seconds (models/timeline.py `PreviewGainCurve`).
 */
export interface PreviewGainCurve {
  step_s: number;
  gain_db: number[];
}

/** What `GET /api/projects/{id}/timeline` returns. */
export interface TimelineBundle {
  timeline: Timeline;
  revision: number;
  sentences: BundleSentence[];
  sections: BundleSection[];
  pictures: BundlePicture[];
  media: BundleMedia;
  /** loudness offset (dB) so the preview plays as loud as the final file; null = unknown */
  preview_gain_db: number | null;
  /** additive: true before the edit step has built timeline.json (worked out from the storyboard) */
  virtual: boolean;
  /** additive: no voice yet, the times are the storyboard's estimates */
  timing_estimated: boolean;
  /** additive: per caption cue id, its words (for the active-word and single-word looks) */
  cue_words: Record<string, BundleWord[]>;
  /** additive: loudnorm's gain over the video once measured for this sound (followed instead of `preview_gain_db`) */
  preview_gain_curve: PreviewGainCurve | null;
}

// ---- other preview routes ----------------------------------------------------------------

/** `GET .../media/peaks/{voice|music}.json`: the lowest and highest sample of every `step_s` (0.01 s), -1 to 1. */
export interface WaveformPeaks {
  step_s: number;
  min: number[];
  max: number[];
  duration_s: number;
  /** "voice", "stem" or "track" */
  source: string;
}

/** `07_edit/music_envelope.json`: the music gain in dB per `step_s`. */
export interface MusicEnvelope {
  step_s: number;
  gain_db: number[];
}

export type PreviewAspect = "16:9" | "9:16";

/** Body of `POST .../popups/preview`. */
export interface PopupPreviewRequest {
  text: string;
  style: string;
  position: string;
  anim: PopupAnim;
  aspect: PreviewAspect;
  /** frame height in pixels the PNG is drawn for (90 to 2160) */
  height: number;
}

/** Answer of `POST .../popups/preview`: the PNG the renderer overlays and where it sits. */
export interface PopupPreviewResult {
  png_url: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Answer of `POST .../music/stem`. */
export interface MusicStemResult {
  stem_url: string | null;
  envelope_url: string | null;
  preview_gain_db: number | null;
}

// ---- parsing (defensive, like the other review payloads) ---------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function nullableStr(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function nullableNum(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function numList(value: unknown): number[] {
  return Array.isArray(value) ? value.map((item) => (typeof item === "number" && Number.isFinite(item) ? item : 0)) : [];
}

function parseWord(raw: unknown): BundleWord | null {
  if (!isRecord(raw)) return null;
  const start = nullableNum(raw.start_s ?? raw.start);
  if (start === null) return null;
  return { w: str(raw.w ?? raw.text ?? raw.word), start_s: start, end_s: Math.max(start, num(raw.end_s ?? raw.end, start)) };
}

export function parseBundleSentence(raw: unknown, position: number): BundleSentence | null {
  if (!isRecord(raw)) return null;
  const start = num(raw.start_s);
  const words = Array.isArray(raw.words) ? raw.words.map(parseWord).filter((word): word is BundleWord => word !== null) : [];
  return {
    id: str(raw.id) || `s${position + 1}`,
    text: str(raw.text),
    start_s: start,
    end_s: Math.max(start, num(raw.end_s, start)),
    words,
  };
}

function parseSection(raw: unknown, position: number): BundleSection | null {
  if (!isRecord(raw)) return null;
  const start = Math.max(0, Math.trunc(num(raw.start_scene)));
  return {
    id: str(raw.id) || `section-${position + 1}`,
    name: str(raw.name),
    start_scene: start,
    end_scene: Math.max(start, Math.trunc(num(raw.end_scene, start))),
  };
}

function parsePicture(raw: unknown): BundlePicture | null {
  if (!isRecord(raw)) return null;
  const scene = nullableNum(raw.scene);
  const path = str(raw.path);
  if (scene === null || !path) return null;
  return { scene: Math.trunc(scene), path, width: num(raw.width), height: num(raw.height), thumb_url: nullableStr(raw.thumb_url) };
}

function parseCueWords(raw: unknown): Record<string, BundleWord[]> {
  if (!isRecord(raw)) return {};
  const out: Record<string, BundleWord[]> = {};
  for (const [id, words] of Object.entries(raw)) {
    if (Array.isArray(words)) out[id] = words.map(parseWord).filter((word): word is BundleWord => word !== null);
  }
  return out;
}

function parseMedia(raw: unknown): BundleMedia {
  const media = isRecord(raw) ? raw : {};
  const fonts = Array.isArray(media.fonts)
    ? media.fonts
        .filter(isRecord)
        .map((font) => ({ family: str(font.family), url: str(font.url) }))
        .filter((font) => font.family && font.url)
    : [];
  return {
    voice_url: nullableStr(media.voice_url),
    stem_url: nullableStr(media.stem_url),
    envelope_url: nullableStr(media.envelope_url),
    fonts,
    music_url: nullableStr(media.music_url),
  };
}

export function parseGainCurve(raw: unknown): PreviewGainCurve | null {
  if (!isRecord(raw)) return null;
  const gain = numList(raw.gain_db);
  const step = num(raw.step_s, 0.1);
  return gain.length > 0 && step > 0 ? { step_s: step, gain_db: gain } : null;
}

/** Turns the answer of `GET /api/projects/{id}/timeline` into a bundle; null when it holds no timeline. */
export function parseTimelineBundle(raw: unknown): TimelineBundle | null {
  if (!isRecord(raw)) return null;
  const timeline = parseTimeline(raw.timeline);
  if (!timeline) return null;
  const revision = Math.max(1, Math.trunc(num(raw.revision, timeline.revision)));
  const gain = nullableNum(raw.preview_gain_db) ?? timeline.render.preview_gain_db;
  return {
    timeline: { ...timeline, revision },
    revision,
    sentences: Array.isArray(raw.sentences)
      ? raw.sentences.map(parseBundleSentence).filter((sentence): sentence is BundleSentence => sentence !== null)
      : [],
    sections: Array.isArray(raw.sections) ? raw.sections.map(parseSection).filter((section): section is BundleSection => section !== null) : [],
    pictures: Array.isArray(raw.pictures) ? raw.pictures.map(parsePicture).filter((picture): picture is BundlePicture => picture !== null) : [],
    media: parseMedia(raw.media),
    preview_gain_db: gain,
    virtual: raw.virtual === true,
    timing_estimated: raw.timing_estimated === true,
    cue_words: parseCueWords(raw.cue_words),
    preview_gain_curve: parseGainCurve(raw.preview_gain_curve),
  };
}

export function parseWaveformPeaks(raw: unknown): WaveformPeaks | null {
  if (!isRecord(raw)) return null;
  const min = numList(raw.min);
  const max = numList(raw.max);
  if (min.length === 0 && max.length === 0) return null;
  const step = num(raw.step_s, 0.01) || 0.01;
  return { step_s: step, min, max, duration_s: num(raw.duration_s, Math.max(min.length, max.length) * step), source: str(raw.source) };
}

export function parseMusicEnvelope(raw: unknown): MusicEnvelope | null {
  if (!isRecord(raw)) return null;
  const gain = numList(raw.gain_db);
  return gain.length > 0 ? { step_s: num(raw.step_s, 0.01) || 0.01, gain_db: gain } : null;
}

export function parsePopupPreview(raw: unknown): PopupPreviewResult | null {
  if (!isRecord(raw)) return null;
  const url = nullableStr(raw.png_url);
  if (!url) return null;
  return { png_url: url, x: num(raw.x), y: num(raw.y), w: num(raw.w), h: num(raw.h) };
}

export function parseMusicStem(raw: unknown): MusicStemResult {
  const data = isRecord(raw) ? raw : {};
  return {
    stem_url: nullableStr(data.stem_url),
    envelope_url: nullableStr(data.envelope_url),
    preview_gain_db: nullableNum(data.preview_gain_db),
  };
}

// ---- the engine (frontend/src/preview/engine.ts), as the React wrapper uses it -----------

/** Full = monitor size x devicePixelRatio (at most 1280 x 720); Half; Auto drops to Half under load. */
export const PREVIEW_QUALITIES = ["auto", "full", "half"] as const;
export type PreviewQuality = (typeof PREVIEW_QUALITIES)[number];

export const PREVIEW_QUALITY_LABELS: Record<PreviewQuality, string> = {
  auto: "Auto",
  full: "Full",
  half: "Half",
};

export function isPreviewQuality(value: unknown): value is PreviewQuality {
  return typeof value === "string" && (PREVIEW_QUALITIES as readonly string[]).includes(value);
}

/** The part of the video that plays over and over while loop is on. */
export interface PreviewLoopRange {
  in_s: number;
  out_s: number;
}

/** What the engine reports about itself (the monitor turns these into chips). */
export interface PreviewEngineStatus {
  /** "webgl2" normally; "canvas2d" when the graphics driver blocks WebGL2 or after a lost context */
  renderer: "webgl2" | "canvas2d";
  /** a lost WebGL context is being restored ("Restarting the preview...") */
  restarting: boolean;
  /** some transitions are drawn as a dissolve ("Simplified preview") */
  simplified: boolean;
  /** placeholder pictures or a silent voice ("Mock media") */
  mockMedia: boolean;
  /** the quality in use right now (Auto switches between them) */
  activeQuality: "full" | "half";
}

export const DEFAULT_ENGINE_STATUS: PreviewEngineStatus = {
  renderer: "webgl2",
  restarting: false,
  simplified: false,
  mockMedia: false,
  activeQuality: "full",
};

/** Event name -> handler. */
export interface PreviewEngineEvents {
  /** the time of the frame on screen, in seconds (while playing, after a seek and on pause) */
  time: (seconds: number) => void;
  play: () => void;
  pause: () => void;
  /** playback reached the end (or the out point with loop off) */
  ended: () => void;
  /** a status change: any subset of the fields */
  status: (status: Partial<PreviewEngineStatus>) => void;
  /** a plain-English problem ("The picture for scene 3 could not be loaded.") */
  error: (message: string) => void;
}
export type PreviewEngineEvent = keyof PreviewEngineEvents;

export interface PreviewEngineOptions {
  projectId: string;
  quality: PreviewQuality;
  loop: PreviewLoopRange | null;
  /** play 60 ms voice grains while scrubbing */
  scrubAudio: boolean;
  /** the bundle comes from the storyboard (an animatic) rather than timeline.json */
  animatic: boolean;
  /** the project uses placeholder pictures or a silent voice (SGE_*_PROVIDER=mock) */
  mockMedia: boolean;
  /** `POST .../popups/preview`, debounced 150 ms per popup; null when it cannot be drawn */
  popupPreview: (key: string, request: PopupPreviewRequest) => Promise<PopupPreviewResult | null>;
  /** a picture of the project at `height` pixels (cached JPEG); the engine may also load `path` itself */
  pictureUrl: (path: string, height: number) => string;
  /** the audio of one sentence (`GET .../media/voice/segments/{sid}.wav`) */
  voiceSegmentUrl: (sentenceId: string) => string;
  /** `/transitions/catalogue.json` when the page has it: each transition's easing and settings */
  catalogue?: TransitionCatalogue | null;
}

/** The handle `createPreview(canvas, bundle, options)` returns. */
export interface PreviewEngine {
  play(): void | Promise<void>;
  pause(): void;
  seek(seconds: number): void;
  setQuality(quality: PreviewQuality): void;
  setLoop(range: PreviewLoopRange | null): void;
  /** show the frame at `seconds` without moving the playhead; null goes back to the playhead */
  skim(seconds: number | null): void;
  destroy(): void;
  /** returns a function that stops listening (or nothing) */
  on<E extends PreviewEngineEvent>(event: E, handler: PreviewEngineEvents[E]): (() => void) | void;
  /** optional: draw a new bundle without starting over (a transition or popup changed) */
  update?(bundle: TimelineBundle): void;
}

export type PreviewEngineFactory = (canvas: HTMLCanvasElement, bundle: TimelineBundle, options: PreviewEngineOptions) => PreviewEngine;
