/**
 * Shapes for `GET` / `PUT /api/settings` (docs/CONTRACT-v0.1.md section 6). Mirrors
 * backend/sigma_editor/config.py (`RenderSettings`, `CaptionsSettings`) and
 * api/settings.py (`SettingsView`, `SettingsUpdate`).
 */

import { DEFAULT_MOTION_STYLE, DEFAULT_TRANSITION_PACK, MOTION_STYLES, TRANSITION_PACKS, type MotionStyle, type TransitionPack } from "./transitions";
import { isRenderPreset, type RenderPreset } from "./timeline";

export const X264_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"] as const;
export type X264Preset = (typeof X264_PRESETS)[number];

export const X264_PRESET_LABELS: Record<X264Preset, string> = {
  ultrafast: "Ultra fast (biggest files)",
  superfast: "Super fast",
  veryfast: "Very fast",
  faster: "Faster",
  fast: "Fast",
  medium: "Medium (a good balance)",
  slow: "Slow",
  slower: "Slower",
  veryslow: "Very slow (smallest files)",
};

/** The three quality choices (docs/CONTRACT-v0.1.md section 5). */
export const QUALITIES = ["standard", "high", "best"] as const;
export type Quality = (typeof QUALITIES)[number];

export const QUALITY_LABELS: Record<Quality, string> = {
  standard: "Standard",
  high: "High",
  best: "Best",
};

export const QUALITY_DESCRIPTIONS: Record<Quality, string> = {
  standard: "Good for most videos. Renders fastest.",
  high: "Sharper detail, about twice the render time.",
  best: "The most detail the encoder can keep. Slow; for the final file only.",
};

export function isQuality(value: unknown): value is Quality {
  return typeof value === "string" && (QUALITIES as readonly string[]).includes(value);
}

/** The upload presets: the bitrate caps YouTube recommends. */
export const UPLOAD_PRESETS = ["none", "youtube_1080p", "youtube_4k"] as const;
export type UploadPreset = (typeof UPLOAD_PRESETS)[number];

export const UPLOAD_PRESET_LABELS: Record<UploadPreset, string> = {
  none: "No upload preset",
  youtube_1080p: "YouTube 1080p",
  youtube_4k: "YouTube 4K",
};

export function isUploadPreset(value: unknown): value is UploadPreset {
  return typeof value === "string" && (UPLOAD_PRESETS as readonly string[]).includes(value);
}

export const CAPTION_STYLES = ["bold-white", "yellow", "clean"] as const;
export type CaptionStyleName = (typeof CAPTION_STYLES)[number];

export const CAPTION_STYLE_LABELS: Record<CaptionStyleName, string> = {
  "bold-white": "Bold white",
  yellow: "Yellow",
  clean: "Clean",
};

export interface RenderSettings {
  default_presets: RenderPreset[];
  quality: Quality;
  /** an encoder speed that overrides the quality's own; null = the quality decides */
  x264_preset: X264Preset | null;
  two_pass_best: boolean;
  h265_copy: boolean;
  enable_4k: boolean;
  transition_pack: TransitionPack;
  motion_style: MotionStyle;
}

export interface CaptionsSettings {
  enabled: boolean;
  style: string;
  preset: string;
}

export function defaultRenderSettings(): RenderSettings {
  return {
    default_presets: ["1080p"],
    quality: "standard",
    x264_preset: null,
    two_pass_best: false,
    h265_copy: false,
    enable_4k: false,
    transition_pack: DEFAULT_TRANSITION_PACK,
    motion_style: DEFAULT_MOTION_STYLE,
  };
}

export function defaultCaptionsSettings(): CaptionsSettings {
  return { enabled: true, style: "bold-white", preset: "bold-outline" };
}

/** `GET /api/settings` */
export interface Settings {
  projects_dir: string;
  exports_dir: string;
  default_projects_dir: string;
  default_exports_dir: string;
  app_data_dir: string;
  ffmpeg_path: string;
  ffprobe_path: string;
  render: Partial<RenderSettings> | null;
  captions: Partial<CaptionsSettings> | null;
  quality_labels: Record<string, string>;
}

/** `PUT /api/settings`: a missing field means "leave as is"; a folder of null or "" = the default. */
export interface SettingsUpdate {
  projects_dir?: string | null;
  exports_dir?: string | null;
  render?: RenderSettings;
  captions?: CaptionsSettings;
}

export type PathKey = "projects_dir" | "exports_dir";

export interface RenderSettingsResolved {
  render: RenderSettings;
  captions: CaptionsSettings;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The render and caption settings with every field filled in. */
export function resolveRenderSettings(settings: Settings | undefined): RenderSettingsResolved {
  const render = defaultRenderSettings();
  const captions = defaultCaptionsSettings();
  const raw = settings?.render;
  if (isRecord(raw)) {
    if (Array.isArray(raw.default_presets)) {
      const presets = raw.default_presets.filter(isRenderPreset);
      if (presets.length > 0) render.default_presets = presets;
    }
    if (isQuality(raw.quality)) render.quality = raw.quality;
    if (typeof raw.x264_preset === "string" && (X264_PRESETS as readonly string[]).includes(raw.x264_preset)) {
      render.x264_preset = raw.x264_preset as X264Preset;
    }
    if (typeof raw.two_pass_best === "boolean") render.two_pass_best = raw.two_pass_best;
    if (typeof raw.h265_copy === "boolean") render.h265_copy = raw.h265_copy;
    if (typeof raw.enable_4k === "boolean") render.enable_4k = raw.enable_4k;
    if (typeof raw.transition_pack === "string" && (TRANSITION_PACKS as readonly string[]).includes(raw.transition_pack)) {
      render.transition_pack = raw.transition_pack as TransitionPack;
    }
    if (typeof raw.motion_style === "string" && (MOTION_STYLES as readonly string[]).includes(raw.motion_style)) {
      render.motion_style = raw.motion_style as MotionStyle;
    }
  }
  const rawCaptions = settings?.captions;
  if (isRecord(rawCaptions)) {
    if (typeof rawCaptions.enabled === "boolean") captions.enabled = rawCaptions.enabled;
    if (typeof rawCaptions.style === "string" && rawCaptions.style) captions.style = rawCaptions.style;
    if (typeof rawCaptions.preset === "string" && rawCaptions.preset) captions.preset = rawCaptions.preset;
  }
  return { render, captions };
}
