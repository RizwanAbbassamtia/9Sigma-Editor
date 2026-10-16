import { describe, expect, it } from "vitest";

import { clampTime, cutTimes, formatRange, formatTenths, frameIndex, lastFrameTime, nextCut, previousCut, sceneAt, stepFrames } from "./previewTime";

const timeline = {
  scenes: [
    { start_s: 0, end_s: 2.6 },
    { start_s: 2.6, end_s: 8.4 },
    { start_s: 8.4, end_s: 13.1 },
  ],
} as never;

describe("formatTenths", () => {
  it("shows minutes, seconds and tenths, cut not rounded", () => {
    expect(formatTenths(0)).toBe("0:00.0");
    expect(formatTenths(0.96)).toBe("0:00.9");
    expect(formatTenths(65.6)).toBe("1:05.6");
    expect(formatTenths(124)).toBe("2:04.0");
    expect(formatTenths(3723.45)).toBe("1:02:03.4");
  });

  it("treats bad values as zero", () => {
    expect(formatTenths(-3)).toBe("0:00.0");
    expect(formatTenths(Number.NaN)).toBe("0:00.0");
  });

  it("writes ranges", () => {
    expect(formatRange(60.8, 71.2)).toBe("1:00.8 to 1:11.2");
  });
});

describe("the frame grid", () => {
  it("uses the renderer's rule floor(t x fps + 1e-6)", () => {
    expect(frameIndex(1, 30)).toBe(30);
    expect(frameIndex(0.0333, 30)).toBe(0);
    // within a millionth of a frame counts as that frame
    expect(frameIndex(0.0333333, 30)).toBe(1);
    expect(frameIndex(1 / 30, 30)).toBe(1);
  });

  it("steps whole frames and stays inside the video", () => {
    expect(stepFrames(1, 30, 1, 10)).toBeCloseTo(31 / 30, 9);
    expect(stepFrames(1, 30, -1, 10)).toBeCloseTo(29 / 30, 9);
    expect(stepFrames(0, 30, -1, 10)).toBe(0);
    expect(stepFrames(9.99, 30, 5, 10)).toBeCloseTo(lastFrameTime(10, 30), 9);
    expect(lastFrameTime(10, 30)).toBeCloseTo(299 / 30, 9);
  });

  it("clamps times", () => {
    expect(clampTime(-1, 5)).toBe(0);
    expect(clampTime(9, 5)).toBe(5);
    expect(clampTime(Number.NaN, 5)).toBe(0);
  });
});

describe("cuts and scenes", () => {
  it("finds the cuts around the playhead", () => {
    const cuts = cutTimes(timeline);
    expect(cuts).toEqual([2.6, 8.4]);
    expect(nextCut(cuts, 0, 30, 13.1)).toBe(2.6);
    expect(nextCut(cuts, 2.6, 30, 13.1)).toBe(8.4);
    expect(nextCut(cuts, 9, 30, 13.1)).toBe(13.1);
    expect(previousCut(cuts, 9, 30)).toBe(8.4);
    expect(previousCut(cuts, 8.4, 30)).toBe(2.6);
    expect(previousCut(cuts, 1, 30)).toBe(0);
  });

  it("names the scene under the playhead", () => {
    expect(sceneAt(timeline, 0)).toBe(0);
    expect(sceneAt(timeline, 2.6)).toBe(1);
    expect(sceneAt(timeline, 20)).toBe(2);
    expect(sceneAt(null, 1)).toBe(-1);
  });
});
