/**
 * Loads the preview engine (`frontend/src/preview/engine.ts`, docs/M5-CONTRACT.md section 4,
 * PixiJS 8) on demand: it is a chunk of its own, so the review pages do not download it until a
 * monitor opens. The engine falls back to Canvas 2D by itself when WebGL2 is not there.
 */
import type { PreviewEngineFactory } from "../../types/preview";

let loading: Promise<PreviewEngineFactory> | null = null;

/** The engine's `createPreview`, loaded once per page (tried again after a failed load). */
export function loadPreviewEngine(): Promise<PreviewEngineFactory> {
  loading ??= import("../../preview/engine").then(
    (module): PreviewEngineFactory => module.createPreview,
    (error: unknown) => {
      loading = null;
      throw error;
    },
  );
  return loading;
}
