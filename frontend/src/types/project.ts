/**
 * An edit (one video) and its list row. Mirrors backend/sigma_editor/models/project.py and
 * models/edit_input.py (Pydantic v2, the source of truth); docs/CONTRACT-v0.1.md sections 2
 * and 3.
 */

export type ProjectFormat = "long" | "shorts";

export const PROJECT_FORMATS: readonly ProjectFormat[] = ["long", "shorts"];

export const FORMAT_LABELS: Record<ProjectFormat, string> = {
  long: "16:9 video",
  shorts: "9:16 Short",
};

export const FORMAT_SHORT_LABELS: Record<ProjectFormat, string> = {
  long: "16:9",
  shorts: "9:16",
};

/** Where an edit stands, for the list. */
export type ProjectStatus = "new" | "ready" | "rendering" | "rendered" | "exported" | "failed";

export const STATUS_LABELS: Record<ProjectStatus, string> = {
  new: "New",
  ready: "Ready to render",
  rendering: "Rendering",
  rendered: "Rendered",
  exported: "Exported",
  failed: "Render failed",
};

export interface Project {
  id: string;
  kind: "edit";
  name: string;
  format: ProjectFormat;
  language: string;
  folder: string;
  created_at: string;
  updated_at: string;
  status: ProjectStatus;
  status_note: string;
  duration_s: number;
  music_licence_note: string;
  schema_version: number;
}

export interface ProjectSummary {
  id: string;
  name: string;
  format: ProjectFormat;
  language: string;
  status: ProjectStatus;
  status_note: string;
  duration_s: number;
  created_at: string;
  updated_at: string;
}

/** The frame shape of a format. */
export function aspectOf(format: ProjectFormat): "16:9" | "9:16" {
  return format === "shorts" ? "9:16" : "16:9";
}

export function isProjectStatus(value: unknown): value is ProjectStatus {
  return typeof value === "string" && value in STATUS_LABELS;
}

// ---- New edit from files (models/edit_input.py) --------------------------------------------

export type ItemOrder = "as_given" | "by_name";

export interface EditItem {
  /** the uploaded file's name */
  file: string;
  /** an optional short line shown as a popup while this scene plays */
  caption: string;
  /** clips only: keep the clip's own sound */
  keep_sound: boolean;
}

export interface EditInput {
  name: string;
  format: ProjectFormat;
  language: string;
  order: ItemOrder;
  items: EditItem[];
  caption_text: string;
  music_licence_note: string;
  keep_clip_sound: boolean;
}

export const LANGUAGES: readonly string[] = [
  "English", "Spanish", "Portuguese", "French", "German", "Italian", "Dutch", "Polish",
  "Turkish", "Russian", "Ukrainian", "Arabic", "Hindi", "Urdu", "Indonesian", "Vietnamese",
  "Thai", "Japanese", "Korean", "Chinese",
];

export type FileKind = "picture" | "clip" | "audio" | "captions" | "other";

const PICTURE = new Set(["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"]);
const CLIP = new Set(["mp4", "mov", "m4v", "mkv", "webm", "avi"]);
const AUDIO = new Set(["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "wma", "aif", "aiff"]);
const CAPTIONS = new Set(["srt", "vtt"]);

/** What a file is for, from its extension (the same table as the backend). */
export function fileKind(name: string): FileKind {
  const dot = name.lastIndexOf(".");
  const suffix = dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
  if (PICTURE.has(suffix)) return "picture";
  if (CLIP.has(suffix)) return "clip";
  if (AUDIO.has(suffix)) return "audio";
  if (CAPTIONS.has(suffix)) return "captions";
  return "other";
}

/** Sort key that compares the numbers inside a name as numbers (scene_2 before scene_10). */
export function compareNatural(a: string, b: string): number {
  const split = (text: string) => text.toLowerCase().split(/(\d+)/).filter(Boolean);
  const left = split(a);
  const right = split(b);
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    const x = left[i];
    const y = right[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const nx = /^\d+$/.test(x) ? Number(x) : null;
    const ny = /^\d+$/.test(y) ? Number(y) : null;
    if (nx !== null && ny !== null) {
      if (nx !== ny) return nx - ny;
    } else if (x !== y) {
      return x < y ? -1 : 1;
    }
  }
  return 0;
}

// ---- checks (the renderer's gate results, shown under the edit) -------------------------------

export type GateSeverity = "block" | "warn";

export interface GateResult {
  id: string;
  title: string;
  severity: GateSeverity;
  passed: boolean;
  detail: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Gate results as the backend sends them (a loose list of objects), typed. */
export function parseGateResults(raw: unknown): GateResult[] {
  if (!Array.isArray(raw)) return [];
  const results: GateResult[] = [];
  for (const item of raw) {
    if (!isRecord(item)) continue;
    const id = typeof item.id === "string" ? item.id : "";
    if (!id) continue;
    results.push({
      id,
      title: typeof item.title === "string" ? item.title : id,
      severity: item.severity === "block" ? "block" : "warn",
      passed: Boolean(item.passed),
      detail: typeof item.detail === "string" && item.detail ? item.detail : null,
    });
  }
  return results;
}

export interface OpenFolderResponse {
  ok: boolean;
  folder: string;
}
