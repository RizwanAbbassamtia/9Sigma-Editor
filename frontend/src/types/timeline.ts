/**
 * Edit stage shapes. Mirrors backend/sigma_editor/models/timeline.py (Pydantic v2, the
 * source of truth); see docs/M3-M4-CONTRACT.md section 3 and the M5.1 additions of
 * docs/M5-CONTRACT.md section 1 (revision, sentence ids, edited lists, motion.ease, voice
 * segments, popup and cue ids, caption presets, markers, the music stem and the preview gain).
 * Every M5 field is additive: a timeline written before M5 parses with the defaults below.
 *
 * `07_edit/timeline.json` is the file on disk; the review payload of
 * `GET /api/projects/{id}/stage/edit` carries the proxy video, a summary of the timeline,
 * the render status per preset, the music tracks the channel folder offers and warnings.
 * Rectangles in the timeline are pixels of the source image `[x, y, w, h]`.
 */
import type { GateResult, ProjectFormat } from "./project";
import { parseGateResults } from "./project";
import type { StoryboardAspect } from "./storyboard";

// ---- render presets -----------------------------------------------------------------

/** The preset ids of config/render.yaml. "2160p" is shown as 4K. */
export const RENDER_PRESETS = ["720p", "1080p", "2160p"] as const;
export type RenderPreset = (typeof RENDER_PRESETS)[number];

export const RENDER_PRESET_LABELS: Record<RenderPreset, string> = {
  "720p": "720p",
  "1080p": "1080p",
  "2160p": "4K",
};

export const RENDER_PRESET_DESCRIPTIONS: Record<RenderPreset, string> = {
  "720p": "1280 x 720. Quick to render; fine for a check or a Short.",
  "1080p": "1920 x 1080. The normal upload size.",
  "2160p": "3840 x 2160. Four times the pixels of 1080p; slow to render on this computer.",
};

/** Frame size per preset for a 16:9 and a 9:16 video. */
export const RENDER_PRESET_SIZES: Record<RenderPreset, { landscape: [number, number]; portrait: [number, number] }> = {
  "720p": { landscape: [1280, 720], portrait: [720, 1280] },
  "1080p": { landscape: [1920, 1080], portrait: [1080, 1920] },
  "2160p": { landscape: [3840, 2160], portrait: [2160, 3840] },
};

export function isRenderPreset(value: unknown): value is RenderPreset {
  return typeof value === "string" && (RENDER_PRESETS as readonly string[]).includes(value);
}

/** "4K" for 2160p, the id itself for anything the backend adds later. */
export function renderPresetLabel(preset: string): string {
  return isRenderPreset(preset) ? RENDER_PRESET_LABELS[preset] : preset;
}

// ---- timeline.json ------------------------------------------------------------------

export const POPUP_ANIMS = ["slide_left", "fade", "pop"] as const;
export type PopupAnim = (typeof POPUP_ANIMS)[number];

/** [x, y, w, h] in source-image pixels */
export type PixelRect = [number, number, number, number];

/**
 * One sentence of the narration placed on the timeline (M5): `src_start_s` to `src_end_s` of the
 * recording plays at `at_s`. The renderer joins the segments (gaps are silence); the preview
 * plays one sound per sentence.
 */
export interface VoiceSegment {
  sentence_id: string;
  src_start_s: number;
  src_end_s: number;
  at_s: number;
}

export interface TimelineVoice {
  path: string;
  start_s: number;
  /** M5: the narration as sentence segments; null = the whole voice.wav from `start_s` */
  segments: VoiceSegment[] | null;
}

export interface TimelineMusic {
  path: string | null;
  start_s: number;
  gain_db: number;
  duck_db: number;
  duck_attack_s: number;
  duck_release_s: number;
  fade_in_s: number;
  fade_out_s: number;
  license_ok: boolean;
  /** M5: `07_edit/music_ducked.wav`, the music after offset, looping, volume, fades and the dip under the voice */
  stem: string | null;
  /** M5: `07_edit/music_envelope.json` (`{step_s: 0.01, gain_db: [...]}`), the dip as a gain curve */
  envelope: string | null;
}

/** How the camera moves over a picture (Motion v2): "ken_burns" or "parallax" (2.5D). */
export const TIMELINE_MOTION_STYLES = ["ken_burns", "parallax"] as const;
export type TimelineMotionStyle = (typeof TIMELINE_MOTION_STYLES)[number];
/** Parallax strength the backend uses when a scene does not say (config/render.yaml). */
export const DEFAULT_PARALLAX_STRENGTH = 0.035;

/** The Motion v2 curves (render/motion.py `EASINGS`) a scene may use instead of the curve of its preset. */
export const MOTION_EASES = ["ease_in_out_cubic", "ease_out_quart", "ease_in_out_sine", "snap", "linear"] as const;
export type MotionEase = (typeof MOTION_EASES)[number];

/** The words editors see for each curve (design section 3.5). */
export const MOTION_EASE_LABELS: Record<MotionEase, string> = {
  ease_in_out_cubic: "In and out",
  ease_out_quart: "Out",
  ease_in_out_sine: "Gentle",
  snap: "Snap",
  linear: "Linear",
};

export function isMotionEase(value: unknown): value is MotionEase {
  return typeof value === "string" && (MOTION_EASES as readonly string[]).includes(value);
}

export interface TimelineMotion {
  preset: string;
  start_rect: PixelRect;
  end_rect: PixelRect;
  /** "ken_burns" (eased pan and zoom) or "parallax" (2.5D depth; rendered as Ken Burns without the depth model) */
  style: TimelineMotionStyle;
  /** parallax only: share of the frame width the nearest things move against the farthest, 0-0.2 */
  strength: number;
  /** M5: overrides the curve of the preset; null = the curve of the preset */
  ease: MotionEase | null;
}

export interface TimelineTransition {
  type: string;
  duration_s: number;
}

export interface TimelineScene {
  index: number;
  /** image file, relative to the project folder (06_images/scene_01.png) */
  image: string;
  start_s: number;
  end_s: number;
  motion: TimelineMotion;
  transition_out: TimelineTransition;
  locked: boolean;
  /** M5: the sentences (timing.json ids) this scene shows */
  sentence_ids: string[];
  /** M5: the fields a person changed ("times", "motion", "transition_out", "image" ...), kept through a rebuild */
  edited: string[];
}

/** M5: a popup tied to a sentence; it starts `offset_s` after that sentence starts. */
export interface PopupAnchor {
  sentence_id: string;
  offset_s: number;
}

export interface TimelinePopup {
  scene: number;
  text: string;
  style: string;
  position: string;
  start_s: number;
  end_s: number;
  anim: PopupAnim;
  png: string;
  /** M5: stable id ("" on a timeline written before M5) */
  id: string;
  /** M5: the sentence the popup follows when times change; null = fixed times */
  anchor: PopupAnchor | null;
  /** M5: a person changed this popup */
  edited: boolean;
  /** M5: the AI must not change this popup */
  locked: boolean;
}

/** M5 caption looks (config/captions.yaml presets; the ASS writer and the preview draw the same). */
export const CAPTION_PRESETS = ["bold-outline", "active-word", "single-word-pop", "boxed"] as const;
export type CaptionPreset = (typeof CAPTION_PRESETS)[number];
export const DEFAULT_CAPTION_PRESET: CaptionPreset = "bold-outline";

export const CAPTION_PRESET_LABELS: Record<CaptionPreset, string> = {
  "bold-outline": "Bold with outline",
  "active-word": "Highlight the spoken word",
  "single-word-pop": "One word at a time",
  boxed: "Text on a box",
};

/** "phrase" = a few words per caption; "single_word" = one word per caption. */
export const CAPTION_LAYOUTS = ["phrase", "single_word"] as const;
export type CaptionLayout = (typeof CAPTION_LAYOUTS)[number];

/** The colour of the spoken word when the server does not say (the "yellow" look of config/captions.yaml). */
export const DEFAULT_ACTIVE_COLOUR = "#FFE34D";

export interface CaptionStyle {
  font: string;
  size: number;
  primary: string;
  outline: number;
  position: string;
  max_lines: number;
  max_chars_per_line: number;
  rtl: boolean;
  /** additive (config/captions.yaml's named looks); kept as the server sends them */
  bold?: boolean;
  outline_colour?: string;
  /** M5: the caption look */
  preset: CaptionPreset;
  /** M5: the colour of the spoken word in the "active-word" look */
  active_colour: string;
  /** M5: phrase or one word per caption */
  layout: CaptionLayout;
}

export interface CaptionCue {
  start_s: number;
  end_s: number;
  /** lines are joined with the ASS line break; the preview splits on it and never re-wraps */
  text: string;
  /** M5: stable id ("" on a timeline written before M5) */
  id: string;
  /** M5: a person changed this caption */
  edited: boolean;
}

export interface TimelineCaptions {
  enabled: boolean;
  style: CaptionStyle;
  cues: CaptionCue[];
}

export interface TimelineClip {
  path: string | null;
}

export interface TimelineProxy {
  width: number;
  height: number;
}

export const DEFAULT_MARKER_COLOUR = "#60A5FA";

/** M5: a marker on the ruler (the renderer ignores them; chapters feed the export later). */
export interface TimelineMarker {
  id: string;
  /** seconds on the timeline */
  t: number;
  label: string;
  colour: string;
  /** becomes a chapter at export */
  chapter: boolean;
}

/** M5: render facts measured by the backend. */
export interface TimelineRender {
  /** loudness offset of voice + music stem, so the preview plays as loud as the final file */
  preview_gain_db: number | null;
}

/** `07_edit/timeline.json` */
export interface Timeline {
  version: number;
  project_id: string;
  format: ProjectFormat;
  aspect: StoryboardAspect;
  fps: number;
  width: number;
  height: number;
  duration_s: number;
  voice: TimelineVoice;
  music: TimelineMusic;
  scenes: TimelineScene[];
  popups: TimelinePopup[];
  captions: TimelineCaptions;
  intro: TimelineClip;
  outro: TimelineClip;
  presets: string[];
  proxy: TimelineProxy;
  /** M5: bumped by every save (starts at 1; the renderer ignores it) */
  revision: number;
  /** M5 */
  markers: TimelineMarker[];
  /** M5 */
  render: TimelineRender;
}

// ---- review payload -----------------------------------------------------------------

export const RENDER_STATUSES = ["not_rendered", "queued", "rendering", "done", "failed"] as const;
export type RenderStatus = (typeof RENDER_STATUSES)[number];

/** One final render (`07_edit/final_<preset>.mp4`) and where it stands. */
export interface RenderOutput {
  preset: string;
  status: RenderStatus;
  /** relative to the project folder when known */
  path: string | null;
  /** a ready http(s) URL when the backend sends one */
  play_url: string | null;
  /** 0-100 while rendering, when the backend reports it in the payload */
  progress: number | null;
  error: string | null;
  duration_s: number | null;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
  /** when the file was made (ISO 8601), when the backend says */
  rendered_at: string | null;
  /** cached scene clips (Transitions v2): how many the last render of this size needed ... */
  scene_clips_total: number | null;
  /** ... and how many of them it could reuse from an earlier render */
  scene_clips_reused: number | null;
  /** plain English, e.g. "Reused 2 of 3 scene clips." */
  reuse_note: string | null;
}

/** A track from the channel's music folder. */
export interface MusicTrack {
  path: string;
  name: string;
  /** a `<track>.license.txt` sidecar or a LICENSE file in the folder was found */
  license_ok: boolean;
  duration_s: number | null;
}

/** The numbers the Edit panel shows above the timeline. */
export interface TimelineSummary {
  scene_count: number;
  duration_s: number;
  /** transition types used, in order of first use */
  transitions: string[];
  popup_count: number;
  caption_cues: number;
  fps: number;
  width: number;
  height: number;
  aspect: StoryboardAspect | null;
}

/** One scene as the scene strip under the player needs it. */
export interface EditSceneView {
  index: number;
  start_s: number;
  end_s: number;
  /** end_s - start_s */
  duration_s: number;
  /** scene image, relative to the project folder when known */
  image_path: string | null;
  /** a ready http(s) URL when the backend sends one */
  image_url: string | null;
  /** the transition out of this scene (a v2 id) */
  transition: string | null;
  /** its length in seconds, when the backend says */
  transition_s: number | null;
  /** the camera move (a motion preset id such as "zoom_in" or "drift") */
  motion: string | null;
  popup_text: string | null;
  locked: boolean;
}

/** What `GET /api/projects/{id}/stage/edit` returns, after `parseEditPayload`. */
export interface EditReviewPayload {
  /** the proxy video (`07_edit/proxy.mp4`), relative to the project folder */
  proxy_path: string | null;
  /** a ready URL for the proxy when the backend sends one */
  proxy_url: string | null;
  /** the whole timeline when the payload carries it */
  timeline: Timeline | null;
  summary: TimelineSummary;
  scenes: EditSceneView[];
  renders: RenderOutput[];
  /** the presets requested for this project (timeline.presets) */
  presets: string[];
  /** the presets the backend offers; RENDER_PRESETS when it sends none */
  available_presets: string[];
  /** null when the payload does not say; Settings > Render decides then */
  enable_4k: boolean | null;
  music_tracks: MusicTrack[];
  music_path: string | null;
  music_license_ok: boolean;
  captions_enabled: boolean;
  popups_enabled: boolean;
  warnings: string[];
  gate_results: GateResult[];
  /** `07_edit/render.log` when the backend names it */
  render_log_path: string | null;
  /** where the voice timing came from ("estimated", "provider", ...), when the backend says */
  timing_source: string | null;
  /**
   * Plain-English notes on the last renders: the scene clips and transitions each one could
   * reuse ("Proxy: reused 11 of 12 scene clips and 10 of 11 transitions.") and scenes that had
   * to use the Ken Burns move because 2.5D parallax was not available.
   */
  render_notes: string[];
  /** M5: `07_edit/music_ducked.wav` through the files route, when the edit step made it */
  stem_url: string | null;
  /** M5: `07_edit/music_envelope.json` through the files route */
  envelope_url: string | null;
  /** M5: the loudness offset the live preview plays with */
  preview_gain_db: number | null;
  // 9Sigma Editor (docs/CONTRACT-v0.1.md sections 4 and 5)
  /** "standard", "high" or "best": the quality of the last render (or the Settings default) */
  quality: string;
  /** "none", "youtube_1080p" or "youtube_4k" */
  upload_preset: string;
  h265_copy: boolean;
  /** what the person wrote about the music's licence */
  music_licence_note: string;
  /** the edit's own music folder (`01_media`) */
  music_folder: string | null;
  /** the editor's own checks (pictures present, caption speed) */
  checks: GateResult[];
  /** a render is running right now */
  rendering: boolean;
  render_message: string;
  render_pct: number | null;
  /** why the last render stopped, in plain English */
  last_render_error: string | null;
}

/**
 * `POST /api/edits/{id}/edit`: the simple choices of the editor (docs/CONTRACT-v0.1.md
 * section 4). Mirrors `EditChoices` in backend/sigma_editor/models/timeline.py. A missing
 * field means "leave as is"; `music_path: ""` removes the music.
 */
export interface EditChoices {
  presets?: string[];
  music_path?: string | null;
  captions_enabled?: boolean;
  popups_enabled?: boolean;
  /** -60 to 12 dB */
  music_gain_db?: number;
  /** -60 to 0 dB: how far the music dips under the voice */
  music_duck_db?: number;
  /** build the timeline again from the scene plan and the timing */
  rebuild?: boolean;
}

// ---------------------------------------------------------------------------
// Defensive parsing of the review payload. The backend's shape is the contract's prose,
// so every field accepts the obvious spellings; unknown fields are kept where a document
// is sent back (the timeline) so an approve never drops what the server wrote.
// ---------------------------------------------------------------------------

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

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function pixelRect(value: unknown, fallback: PixelRect): PixelRect {
  if (!Array.isArray(value) || value.length !== 4) return fallback;
  const parts = value.map((item) => (typeof item === "number" && Number.isFinite(item) ? item : NaN));
  return parts.some(Number.isNaN) ? fallback : (parts as PixelRect);
}

/** "ken_burns" or "parallax" from what the server wrote ("2.5D parallax" and the like map too). */
export function motionStyleOf(value: unknown): TimelineMotionStyle {
  const key = typeof value === "string" ? value.trim().toLowerCase().replace(/[\s-]+/g, "_") : "";
  return key === "parallax" || key === "2.5d" || key === "2_5d" || key === "2.5d_parallax" || key === "depth" ? "parallax" : "ken_burns";
}

function parseTimelineScene(raw: unknown, position: number): TimelineScene {
  const base: TimelineScene = {
    index: position,
    image: "",
    start_s: 0,
    end_s: 0,
    motion: { preset: "hold", start_rect: [0, 0, 0, 0], end_rect: [0, 0, 0, 0], style: "ken_burns", strength: DEFAULT_PARALLAX_STRENGTH, ease: null },
    transition_out: { type: "film_dissolve", duration_s: 0.6 },
    locked: false,
    sentence_ids: [],
    edited: [],
  };
  if (!isRecord(raw)) return base;
  const motion = isRecord(raw.motion) ? raw.motion : {};
  const transition = isRecord(raw.transition_out) ? raw.transition_out : {};
  const image = typeof raw.image === "string" ? raw.image : isRecord(raw.image) ? str(raw.image.path) : str(raw.image_path);
  return {
    ...raw,
    index: num(raw.index, position),
    image,
    start_s: num(raw.start_s),
    end_s: num(raw.end_s),
    motion: {
      ...motion,
      preset: str(motion.preset, base.motion.preset),
      start_rect: pixelRect(motion.start_rect, base.motion.start_rect),
      end_rect: pixelRect(motion.end_rect, base.motion.end_rect),
      style: motionStyleOf(motion.style),
      strength: Math.min(Math.max(num(motion.strength, DEFAULT_PARALLAX_STRENGTH), 0), 0.2),
      ease: isMotionEase(motion.ease) ? motion.ease : null,
    },
    transition_out: { ...transition, type: str(transition.type, base.transition_out.type), duration_s: num(transition.duration_s, 0.6) },
    locked: bool(raw.locked),
    sentence_ids: strList(raw.sentence_ids),
    edited: strList(raw.edited),
  };
}

function parseAnchor(raw: unknown): PopupAnchor | null {
  if (!isRecord(raw)) return null;
  const sentenceId = str(raw.sentence_id);
  return sentenceId ? { sentence_id: sentenceId, offset_s: num(raw.offset_s) } : null;
}

function parsePopup(raw: unknown): TimelinePopup | null {
  if (!isRecord(raw)) return null;
  const anim = str(raw.anim);
  return {
    ...raw,
    scene: num(raw.scene),
    text: str(raw.text),
    style: str(raw.style),
    position: str(raw.position),
    start_s: num(raw.start_s),
    end_s: num(raw.end_s),
    anim: (POPUP_ANIMS as readonly string[]).includes(anim) ? (anim as PopupAnim) : "fade",
    png: str(raw.png),
    id: str(raw.id),
    anchor: parseAnchor(raw.anchor),
    edited: bool(raw.edited),
    locked: bool(raw.locked),
  };
}

function parseCue(raw: Record<string, unknown>): CaptionCue {
  return { ...raw, start_s: num(raw.start_s), end_s: num(raw.end_s), text: str(raw.text), id: str(raw.id), edited: bool(raw.edited) };
}

function oneOfOr<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

function parseCaptions(raw: unknown): TimelineCaptions {
  const style: CaptionStyle = {
    font: "Noto Sans",
    size: 48,
    primary: "#FFFFFF",
    outline: 2,
    position: "bottom",
    max_lines: 2,
    max_chars_per_line: 32,
    rtl: false,
    preset: DEFAULT_CAPTION_PRESET,
    active_colour: DEFAULT_ACTIVE_COLOUR,
    layout: "phrase",
  };
  if (!isRecord(raw)) return { enabled: true, style, cues: [] };
  const rawStyle = isRecord(raw.style) ? raw.style : {};
  const cues = Array.isArray(raw.cues) ? raw.cues.filter(isRecord).map(parseCue) : [];
  return {
    ...raw,
    enabled: bool(raw.enabled, true),
    style: {
      ...rawStyle,
      font: str(rawStyle.font, style.font),
      size: num(rawStyle.size, style.size),
      primary: str(rawStyle.primary, style.primary),
      outline: num(rawStyle.outline, style.outline),
      position: str(rawStyle.position, style.position),
      max_lines: num(rawStyle.max_lines, style.max_lines),
      max_chars_per_line: num(rawStyle.max_chars_per_line, style.max_chars_per_line),
      rtl: bool(rawStyle.rtl),
      preset: oneOfOr(rawStyle.preset, CAPTION_PRESETS, style.preset),
      active_colour: str(rawStyle.active_colour, style.active_colour) || style.active_colour,
      layout: oneOfOr(rawStyle.layout, CAPTION_LAYOUTS, style.layout),
    },
    cues,
  };
}

function parseMusic(raw: unknown): TimelineMusic {
  const base: TimelineMusic = {
    path: null,
    start_s: 0,
    gain_db: -18,
    duck_db: -12,
    duck_attack_s: 0.3,
    duck_release_s: 0.8,
    fade_in_s: 1,
    fade_out_s: 2,
    license_ok: false,
    stem: null,
    envelope: null,
  };
  if (!isRecord(raw)) return base;
  return {
    ...raw,
    path: nullableStr(raw.path),
    start_s: num(raw.start_s, base.start_s),
    gain_db: num(raw.gain_db, base.gain_db),
    duck_db: num(raw.duck_db, base.duck_db),
    duck_attack_s: num(raw.duck_attack_s, base.duck_attack_s),
    duck_release_s: num(raw.duck_release_s, base.duck_release_s),
    fade_in_s: num(raw.fade_in_s, base.fade_in_s),
    fade_out_s: num(raw.fade_out_s, base.fade_out_s),
    license_ok: bool(raw.license_ok),
    stem: nullableStr(raw.stem),
    envelope: nullableStr(raw.envelope),
  };
}

function parseSegment(raw: unknown): VoiceSegment | null {
  if (!isRecord(raw)) return null;
  const sentenceId = str(raw.sentence_id);
  const srcStart = num(raw.src_start_s);
  const srcEnd = num(raw.src_end_s, srcStart);
  // The model refuses a clip without a sentence or with no length.
  if (!sentenceId || srcEnd <= srcStart) return null;
  return { ...raw, sentence_id: sentenceId, src_start_s: srcStart, src_end_s: srcEnd, at_s: num(raw.at_s) };
}

/** `voice.segments`: null (the whole voice.wav) unless the timeline lists them. */
function parseSegments(raw: unknown): VoiceSegment[] | null {
  if (!Array.isArray(raw)) return null;
  return raw.map(parseSegment).filter((segment): segment is VoiceSegment => segment !== null);
}

function parseMarker(raw: unknown): TimelineMarker | null {
  if (!isRecord(raw)) return null;
  const t = nullableNum(raw.t);
  if (t === null) return null;
  return { ...raw, id: str(raw.id), t, label: str(raw.label), colour: str(raw.colour, DEFAULT_MARKER_COLOUR) || DEFAULT_MARKER_COLOUR, chapter: bool(raw.chapter) };
}

/**
 * Unique ids, as the model fills them when it reads a timeline (`_fill_ids`): an empty or
 * repeated id becomes `<prefix>-<n>` with the item's position (`pop-001`, `cue-0001`,
 * `mark-001`), skipping ids already in use.
 */
export function fillIds<T extends { id: string }>(items: T[], prefix: string, width: number): T[] {
  const used = new Set(items.map((item) => item.id).filter(Boolean));
  const seen = new Set<string>();
  return items.map((item, index) => {
    if (item.id && !seen.has(item.id)) {
      seen.add(item.id);
      return item;
    }
    let number = index + 1;
    let candidate = `${prefix}-${String(number).padStart(width, "0")}`;
    while (used.has(candidate) || seen.has(candidate)) {
      number += 1;
      candidate = `${prefix}-${String(number).padStart(width, "0")}`;
    }
    used.add(candidate);
    seen.add(candidate);
    return { ...item, id: candidate };
  });
}

export function parseTimeline(raw: unknown): Timeline | null {
  if (!isRecord(raw) || !Array.isArray(raw.scenes)) return null;
  const format: ProjectFormat = raw.format === "shorts" ? "shorts" : "long";
  const voice = isRecord(raw.voice) ? raw.voice : {};
  const proxy = isRecord(raw.proxy) ? raw.proxy : {};
  const render = isRecord(raw.render) ? raw.render : {};
  const scenes = raw.scenes.map((scene, index) => parseTimelineScene(scene, index));
  const parsedCaptions = parseCaptions(raw.captions);
  const captions = { ...parsedCaptions, cues: fillIds(parsedCaptions.cues, "cue", 4) };
  const lastEnd = scenes.length > 0 ? scenes[scenes.length - 1]!.end_s : 0;
  return {
    ...raw,
    version: num(raw.version, 1),
    project_id: str(raw.project_id),
    format,
    aspect: raw.aspect === "9:16" ? "9:16" : "16:9",
    fps: num(raw.fps, 30),
    width: num(raw.width),
    height: num(raw.height),
    duration_s: num(raw.duration_s, lastEnd),
    voice: { ...voice, path: str(voice.path), start_s: num(voice.start_s), segments: parseSegments(voice.segments) },
    music: parseMusic(raw.music),
    scenes,
    popups: fillIds(Array.isArray(raw.popups) ? raw.popups.map(parsePopup).filter((popup): popup is TimelinePopup => popup !== null) : [], "pop", 3),
    captions,
    intro: { path: nullableStr(isRecord(raw.intro) ? raw.intro.path : null) },
    outro: { path: nullableStr(isRecord(raw.outro) ? raw.outro.path : null) },
    presets: strList(raw.presets),
    proxy: { width: num(proxy.width, format === "shorts" ? 480 : 854), height: num(proxy.height, format === "shorts" ? 854 : 480) },
    revision: Math.max(1, Math.trunc(num(raw.revision, 1))),
    markers: fillIds(Array.isArray(raw.markers) ? raw.markers.map((marker) => parseMarker(marker)).filter((marker): marker is TimelineMarker => marker !== null) : [], "mark", 3),
    render: { ...render, preview_gain_db: nullableNum(render.preview_gain_db) },
  };
}

const DONE_WORDS = new Set(["done", "ok", "ready", "rendered", "complete", "completed", "finished", "success"]);
const RUNNING_WORDS = new Set(["rendering", "running", "in_progress", "progress", "started"]);
const QUEUED_WORDS = new Set(["queued", "pending", "requested", "waiting", "planned"]);
const FAILED_WORDS = new Set(["failed", "error", "cancelled", "canceled"]);

function renderStatus(value: unknown, hasFile: boolean): RenderStatus {
  if (typeof value === "boolean") return value ? "done" : "not_rendered";
  if (typeof value === "string") {
    const word = value.trim().toLowerCase();
    if (DONE_WORDS.has(word)) return "done";
    if (RUNNING_WORDS.has(word)) return "rendering";
    if (QUEUED_WORDS.has(word)) return "queued";
    if (FAILED_WORDS.has(word)) return "failed";
  }
  return hasFile ? "done" : "not_rendered";
}

function parseRender(preset: string, raw: unknown): RenderOutput {
  const base: RenderOutput = {
    preset,
    status: "not_rendered",
    path: null,
    play_url: null,
    progress: null,
    error: null,
    duration_s: null,
    size_bytes: null,
    width: null,
    height: null,
    rendered_at: null,
    scene_clips_total: null,
    scene_clips_reused: null,
    reuse_note: null,
  };
  if (typeof raw === "string") {
    // either a status word or a file path
    const looksLikePath = /[\\/.]/.test(raw) && !DONE_WORDS.has(raw.toLowerCase());
    return looksLikePath ? { ...base, status: "done", path: raw } : { ...base, status: renderStatus(raw, false) };
  }
  if (typeof raw === "boolean") return { ...base, status: raw ? "done" : "not_rendered" };
  if (!isRecord(raw)) return base;
  const path = nullableStr(raw.path) ?? nullableStr(raw.file) ?? nullableStr(raw.output);
  const playUrl = nullableStr(raw.play_url) ?? nullableStr(raw.url);
  const status = renderStatus(raw.status ?? raw.state ?? raw.done ?? raw.rendered, Boolean(path || playUrl));
  const clipsTotal = nullableNum(raw.scene_clips_total);
  const clipsReused = nullableNum(raw.scene_clips_reused);
  const counted = clipsTotal !== null && clipsReused !== null && clipsTotal > 0;
  return {
    preset,
    status,
    path,
    play_url: playUrl,
    progress: nullableNum(raw.progress ?? raw.pct ?? raw.percent),
    error: nullableStr(raw.error) ?? nullableStr(raw.detail),
    duration_s: nullableNum(raw.duration_s ?? raw.duration),
    size_bytes: nullableNum(raw.size_bytes ?? raw.bytes ?? raw.size),
    width: nullableNum(raw.width),
    height: nullableNum(raw.height),
    rendered_at: nullableStr(raw.rendered_at),
    scene_clips_total: counted ? clipsTotal : null,
    scene_clips_reused: counted ? clipsReused : null,
    reuse_note: nullableStr(raw.reuse_note) ?? (counted ? reuseNote(clipsReused, clipsTotal) : null),
  };
}

/** "Reused 2 of 3 scene clips." (the backend's wording, for a payload that sends only the numbers). */
export function reuseNote(reused: number, total: number): string {
  return `Reused ${reused} of ${total} scene clip${total === 1 ? "" : "s"}.`;
}

/** Accepts `[{preset, status, ...}]`, `{"1080p": {...}}`, `{"1080p": "done"}` or `{"1080p": "07_edit/final_1080p.mp4"}`. */
export function parseRenders(raw: unknown): RenderOutput[] {
  const renders: RenderOutput[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (isRecord(item)) {
        const preset = str(item.preset) || str(item.name) || str(item.id);
        if (preset) renders.push(parseRender(preset, item));
      } else if (typeof item === "string" && item) {
        renders.push(parseRender(item, "done"));
      }
    }
  } else if (isRecord(raw)) {
    for (const [preset, value] of Object.entries(raw)) renders.push(parseRender(preset, value));
  }
  return renders;
}

function parseMusicTrack(raw: unknown): MusicTrack | null {
  if (typeof raw === "string") return raw ? { path: raw, name: fileName(raw), license_ok: false, duration_s: null } : null;
  if (!isRecord(raw)) return null;
  const path = nullableStr(raw.path) ?? nullableStr(raw.file);
  if (!path) return null;
  const license =
    typeof raw.license_ok === "boolean" ? raw.license_ok : typeof raw.licence_ok === "boolean" ? raw.licence_ok : typeof raw.licensed === "boolean" ? raw.licensed : bool(raw.license);
  return {
    path,
    name: str(raw.name) || str(raw.title) || fileName(path),
    license_ok: license,
    duration_s: nullableNum(raw.duration_s),
  };
}

export function parseMusicTracks(raw: unknown): MusicTrack[] {
  const list = Array.isArray(raw) ? raw : isRecord(raw) && Array.isArray(raw.tracks) ? raw.tracks : [];
  return list.map(parseMusicTrack).filter((track): track is MusicTrack => track !== null);
}

/** The last path segment without its extension: "C:\\music\\calm_piano.mp3" -> "calm_piano". */
export function fileName(path: string): string {
  const last = path.split(/[\\/]+/).filter(Boolean).at(-1) ?? path;
  return last.replace(/\.[a-z0-9]{2,5}$/i, "");
}

function uniqueInOrder(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

function parseSceneView(raw: unknown, position: number, popupByScene: Map<number, string>): EditSceneView {
  const base: EditSceneView = {
    index: position,
    start_s: 0,
    end_s: 0,
    duration_s: 0,
    image_path: null,
    image_url: null,
    transition: null,
    transition_s: null,
    motion: null,
    popup_text: null,
    locked: false,
  };
  if (!isRecord(raw)) return base;
  const index = num(raw.index, position);
  const image = raw.image;
  const imagePath =
    typeof image === "string" ? nullableStr(image) : isRecord(image) ? nullableStr(image.path) : (nullableStr(raw.image_path) ?? nullableStr(raw.thumbnail));
  const imageUrl =
    nullableStr(raw.image_url) ?? nullableStr(raw.thumbnail_url) ?? (isRecord(image) ? nullableStr(image.url) : null);
  const transition = isRecord(raw.transition_out) ? nullableStr(raw.transition_out.type) : nullableStr(raw.transition);
  const transitionS = isRecord(raw.transition_out) ? nullableNum(raw.transition_out.duration_s) : nullableNum(raw.transition_s);
  const motion = isRecord(raw.motion) ? nullableStr(raw.motion.preset) : nullableStr(raw.motion);
  const start = num(raw.start_s);
  const end = num(raw.end_s);
  return {
    index,
    start_s: start,
    end_s: end,
    duration_s: num(raw.duration_s, Math.max(0, Math.round((end - start) * 1000) / 1000)),
    image_path: imagePath,
    image_url: imageUrl,
    transition,
    transition_s: transitionS,
    motion,
    popup_text: nullableStr(raw.popup_text) ?? (isRecord(raw.popup) ? nullableStr(raw.popup.text) : null) ?? popupByScene.get(index) ?? null,
    locked: bool(raw.locked),
  };
}

function summaryFrom(raw: Record<string, unknown> | null, timeline: Timeline | null, scenes: EditSceneView[]): TimelineSummary {
  const lastEnd = scenes.length > 0 ? Math.max(...scenes.map((scene) => scene.end_s)) : 0;
  const transitions = uniqueInOrder(timeline ? timeline.scenes.map((scene) => scene.transition_out.type) : scenes.map((scene) => scene.transition ?? ""));
  const base: TimelineSummary = {
    scene_count: timeline?.scenes.length ?? scenes.length,
    duration_s: timeline?.duration_s ?? lastEnd,
    transitions,
    popup_count: timeline?.popups.length ?? scenes.filter((scene) => scene.popup_text).length,
    caption_cues: timeline?.captions.cues.length ?? 0,
    fps: timeline?.fps ?? 30,
    width: timeline?.width ?? 0,
    height: timeline?.height ?? 0,
    aspect: timeline?.aspect ?? null,
  };
  if (!raw) return base;
  const rawTransitions = Array.isArray(raw.transitions) ? uniqueInOrder(strList(raw.transitions)) : transitions;
  return {
    scene_count: num(raw.scene_count ?? raw.scenes, base.scene_count),
    duration_s: num(raw.duration_s ?? raw.duration, base.duration_s),
    transitions: rawTransitions.length > 0 ? rawTransitions : base.transitions,
    popup_count: num(raw.popup_count ?? raw.popups, base.popup_count),
    caption_cues: num(raw.caption_cues ?? raw.captions, base.caption_cues),
    fps: num(raw.fps, base.fps),
    width: num(raw.width, base.width),
    height: num(raw.height, base.height),
    aspect: raw.aspect === "9:16" || raw.aspect === "16:9" ? raw.aspect : base.aspect,
  };
}

/**
 * Turn whatever `GET /api/projects/{id}/stage/edit` returns into a review payload.
 * Returns null when the stage has not written anything yet (the engine answers `{}`).
 */
export function parseEditPayload(raw: unknown): EditReviewPayload | null {
  if (!isRecord(raw) || Object.keys(raw).length === 0) return null;

  const timeline = parseTimeline(raw.timeline);
  const proxy = raw.proxy;
  const proxyPath =
    (typeof proxy === "string" ? nullableStr(proxy) : isRecord(proxy) ? nullableStr(proxy.path) : null) ?? nullableStr(raw.proxy_path);
  const proxyUrl =
    (isRecord(proxy) ? (nullableStr(proxy.play_url) ?? nullableStr(proxy.url)) : null) ?? nullableStr(raw.proxy_url) ?? nullableStr(raw.play_url);

  const popupByScene = new Map<number, string>();
  for (const popup of timeline?.popups ?? []) if (popup.text && !popupByScene.has(popup.scene)) popupByScene.set(popup.scene, popup.text);
  const rawScenes = Array.isArray(raw.scenes) ? raw.scenes : Array.isArray(raw.timeline_scenes) ? raw.timeline_scenes : null;
  const scenes = rawScenes
    ? rawScenes.map((scene, index) => parseSceneView(scene, index, popupByScene))
    : (timeline?.scenes.map((scene) => parseSceneView(scene, scene.index, popupByScene)) ?? []);

  const summaryRaw = isRecord(raw.summary) ? raw.summary : isRecord(raw.timeline_summary) ? raw.timeline_summary : null;
  const summary = summaryFrom(summaryRaw, timeline, scenes);

  const renders = parseRenders(raw.renders ?? raw.render_status ?? raw.outputs ?? raw.finals);
  // The requested presets: what the backend says, else the timeline's list, else whatever
  // has a render under way or done (a "not_rendered" row is only an offer, not a request).
  const requested = strList(raw.presets);
  const presets = uniqueInOrder(
    requested.length > 0
      ? requested
      : timeline && timeline.presets.length > 0
        ? timeline.presets
        : renders.filter((render) => render.status !== "not_rendered" && render.status !== "failed").map((render) => render.preset),
  );
  const available = uniqueInOrder(strList(raw.available_presets ?? raw.presets_available ?? raw.preset_options));
  const render = isRecord(raw.render) ? raw.render : {};
  const enable4k = typeof raw.enable_4k === "boolean" ? raw.enable_4k : typeof render.enable_4k === "boolean" ? render.enable_4k : null;

  const music = isRecord(raw.music) ? raw.music : {};
  const musicTracks = parseMusicTracks(raw.music_tracks ?? raw.tracks ?? music.tracks);
  const musicPath = nullableStr(raw.music_path) ?? nullableStr(music.path) ?? timeline?.music.path ?? null;
  const musicLicense =
    typeof raw.music_license_ok === "boolean"
      ? raw.music_license_ok
      : typeof music.license_ok === "boolean"
        ? music.license_ok
        : (timeline?.music.license_ok ?? musicTracks.find((track) => track.path === musicPath)?.license_ok ?? false);

  const captions = isRecord(raw.captions) ? raw.captions : {};
  const captionsEnabled =
    typeof raw.captions_enabled === "boolean" ? raw.captions_enabled : typeof captions.enabled === "boolean" ? captions.enabled : (timeline?.captions.enabled ?? true);
  const popups = isRecord(raw.popups) ? raw.popups : {};
  const popupsEnabled = typeof raw.popups_enabled === "boolean" ? raw.popups_enabled : typeof popups.enabled === "boolean" ? popups.enabled : true;

  const gates = parseGateResults(raw.gate_results);
  return {
    proxy_path: proxyPath,
    proxy_url: proxyUrl,
    timeline,
    summary,
    scenes,
    renders,
    presets,
    available_presets: available.length > 0 ? available : [...RENDER_PRESETS],
    enable_4k: enable4k,
    music_tracks: musicTracks,
    music_path: musicPath,
    music_license_ok: musicLicense,
    captions_enabled: captionsEnabled,
    popups_enabled: popupsEnabled,
    warnings: strList(raw.warnings),
    gate_results: gates,
    render_log_path: nullableStr(raw.render_log) ?? nullableStr(raw.render_log_path),
    timing_source: nullableStr(raw.timing_source),
    render_notes: strList(raw.render_notes).filter((note) => note.trim().length > 0),
    stem_url: nullableStr(raw.stem_url) ?? null,
    envelope_url: nullableStr(raw.envelope_url) ?? null,
    preview_gain_db: nullableNum(raw.preview_gain_db) ?? timeline?.render.preview_gain_db ?? null,
    quality: str(raw.quality, "standard"),
    upload_preset: str(raw.upload_preset, "none"),
    h265_copy: bool(raw.h265_copy),
    music_licence_note: str(raw.music_licence_note),
    music_folder: nullableStr(raw.music_folder),
    checks: parseGateResults(raw.checks),
    rendering: bool(raw.rendering),
    render_message: str(raw.render_message),
    render_pct: nullableNum(raw.render_pct),
    last_render_error: nullableStr(raw.last_render_error),
  };
}
