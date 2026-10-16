// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createPreview } from "../engine";
import type { PreviewBundle, PreviewScene } from "../types";
import { FakeContext, contexts, handFetch, wavBytes, type HandCall } from "./fakeAudio";

/** A 2D context that does nothing but remember the text drawn on it. */
function fakeContext(canvas: HTMLCanvasElement) {
  const state: Record<string | symbol, unknown> = { canvas, texts: [] as string[] };
  return new Proxy(state, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (prop === "fillText") return (text: string) => (target.texts as string[]).push(text);
      if (prop === "measureText") return () => ({ width: 10, actualBoundingBoxAscent: 8, actualBoundingBoxDescent: 2, fontBoundingBoxAscent: 9, fontBoundingBoxDescent: 3 });
      if (prop === "getImageData" || prop === "createImageData") return (_x: number, _y: number, w = 1, h = 1) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h });
      return () => undefined;
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  });
}

const contextsOf = new WeakMap<HTMLCanvasElement, ReturnType<typeof fakeContext>>();

function scenes(count: number, seconds: number): PreviewScene[] {
  return Array.from({ length: count }, (_, index) => ({
    index,
    image: "",
    start_s: index * seconds,
    end_s: (index + 1) * seconds,
    motion: { preset: "hold", start_rect: [0, 0, 1, 1], end_rect: [0, 0, 1, 1] },
    transition_out: { type: "film_dissolve", duration_s: 0.5 },
  }));
}

function bundle(count: number, seconds: number, voice = false): PreviewBundle {
  return {
    timeline: {
      project_id: "p1",
      aspect: "16:9",
      fps: 30,
      width: 1920,
      height: 1080,
      duration_s: count * seconds,
      scenes: scenes(count, seconds),
      voice: voice ? { path: "05_voice/voice.wav", start_s: 0, segments: [{ sentence_id: "s1", src_start_s: 0, src_end_s: 4, at_s: 0 }] } : { path: "" },
    },
    sentences: voice ? [{ id: "s1", text: "one", start_s: 0, end_s: 4 }] : [],
  };
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("the engine", () => {
  let created: HTMLCanvasElement[] = [];

  beforeEach(() => {
    created = [];
    contexts.length = 0;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
      let ctx = contextsOf.get(this);
      if (!ctx) {
        ctx = fakeContext(this);
        contextsOf.set(this, ctx);
      }
      return ctx as unknown as RenderingContext;
    } as never);
    const createElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((name: string) => {
      const element = createElement(name);
      if (name === "canvas") created.push(element as HTMLCanvasElement);
      return element;
    }) as typeof document.createElement);
    vi.stubGlobal("AudioContext", FakeContext);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps one slate per scene slot however many scenes show one", async () => {
    const { fetchFn } = handFetch();
    const canvas = document.createElement("canvas");
    const preview = createPreview(canvas, bundle(60, 2), { renderer: "canvas2d", audio: false, fetch: fetchFn });
    await preview.ready;
    await preview.renderFrame(1);
    await preview.renderFrame(3);
    const early = created.length;
    for (let scene = 0; scene < 60; scene++) await preview.renderFrame(scene * 2 + 1);
    // no canvas per scene: the slot's slate is drawn again with the scene's number
    expect(created.length).toBe(early);
    const texts = created.flatMap((element) => (contextsOf.get(element)?.texts as string[] | undefined) ?? []);
    expect(texts.at(-1)).toBe("Picture missing (scene 60)");
    expect(new Set(texts).size).toBe(60);
    preview.destroy();
  });

  it("holds the picture on Play until the sound under the playhead is ready, then starts both there", async () => {
    const { calls, fetchFn } = handFetch();
    const canvas = document.createElement("canvas");
    const preview = createPreview(canvas, bundle(1, 10, true), { renderer: "canvas2d", fetch: fetchFn, voiceSegmentUrl: (sid) => `/seg/${sid}.wav` });
    await preview.ready;
    preview.seek(1);
    const playing = preview.play();
    await wait(20);
    expect(preview.isPlaying()).toBe(true);
    expect(preview.currentTime()).toBe(1);
    const ctx = contexts[0]!;
    expect(ctx.started).toEqual([]);
    const clip = calls.find((call: HandCall) => call.url === "/seg/s1.wav")!;
    expect(clip).toBeDefined();
    clip.answer(wavBytes(4));
    await playing;
    // the sentence under the playhead starts at the playhead (to the millisecond the picture
    // has moved meanwhile): none of it is skipped
    expect(ctx.started).toHaveLength(1);
    expect(ctx.started[0]!.offset).toBeCloseTo(1, 2);
    preview.pause();
    expect(preview.currentTime()).toBeGreaterThanOrEqual(1);
    preview.destroy();
  });

  it("keeps the playhead when paused while Play is still waiting for the sound", async () => {
    const { calls, fetchFn } = handFetch();
    const canvas = document.createElement("canvas");
    const preview = createPreview(canvas, bundle(1, 10, true), { renderer: "canvas2d", fetch: fetchFn, voiceSegmentUrl: (sid) => `/seg/${sid}.wav` });
    await preview.ready;
    preview.seek(2);
    const playing = preview.play();
    await wait(10);
    preview.pause();
    expect(preview.isPlaying()).toBe(false);
    expect(preview.currentTime()).toBe(2);
    calls.find((call: HandCall) => call.url === "/seg/s1.wav")?.answer(wavBytes(4));
    await playing;
    await wait(20);
    expect(preview.isPlaying()).toBe(false);
    expect(contexts[0]?.started ?? []).toEqual([]);
    preview.destroy();
  });
});
