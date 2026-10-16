/**
 * Live preview calls (docs/M5-CONTRACT.md section 2): the timeline bundle the preview engine
 * draws, picture thumbnails, waveform peaks, the audio of one sentence, popup pictures and the
 * music stem. Everything here only reads or computes previews; viewers may call all of it.
 *
 * Every route lives under `/api/projects/{id}`; fonts under `/api/fonts/{file}`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  parseMusicEnvelope,
  parseMusicStem,
  parsePopupPreview,
  parseTimelineBundle,
  parseWaveformPeaks,
  type MusicEnvelope,
  type MusicStemResult,
  type PopupPreviewRequest,
  type PopupPreviewResult,
  type TimelineBundle,
  type WaveformPeaks,
} from "../types/preview";
import { ApiError, api } from "./client";
import { queryKeys } from "./keys";

function projectBase(projectId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}`;
}

// ---- cache keys ---------------------------------------------------------------------------

/**
 * Under the project's detail key, so `useProjectCacheUpdate` (after a render, an approve or a
 * redo) refreshes the bundle together with the stage payloads.
 */
export const previewKeys = {
  bundle: (projectId: string) => [...queryKeys.project(projectId), "preview", "bundle"] as const,
  peaks: (projectId: string, kind: PeaksKind) => [...queryKeys.project(projectId), "preview", "peaks", kind] as const,
};

// ---- URLs ---------------------------------------------------------------------------------

export function timelineBundleUrl(projectId: string): string {
  return `${projectBase(projectId)}/timeline`;
}

/** The heights the thumbnail route makes (`h` from 16 to 2160 pixels). */
export const THUMB_MIN_HEIGHT = 16;
export const THUMB_MAX_HEIGHT = 2160;

/**
 * A cached JPEG of a project picture, `height` pixels tall (128 for strips and lanes). The
 * server makes them 16 to 2160 pixels tall (never taller than the picture), so a taller ask
 * gets the 2160 one: plenty for a 9:16 monitor at twice its size.
 */
export function mediaThumbUrl(projectId: string, path: string, height = 128): string {
  const wanted = Number.isFinite(height) ? Math.round(height) : 128;
  const h = Math.min(THUMB_MAX_HEIGHT, Math.max(THUMB_MIN_HEIGHT, wanted));
  const query = new URLSearchParams({ path, h: String(h) });
  return `${projectBase(projectId)}/media/thumb?${query.toString()}`;
}

export type PeaksKind = "voice" | "music";

export function peaksUrl(projectId: string, kind: PeaksKind): string {
  return `${projectBase(projectId)}/media/peaks/${kind}.json`;
}

/** The audio of one sentence; an own recording is split once on the server with timing.json. */
export function voiceSegmentUrl(projectId: string, sentenceId: string): string {
  return `${projectBase(projectId)}/media/voice/segments/${encodeURIComponent(sentenceId)}.wav`;
}

/** A Noto font file from `assets/fonts` (allow-listed by the server). */
export function fontUrl(file: string): string {
  return `/api/fonts/${encodeURIComponent(file)}`;
}

// ---- the bundle ---------------------------------------------------------------------------

/** `GET /api/projects/{id}/timeline`; null when the answer holds no timeline. */
export async function getTimelineBundle(projectId: string): Promise<TimelineBundle | null> {
  return parseTimelineBundle(await api.get<unknown>(timelineBundleUrl(projectId)));
}

/** A 404 means "no timeline yet" (or a server without the M5 routes): not worth retrying. */
export function isMissing(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 405);
}

/** The timeline bundle the live preview draws. */
export function useTimelineBundle(projectId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: previewKeys.bundle(projectId ?? ""),
    queryFn: () => getTimelineBundle(projectId as string),
    enabled: Boolean(projectId) && enabled,
    retry: (failures, error) => !isMissing(error) && failures < 1,
    refetchOnWindowFocus: false,
  });
}

// ---- peaks and the music envelope ---------------------------------------------------------

export async function getPeaks(projectId: string, kind: PeaksKind): Promise<WaveformPeaks | null> {
  return parseWaveformPeaks(await api.get<unknown>(peaksUrl(projectId, kind)));
}

/** Min/max peaks per 10 ms for the waveform under the monitor (null while loading or absent). */
export function usePeaks(projectId: string | undefined, kind: PeaksKind, enabled = true) {
  return useQuery({
    queryKey: previewKeys.peaks(projectId ?? "", kind),
    queryFn: () => getPeaks(projectId as string, kind),
    enabled: Boolean(projectId) && enabled,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
}

/** `07_edit/music_envelope.json` from the URL the bundle or the stem rebuild gave. */
export async function getMusicEnvelope(url: string): Promise<MusicEnvelope | null> {
  return parseMusicEnvelope(await api.get<unknown>(url));
}

// ---- popups -------------------------------------------------------------------------------

/** `POST .../popups/preview`: the popup PNG the renderer would overlay, and where it sits. */
export async function previewPopup(projectId: string, request: PopupPreviewRequest): Promise<PopupPreviewResult | null> {
  return parsePopupPreview(await api.post<unknown>(`${projectBase(projectId)}/popups/preview`, request));
}

interface PendingPopup {
  timer: ReturnType<typeof setTimeout> | null;
  request: PopupPreviewRequest;
  waiters: Array<{ resolve: (value: PopupPreviewResult | null) => void; reject: (error: unknown) => void }>;
}

/**
 * Debounced popup pictures, one queue per popup `key`: calls within `delayMs` (150 ms in the
 * contract) collapse into one request with the latest text and style, and every caller gets
 * its answer. `fetcher` is replaceable for tests.
 */
export function createPopupPreviewer(
  projectId: string,
  delayMs = 150,
  fetcher: (projectId: string, request: PopupPreviewRequest) => Promise<PopupPreviewResult | null> = previewPopup,
): (key: string, request: PopupPreviewRequest) => Promise<PopupPreviewResult | null> {
  const pending = new Map<string, PendingPopup>();
  return (key, request) =>
    new Promise<PopupPreviewResult | null>((resolve, reject) => {
      const entry: PendingPopup = pending.get(key) ?? { timer: null, request, waiters: [] };
      if (entry.timer !== null) clearTimeout(entry.timer);
      entry.request = request;
      entry.waiters.push({ resolve, reject });
      entry.timer = setTimeout(() => {
        pending.delete(key);
        fetcher(projectId, entry.request).then(
          (result) => entry.waiters.forEach((waiter) => waiter.resolve(result)),
          (error: unknown) => entry.waiters.forEach((waiter) => waiter.reject(error)),
        );
      }, delayMs);
      pending.set(key, entry);
    });
}

// ---- the music stem -----------------------------------------------------------------------

/** `POST .../music/stem`: rebuilds `music_ducked.wav` and its envelope from the timeline's music. */
export async function rebuildMusicStem(projectId: string): Promise<MusicStemResult> {
  return parseMusicStem(await api.post<unknown>(`${projectBase(projectId)}/music/stem`, {}));
}

/** Rebuilds the stem, then refreshes the bundle so the preview plays the new music. */
export function useRebuildMusicStem(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => rebuildMusicStem(projectId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: previewKeys.bundle(projectId) }),
  });
}
