/**
 * The edits API (`/api/edits`, docs/CONTRACT-v0.1.md sections 2 to 5): the list, one edit,
 * New edit from files, rename, delete to the Recycle Bin, open the folder, the editor payload,
 * the timeline autosave, the timing tool, scenes, render and export.
 *
 * The file keeps its CashCow name (`api/projects.ts`) and the hook names the copied editor
 * components use (`useProject`, `useProjects`, `useOpenProjectFolder`, `useProjectCacheUpdate`),
 * so those components need few changes.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { EditInput, OpenFolderResponse, Project, ProjectSummary } from "../types/project";
import type { CaptionCue, EditChoices, Timeline } from "../types/timeline";
import type { TimingDoc } from "../types/timing";
import { api } from "./client";
import { queryKeys } from "./keys";

function base(id: string): string {
  return `/api/edits/${encodeURIComponent(id)}`;
}

// ---- the list and one edit ------------------------------------------------------------------

export function listProjects(): Promise<ProjectSummary[]> {
  return api.get<ProjectSummary[]>("/api/edits");
}

export function getProject(id: string): Promise<Project> {
  return api.get<Project>(base(id));
}

/** The files of "New edit from files", by role. */
export interface NewEditFiles {
  /** the pictures and clips, in the order they were added */
  items: File[];
  narration?: File | null;
  music?: File | null;
  captions?: File | null;
}

/** `POST /api/edits`: one multipart request with the form and every file. */
export function createEdit(form: EditInput, files: NewEditFiles): Promise<Project> {
  const body = new FormData();
  body.append("form", JSON.stringify(form));
  for (const file of files.items) body.append("files", file, file.name);
  if (files.narration) body.append("narration", files.narration, files.narration.name);
  if (files.music) body.append("music", files.music, files.music.name);
  if (files.captions) body.append("captions", files.captions, files.captions.name);
  return api.upload<Project>("/api/edits", body);
}

export interface ProjectPatch {
  name?: string;
  language?: string;
  music_licence_note?: string;
}

export function patchProject(id: string, patch: ProjectPatch): Promise<Project> {
  return api.post<Project>(`${base(id)}/update`, patch);
}

/** Moves the edit's folder to the Recycle Bin; never hard-deletes. */
export function deleteProject(id: string): Promise<void> {
  return api.delete(base(id));
}

export interface OpenFolderRequest {
  target?: "project" | "export";
}

/** Asks the backend to show the edit's folder (or its last export) in Windows Explorer. */
export function openProjectFolder(id: string, request: OpenFolderRequest = {}): Promise<OpenFolderResponse> {
  return api.post<OpenFolderResponse>(`${base(id)}/open-folder`, request);
}

// ---- the editor payload and the timeline ------------------------------------------------------

/** `GET /api/edits/{id}/edit`: the timeline, the renders, the checks (types/timeline.ts). */
export function getEditPayload<T = unknown>(id: string): Promise<T> {
  return api.get<T>(`${base(id)}/edit`);
}

export interface TimelineSave {
  timeline: Timeline;
  /** the revision the page is editing; the server refuses (409) when the file moved on */
  revision: number;
}

export interface TimelineSaved {
  revision: number;
  timeline: Timeline;
}

/** `PUT /api/edits/{id}/timeline`: the autosave. */
export function saveTimeline(id: string, body: TimelineSave): Promise<TimelineSaved> {
  return api.put<TimelineSaved>(`${base(id)}/timeline`, body);
}

/** `POST /api/edits/{id}/edit`: the simple choices (sizes, music, switches, volume and dip). */
export function applyEdits<T = unknown>(id: string, edits: EditChoices): Promise<T> {
  return api.post<T>(`${base(id)}/edit`, edits);
}

// ---- the timing tool ------------------------------------------------------------------------

export function getTiming(id: string): Promise<TimingDoc> {
  return api.get<TimingDoc>(`${base(id)}/timing`);
}

export interface TimingNudge {
  /** 0-based sentence */
  index: number;
  /** drag: where the sentence should start */
  start_s?: number;
  /** shift this sentence and everything after it by this many seconds */
  shift_s?: number;
}

/** `POST /api/edits/{id}/timing`: one nudge; the captions and scene times follow. */
export function nudgeTiming(id: string, body: TimingNudge): Promise<TimingDoc> {
  return api.post<TimingDoc>(`${base(id)}/timing`, body);
}

// ---- scenes ---------------------------------------------------------------------------------

/** What the scene routes answer: the fresh editor payload (raw) and plain-English warnings. */
export interface EditedPayload {
  payload: unknown;
  warnings: string[];
}

export interface AddSceneOptions {
  caption?: string;
  keep_sound?: boolean;
  /** insert after this scene (0-based); -1 = at the start; omitted = at the end */
  after?: number;
}

/** `POST /api/edits/{id}/scenes`: a new scene from a picture or clip file. */
export function addScene(id: string, file: File, options: AddSceneOptions = {}): Promise<EditedPayload> {
  const body = new FormData();
  body.append("file", file, file.name);
  if (options.caption) body.append("caption", options.caption);
  if (options.keep_sound) body.append("keep_sound", "true");
  if (options.after !== undefined) body.append("after", String(options.after));
  return api.upload<EditedPayload>(`${base(id)}/scenes`, body);
}

/** `POST /api/edits/{id}/scenes/{index}/picture`: replace one scene's picture or clip. */
export function replaceScenePicture(id: string, index: number, file: File): Promise<EditedPayload> {
  const body = new FormData();
  body.append("file", file, file.name);
  return api.upload<EditedPayload>(`${base(id)}/scenes/${index}/picture`, body);
}

/** `POST /api/edits/{id}/scenes/reorder`: the new order as the old 0-based indexes. */
export function reorderScenes(id: string, order: number[]): Promise<EditedPayload> {
  return api.post<EditedPayload>(`${base(id)}/scenes/reorder`, { order });
}

export function removeScene(id: string, index: number): Promise<EditedPayload> {
  return api.delete<EditedPayload>(`${base(id)}/scenes/${index}`);
}

// ---- captions ---------------------------------------------------------------------------------

export interface CueInput {
  id: string;
  start_s: number;
  end_s: number;
  text: string;
}

export interface CaptionsBody {
  cues: CueInput[];
}

export interface CaptionsView {
  enabled: boolean;
  cues: CaptionCue[];
  revision: number;
}

export function getCaptions(id: string): Promise<CaptionsView> {
  return api.get<CaptionsView>(`${base(id)}/captions`);
}

/** `PUT /api/edits/{id}/captions`: the caption table (text and times). */
export function saveCaptions(id: string, body: CaptionsBody): Promise<CaptionsView> {
  return api.put<CaptionsView>(`${base(id)}/captions`, body);
}

// ---- render and export ------------------------------------------------------------------------

export type Quality = "standard" | "high" | "best";
export type UploadPreset = "none" | "youtube_1080p" | "youtube_4k";

export interface RenderRequest {
  /** the sizes to render; missing = the edit's ticked sizes, [] = the preview only */
  presets?: string[];
  quality?: Quality;
  upload_preset?: UploadPreset;
  h265_copy?: boolean;
  /** only this part of the video, for a check */
  range?: { start_s: number; end_s: number };
}

export interface RenderStarted {
  job_id: string;
  message: string;
}

export interface RenderStatus {
  running: boolean;
  job_id: string | null;
  preset: string | null;
  /** 0-100, or null when unknown */
  pct: number | null;
  message: string;
  error: string | null;
  /** when the last render finished, so the page knows to refresh the payload */
  finished_at: string | null;
  /** the sizes the last render made */
  rendered: string[];
  /** the sizes the last render found up to date and kept */
  kept: string[];
}

export function startRender(id: string, body: RenderRequest): Promise<RenderStarted> {
  return api.post<RenderStarted>(`${base(id)}/render`, body);
}

export function getRenderStatus(id: string): Promise<RenderStatus> {
  return api.get<RenderStatus>(`${base(id)}/render/status`);
}

export function cancelRender(id: string): Promise<void> {
  return api.post<void>(`${base(id)}/render/cancel`, {});
}

export interface ExportRequest {
  presets: string[];
}

export interface ExportedFileInfo {
  name: string;
  label: string;
  size_bytes: number;
  sha256: string;
}

export interface ExportResult {
  folder: string;
  files: ExportedFileInfo[];
}

export function exportEdit(id: string, body: ExportRequest): Promise<ExportResult> {
  return api.post<ExportResult>(`${base(id)}/export`, body);
}

/** One finished render of any edit, for the Renders page. */
export interface RenderListRow {
  project_id: string;
  name: string;
  format: "long" | "shorts";
  preset: string;
  quality: string;
  path: string;
  url: string;
  duration_s: number | null;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
  rendered_at: string | null;
  exported: boolean;
}

export function listRenders(): Promise<RenderListRow[]> {
  return api.get<RenderListRow[]>("/api/renders");
}

// ---- hooks ------------------------------------------------------------------------------------

export function useProjects(options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: queryKeys.projectList(),
    queryFn: listProjects,
    refetchInterval: options.refetchInterval ?? false,
  });
}

/** One edit; polls every few seconds while a render runs so the list and the page keep moving. */
export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.project(id ?? ""),
    queryFn: () => getProject(id as string),
    enabled: Boolean(id),
    refetchInterval: (query) => (query.state.data?.status === "rendering" ? 4_000 : false),
  });
}

/** Puts a fresh Project into the cache and refreshes the lists and the payloads under it. */
export function useProjectCacheUpdate() {
  const queryClient = useQueryClient();
  return (project: Project) => {
    queryClient.setQueryData(queryKeys.project(project.id), project);
    void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    void queryClient.invalidateQueries({ queryKey: queryKeys.renders });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.project(project.id),
      // the detail itself was just set; only the payloads underneath need a refetch
      predicate: (query) => query.queryKey.length > queryKeys.project(project.id).length,
    });
  };
}

export function useCreateEdit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ form, files }: { form: EditInput; files: NewEditFiles }) => createEdit(form, files),
    onSuccess: (project) => {
      queryClient.setQueryData(queryKeys.project(project.id), project);
      void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    },
  });
}

export function usePatchProject() {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ProjectPatch }) => patchProject(id, patch),
    onSuccess: update,
  });
}

export function useDeleteProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteProject(id),
    onSuccess: (_result, id) => {
      queryClient.removeQueries({ queryKey: queryKeys.project(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
      void queryClient.invalidateQueries({ queryKey: queryKeys.renders });
    },
  });
}

export function useOpenProjectFolder() {
  return useMutation({
    mutationFn: ({ id, request }: { id: string; request?: OpenFolderRequest }) => openProjectFolder(id, request ?? {}),
  });
}

export function useTiming(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.timing(id ?? ""),
    queryFn: () => getTiming(id as string),
    enabled: Boolean(id) && enabled,
    retry: false,
  });
}

/** The render job's progress, asked every second while it runs. */
export function useRenderStatus(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.renderStatus(id ?? ""),
    queryFn: () => getRenderStatus(id as string),
    enabled: Boolean(id) && enabled,
    retry: false,
    refetchInterval: (query) => (query.state.data?.running ? 1_000 : 5_000),
  });
}

export function useRenders() {
  return useQuery({ queryKey: queryKeys.renders, queryFn: listRenders, staleTime: 10_000 });
}
