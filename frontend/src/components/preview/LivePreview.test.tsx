// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef, type ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { PreviewEngine, PreviewEngineFactory, TimelineBundle } from "../../types/preview";
import { parseTimeline } from "../../types/timeline";
import { bundleFromTimeline } from "./bundle";
import { LivePreview, type LivePreviewHandle } from "./LivePreview";
import { describeFrame } from "./previewTime";

type Handler = (...args: unknown[]) => void;

function makeBundle(transition = "film_dissolve"): TimelineBundle {
  const timeline = parseTimeline({
    project_id: "p1",
    format: "long",
    aspect: "16:9",
    fps: 30,
    width: 1920,
    height: 1080,
    duration_s: 10,
    scenes: [
      { index: 0, image: "06_images/scene_01.png", start_s: 0, end_s: 4, motion: { preset: "hold", start_rect: [0, 0, 1920, 1080], end_rect: [0, 0, 1920, 1080] }, transition_out: { type: transition, duration_s: 0.6 } },
      { index: 1, image: "06_images/scene_02.png", start_s: 4, end_s: 10, motion: { preset: "hold", start_rect: [0, 0, 1920, 1080], end_rect: [0, 0, 1920, 1080] }, transition_out: { type: "film_dissolve", duration_s: 1 } },
    ],
    popups: [{ scene: 0, text: "Fifty years earlier", start_s: 1, end_s: 3, anim: "pop" }],
    captions: { enabled: true, cues: [{ start_s: 0.5, end_s: 2, text: "It began\\Nin winter" }] },
  });
  return bundleFromTimeline("p1", timeline!);
}

interface FakeEngine {
  factory: ReturnType<typeof vi.fn> & PreviewEngineFactory;
  engines: Array<PreviewEngine & { emit: (event: string, ...args: unknown[]) => void }>;
}

function fakeEngines({ withUpdate = true, ready }: { withUpdate?: boolean; ready?: Promise<void> } = {}): FakeEngine {
  const engines: FakeEngine["engines"] = [];
  const factory = vi.fn((_canvas: HTMLCanvasElement) => {
    const listeners = new Map<string, Set<Handler>>();
    const emit = (event: string, ...args: unknown[]) => {
      for (const handler of listeners.get(event) ?? []) handler(...args);
    };
    const engine = {
      emit,
      play: vi.fn(async () => emit("play")),
      pause: vi.fn(() => emit("pause")),
      seek: vi.fn((seconds: number) => emit("time", seconds)),
      setQuality: vi.fn(),
      setLoop: vi.fn(),
      skim: vi.fn(),
      destroy: vi.fn(),
      on: vi.fn((event: string, handler: Handler) => {
        const set = listeners.get(event) ?? new Set<Handler>();
        set.add(handler);
        listeners.set(event, set);
        return () => set.delete(handler);
      }),
      ...(withUpdate ? { update: vi.fn() } : {}),
      ...(ready ? { ready } : {}),
    };
    engines.push(engine as unknown as FakeEngine["engines"][number]);
    return engine as unknown as PreviewEngine;
  });
  return { factory: factory as FakeEngine["factory"], engines };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function currentTime(): string {
  return screen.getByTestId("preview-current-time").textContent ?? "";
}

beforeAll(() => {
  // jsdom has no media playback.
  window.HTMLMediaElement.prototype.pause = vi.fn();
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
});

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("no network in tests"))));
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LivePreview", () => {
  it("starts the engine on its own canvas and shows the Live chip and the clock", async () => {
    const { factory, engines } = fakeEngines();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(factory).toHaveBeenCalledTimes(1));
    const [canvas, bundle, options] = factory.mock.calls[0]!;
    expect(canvas).toBeInstanceOf(HTMLCanvasElement);
    expect(screen.getByTestId("preview-canvas-host").contains(canvas as Node)).toBe(true);
    expect((bundle as TimelineBundle).timeline.scenes).toHaveLength(2);
    expect(options).toMatchObject({ projectId: "p1", quality: "auto", loop: null, animatic: false });
    expect(screen.getByText("Live")).toBeTruthy();
    expect(currentTime()).toBe("0:00.0");
    expect(screen.getByRole("timer").textContent).toContain("0:10.0");
    expect(engines[0]!.seek).toHaveBeenCalledWith(0);
  });

  it("plays, pauses and follows the engine's clock in tenths", async () => {
    const { factory, engines } = fakeEngines();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    const engine = engines[0]!;
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(engine.play).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy());
    act(() => engine.emit("time", 3.47));
    expect(currentTime()).toBe("0:03.4");
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(engine.pause).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Play" })).toBeTruthy());
    act(() => engine.emit("play"));
    act(() => engine.emit("ended"));
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
  });

  it("steps frames, jumps cuts and marks a loop range from the keyboard", async () => {
    const { factory, engines } = fakeEngines();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    const engine = engines[0]!;
    const monitor = screen.getByRole("region", { name: "Preview" });
    fireEvent.keyDown(monitor, { key: "ArrowRight" });
    expect(engine.seek).toHaveBeenLastCalledWith(1 / 30);
    fireEvent.keyDown(monitor, { key: "ArrowDown" });
    expect(engine.seek).toHaveBeenLastCalledWith(4);
    fireEvent.keyDown(monitor, { key: "ArrowRight", shiftKey: true });
    expect(engine.seek).toHaveBeenLastCalledWith(5);
    fireEvent.keyDown(monitor, { key: "i" });
    fireEvent.keyDown(monitor, { key: "End" });
    expect(engine.seek).toHaveBeenLastCalledWith(299 / 30);
    fireEvent.keyDown(monitor, { key: "o" });
    expect(screen.getByTitle("The in-out range").textContent).toBe("0:05.0-0:09.9");
    fireEvent.keyDown(monitor, { key: "l", ctrlKey: true });
    await waitFor(() => expect(engine.setLoop).toHaveBeenLastCalledWith({ in_s: 5, out_s: 299 / 30 }));
    expect(screen.getByRole("button", { name: "Loop the in-out range" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(monitor, { key: "x", altKey: true });
    await waitFor(() => expect(engine.setLoop).toHaveBeenLastCalledWith({ in_s: 0, out_s: 10 }));
    fireEvent.keyDown(monitor, { key: "Home" });
    expect(engine.seek).toHaveBeenLastCalledWith(0);
    fireEvent.keyDown(monitor, { key: " " });
    expect(engine.play).toHaveBeenCalledTimes(1);
  });

  it("turns engine status into chips", async () => {
    const { factory, engines } = fakeEngines();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    act(() => engines[0]!.emit("status", { renderer: "canvas2d", simplified: true, mockMedia: true }));
    expect(screen.getByText("Simplified preview")).toBeTruthy();
    expect(screen.getByText("Mock media")).toBeTruthy();
    act(() => engines[0]!.emit("status", { restarting: true }));
    expect(screen.getAllByText("Restarting the preview...").length).toBeGreaterThan(0);
    act(() => engines[0]!.emit("status", { restarting: false, activeQuality: "half" }));
    expect(screen.getByText("Half quality")).toBeTruthy();
    act(() => engines[0]!.emit("error", "The picture for scene 2 could not be loaded."));
    expect(screen.getByText("The picture for scene 2 could not be loaded.")).toBeTruthy();
  });

  it("waits for the engine's first frame before it lets you play", async () => {
    let firstFrame!: () => void;
    const ready = new Promise<void>((resolve) => {
      firstFrame = resolve;
    });
    const { factory, engines } = fakeEngines({ ready });
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    expect(screen.getByText("Starting the preview...")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Play" }).hasAttribute("disabled")).toBe(true);
    await act(async () => {
      firstFrame();
      await ready;
    });
    expect(screen.queryByText("Starting the preview...")).toBeNull();
    expect(screen.getByRole("button", { name: "Play" }).hasAttribute("disabled")).toBe(false);
  });

  it("says so when the engine cannot start", async () => {
    const failed = Promise.reject(new Error("no WebGL2 and no Canvas 2D"));
    failed.catch(() => undefined);
    const { factory, engines } = fakeEngines({ ready: failed });
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(/The live preview could not start/)).toBeTruthy());
  });

  it("shows a problem met while drawing under the picture, until it is hidden", async () => {
    const { factory, engines } = fakeEngines();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    act(() => engines[0]!.emit("error", "The picture for scene 2 could not be loaded."));
    expect(screen.getByRole("status").textContent).toContain("The picture for scene 2 could not be loaded.");
    // the picture and the transport stay usable
    expect(screen.queryByText(/could not start/)).toBeNull();
    expect(screen.getByRole("button", { name: "Play" }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Hide this message" }));
    expect(screen.queryByText("The picture for scene 2 could not be loaded.")).toBeNull();
  });

  it("asks the server for pictures of known size, and for the whole file otherwise", async () => {
    const { factory, engines } = fakeEngines();
    const bundle = makeBundle();
    const sized = { ...bundle, pictures: [{ ...bundle.pictures[0]!, width: 2560, height: 1440 }, bundle.pictures[1]!] };
    render(<LivePreview projectId="p1" bundle={sized} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    const options = factory.mock.calls[0]![2] as { pictureUrl: (path: string, height: number) => string };
    expect(options.pictureUrl("06_images/scene_01.png", 1080)).toBe("/api/projects/p1/media/thumb?path=06_images%2Fscene_01.png&h=1080");
    expect(options.pictureUrl("06_images/scene_02.png", 1080)).toBe("/api/projects/p1/files/06_images/scene_02.png");
  });

  it("changes quality and remembers it", async () => {
    const { factory, engines } = fakeEngines();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Preview quality"), { target: { value: "half" } });
    expect(engines[0]!.setQuality).toHaveBeenLastCalledWith("half");
    expect(window.localStorage.getItem("sge-preview-quality")).toBe("half");
  });

  it("hands a changed bundle to the engine without starting over", async () => {
    const { factory, engines } = fakeEngines();
    const { rerender } = render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    const next = makeBundle("whip_left");
    rerender(<LivePreview projectId="p1" bundle={next} aspect="16:9" engineFactory={factory} />);
    await waitFor(() => expect(engines[0]!.update).toHaveBeenCalledWith(next));
    expect(factory).toHaveBeenCalledTimes(1);
  });

  it("starts a fresh engine for a changed bundle when the engine cannot update", async () => {
    const { factory, engines } = fakeEngines({ withUpdate: false });
    const { rerender } = render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    act(() => engines[0]!.emit("time", 6));
    rerender(<LivePreview projectId="p1" bundle={makeBundle("whip_left")} aspect="16:9" engineFactory={factory} />);
    await waitFor(() => expect(engines).toHaveLength(2));
    expect(engines[0]!.destroy).toHaveBeenCalled();
    expect(engines[1]!.seek).toHaveBeenCalledWith(6);
    expect(screen.getByTestId("preview-canvas-host").querySelectorAll("canvas")).toHaveLength(1);
  });

  it("switches to the last render and back, keeping the playhead", async () => {
    const { factory, engines } = fakeEngines();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} proxySrc="/proxy.mp4" proxyNote="Low-resolution render" />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    act(() => engines[0]!.emit("time", 2.5));
    const toggle = screen.getByRole("button", { name: "Watch the last render" });
    fireEvent.click(toggle);
    expect(engines[0]!.pause).toHaveBeenCalled();
    expect(document.querySelector("video")?.getAttribute("src")).toBe("/proxy.mp4");
    expect(screen.getByText("Last render")).toBeTruthy();
    expect(screen.getByText("Low-resolution render")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Watch the last render" }));
    await waitFor(() => expect(screen.getByTestId("preview-canvas-host").querySelector("canvas")).toBeTruthy());
    expect(factory).toHaveBeenCalledTimes(1);
    expect(engines[0]!.seek).toHaveBeenLastCalledWith(2.5);
  });

  it("plays the last render while there is nothing to draw", () => {
    render(<LivePreview projectId="p1" bundle={null} aspect="16:9" proxySrc="/proxy.mp4" unavailable="Not built yet." />, { wrapper });
    expect(document.querySelector("video")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Watch the last render" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("says why there is nothing to play", () => {
    render(<LivePreview projectId="p1" bundle={null} aspect="16:9" unavailable="The live preview appears when the edit step has built the timeline." />, { wrapper });
    expect(screen.getByText("The live preview appears when the edit step has built the timeline.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Play" }).hasAttribute("disabled")).toBe(true);
  });

  it("answers the review panel through its handle and reports the time", async () => {
    const { factory, engines } = fakeEngines();
    const ref = createRef<LivePreviewHandle>();
    const onTimeChange = vi.fn();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} controllerRef={ref} onTimeChange={onTimeChange} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    act(() => ref.current!.seek(4.05, { play: true }));
    expect(engines[0]!.seek).toHaveBeenLastCalledWith(4.05);
    expect(engines[0]!.play).toHaveBeenCalled();
    expect(onTimeChange).toHaveBeenLastCalledWith(4.05);
    expect(ref.current!.time()).toBe(4.05);
    expect(ref.current!.monitor()).toBe("live");
    // play around a cut: from 2 s before to 2 s after, then pause
    act(() => ref.current!.playAround(4));
    expect(engines[0]!.seek).toHaveBeenLastCalledWith(2);
    act(() => engines[0]!.emit("time", 6.01));
    expect(engines[0]!.pause).toHaveBeenCalled();
  });

  it("shows the animatic's chips", async () => {
    const { factory, engines } = fakeEngines();
    render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} title="Animatic" animatic estimatedTiming />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    expect(screen.getAllByText("Animatic").length).toBeGreaterThan(0);
    expect(screen.getByText("Estimated timing")).toBeTruthy();
    expect(factory.mock.calls[0]![2]).toMatchObject({ animatic: true });
  });

  it("destroys the engine with the monitor", async () => {
    const { factory, engines } = fakeEngines();
    const { unmount } = render(<LivePreview projectId="p1" bundle={makeBundle()} aspect="16:9" engineFactory={factory} />, { wrapper });
    await waitFor(() => expect(engines).toHaveLength(1));
    unmount();
    expect(engines[0]!.destroy).toHaveBeenCalled();
  });
});

describe("describeFrame", () => {
  it("reads the scene, popup and caption at a time", () => {
    const bundle = makeBundle();
    expect(describeFrame(bundle, 1.5)).toBe("Scene 1 of 2 at 0:01.5. Popup: Fifty years earlier. Caption: It began in winter");
    expect(describeFrame(bundle, 5)).toBe("Scene 2 of 2 at 0:05.0.");
    expect(describeFrame(null, 1)).toBe("Preview");
  });
});
