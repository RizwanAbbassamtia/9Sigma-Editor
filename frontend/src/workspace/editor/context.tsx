import { createContext, useContext } from "react";

import type { TimeStore } from "../../components/preview/timeStore";
import type { LivePreviewHandle } from "../../components/preview/LivePreview";
import type { Project, ProjectFormat } from "../../types/project";
import type { BundlePicture, BundleSentence, TimelineBundle, WaveformPeaks, MusicEnvelope } from "../../types/preview";
import type { TransitionCatalogue, TransitionPack } from "../../types/storyboard";
import type { EditChoices, EditReviewPayload, Timeline } from "../../types/timeline";
import type { Selection } from "./document";
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
  addScene(file: File): Promise<void>;
  reorderScenes(order: number[]): Promise<void>;
  removeScene(index: number): Promise<void>;
  shortenPause(sentenceIndex: number, shift_s: number): Promise<void>;
  applyChoices(choices: EditChoices): Promise<void>;
  rebuild(): Promise<void>;
  /** tells the person something (a toast) */
  notify(tone: "info" | "success" | "error", title: string, description?: string): void;
}

export const EditorContext = createContext<{ state: EditorState; actions: EditorActions } | null>(null);

export function useEditor(): { state: EditorState; actions: EditorActions } {
  const value = useContext(EditorContext);
  if (!value) throw new Error("useEditor must be used inside the Edit room");
  return value;
}
