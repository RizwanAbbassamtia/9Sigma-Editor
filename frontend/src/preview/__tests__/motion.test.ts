import { describe, expect, it } from "vitest";

import {
  MOTION_PRESETS,
  MotionPath,
  coverRegion,
  ease,
  normalizeMotionEase,
  parallaxGain,
  parallaxOffsets,
  presetSpec,
  pyRound,
  rectInPixels,
  shakeOffsets,
} from "../motion";
import { close, closeAll, fixture } from "./fixtures";

interface MotionFixture {
  ease: { name: string | null; t: number; value: number }[];
  presets: { id: string; label: string; energy: string; start_rect: number[]; end_rect: number[]; easing: string; handheld: boolean }[];
  cover_region: { image: [number, number]; frame: [number, number]; region: number[] }[];
  rect_in_pixels: { frac: number[]; image: [number, number]; frame: [number, number]; rect: number[] }[];
  shake: { time_s: number; seed: number; xy: [number, number] }[];
  paths: {
    preset: string;
    ease: string | null;
    seed: number;
    frames: number;
    fps: number;
    start_rect: number[];
    end_rect: number[];
    bounds: number[] | null;
    easing: string;
    handheld: number;
    samples: { k: number; rect: number[]; camera: [number, number, number] }[];
  }[];
  parallax: { strength: number; out_width: number; camera: [number, number, number]; depth: number; mean: number; n: [number, number]; offset: [number, number]; gain: number }[];
  handheld_amplitude: number;
}

const data = fixture<MotionFixture>("motion.json");
const TOL = 1e-6;

describe("pyRound (Python's round)", () => {
  it("rounds ties to even like Python", () => {
    expect(pyRound(2.5)).toBe(2);
    expect(pyRound(3.5)).toBe(4);
    expect(pyRound(-2.5)).toBe(-2);
    expect(pyRound(0.5)).toBe(0);
    expect(pyRound(1.5)).toBe(2);
    expect(pyRound(37.5)).toBe(38);
    expect(pyRound(0.0625, 3)).toBe(0.062);
    expect(pyRound(0.1875, 3)).toBe(0.188);
    expect(pyRound(1.0005, 3)).toBe(1.0);
    expect(pyRound(2.675, 2)).toBe(2.67);
    expect(pyRound(-0.0625, 3)).toBe(-0.062);
    expect(pyRound(123.456789, 4)).toBe(123.4568);
  });
});

describe("motion easing (render/motion.py ease)", () => {
  it("matches every curve to 1e-6", () => {
    for (const item of data.ease) close(ease(item.name, item.t), item.value, TOL, `${item.name}(${item.t})`);
  });
});

describe("presets", () => {
  it("has the same table as render/motion.py", () => {
    expect(Object.keys(MOTION_PRESETS)).toEqual(data.presets.map((p) => p.id));
    for (const reference of data.presets) {
      const spec = MOTION_PRESETS[reference.id]!;
      expect(spec.label).toBe(reference.label);
      expect(spec.energy).toBe(reference.energy);
      expect(spec.easing).toBe(reference.easing);
      expect(spec.handheld).toBe(reference.handheld);
      closeAll(spec.start, reference.start_rect, 0, `${reference.id} start`);
      closeAll(spec.end, reference.end_rect, 0, `${reference.id} end`);
    }
    expect(presetSpec("Pan-Left")?.id).toBe("pan_left");
    expect(presetSpec("slow push")?.id).toBe("slow_push");
    expect(presetSpec("nope")).toBeNull();
  });
});

describe("geometry (render/geometry.py)", () => {
  it("cover_region", () => {
    for (const item of data.cover_region) {
      closeAll(coverRegion(item.image[0], item.image[1], item.frame[0], item.frame[1]), item.region, TOL, `cover ${item.image} in ${item.frame}`);
    }
  });
  it("rect_in_pixels (rounded to 2 decimals like Python)", () => {
    for (const item of data.rect_in_pixels) {
      closeAll(rectInPixels(item.frac, item.image[0], item.image[1], item.frame[0], item.frame[1]), item.rect, 1e-9, `rect ${item.frac}`);
    }
  });
});

describe("handheld wobble", () => {
  it("shake_offsets", () => {
    for (const item of data.shake) closeAll(shakeOffsets(item.time_s, item.seed), item.xy, TOL, `shake ${item.time_s} seed ${item.seed}`);
    expect(data.handheld_amplitude).toBe(0.004);
  });
});

describe("camera paths (MotionPath.rect and camera)", () => {
  it("matches every frame window and camera term to 1e-6", () => {
    for (const item of data.paths) {
      const path = MotionPath.forPreset(item.preset, item.start_rect, item.end_rect, {
        seed: item.seed,
        bounds: item.bounds,
        easing: item.ease,
        handheldAmplitude: data.handheld_amplitude,
      });
      expect(path.easing).toBe(item.easing);
      expect(path.handheld).toBe(item.handheld);
      for (const sample of item.samples) {
        const label = `${item.preset}/${item.ease ?? "preset"} k=${sample.k}/${item.frames}`;
        closeAll(path.frameRect(sample.k, item.frames, item.fps), sample.rect, TOL, label);
        const camera = path.frameCamera(sample.k, item.frames, item.fps);
        closeAll([camera.px, camera.py, camera.zoom], sample.camera, TOL, `${label} camera`);
      }
    }
  });
});

describe("motion.ease override (models/timeline.py normalize_motion_ease)", () => {
  it("reads loose spellings and falls back to the preset's own curve", () => {
    expect(normalizeMotionEase("Ease-Out Quart")).toBe("ease_out_quart");
    expect(normalizeMotionEase(" linear ")).toBe("linear");
    expect(normalizeMotionEase("bouncy")).toBeNull();
    expect(normalizeMotionEase(null)).toBeNull();
    const rect = [0, 0, 100, 56.25];
    expect(MotionPath.forPreset("drift", rect, rect, { easing: "bouncy" }).easing).toBe("ease_in_out_sine");
    expect(MotionPath.forPreset("drift", rect, rect, { easing: "Snap" }).easing).toBe("snap");
    expect(MotionPath.forPreset("zoom_in", rect, rect, { easing: "" }).easing).toBe("ease_in_out_cubic");
  });
});

describe("parallax displacement", () => {
  it("parallax_offsets and gain", () => {
    for (const item of data.parallax) {
      close(parallaxGain(item.strength, item.out_width), item.gain, TOL, "gain");
      const [px, py, zoom] = item.camera;
      closeAll(parallaxOffsets(item.depth, item.mean, item.n[0], item.n[1], { px, py, zoom }, item.strength, item.out_width), item.offset, TOL, "offset");
    }
  });
});
