/**
 * The messages between the page (parallaxJobs.ts) and the parallax worker (parallax.worker.ts).
 * Kept apart from parallaxJobs.ts so the worker's bundle does not contain the code that starts it.
 */
import type { Grid, PlatePixels } from "./layers";
import type { Rect } from "./motion";
import type { CropAnalysis } from "./parallax";

export interface AnalyseJob {
  depth: Blob;
  crop: Rect;
  image: [number, number];
}

export interface PlatesJob {
  /** a copy of the decoded picture (it is closed, or moved to the worker) */
  bitmap: ImageBitmap;
  image: [number, number];
  crop: Rect;
  alpha: Grid;
  maxWidth: number;
}

/** A message to parallax.worker.ts. */
export type WorkerRequest = ({ id: number; kind: "analyse" } & AnalyseJob) | ({ id: number; kind: "plates" } & PlatesJob);

/** What parallax.worker.ts answers. */
export type WorkerReply = { id: number; ok: true; analysis?: CropAnalysis | null; plates?: PlatePixels | null } | { id: number; ok: false; error: string };

/** The buffers of an analysis' grids, each once (they go back to the page without a copy). */
export function analysisBuffers(found: CropAnalysis): ArrayBuffer[] {
  const grids = [found.analysis.envelope, found.analysis.alpha, found.analysis.backDepth, found.analysis.frontDepth];
  const buffers = new Set<ArrayBuffer>();
  for (const grid of grids) if (grid) buffers.add(grid.values.buffer as ArrayBuffer);
  return [...buffers];
}
