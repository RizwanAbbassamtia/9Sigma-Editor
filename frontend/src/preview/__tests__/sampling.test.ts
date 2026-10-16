import { describe, expect, it } from "vitest";

import { kenBurnsSampling } from "../gpu/passes";
import { close } from "./fixtures";

describe("Ken Burns supersampling (gpu/passes.ts)", () => {
  it("steps one output pixel in texture coordinates and stays on the full picture up to 2x", () => {
    // the whole of a 1920 x 1080 picture on a 960 x 540 preview: two texels per pixel
    const half = kenBurnsSampling([1, 1], [960, 540], [1920, 1080]);
    close(half.step[0], 1 / 960, 1e-12, "step u");
    close(half.step[1], 1 / 540, 1e-12, "step v");
    expect(half.lod).toBe(0);
    // 1.6 texels per pixel (a 1536 x 864 cover region): still the full-size level
    expect(kenBurnsSampling([1, 1], [960, 540], [1536, 864]).lod).toBe(0);
    // a camera window of 60 % at 1:1 or larger: magnified, level 0
    expect(kenBurnsSampling([0.6, 0.6], [960, 540], [1024, 576]).lod).toBe(0);
  });

  it("moves to the mip level where a pixel is at most two texels", () => {
    // 4 texels per pixel (a Full-size texture shown at Half quality): one level down
    close(kenBurnsSampling([1, 1], [480, 270], [1920, 1080]).lod, 1, 1e-12, "4x");
    // 3 texels per pixel: between the two levels
    close(kenBurnsSampling([1, 1], [640, 360], [1920, 1080]).lod, Math.log2(3) - 1, 1e-12, "3x");
    // the wider of the two directions decides
    close(kenBurnsSampling([1, 0.5], [480, 540], [1920, 1080]).lod, 1, 1e-12, "wide");
  });
});
