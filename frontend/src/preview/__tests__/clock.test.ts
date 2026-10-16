import { afterEach, describe, expect, it, vi } from "vitest";

import { AutoQuality, FRAME_FALLBACK_MS, FramePacer, PlaybackClock, frameNumber, requestFrame } from "../clock";

describe("the clock", () => {
  it("draws the renderer's frame for any time", () => {
    expect(frameNumber(0, 30)).toBe(0);
    expect(frameNumber(1 / 30, 30)).toBe(1);
    expect(frameNumber(0.1, 30)).toBe(3);
    expect(frameNumber(2 / 3, 30)).toBe(20);
    expect(frameNumber(0.0333, 30)).toBe(0);
  });

  it("follows the audio clock while it runs, else the performance clock", () => {
    let perf = 1000;
    let audioTime = 5;
    const audio = { running: true, now: () => audioTime };
    const clock = new PlaybackClock(audio, () => perf);
    clock.start(5);
    audioTime = 6.5;
    expect(clock.time()).toBe(6.5);
    audio.running = false;
    perf = 3000;
    expect(clock.time()).toBe(7);
    expect(clock.stop()).toBe(7);
    perf = 9000;
    expect(clock.time()).toBe(7);
  });

  it("reads the clock at each animation frame's own time, so every frame shows for two refreshes", () => {
    // a 60 Hz screen; the audio clock is read with a few milliseconds of noise and the video's
    // frame starts fall right next to the refreshes (the worst phase for a stutter)
    let seed = 7;
    const noise = () => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return (seed / 2147483648 - 0.5) * 0.006;
    };
    const refresh = 1000 / 60;
    const start = 1000;
    const audio = { running: true, now: () => 0, nowAt: (ms: number) => 10 - 0.002 + (ms - start) / 1000 + noise() };
    const clock = new PlaybackClock(audio, () => start);
    clock.start(10);
    const numbers: number[] = [];
    for (let k = 0; k < 600; k++) numbers.push(frameNumber(clock.frameTime(start + k * refresh), 30));
    // after the first few refreshes, a new frame every second refresh, never one held for three
    const runs: number[] = [];
    let run = 1;
    for (let k = 31; k < numbers.length; k++) {
      if (numbers[k] === numbers[k - 1]) run += 1;
      else {
        runs.push(run);
        run = 1;
      }
      expect(numbers[k]! - numbers[k - 1]!).toBeLessThanOrEqual(1);
    }
    expect(runs.slice(1).every((length) => length === 2)).toBe(true);
    // the playhead stays within a few milliseconds of the audio clock
    const t = clock.frameTime(start + 600 * refresh);
    expect(Math.abs(t - (10 - 0.002 + (600 * refresh) / 1000))).toBeLessThan(0.004);
  });

  it("takes a jump of the audio clock at once and never runs backwards", () => {
    let audioTime = 5;
    const audio = { running: true, now: () => audioTime, nowAt: () => audioTime };
    const clock = new PlaybackClock(audio, () => 0);
    clock.start(5);
    expect(clock.frameTime(0)).toBe(5);
    audioTime = 7;
    expect(clock.frameTime(16)).toBe(7);
    audioTime = 6.99;
    expect(clock.frameTime(33)).toBeGreaterThanOrEqual(7);
    // without the audio clock: the performance clock at the frame's time
    audio.running = false;
    clock.start(2);
    expect(clock.frameTime(500)).toBeCloseTo(2.5, 9);
  });

  describe("requestFrame", () => {
    afterEach(() => {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    });

    it("runs once per animation frame, or from a timer when the frame is held back", () => {
      vi.useFakeTimers();
      const frames: ((time: number) => void)[] = [];
      vi.stubGlobal("requestAnimationFrame", (callback: (time: number) => void) => frames.push(callback));
      vi.stubGlobal("cancelAnimationFrame", () => undefined);
      const times: number[] = [];
      requestFrame((time) => times.push(time));
      frames[0]!(123);
      vi.advanceTimersByTime(FRAME_FALLBACK_MS * 2);
      expect(times).toEqual([123]);
      // an animation frame that never comes (a view off screen that says it is visible)
      requestFrame((time) => times.push(time));
      vi.advanceTimersByTime(FRAME_FALLBACK_MS + 1);
      expect(times).toHaveLength(2);
      frames[1]!(456);
      expect(times).toHaveLength(2);
      // cancelled before either came
      const cancel = requestFrame((time) => times.push(time));
      cancel();
      vi.advanceTimersByTime(FRAME_FALLBACK_MS * 2);
      frames[2]!(789);
      expect(times).toHaveLength(2);
    });
  });

  it("counts dropped frames", () => {
    const pacer = new FramePacer();
    expect(pacer.next(0, 30)).toBe(0);
    expect(pacer.next(0.01, 30)).toBeNull();
    expect(pacer.next(1 / 30, 30)).toBe(1);
    expect(pacer.next(4 / 30, 30)).toBe(4);
    expect(pacer.dropped).toBe(2);
    expect(pacer.shown).toBe(3);
  });

  it("Auto drops to Half after 3 slow frames and returns after 5 s of headroom", () => {
    const auto = new AutoQuality();
    expect(auto.report(40, 0)).toBe("full");
    expect(auto.report(20, 10)).toBe("full");
    expect(auto.report(40, 20)).toBe("full");
    expect(auto.report(40, 30)).toBe("half");
    expect(auto.report(5, 1000)).toBe("half");
    expect(auto.report(5, 4000)).toBe("half");
    expect(auto.report(5, 5031)).toBe("full");
    // a second drop waits twice as long before trying Full again
    auto.report(50, 6000);
    auto.report(50, 6010);
    expect(auto.report(50, 6020)).toBe("half");
    expect(auto.report(5, 11021)).toBe("half");
    expect(auto.report(5, 16021)).toBe("full");
  });
});
