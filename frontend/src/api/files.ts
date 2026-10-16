/**
 * Files inside an edit's folder: `GET /api/projects/{id}/files/{path}` serves them (with
 * range requests, so video and audio seek), `POST /api/projects/{id}/upload/{stage}` saves one.
 * The routes keep the engine's `/api/projects` prefix; the ids are the edit ids.
 */
import { useMutation } from "@tanstack/react-query";

import { api } from "./client";

/** URL of a file inside the edit's folder, for <audio>, <video> and <img> elements. */
export function projectFileUrl(projectId: string, relativePath: string): string {
  const safe = relativePath
    .split(/[\\/]+/)
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  return `/api/projects/${encodeURIComponent(projectId)}/files/${safe}`;
}

export interface UploadResult {
  path: string;
}

export type UploadStage = "voice" | "images" | "edit" | "export" | "media";

/** Uploads a file into one of the edit's folders; returns the saved relative path. */
export async function uploadProjectFile(projectId: string, stage: UploadStage, file: File, scene?: number): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  if (scene !== undefined) form.append("scene", String(scene));
  return api.upload<UploadResult>(`/api/projects/${encodeURIComponent(projectId)}/upload/${stage}`, form);
}

export interface UploadProjectFileInput {
  projectId: string;
  stage: UploadStage;
  file: File;
  scene?: number;
}

/** Mutation form of `uploadProjectFile` for buttons that show a spinner while the file goes up. */
export function useUploadProjectFile() {
  return useMutation({
    mutationFn: ({ projectId, stage, file, scene }: UploadProjectFileInput) => uploadProjectFile(projectId, stage, file, scene),
  });
}
