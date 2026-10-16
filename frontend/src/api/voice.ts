/**
 * The AI voice (docs/CONTRACT-v0.2.md section 5): the key in Settings, the account's voices,
 * and "Make a voice from text" in the Edit room.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { VoiceInfo, VoiceRequest, VoiceResult, VoiceStatus } from "../types/voice";
import { api } from "./client";
import { queryKeys } from "./keys";

export function getVoiceStatus(): Promise<VoiceStatus> {
  return api.get<VoiceStatus>("/api/voice/status");
}

/** An empty key removes the saved one. */
export function saveVoiceKey(apiKey: string): Promise<VoiceStatus> {
  return api.put<VoiceStatus>("/api/voice/key", { api_key: apiKey });
}

export function listVoices(): Promise<VoiceInfo[]> {
  return api.get<VoiceInfo[]>("/api/voice/voices");
}

export function makeVoice(projectId: string, request: VoiceRequest): Promise<VoiceResult> {
  return api.post<VoiceResult>(`/api/edits/${encodeURIComponent(projectId)}/voice`, request);
}

export function useVoiceStatus() {
  return useQuery({ queryKey: queryKeys.voiceStatus, queryFn: getVoiceStatus, staleTime: 60_000 });
}

export function useSaveVoiceKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveVoiceKey,
    onSuccess: (status) => {
      queryClient.setQueryData(queryKeys.voiceStatus, status);
      void queryClient.invalidateQueries({ queryKey: queryKeys.voices });
    },
  });
}

export function useVoices(enabled: boolean) {
  return useQuery({ queryKey: queryKeys.voices, queryFn: listVoices, enabled, staleTime: 5 * 60_000, retry: false });
}

export function useMakeVoice(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: VoiceRequest) => makeVoice(projectId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.media(projectId) });
    },
  });
}
