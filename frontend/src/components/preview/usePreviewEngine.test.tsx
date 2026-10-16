// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PreviewEngine, PreviewEngineFactory } from "../../types/preview";
import { parseTimeline } from "../../types/timeline";
import { bundleFromTimeline } from "./bundle";
import { createTimeStore } from "./timeStore";
import { READY_TIMEOUT_MS, START_MESSAGE, usePreviewEngine, type EngineOptionsBase } from "./usePreviewEngine";

type Handler = (...args: unknown[]) => void;

const BUNDLE = bundleFromTimeline(
  "p1",
  parseTimeline({
    project_id: "p1",
    aspect: "16:9",
    fps: 30,
    width: 1920,
    height: 1080,
    duration_s: 4,
    scenes: [{ index: 0, image: "06_images/scene_01.png", start_s: 0, end_s: 4, motion: { preset: "hold", start_rect: [0, 0, 1920, 1080], end_rect: [0, 0, 1920, 1080] } }],
  })!,
);

/** An engine that never draws its first frame on its own; the test emits its events. */
function silentEngine(): { factory: PreviewEngineFactory; emit: (event: string, ...args: unknown[]) => void } {
  const listeners = new Map<string, Set<Handler>>();
  const engine = {
    ready: new Promise<void>(() => undefined),
    play: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    setQuality: vi.fn(),
    setLoop: vi.fn(),
    skim: vi.fn(),
    destroy: vi.fn(),
    update: vi.fn(),
    on: (event: string, handler: Handler) => {
      const set = listeners.get(event) ?? new Set<Handler>();
      set.add(handler);
      listeners.set(event, set);
      return () => set.delete(handler);
    },
  };
  return {
    factory: () => engine as unknown as PreviewEngine,
    emit: (event, ...args) => listeners.get(event)?.forEach((handler) => handler(...args)),
  };
}

function start(factory: PreviewEngineFactory) {
  const host = document.createElement("div");
  const store = createTimeStore(0);
  const options: EngineOptionsBase = {
    projectId: "p1",
    scrubAudio: false,
    animatic: false,
    mockMedia: false,
    popupPreview: async () => null,
    pictureUrl: (path) => path,
    voiceSegmentUrl: (id) => id,
  };
  return renderHook(() => usePreviewEngine({ host, bundle: BUNDLE, quality: "auto", loop: null, options, store, factory }));
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("usePreviewEngine", () => {
  it("takes an engine that never says it drew as ready after a while", async () => {
    const { factory } = silentEngine();
    const { result } = start(factory);
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.ready).toBe(false);
    act(() => {
      vi.advanceTimersByTime(READY_TIMEOUT_MS);
    });
    expect(result.current.ready).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("calls it a failed start when the engine said what went wrong before drawing anything", async () => {
    const { factory, emit } = silentEngine();
    const { result } = start(factory);
    await act(async () => {
      await Promise.resolve();
    });
    act(() => emit("error", "This computer cannot draw the live preview. Watch the last render instead."));
    expect(result.current.notice).toBe("This computer cannot draw the live preview. Watch the last render instead.");
    expect(result.current.error).toBeNull();
    act(() => {
      vi.advanceTimersByTime(READY_TIMEOUT_MS);
    });
    expect(result.current.error).toBe("This computer cannot draw the live preview. Watch the last render instead.");
    expect(result.current.ready).toBe(false);
  });

  it("reports an engine that cannot be made", async () => {
    const { result } = start(() => {
      throw new Error("no canvas");
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.error).toBe(START_MESSAGE);
  });
});
