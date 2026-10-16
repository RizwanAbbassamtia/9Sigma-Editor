/**
 * The React wrapper and the engine package meet at `createPreview(canvas, bundle, options)`
 * (docs/M5-CONTRACT.md section 4). The wrapper loads the engine on demand, so nothing else
 * checks at build time that the two sides still agree: these assignments do (`npm run
 * typecheck` fails when one side changes its shapes), and the test reads them at run time.
 */
import { describe, expect, it } from "vitest";

import type { createPreview } from "../../preview/engine";
import type { PreviewBundle, PreviewEvents, PreviewHandle, PreviewOptions, PreviewStatus } from "../../preview/types";
import type { PreviewEngine, PreviewEngineEvents, PreviewEngineFactory, PreviewEngineOptions, PreviewEngineStatus, TimelineBundle } from "../../types/preview";

/** `true` when every A is a B. */
type Fits<A, B> = [A] extends [B] ? true : false;

// The engine's entry point is a factory the wrapper can call (engineLoader.ts loads it) ...
const factoryFits: Fits<typeof createPreview, PreviewEngineFactory> = true;
// ... what the wrapper hands the engine is what the engine takes ...
const bundleFits: Fits<TimelineBundle, PreviewBundle> = true;
const optionsFit: Fits<PreviewEngineOptions, PreviewOptions> = true;
// ... and what the engine gives back is what the wrapper drives and listens to.
const handleFits: Fits<PreviewHandle, PreviewEngine> = true;
const statusFits: Fits<PreviewStatus, PreviewEngineStatus> = true;
const timeEventFits: Fits<PreviewEngineEvents["time"], PreviewEvents["time"]> = true;
const statusEventFits: Fits<PreviewEngineEvents["status"], PreviewEvents["status"]> = true;
const errorEventFits: Fits<PreviewEngineEvents["error"], PreviewEvents["error"]> = true;

describe("the engine's public API", () => {
  it("takes the wrapper's bundle and options and gives back the handle the wrapper drives", () => {
    expect([factoryFits, bundleFits, optionsFit, handleFits, statusFits, timeEventFits, statusEventFits, errorEventFits].every(Boolean)).toBe(true);
  });
});
