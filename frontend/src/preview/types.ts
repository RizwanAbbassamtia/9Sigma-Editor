/**
 * Types of the preview engine (docs/M5-CONTRACT.md section 4).
 *
 * Inputs are structural and tolerant: a timeline from before M5 (no `revision`, no
 * `voice.segments`, no `motion.ease` ...) still plays, and the engine never needs the app's
 * own `types/timeline.ts` (it is framework-free). `PreviewBundle` is the body of
 * `GET /api/projects/{id}/timeline` (contract section 2).
 */
import type { AdjustDesc, FrameDesc } from "./frame";
import type { CameraTerms, MotionPath, Rect } from "./motion";
import type { RGB } from "./transitions/common";
import type { ResolvedTransition, TransitionCatalogue } from "./transitions/registry";

export type { Rect } from "./motion";

// ---- the bundle (inputs) ------------------------------------------------------------------

export interface PreviewMotion {
  preset: string;
  start_rect: readonly number[];
  end_rect: readonly number[];
  style?: string | null;
  strength?: number | null;
  /** overrides the preset's curve: ease_in_out_cubic, ease_out_quart, ease_in_out_sine, snap, linear */
  ease?: string | null;
}

export interface PreviewTransition {
  type: string;
  duration_s: number;
}

export interface PreviewScene {
  index: number;
  image: string;
  start_s: number;
  end_s: number;
  motion: PreviewMotion;
  transition_out?: PreviewTransition | null;
  locked?: boolean;
  sentence_ids?: readonly string[];
  edited?: readonly string[];
  /** v0.2: the clip the scene plays (the image is its poster frame) */
  clip?: { path: string; in_s?: number; speed?: number; keep_sound?: boolean; gain_db?: number; duration_s?: number | null } | null;
  /** v0.2: where a clip sits in the frame (null = fills it) */
  frame?: unknown;
  /** v0.2: colour */
  adjust?: unknown;
}

/** v0.2: a picture or clip over the scenes. */
export interface PreviewLayerClip {
  id?: string;
  source: string;
  kind?: string;
  name?: string;
  start_s: number;
  end_s: number;
  in_s?: number;
  speed?: number;
  keep_sound?: boolean;
  gain_db?: number;
  duration_s?: number | null;
  frame?: unknown;
  adjust?: unknown;
}

export interface PreviewLayer {
  id?: string;
  name?: string;
  visible?: boolean;
  locked?: boolean;
  clips?: readonly PreviewLayerClip[];
}

/** v0.2: a sound clip on a sound track. */
export interface PreviewAudioClip {
  id?: string;
  source: string;
  start_s: number;
  end_s: number;
  in_s?: number;
  speed?: number;
  gain_db?: number;
  fade_in_s?: number;
  fade_out_s?: number;
}

export interface PreviewAudioTrack {
  id?: string;
  muted?: boolean;
  gain_db?: number;
  clips?: readonly PreviewAudioClip[];
}

export interface PreviewPopup {
  id?: string;
  scene: number;
  text: string;
  style?: string;
  position?: string;
  start_s: number;
  end_s: number;
  anim?: string;
  png?: string;
  anchor?: { sentence_id: string; offset_s: number } | null;
  edited?: boolean;
  locked?: boolean;
}

export interface PreviewCaptionStyle {
  font?: string;
  size?: number;
  primary?: string;
  outline?: number;
  position?: string;
  max_lines?: number;
  max_chars_per_line?: number;
  rtl?: boolean;
  bold?: boolean;
  outline_colour?: string;
  /** M5: bold-outline | active-word | single-word-pop | boxed */
  preset?: string | null;
  active_colour?: string | null;
  layout?: "phrase" | "single_word" | string | null;
}

export interface PreviewCue {
  id?: string;
  start_s: number;
  end_s: number;
  text: string;
  edited?: boolean;
}

export interface PreviewVoiceSegment {
  sentence_id: string;
  src_start_s: number;
  src_end_s: number;
  at_s: number;
}

export interface PreviewTimeline {
  version?: number;
  revision?: number;
  project_id: string;
  format?: string;
  aspect: string;
  fps: number;
  width: number;
  height: number;
  duration_s: number;
  voice?: { path?: string; start_s?: number; segments?: readonly PreviewVoiceSegment[] | null } | null;
  music?: {
    path?: string | null;
    start_s?: number;
    gain_db?: number;
    duck_db?: number;
    duck_attack_s?: number;
    duck_release_s?: number;
    fade_in_s?: number;
    fade_out_s?: number;
    stem?: string | null;
    envelope?: string | null;
  } | null;
  scenes: readonly PreviewScene[];
  popups?: readonly PreviewPopup[];
  captions?: { enabled?: boolean; style?: PreviewCaptionStyle; cues?: readonly PreviewCue[] } | null;
  intro?: { path?: string | null } | null;
  outro?: { path?: string | null } | null;
  markers?: readonly { id: string; t: number; label?: string; colour?: string; chapter?: boolean }[];
  render?: { preview_gain_db?: number | null } | null;
  /** v0.2 */
  layers?: readonly PreviewLayer[];
  audio_tracks?: readonly PreviewAudioTrack[];
}

/** v0.2: a file a layer or sound clip (or a clip scene) plays, with what the preview needs. */
export interface PreviewSource {
  path: string;
  kind?: string;
  width?: number;
  height?: number;
  duration_s?: number | null;
  thumb_url?: string | null;
  url?: string;
}

export interface PreviewWord {
  w: string;
  start_s: number;
  end_s: number;
}

export interface PreviewSentence {
  id: string;
  text: string;
  start_s: number;
  end_s: number;
  words?: readonly PreviewWord[];
}

export interface PreviewPicture {
  scene: number;
  path: string;
  width: number;
  height: number;
  thumb_url?: string | null;
  /** additive: a full-size URL when the backend sends one (else the project files route) */
  url?: string | null;
  /** additive: `06_images/scene_NN.depth.png` as a URL, for 2.5D parallax */
  depth_url?: string | null;
}

export interface PreviewMedia {
  voice_url?: string | null;
  stem_url?: string | null;
  envelope_url?: string | null;
  /** the timeline's own music track (`GET .../media/music?v=`), played while the stem is not current */
  music_url?: string | null;
  fonts?: readonly { family: string; url: string }[];
  /** additive: true when the project runs on mock providers (placeholder pictures, silent voice) */
  mock?: boolean;
}

export interface PreviewBundle {
  timeline: PreviewTimeline;
  revision?: number;
  sentences?: readonly PreviewSentence[];
  sections?: readonly { id: string; name: string; start_scene: number; end_scene: number }[];
  pictures?: readonly PreviewPicture[];
  media?: PreviewMedia;
  preview_gain_db?: number | null;
  /** additive: worked out from the storyboard before timeline.json exists (an animatic) */
  virtual?: boolean;
  /** additive: no voice yet, the times are the storyboard's estimates */
  timing_estimated?: boolean;
  /** additive: per caption cue id, its words with the times render/captions.py uses */
  cue_words?: Readonly<Record<string, readonly PreviewWord[]>>;
  /** additive: the gain loudnorm gives the mix, every `step_s` (measured for this sound) */
  preview_gain_curve?: { step_s: number; gain_db: readonly number[] } | null;
  /** v0.2: the files of the layer and sound clips and the clip scenes */
  sources?: readonly PreviewSource[];
}

// ---- settings (config/render.yaml defaults) -------------------------------------------------

export interface RenderSettings {
  /** transitions.min_frames */
  minFrames: number;
  /** transitions.max_share_of_shorter_scene */
  maxShare: number;
  /** popups (sizes at 1080p) */
  popupFadeS: number;
  popupSlideS: number;
  popupSlidePx: number;
  popupMarginPx: number;
  /** captions */
  captionMarginVPx: number;
  /** motion.handheld_amplitude */
  handheldAmplitude: number;
  /** parallax.max_overscan */
  maxOverscan: number;
  /** tail_s (virtual timelines) */
  tailS: number;
}

export const DEFAULT_SETTINGS: RenderSettings = {
  minFrames: 2,
  maxShare: 0.25,
  popupFadeS: 0.3,
  popupSlideS: 0.35,
  popupSlidePx: 120,
  popupMarginPx: 60,
  captionMarginVPx: 60,
  handheldAmplitude: 0.004,
  maxOverscan: 0.03,
  tailS: 0.8,
};

// ---- the render description ------------------------------------------------------------------

export interface SceneDesc {
  /** 1-based scene number (the cut seed, the file name's NN) */
  number: number;
  /** index in the timeline */
  index: number;
  image: string;
  /** picture size in pixels when known (the cover region needs it) */
  imageSize: [number, number] | null;
  /** first frame of the scene on the video clock (relative to the first scene) */
  startFrame: number;
  /** frames from this scene's start to the next kept scene's start (the last: to its end) */
  length: number;
  /** frames of the scene clip before the scene starts (the incoming overlap, h_in) */
  leadIn: number;
  /** frames of the clip after the scene ends (the outgoing overlap) */
  tailOut: number;
  /** all frames of the scene clip: leadIn + length + tailOut */
  clipFrames: number;
  /** first output frame of the body (not under a cut) */
  bodyStart: number;
  body: number;
  /** the camera path in picture pixels, bounded by the cover region when the size is known */
  path: MotionPath;
  style: "ken_burns" | "parallax";
  strength: number;
  /** v0.2: the clip the scene plays, or null for a picture */
  clip: SceneClipDesc | null;
  /** v0.2: a clip's place in the frame (null = fills it) */
  frame: FrameDesc | null;
  /** v0.2: colour (null = as shot) */
  adjust: AdjustDesc | null;
}

/** v0.2: a scene's clip (docs/CONTRACT-v0.2.md section 7). */
export interface SceneClipDesc {
  url: string;
  inS: number;
  speed: number;
  keepSound: boolean;
  gainDb: number;
}

/** v0.2: a layer clip, drawn over the scenes between `startS` and `endS`. */
export interface LayerClipDesc {
  key: string;
  /** position of its layer, bottom layer first */
  layer: number;
  source: string;
  url: string;
  kind: "picture" | "clip";
  startS: number;
  endS: number;
  inS: number;
  speed: number;
  keepSound: boolean;
  gainDb: number;
  frame: FrameDesc;
  adjust: AdjustDesc | null;
  /** the source's size when the bundle knows it */
  size: [number, number] | null;
}

/** v0.2: a sound clip of a sound track. */
export interface SoundClipDesc {
  key: string;
  url: string;
  startS: number;
  endS: number;
  inS: number;
  speed: number;
  /** the clip's gain plus its track's */
  gainDb: number;
  fadeInS: number;
  fadeOutS: number;
}

export interface CutDesc {
  /** 1-based number of the scene it leaves (also the seed) */
  number: number;
  /** position in `scenes` of the outgoing scene */
  from: number;
  /** position in `scenes` of the incoming scene; null for the fade at the very end */
  to: number | null;
  /** first output frame of the cut */
  startFrame: number;
  frames: number;
  durationS: number;
  transition: ResolvedTransition | null;
  fadeColour: RGB | null;
  seed: number;
}

export type PopupAnim = "slide_left" | "fade" | "pop";

export interface PopupDesc {
  key: string;
  index: number;
  scene: number;
  text: string;
  style: string;
  position: string;
  anim: PopupAnim;
  startS: number;
  endS: number;
  png: string;
}

export interface CueDesc {
  key: string;
  index: number;
  startS: number;
  endS: number;
  /** lines as the renderer broke them (`\N`) */
  lines: string[];
  text: string;
}

export interface VoiceClip {
  /** sentence id, or "voice" for the whole file */
  id: string;
  url: string;
  /** seconds into the clip's own file where playback starts */
  offsetS: number;
  /** seconds to play */
  durationS: number;
  /** where it starts on the video clock */
  atS: number;
}

export interface AudioDesc {
  clips: VoiceClip[];
  stemUrl: string | null;
  envelopeUrl: string | null;
  /** the raw track when there is no stem yet (the envelope is applied live) */
  music: { url: string; startS: number; gainDb: number; fadeInS: number; fadeOutS: number } | null;
  /** master gain (dB) so the preview plays as loud as the final file */
  gainDb: number;
  /**
   * loudnorm's own gain (dB) every `stepS` seconds, when measured: the master gain follows it
   * instead of `gainDb` (one-pass loudnorm is louder or quieter than one gain at the start and
   * in short videos)
   */
  gainCurve: GainCurve | null;
}

export interface GainCurve {
  stepS: number;
  gainDb: readonly number[];
}

export interface RenderDescription {
  projectId: string;
  revision: number;
  fps: number;
  /** the timeline's frame size (the first requested preset) */
  width: number;
  height: number;
  aspect: string;
  /** frames before the first scene that the render skips (round(first start x fps)) */
  firstFrame: number;
  totalFrames: number;
  durationS: number;
  scenes: SceneDesc[];
  cuts: CutDesc[];
  popups: PopupDesc[];
  cues: CueDesc[];
  captionsEnabled: boolean;
  captionStyle: PreviewCaptionStyle;
  audio: AudioDesc;
  settings: RenderSettings;
  catalogue: TransitionCatalogue | null;
  mock: boolean;
  /** an animatic over the storyboard's estimated times */
  estimatedTiming: boolean;
  virtual: boolean;
  /** per cue key, its words with times (active-word and single-word looks) */
  cueWords: Map<string, PreviewWord[]>;
  /** v0.2: the layer clips, bottom layer first, each layer's clips in time order */
  layers: LayerClipDesc[];
  /** v0.2: the sound clips of the tracks that are not muted */
  sounds: SoundClipDesc[];
}

export interface SceneRef {
  scene: SceneDesc;
  /** frame index inside the scene clip */
  clipFrame: number;
  rect: Rect;
  camera: CameraTerms | null;
}

export type FrameState =
  | { kind: "scene"; n: number; a: SceneRef }
  | { kind: "cut"; n: number; cut: CutDesc; a: SceneRef; b: SceneRef; k: number; tRaw: number; t: number }
  | { kind: "fade"; n: number; cut: CutDesc; a: SceneRef; k: number; weight: number; colour: RGB }
  | { kind: "empty"; n: number };

// ---- engine API (docs/M5-CONTRACT.md section 4; the wrapper's view is types/preview.ts) ----------

export type Quality = "auto" | "full" | "half";
export type ActiveQuality = "full" | "half";

/** The part of the video that plays over and over while loop is on. */
export interface LoopRange {
  in_s: number;
  out_s: number;
}

/** Body of `POST /api/projects/{id}/popups/preview`. */
export interface PopupPreviewRequest {
  text: string;
  style: string;
  position: string;
  anim: PopupAnim;
  aspect: "16:9" | "9:16";
  /** frame height in pixels the PNG is drawn for (90 to 2160) */
  height: number;
}

/** Answer of `POST /api/projects/{id}/popups/preview`. */
export interface PopupPreviewResult {
  png_url: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PreviewOptions {
  projectId?: string;
  quality?: Quality;
  loop?: LoopRange | null;
  /** play 60 ms voice grains while scrubbing */
  scrubAudio?: boolean;
  /** the bundle comes from the storyboard (an animatic) */
  animatic?: boolean;
  /** placeholder pictures or a silent voice (SGE_*_PROVIDER=mock) */
  mockMedia?: boolean;
  /** `POST .../popups/preview`; the engine debounces it 150 ms per popup */
  popupPreview?: (key: string, request: PopupPreviewRequest) => Promise<PopupPreviewResult | null>;
  /** a picture of the project at `height` pixels (cached JPEG); default: the full file */
  pictureUrl?: (path: string, height: number) => string;
  /** the audio of one sentence (`GET .../media/voice/segments/{sid}.wav`) */
  voiceSegmentUrl?: (sentenceId: string) => string;
  /** `config/render.yaml` values when they differ from the defaults */
  settings?: Partial<RenderSettings>;
  /** /transitions/catalogue.json when the app has it (easing and params of each transition) */
  catalogue?: TransitionCatalogue | null;
  /** "auto" (WebGL2, else Canvas 2D), or force one (tests, the parity harness) */
  renderer?: "auto" | "webgl2" | "canvas2d";
  /** largest drawing size for Full quality (default 1280 x 720) */
  maxSize?: [number, number];
  /** false: no sound at all (the parity harness) */
  audio?: boolean;
  /** false: the popups are not drawn (the review's "popups" switch) */
  popupsEnabled?: boolean;
  /** replaces `fetch` (tests) */
  fetch?: typeof fetch;
}

/** What the engine reports about itself (the monitor turns these into chips). */
export interface PreviewStatus {
  renderer: "webgl2" | "canvas2d";
  /** a lost WebGL context is being restored ("Restarting the preview...") */
  restarting: boolean;
  /** some transitions are drawn as a dissolve ("Simplified preview") */
  simplified: boolean;
  /** placeholder pictures or a silent voice ("Mock media") */
  mockMedia: boolean;
  activeQuality: ActiveQuality;
}

export interface PreviewStats {
  /** frames of video time that were not shown while playing */
  dropped: number;
  shown: number;
  /** milliseconds of the last frame's drawing on the main thread */
  lastFrameMs: number;
  /** the last seek in milliseconds (from the call to the frame on screen) */
  lastSeekMs: number;
  /** milliseconds from createPreview to the first frame on screen */
  firstFrameMs: number;
  renderer: "webgl2" | "canvas2d" | "none";
  quality: ActiveQuality;
  width: number;
  height: number;
  textures: number;
}

/** Event name -> handler (the same names and arguments as the wrapper's `PreviewEngineEvents`). */
export interface PreviewEvents {
  /** the time of the frame on screen, in seconds */
  time: (seconds: number) => void;
  play: () => void;
  pause: () => void;
  /** playback reached the end (or the out point with loop off) */
  ended: () => void;
  status: (status: Partial<PreviewStatus>) => void;
  /** a plain-English problem */
  error: (message: string) => void;
  /** extra: drawing statistics after each played frame (throttled) */
  stats: (stats: PreviewStats) => void;
  /** extra: a frame reached the screen */
  frame: (info: { frame: number; seconds: number; kind: FrameState["kind"] }) => void;
}

export type PreviewEventName = keyof PreviewEvents;

/** The handle `createPreview(canvas, bundle, options)` returns. */
export interface PreviewHandle {
  play(): Promise<void>;
  pause(): void;
  seek(seconds: number): void;
  setQuality(quality: Quality): void;
  setLoop(range: LoopRange | null): void;
  /** show the frame at `seconds` without moving the playhead; null goes back to the playhead */
  skim(seconds: number | null): void;
  destroy(): void;
  on<E extends PreviewEventName>(event: E, handler: PreviewEvents[E]): () => void;
  /** draw a new bundle without starting over (a transition or popup changed) */
  update(bundle: PreviewBundle): void;
  /** resolves when the engine can draw (WebGL2 or Canvas 2D chosen, first frame drawn) */
  readonly ready: Promise<void>;
  /** the playhead in seconds */
  currentTime(): number;
  duration(): number;
  isPlaying(): boolean;
  /** turn scrub audio on or off */
  setScrubAudio(on: boolean): void;
  /** draw the frame at `seconds` now and resolve when it is on screen (parity harness) */
  renderFrame(seconds: number): Promise<void>;
  stats(): PreviewStats;
  /** the render description in use (read-only; tests and the parity harness) */
  description(): RenderDescription;
}
