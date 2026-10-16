/**
 * The AI voice (docs/CONTRACT-v0.2.md section 5). Mirrors backend/sigma_editor/models/voice.py.
 */
import type { MediaItem } from "./media";

export const VOICE_PROVIDER_LABEL = "ElevenLabs";
export const MAX_VOICE_TEXT_CHARS = 5000;
export const MIN_PROVIDER_SPEED = 0.7;
export const MAX_PROVIDER_SPEED = 1.2;

export interface VoiceStatus {
  provider: string;
  provider_label: string;
  key_set: boolean;
  /** "...a1b2" */
  key_hint: string;
}

export interface VoiceInfo {
  id: string;
  name: string;
  description: string;
  preview_url: string | null;
  labels: Record<string, string>;
}

export type VoiceUse = "sound" | "narration";

export interface VoiceRequest {
  text: string;
  voice_id: string;
  /** the voice's name for the library's label (filled from the voices list when empty) */
  voice_name?: string;
  /** the provider's own speaking speed, 0.7 to 1.2 */
  speed: number;
  name: string;
  use: VoiceUse;
  /** sound: where the clip starts on the timeline */
  at_s: number;
}

export interface VoiceResult {
  item: MediaItem;
  duration_s: number;
  /** the fresh editor payload when the voice became the narration */
  payload: unknown | null;
  warnings: string[];
}
