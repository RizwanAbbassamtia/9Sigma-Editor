import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { resolveRenderSettings, type RenderSettingsResolved, type Settings, type SettingsUpdate } from "../types/settings";
import { api } from "./client";
import { queryKeys } from "./keys";

export function getSettings(): Promise<Settings> {
  return api.get<Settings>("/api/settings");
}

/** Only the fields present are changed. */
export function updateSettings(body: SettingsUpdate): Promise<Settings> {
  return api.put<Settings>("/api/settings", body);
}

export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: getSettings });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateSettings,
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      // The folders change what the doctor and the system info report, and the edits list.
      void queryClient.invalidateQueries({ queryKey: queryKeys.system });
      void queryClient.invalidateQueries({ queryKey: queryKeys.doctor });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      void queryClient.invalidateQueries({ queryKey: queryKeys.renders });
    },
  });
}

/** The `render` and `captions` settings with every field filled in. */
export function useRenderSettings(): { query: ReturnType<typeof useSettings>; resolved: RenderSettingsResolved } {
  const query = useSettings();
  const resolved = useMemo(() => resolveRenderSettings(query.data), [query.data]);
  return { query, resolved };
}
