import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AudioEngine, IDLE_SUSPEND_MS, MAX_LOADS, RETRY_AFTER_MS, STEM_CHUNK_S, loadOrder, parseWavHeader, stemPieceAt, wavHeader, withWavHeader } from "../audio";
import type { RenderDescription, VoiceClip } from "../types";
import { FakeContext, contexts, flush, handFetch, wavBytes, type HandCall } from "./fakeAudio";

const STEM = "/api/projects/p1/files/07_edit/music_ducked.wav?v=k1";

/** 30 sentences of 4 s each, one after the other. */
function clips(): VoiceClip[] {
  return Array.from({ length: 30 }, (_, i) => ({ id: `s${i + 1}`, url: `/seg/s${i + 1}.wav`, offsetS: 0, durationS: 4, atS: i * 4 }));
}

function description(stem: string | null = STEM, count = 30): RenderDescription {
  return {
    durationS: 120,
    audio: { clips: clips().slice(0, count), stemUrl: stem, envelopeUrl: null, music: null, gainDb: 0, gainCurve: null },
  } as unknown as RenderDescription;
}

/** The stem's first 8 KB: a header saying 120 s of 48 kHz stereo 16-bit PCM follow. */
function stemHead(): ArrayBuffer {
  const header = wavHeader({ channels: 2, sampleRate: 48000, bitsPerSample: 16, format: 1 }, 120 * 48000 * 4);
  const out = new Uint8Array(8192);
  out.set(new Uint8Array(header), 0);
  return out.buffer;
}

/** Answers a call the way the server would (sentence WAVs, the stem's header and ranges). */
function answer(call: HandCall): void {
  if (call.url.startsWith("/seg/")) return call.answer(wavBytes(4));
  if (call.range === "bytes=0-8191") return call.answer(stemHead());
  const [first, last] = call.range!.replace("bytes=", "").split("-").map(Number);
  call.answer(new ArrayBuffer(last! - first! + 1));
}

describe("loading the sound around the playhead", () => {
  beforeEach(() => {
    contexts.length = 0;
    vi.stubGlobal("window", { AudioContext: FakeContext });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("orders the clip under the playhead and the music piece there first", () => {
    const order = loadOrder(clips(), 41, true, 120);
    expect(order[0]).toEqual({ kind: "clip", url: "/seg/s11.wav" });
    expect(order[1]).toEqual({ kind: "stem", index: 8 });
    // the window follows, without the clip under the playhead again
    expect(order.filter((item) => item.kind === "clip" && item.url === "/seg/s11.wav")).toHaveLength(1);
    expect(order.length).toBe(1 + 1 + 18);
    // near the end of a piece, the next piece too; a clip starting within 0.25 s counts as under
    const late = loadOrder(clips(), 43.9, true, 120);
    expect(late.slice(0, 4)).toEqual([
      { kind: "clip", url: "/seg/s11.wav" },
      { kind: "clip", url: "/seg/s12.wav" },
      { kind: "stem", index: 8 },
      { kind: "stem", index: 9 },
    ]);
    expect(loadOrder(clips(), 41, false, 120).some((item) => item.kind === "stem")).toBe(false);
    expect(stemPieceAt(41)).toBe(Math.floor(41 / STEM_CHUNK_S));
  });

  it("puts a WAV header in front of a range of the stem", () => {
    const info = { channels: 2, sampleRate: 48000, bitsPerSample: 16, format: 1 };
    const bytes = new Uint8Array(4 * 480).fill(7).buffer;
    const wav = withWavHeader(info, bytes);
    expect(parseWavHeader(wav)).toEqual({ ...info, dataOffset: 44, dataBytes: 1920 });
    expect(new Uint8Array(wav, 44)).toEqual(new Uint8Array(bytes));
    expect(new Uint8Array(bytes)[0]).toBe(7); // the range itself is left as it was
    expect(parseWavHeader(wavHeader({ ...info, bitsPerSample: 32, format: 3 }, 8))).toMatchObject({ format: 3, bitsPerSample: 32 });
  });

  it("decodes a few at a time, the playhead's sound first", async () => {
    const { calls, fetchFn, mostOpen } = handFetch();
    const audio = new AudioEngine({ fetch: fetchFn });
    audio.setDescription(description());
    audio.preload(41);
    await flush();
    expect(calls.map((call) => call.url)[0]).toBe("/seg/s11.wav");
    expect(calls[1]).toMatchObject({ url: STEM, range: "bytes=0-8191" });
    expect(calls.length).toBe(MAX_LOADS);
    // as each one is in, the next starts; never more than MAX_LOADS at once
    for (let round = 0; round < 40 && calls.some((call) => !answered.has(call)); round++) {
      for (const call of calls.filter((item) => !answered.has(item))) {
        answered.add(call);
        answer(call);
      }
      await flush();
    }
    expect(mostOpen()).toBeLessThanOrEqual(MAX_LOADS);
    // the music piece under the playhead went through the browser's decoder with a WAV header
    const ctx = contexts[0]!;
    const stemPiece = ctx.decoded.find((bytes) => parseWavHeader(bytes)?.channels === 2);
    expect(stemPiece).toBeDefined();
    expect(parseWavHeader(stemPiece!)).toMatchObject({ sampleRate: 48000, bitsPerSample: 16, dataBytes: STEM_CHUNK_S * 48000 * 4 });
    expect(calls.find((call) => call.range?.startsWith(`bytes=${44 + 8 * STEM_CHUNK_S * 48000 * 4}-`))).toBeDefined();
    audio.destroy();
  });

  it("is ready once the sound under the playhead is decoded, and starts it there", async () => {
    const { calls, fetchFn } = handFetch();
    const audio = new AudioEngine({ fetch: fetchFn });
    audio.setDescription(description());
    let ready: boolean | null = null;
    void audio.ready(41, 5000).then((value) => (ready = value));
    await flush();
    expect(ready).toBeNull();
    for (let round = 0; round < 10; round++) {
      for (const call of calls.filter((item) => !answered.has(item))) {
        answered.add(call);
        answer(call);
      }
      await flush();
      if (ready !== null) break;
    }
    expect(ready).toBe(true);
    const ctx = contexts[0]!;
    expect(ctx.state).toBe("running");
    await audio.play(41);
    // the sentence under the playhead and the music piece start at the playhead: nothing skipped
    const voice = ctx.started.find((call) => call.buffer?.numberOfChannels === 1)!;
    const music = ctx.started.find((call) => call.buffer?.numberOfChannels === 2)!;
    expect(voice.offset).toBeCloseTo(1, 9);
    expect(music.offset).toBeCloseTo(1, 9);
    expect(voice.when).toBeCloseTo(music.when, 9);
    audio.destroy();
  });

  it("keeps the sound device running while the preview is in use, and stops it after a minute idle", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
    try {
      const { fetchFn } = handFetch();
      const audio = new AudioEngine({ fetch: fetchFn });
      audio.setDescription(description(null, 1));
      audio.prepare();
      const ctx = contexts[0]!;
      await vi.advanceTimersByTimeAsync(10);
      expect(ctx.state).toBe("running");
      await vi.advanceTimersByTimeAsync(IDLE_SUSPEND_MS - 1000);
      audio.preload(3); // a seek: still in use
      await vi.advanceTimersByTimeAsync(IDLE_SUSPEND_MS - 1000);
      expect(ctx.state).toBe("running");
      await vi.advanceTimersByTimeAsync(2000);
      expect(ctx.state).toBe("suspended");
      audio.preload(2); // the next seek wakes it before Play is pressed
      await vi.advanceTimersByTimeAsync(10);
      expect(ctx.state).toBe("running");
      audio.destroy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("waits longer for the sound device than for the sound itself", async () => {
    let resumed: () => void = () => undefined;
    class SlowDevice extends FakeContext {
      override resume(): Promise<void> {
        return new Promise((resolve) => {
          resumed = () => {
            this.state = "running";
            resolve();
          };
        });
      }
    }
    vi.stubGlobal("window", { AudioContext: SlowDevice });
    const { fetchFn } = handFetch();
    const audio = new AudioEngine({ fetch: fetchFn });
    audio.setDescription(description(null, 0));
    let answer: boolean | null = null;
    void audio.ready(1, 20, 2000).then((value) => (answer = value));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(answer).toBeNull(); // past the sound's 20 ms: still waiting for the device
    resumed();
    await flush();
    expect(answer).toBe(true);
    // a device that does not start: given up after its own wait
    const stuck = new AudioEngine({ fetch: fetchFn });
    stuck.setDescription(description(null, 0));
    await expect(stuck.ready(1, 20, 60)).resolves.toBe(false);
    audio.destroy();
    stuck.destroy();
  });

  it("gives up waiting after the time it is given", async () => {
    const { fetchFn } = handFetch();
    const audio = new AudioEngine({ fetch: fetchFn });
    audio.setDescription(description(null));
    await expect(audio.ready(10, 30)).resolves.toBe(false);
    audio.destroy();
  });

  it("asks again for a clip that could not be loaded, after a while", async () => {
    let now = 1000;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const { calls, fetchFn } = handFetch();
    const audio = new AudioEngine({ fetch: fetchFn });
    audio.setDescription(description(null, 1));
    audio.preload(1);
    await flush();
    const first = calls.filter((call) => call.url === "/seg/s1.wav");
    expect(first).toHaveLength(1);
    first[0]!.answer(null, 404);
    await flush();
    audio.preload(1);
    await flush();
    expect(calls.filter((call) => call.url === "/seg/s1.wav")).toHaveLength(1);
    now += RETRY_AFTER_MS + 1;
    audio.preload(1);
    await flush();
    expect(calls.filter((call) => call.url === "/seg/s1.wav")).toHaveLength(2);
    audio.destroy();
  });
});

const answered = new Set<HandCall>();
