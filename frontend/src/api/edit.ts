/**
 * The editor's calls (docs/CONTRACT-v0.1.md sections 4 and 5) as react-query hooks: the editor
 * payload, the simple choices, the timeline autosave, scenes, captions, the timing tool,
 * render (started, then polled) and export. The plain fetch functions live in api/projects.ts.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { parseEditPayload, type EditChoices, type EditReviewPayload } from "../types/timeline";
import type { TimingDoc } from "../types/timing";
import { projectFileUrl } from "./files";
import { queryKeys } from "./keys";
import {
  addScene,
  applyEdits,
  cancelRender,
  exportEdit,
  getCaptions,
  getEditPayload,
  nudgeTiming,
  removeScene,
  reorderScenes,
  replaceScenePicture,
  saveCaptions,
  saveTimeline,
  startRender,
  useRenderStatus,
  type AddSceneOptions,
  type CaptionsBody,
  type EditedPayload,
  type ExportRequest,
  type RenderRequest,
  type RenderStatus,
  type TimelineSave,
  type TimingNudge,
} from "./projects";

// ---- files inside the edit's folder ---------------------------------------------------------

export const PROXY_FILE = "07_edit/proxy.mp4";
export const RENDER_LOG_FILE = "07_edit/render.log";

/** `07_edit/final_1080p.mp4` */
export function finalFile(preset: string): string {
  return `07_edit/final_${preset}.mp4`;
}

/** `06_images/scene_01.png` for scene index 0 */
export function sceneImageFile(index: number): string {
  return `06_images/scene_${String(index + 1).padStart(2, "0")}.png`;
}

const STAGE_DIR_PATTERN = /(?:^|[\\/])(0[1-8]_[a-z]+(?:[\\/].*)?)$/i;

/**
 * The path of a file relative to the edit's folder, with forward slashes:
 * "C:\\...\\edit\\06_images\\scene_01.png" -> "06_images/scene_01.png". A relative path passes
 * through; an absolute path outside any of the numbered folders gives null.
 */
export function projectRelativePath(path: string): string | null {
  const trimmed = path.trim();
  if (!trimmed) return null;
  const match = STAGE_DIR_PATTERN.exec(trimmed);
  if (match) return match[1]!.split(/[\\/]+/).filter(Boolean).join("/");
  const absolute = /^[a-z]:[\\/]/i.test(trimmed) || trimmed.startsWith("\\\\") || trimmed.startsWith("/");
  if (absolute) return null;
  return trimmed.split(/[\\/]+/).filter(Boolean).join("/");
}

/**
 * Where the browser loads a file of the edit from: the backend's own URL when it sent one,
 * else the file-serving route for the path; null when the path cannot be served.
 */
export function projectMediaUrl(projectId: string, path: string | null | undefined, url?: string | null): string | null {
  if (url) return url;
  if (!path) return null;
  if (/^(https?:)?\/\//i.test(path) || path.startsWith("data:") || path.startsWith("/api/")) return path;
  const relative = projectRelativePath(path);
  return relative ? projectFileUrl(projectId, relative) : null;
}

/** The captions of an edit as an SRT file to save. */
export function captionsSrtUrl(projectId: string): string {
  return `/api/edits/${encodeURIComponent(projectId)}/captions.srt`;
}

// ---- the payload -------------------------------------------------------------------------------

/** The parsed editor payload; null while there is no timeline (404) or before it loads. */
export function useEditPayload(projectId: string | undefined, enabled = true) {
  const query = useQuery({
    queryKey: queryKeys.editPayload(projectId ?? ""),
    queryFn: () => getEditPayload<unknown>(projectId as string),
    enabled: Boolean(projectId) && enabled,
    retry: false,
  });
  const payload = useMemo(() => parseEditPayload(query.data), [query.data]);
  return { query, payload };
}

/** Puts a fresh payload into the cache and refreshes what hangs off it (the preview bundle). */
export function usePayloadUpdate(projectId: string) {
  const queryClient = useQueryClient();
  return (raw: unknown) => {
    queryClient.setQueryData(queryKeys.editPayload(projectId), raw);
    void queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "preview"] });
    void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
    void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
  };
}

/** Everything of this edit is read again (after a render finished or a timing move). */
export function useRefreshEdit(projectId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    void queryClient.invalidateQueries({ queryKey: queryKeys.renders });
  };
}

// ---- live render progress --------------------------------------------------------------------

export interface RenderProgress {
  /** the preset named in the progress message, when it names one */
  preset: string | null;
  /** 0-100 or null when the server cannot estimate */
  pct: number | null;
  message: string;
}

const PRESET_PATTERN = /render(?:ing)?\s+(?:the\s+)?([0-9]{3,4}p|proxy|preview|4k)\b/i;
const PERCENT_PATTERN = /(\d{1,3}(?:\.\d+)?)\s*%/;

/** "Rendering 1080p 45%" -> { preset: "1080p", pct: 45 }. Unknown messages give nulls. */
export function parseRenderProgress(message: string, pct?: number | null): { preset: string | null; pct: number | null } {
  const presetMatch = PRESET_PATTERN.exec(message);
  const word = presetMatch?.[1]?.toLowerCase() ?? null;
  const preset = word === null ? null : word === "4k" ? "2160p" : word === "preview" ? "proxy" : word;
  const percentMatch = PERCENT_PATTERN.exec(message);
  const fromMessage = percentMatch?.[1] ? Math.round(Number(percentMatch[1])) : null;
  const value = typeof pct === "number" && Number.isFinite(pct) ? pct : fromMessage;
  return { preset, pct: value !== null ? Math.max(0, Math.min(100, value)) : null };
}

/** The render job's progress while it runs, from `GET /render/status` (asked every second). */
export function renderProgressOf(status: RenderStatus | undefined): RenderProgress | null {
  if (!status || !status.running) return null;
  const parsed = parseRenderProgress(status.message, status.pct);
  return { preset: status.preset ?? parsed.preset, pct: parsed.pct, message: status.message };
}

export { useRenderStatus };

// ---- mutations ----------------------------------------------------------------------------------

/** The simple choices: sizes, music, captions, popups, volume and dip, or a rebuild. */
export function useApplyEdits(projectId: string) {
  const update = usePayloadUpdate(projectId);
  return useMutation({
    mutationFn: (edits: EditChoices) => applyEdits<unknown>(projectId, edits),
    onSuccess: update,
  });
}

/** The autosave of the whole timeline (cuts, scene choices, markers). */
export function useSaveTimeline(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TimelineSave) => saveTimeline(projectId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    },
  });
}

function useSceneMutation<Input>(projectId: string, run: (input: Input) => Promise<EditedPayload>) {
  const update = usePayloadUpdate(projectId);
  return useMutation({
    mutationFn: run,
    onSuccess: (result) => update(result.payload),
  });
}

export function useAddScene(projectId: string) {
  return useSceneMutation(projectId, ({ file, options }: { file: File; options?: AddSceneOptions }) => addScene(projectId, file, options));
}

export function useReplaceScenePicture(projectId: string) {
  return useSceneMutation(projectId, ({ index, file }: { index: number; file: File }) => replaceScenePicture(projectId, index, file));
}

export function useReorderScenes(projectId: string) {
  return useSceneMutation(projectId, (order: number[]) => reorderScenes(projectId, order));
}

export function useRemoveScene(projectId: string) {
  return useSceneMutation(projectId, (index: number) => removeScene(projectId, index));
}

export function useCaptions(projectId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.captions(projectId ?? ""),
    queryFn: () => getCaptions(projectId as string),
    enabled: Boolean(projectId) && enabled,
    retry: false,
  });
}

export function useSaveCaptions(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CaptionsBody) => saveCaptions(projectId, body),
    onSuccess: (view) => {
      queryClient.setQueryData(queryKeys.captions(projectId), view);
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

/** One move of the timing tool; the timeline and the captions follow, so everything reloads. */
export function useNudgeTiming(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TimingNudge) => nudgeTiming(projectId, body),
    onSuccess: (doc: TimingDoc) => {
      queryClient.setQueryData(queryKeys.timing(projectId), doc);
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId), predicate: (query) => query.queryKey !== queryKeys.timing(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    },
  });
}

export function useStartRender(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RenderRequest) => startRender(projectId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.renderStatus(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    },
  });
}

export function useCancelRender(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => cancelRender(projectId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.renderStatus(projectId) }),
  });
}

export function useExportEdit(projectId: string) {
  const refresh = useRefreshEdit(projectId);
  return useMutation({
    mutationFn: (body: ExportRequest) => exportEdit(projectId, body),
    onSuccess: refresh,
  });
}

export type { EditReviewPayload };
