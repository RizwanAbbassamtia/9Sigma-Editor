import { describe, expect, it } from "vitest";

import { analyseDepth, frontDepth, gaussianGrid, oddReflectPad, resizeArea, resizeLinear, squareFilter, type Grid } from "../layers";
import { closeAll, fixture } from "./fixtures";

interface LayersFixture {
  small: { w: number; h: number; values: number[] };
  medium: { w: number; h: number; values: number[] };
  pad: number[];
  erode: number[];
  dilate: number[];
  gauss: number[];
  area_10x6: number[];
  area_7x5: number[];
  linear_23x13: number[];
  disc: { w: number; h: number; values: number[] };
  envelope: number[];
  alpha: number[] | null;
  front: number[] | null;
}

const data = fixture<LayersFixture>("layers.json");
const grid = (g: { w: number; h: number; values: number[] }): Grid => ({ values: Float32Array.from(g.values), width: g.w, height: g.h });
const TOL = 2e-5;

describe("depth-layer helpers (OpenCV and render/parallax/warp.py)", () => {
  it("pads by point reflection, erodes and dilates by a square", () => {
    const small = grid(data.small);
    closeAll(Array.from(oddReflectPad(small, 2).values), data.pad, TOL, "pad");
    closeAll(Array.from(squareFilter(small, 2, "min").values), data.erode, TOL, "erode");
    closeAll(Array.from(squareFilter(small, 2, "max").values), data.dilate, TOL, "dilate");
  });

  it("blurs and resizes like cv2", () => {
    const medium = grid(data.medium);
    closeAll(Array.from(gaussianGrid(medium, 1.5).values), data.gauss, 1e-4, "gauss");
    closeAll(Array.from(resizeArea(medium, 10, 6).values), data.area_10x6, 1e-4, "area 10x6");
    closeAll(Array.from(resizeArea(medium, 7, 5).values), data.area_7x5, 1e-4, "area 7x5");
    closeAll(Array.from(resizeLinear(grid(data.small), 23, 13).values), data.linear_23x13, 1e-4, "linear");
  });

  it("finds the same near object as analyse_depth", () => {
    const depth = grid(data.disc);
    const analysis = analyseDepth(depth);
    closeAll(Array.from(analysis.envelope.values), data.envelope, 1e-3, "envelope");
    expect(analysis.alpha !== null).toBe(data.alpha !== null);
    if (analysis.alpha && data.alpha) {
      closeAll(Array.from(analysis.alpha.values), data.alpha, 1e-3, "alpha");
      closeAll(Array.from(frontDepth(depth, analysis.alpha).values), data.front!, 1e-4, "front depth");
    }
  });
});
