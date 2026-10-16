/**
 * The parallax worker (parallaxJobs.ts `WorkerJobs`): works out a scene's depth layers and the
 * colours of its two plates away from the page's main thread, so a parallax scene coming up never
 * holds up a frame or the sound. One message, one answer; grids and pixels go back without a copy.
 */
import { cropPixels, platePixels, plateSize } from "./layers";
import { analyseCrop } from "./parallax";
import { analysisBuffers, type WorkerReply, type WorkerRequest } from "./parallaxMessages";
import { decodeDepthMap } from "./pictures";

interface WorkerScope {
  onmessage: ((event: MessageEvent<WorkerRequest>) => void) | null;
  postMessage(message: WorkerReply, transfer?: Transferable[]): void;
}

const scope = self as unknown as WorkerScope;

async function handle(request: WorkerRequest): Promise<void> {
  try {
    if (request.kind === "analyse") {
      const depth = await decodeDepthMap(request.depth);
      const found = depth ? analyseCrop(depth, request.crop, request.image) : null;
      scope.postMessage({ id: request.id, ok: true, analysis: found }, found ? analysisBuffers(found) : []);
      return;
    }
    let pixels: Uint8ClampedArray | null = null;
    const size = plateSize(request.bitmap, request.image, request.crop, request.maxWidth);
    try {
      pixels = cropPixels(request.bitmap, request.image, request.crop, size);
    } finally {
      request.bitmap.close();
    }
    const plates = pixels ? platePixels(pixels, size[0], size[1], request.alpha) : null;
    scope.postMessage({ id: request.id, ok: true, plates }, plates ? [plates.back.buffer as ArrayBuffer, plates.front.buffer as ArrayBuffer] : []);
  } catch (error) {
    scope.postMessage({ id: request.id, ok: false, error: error instanceof Error ? error.message : String(error) });
  }
}

scope.onmessage = (event) => {
  void handle(event.data);
};
