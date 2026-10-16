import { createContext, useContext } from "react";

import type { TimeStore } from "../../components/preview/timeStore";
import type { LivePreviewHandle } from "../../components/preview/LivePreview";
import type { Project, ProjectFormat } from "../../types/project";
import type { BundlePicture, BundleSentence, BundleSource, TimelineBundle, WaveformPeaks, MusicEnvelope } from "../../types/preview";
import type { TransitionCatalogue, TransitionPack } from "../../types/storyboard";
import type { EditChoices, EditReviewPayload, Timeline } from "../../types/timeline";
import type { MediaLibrary } from "../../types/media";
import type { VoiceRequest, VoiceResult, VoiceStatus } from "../../types/voice";
import type { Selection } from "./document";
import type { NarrationInput } from "../../api/media";
import type { RenderStatus } from "../../api/projects";

export type SaveState = { kind: "saved"; at: string } | { kind: "saving" } | { kind: "retrying"; error: string } | { kind: "conflict" } | { kind: "clean" };

export interface EditorState {
  projectId: string;
  project: Project;
  payload: EditReviewPayload;
  bundle: TimelineBundle | null;
  /** the live document */
  timeline: Timeline;
  sentences: readonly BundleSentence[];
  /** by scene index */
  pictures: ReadonlyMap<number, BundlePicture>;
  /** v0.2: the files of layer and sound clips and clip scenes, by path */
  sources: ReadonlyMap<string, BundleSource>;
  /** v0.2: the media library (01_media) */
  media: MediaLibrary | null;
  /** v0.2: where the timing comes from: "scenes" means no narration (picture time ripples) */
  timingSource: string | null;
  /** v0.2: the AI voice's key state */
  voiceStatus: VoiceStatus | null;
  voicePeaks: WaveformPeaks | null;
  musicPeaks: WaveformPeaks | null;
  envelope: MusicEnvelope | null;
  catalogue: TransitionCatalogue | null;
  pack: TransitionPack;
  format: ProjectFormat;
  fps: number;
  duration: number;
  selection: Selection;
  /** a render is running: the document is read-only */
  locked: boolean;
  renderStatus: RenderStatus | undefined;
  saveState: SaveState;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;
  playing: boolean;
  /** the in-out range */
  range: { in_s: number | null; out_s: number | null };
  loop: boolean;
  snapping: boolean;
  linkPopups: boolean;
  followPlayhead: boolean;
  timeStore: TimeStore;
  /** v0.2: a server call that takes a while is running (import, narration, the AI voice) */
  busy: string | null;
}

export interface EditorActions {
  select(selection: Selection): void;
  /** One undo step: `label` names it ("move cut 7 to 8"). Refused while a render runs. */
  edit(label: string, next: (timeline: Timeline) => Timeline): boolean;
  undo(): void;
  redo(): void;
  seek(seconds: number, play?: boolean): void;
  playPause(): void;
  stepFrames(frames: number): void;
  playAround(seconds: number): void;
  markIn(): void;
  markOut(): void;
  clearRange(): void;
  toggleLoop(): void;
  toggleSnapping(): void;
  toggleLinkPopups(): void;
  toggleFollowPlayhead(): void;
  /** the preview's handle, for the parts that drive it directly */
  preview: React.RefObject<LivePreviewHandle | null>;
  /** server-side changes (the timeline is built again on the server) */
  replacePicture(index: number, file: File): Promise<void>;
  /** a new scene from an upload, or from a library file (its path) */
  addScene(source: File | string, after?: number, keepSound?: boolean): Promise<void>;
  reorderScenes(order: number[]): Promise<void>;
  removeScene(index: number): Promise<void>;
  shortenPause(sentenceIndex: number, shift_s: number): Promise<void>;
  applyChoices(choices: EditChoices): Promise<void>;
  rebuild(): Promise<void>;
  /** v0.2: the media library */
  importMedia(files: File[]): Promise<void>;
  removeMedia(path: string): Promise<void>;
  setNarration(input: NarrationInput): Promise<void>;
  /** v0.2: the AI voice; null when it failed (the reason was shown) */
  makeVoice(request: VoiceRequest): Promise<VoiceResult | null>;
  /** tells the person something (a toast) */
  notify(tone: "info" | "success" | "error", title: string, description?: string): void;
}

export const EditorContext = createContext<{ state: EditorState; actions: EditorActions } | null>(null);

export function useEditor(): { state: EditorState; actions: EditorActions } {
  const value = useContext(EditorContext);
  if (!value) throw new Error("useEditor must be used inside the Edit room");
  return value;
}
