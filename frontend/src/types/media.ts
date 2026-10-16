/**
 * The media library of an edit (docs/CONTRACT-v0.2.md section 2). Mirrors
 * backend/sigma_editor/models/media.py (`MediaItem`, `MediaLibrary`, `BlankEditInput`).
 */
import type { ProjectFormat } from "./project";

export type MediaKind = "picture" | "clip" | "audio" | "captions";

export const MEDIA_KIND_LABELS: Record<MediaKind, string> = {
  picture: "Picture",
  clip: "Clip",
  audio: "Sound",
  captions: "Captions",
};

export interface MediaItem {
  /** relative to the edit's folder, e.g. 01_media/beach.jpg */
  path: string;
  name: string;
  kind: MediaKind;
  size_bytes: number;
  /** clips and sound */
  duration_s: number | null;
  /** pictures and clips; 0 when unknown */
  width: number;
  height: number;
  /** a small picture of it (pictures and clips); null for sound */
  thumb_url: string | null;
  /** the file itself through the files route */
  url: string;
  /** where it is used, in plain words ("scene 3", "Layer 1") */
  in_use: string[];
  added_at: string;
  /** made by the AI voice */
  ai_voice: boolean;
  /** an AI voice: the text it says */
  text: string;
  /** an AI voice: the voice's name */
  voice: string;
}

export interface MediaLibrary {
  folder: string;
  items: MediaItem[];
}

/** `POST /api/edits/blank` */
export interface BlankEditInput {
  name: string;
  format: ProjectFormat;
  language: string;
}

/** The MIME type of what the library's drag carries (a library path and its kind). */
export const MEDIA_DRAG_TYPE = "application/x-sge-media";

export interface MediaDrag {
  path: string;
  kind: MediaKind;
  name: string;
  duration_s: number | null;
}

export function readMediaDrag(transfer: DataTransfer): MediaDrag | null {
  try {
    const raw = transfer.getData(MEDIA_DRAG_TYPE);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<MediaDrag>;
    if (typeof parsed.path !== "string" || typeof parsed.kind !== "string") return null;
    return { path: parsed.path, kind: parsed.kind as MediaKind, name: typeof parsed.name === "string" ? parsed.name : parsed.path, duration_s: typeof parsed.duration_s === "number" ? parsed.duration_s : null };
  } catch {
    return null;
  }
}

/** "beach.jpg" from "01_media/beach.jpg" */
export function mediaName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}
