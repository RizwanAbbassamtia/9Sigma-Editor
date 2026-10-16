import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { UpdateStatus } from "../types";
import { api } from "./client";
import { queryKeys } from "./keys";

/** docs/RELEASE-CONTRACT.md section 5: never waits for GitHub, the server checks in the background. */
export function getUpdateStatus(): Promise<UpdateStatus> {
  return api.get<UpdateStatus>("/api/system/update");
}

export function checkForUpdates(): Promise<UpdateStatus> {
  return api.post<UpdateStatus>("/api/system/update/check", {});
}

export function installUpdate(): Promise<UpdateStatus> {
  return api.post<UpdateStatus>("/api/system/update/install", {});
}

const BUSY: ReadonlySet<UpdateStatus["status"]> = new Set(["downloading", "installing"]);

/** Every 5 minutes (the server itself checks GitHub at most every 6 hours); every second while
 * a check, a download or an install is running. */
export function updatePollInterval(status: UpdateStatus | undefined): number {
  if (!status) return 5 * 60_000;
  return BUSY.has(status.status) || status.checking ? 1_000 : 5 * 60_000;
}

export function useUpdateStatus() {
  return useQuery({
    queryKey: queryKeys.update,
    queryFn: getUpdateStatus,
    staleTime: 30_000,
    retry: false,
    refetchInterval: (query) => updatePollInterval(query.state.data),
  });
}

export function useCheckForUpdates() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: checkForUpdates,
    onSuccess: (status) => client.setQueryData(queryKeys.update, status),
  });
}

export function useInstallUpdate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: installUpdate,
    onSuccess: (status) => client.setQueryData(queryKeys.update, status),
  });
}
