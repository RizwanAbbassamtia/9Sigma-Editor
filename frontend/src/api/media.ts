/**
 * The media library (docs/CONTRACT-v0.2.md section 2): `GET`, `POST` (import) and `DELETE`
 * `/api/edits/{id}/media`, plus the empty project and the narration route.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { BlankEditInput, MediaLibrary } from "../types/media";
import type { Project } from "../types/project";
import { api } from "./client";
import { queryKeys } from "./keys";
import { type EditedPayload } from "./projects";

function base(id: string): string {
  return `/api/edits/${encodeURIComponent(id)}`;
}

/** `POST /api/edits/blank`: an empty project, opened in the Edit room at once. */
export function createBlankEdit(input: BlankEditInput): Promise<Project> {
  return api.post<Project>("/api/edits/blank", input);
}

export function getMediaLibrary(id: string): Promise<MediaLibrary> {
  return api.get<MediaLibrary>(`${base(id)}/media`);
}

/** `POST /api/edits/{id}/media`: copies the files into 01_media and answers the whole library. */
export function importMedia(id: string, files: File[]): Promise<MediaLibrary> {
  const body = new FormData();
  for (const file of files) body.append("files", file, file.name);
  return api.upload<MediaLibrary>(`${base(id)}/media`, body);
}

/** `DELETE /api/edits/{id}/media?path=`: removes a file nothing uses. */
export function removeMedia(id: string, path: string): Promise<MediaLibrary> {
  return api.delete<MediaLibrary>(`${base(id)}/media?path=${encodeURIComponent(path)}`);
}

export interface NarrationInput {
  /** a library file (01_media/...) */
  media?: string;
  /** or an upload */
  file?: File;
  /** the caption text the timing follows; the edit's own text when empty */
  caption_text?: string;
}

/** `POST /api/edits/{id}/narration`: use a sound file as the narration. */
export function setNarration(id: string, input: NarrationInput): Promise<EditedPayload> {
  const body = new FormData();
  if (input.file) body.append("file", input.file, input.file.name);
  if (input.media) body.append("media", input.media);
  if (input.caption_text) body.append("caption_text", input.caption_text);
  return api.upload<EditedPayload>(`${base(id)}/narration`, body);
}

// ---- hooks ------------------------------------------------------------------------------------

export function useCreateBlankEdit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createBlankEdit,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists }),
  });
}

export function useMediaLibrary(projectId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.media(projectId ?? ""),
    queryFn: () => getMediaLibrary(projectId as string),
    enabled: Boolean(projectId) && enabled,
    staleTime: 10_000,
  });
}

export function useImportMedia(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => importMedia(projectId, files),
    onSuccess: (library) => queryClient.setQueryData(queryKeys.media(projectId), library),
  });
}

export function useRemoveMedia(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => removeMedia(projectId, path),
    onSuccess: (library) => queryClient.setQueryData(queryKeys.media(projectId), library),
  });
}
