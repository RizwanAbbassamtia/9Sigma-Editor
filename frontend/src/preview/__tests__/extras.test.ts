import { describe, expect, it } from "vitest";

import { linearTable } from "../canvas2d/transitions";
import { drawingSize } from "../engine";
import { blurPlan, kernelRadius } from "../gpu/passes";
import { MotionPath, coverRegion } from "../motion";
import { fitOverscan, tighten, workingCrop } from "../parallax";
import { PICTURE_STEPS, decodeHeight, depthMapPath, pictureUrls, stepHeight } from "../pictures";
import { popupReservedPx } from "../text";
import { DEFAULT_SETTINGS } from "../types";
import { close, closeAll, fixture } from "./fixtures";

interface ExtrasFixture {
  linear_table: { weight: number; gain: number; values: [number, number, number][] }[];
  overscan: {
    preset: string;
    image: [number, number];
    strength: number;
    frames: number;
    stats: [number, number, number];
    start: number[];
    end: number[];
    crop: number[];
    share: number;
    soft: number;
  }[];
}

const data = fixture<ExtrasFixture>("extras.json");

describe("the Canvas 2D dissolve table (common.py _linear_table)", () => {
  it("gives the same bytes as mix_linear", () => {
    for (const item of data.linear_table) {
      const table = linearTable(item.weight, item.gain);
      for (const [a, b, value] of item.values) expect(table[a * 256 + b], `w=${item.weight} g=${item.gain} ${a},${b}`).toBe(value);
    }
  });
});

describe("2.5D parallax overscan (render/parallax/warp.py fit_overscan)", () => {
  it("tightens and softens like the renderer", () => {
    for (const item of data.overscan) {
      const [iw, ih] = item.image;
      const region = coverRegion(iw, ih, 1920, 1080);
      const path = MotionPath.forPreset(item.preset, item.start, item.end, { seed: 2, bounds: region });
      const crop = workingCrop(region, iw, ih);
      closeAll(crop, item.crop, 1e-9, `${item.preset} crop`);
      const [mean, low, high] = item.stats;
      const fit = fitOverscan(path, { mean, low, high }, 1920, 1080, { strength: item.strength, frames: item.frames, fps: 30, limit: 0.03, picture: crop, tapMarginPx: 1 });
      close(fit.share, item.share, 1e-9, `${item.preset} share`);
      close(fit.strength, item.soft, 1e-9, `${item.preset} strength`);
    }
    expect(tighten([10, 20, 100, 50], 0.1)).toEqual([20, 25, 80, 40]);
  });
});

describe("sizes", () => {
  it("draws at the monitor size in device pixels, capped at 1280 x 720, half for Half", () => {
    expect(drawingSize([960, 540], 1, [1920, 1080], "full")).toEqual([960, 540]);
    expect(drawingSize([960, 540], 1.5, [1920, 1080], "full")).toEqual([1280, 720]);
    expect(drawingSize([960, 540], 1, [1920, 1080], "half")).toEqual([480, 270]);
    expect(drawingSize([300, 533], 2, [1080, 1920], "full")).toEqual([600, 1066]);
    expect(drawingSize([0, 0], 1, [1920, 1080], "full")).toEqual([1280, 720]);
    expect(drawingSize([1000, 1000], 1, [1920, 1080], "full")).toEqual([1000, 562]);
  });

  it("decodes pictures at most twice the preview and 1920 wide", () => {
    expect(decodeHeight([1536, 1024], 540, 864)).toBe(1024);
    expect(decodeHeight([4000, 3000], 540, 2250)).toBe(1440);
    expect(decodeHeight([6000, 2000], 540, 2000)).toBe(640);
    expect(decodeHeight(null, 540, null)).toBe(1080);
  });

  it("asks for a few fixed heights, the ones the backend makes ahead (render/thumbs.py)", () => {
    // the same cases as tests/test_preview_api.py test_the_preview_heights_are_the_engine_s
    expect(decodeHeight([2048, 1152], 528, 1152)).toBe(1080);
    expect(decodeHeight([2048, 1152], 550, 1152)).toBe(1080);
    expect(decodeHeight(null, 600, null)).toBe(1440);
    expect(decodeHeight([4096, 2304], 600, 2304)).toBe(1080);
    expect(decodeHeight([2048, 1152], 270, 1152)).toBe(540);
    expect(decodeHeight([2048, 2048], 540, 1152)).toBe(1920);
    expect(stepHeight(1101)).toBe(1080);
    expect(stepHeight(1102)).toBe(1440);
    expect(stepHeight(2500)).toBe(2500);
    // Half quality and a slightly smaller window share Full's neighbours' files
    const heights = [270, 300, 360, 400, 528, 540, 600, 720].map((drawn) => decodeHeight([2048, 1152], drawn, 1152));
    expect(new Set(heights)).toEqual(new Set([540, 720, 1080]));
    expect(PICTURE_STEPS).toEqual([540, 720, 1080, 1440, 1920, 2160]);
  });

  it("reads the sized copy up to 2160 high, else the file itself", () => {
    const sized = (path: string, height: number) => `thumb:${path}@${height}`;
    const file = (path: string) => `file:${path}`;
    expect(pictureUrls("a.png", 720, sized, file)).toEqual(["thumb:a.png@720", "file:a.png"]);
    expect(pictureUrls("a.png", 1080, sized, file)).toEqual(["thumb:a.png@1080", "file:a.png"]);
    // a 9:16 preview 960 high wants 1920: still a sized copy (a JPEG decodes far faster than a PNG)
    expect(pictureUrls("a.png", 1920, sized, file)).toEqual(["thumb:a.png@1920", "file:a.png"]);
    // taller than the route makes: the file (decoded down), the 2160 copy only if that fails
    expect(pictureUrls("a.png", 2400, sized, file)).toEqual(["file:a.png", "thumb:a.png@2160"]);
    expect(pictureUrls("a.png", 1920, undefined, file)).toEqual(["file:a.png"]);
    expect(pictureUrls("a.png", 400, (path) => file(path), file)).toEqual(["file:a.png"]);
  });

  it("finds the depth map beside the picture like render/parallax/depth.py", () => {
    expect(depthMapPath("06_images/scene_03.png")).toBe("06_images/scene_03.depth.png");
    expect(depthMapPath("06_images/scene_03.jpg")).toBe("06_images/scene_03.depth.png");
    expect(depthMapPath("pics\\my.photo.webp")).toBe("pics\\my.photo.depth.png");
    expect(depthMapPath("scene_01")).toBe("scene_01.depth.png");
    expect(depthMapPath("https://example.test/a.png")).toBeNull();
    expect(depthMapPath("")).toBeNull();
  });

  it("keeps popups clear of the caption band", () => {
    expect(popupReservedPx({ size: 48, max_lines: 2 }, true, true, [1920, 1080], DEFAULT_SETTINGS)).toBe(194);
    expect(popupReservedPx({ size: 48, max_lines: 2 }, true, false, [1920, 1080], DEFAULT_SETTINGS)).toBe(0);
    expect(popupReservedPx({ size: 48, max_lines: 2 }, true, true, [854, 480], DEFAULT_SETTINGS)).toBe(86);
  });

  it("blurs like fast_gaussian", () => {
    expect(blurPlan(0.2)).toBeNull();
    expect(blurPlan(2)).toEqual({ factor: 1, sigma: 2, radius: kernelRadius(2) });
    expect(kernelRadius(2)).toBe(6);
    const plan = blurPlan(12)!;
    expect(plan.factor).toBe(8);
    close(plan.sigma, Math.sqrt(144 - 16) / 8, 1e-12, "rest");
  });
});
