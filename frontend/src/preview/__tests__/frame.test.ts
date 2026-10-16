import { describe, expect, it } from "vitest";

import { IDENTITY_FRAME, adjustOf, cssFilter, frameOf, isIdentityFrame, placeInFrame } from "../frame";

describe("the frame of a layer clip (docs/CONTRACT-v0.2.md section 4)", () => {
  it("reads the timeline's frame and adjust, with limits", () => {
    expect(frameOf(null)).toBeNull();
    expect(frameOf({ scale: 0.5, x: 0.25, crop: [0.1, 0, 2, 0], fit: "cover", flip_h: true })).toEqual({ ...IDENTITY_FRAME, scale: 0.5, x: 0.25, crop: [0.1, 0, 0.95, 0], fit: "cover", flipH: true });
    expect(isIdentityFrame(frameOf({ fit: "cover" }))).toBe(true);
    expect(isIdentityFrame(frameOf({ rotation: 5 }))).toBe(false);
    expect(adjustOf({ brightness: 0, contrast: 0, saturation: 0 })).toBeNull();
    expect(adjustOf({ brightness: 2, saturation: -0.5 })).toEqual({ brightness: 1, contrast: 0, saturation: -0.5 });
  });

  it("places a source in the frame as the renderer does: crop, fit, scale, centre", () => {
    const contain = placeInFrame({ ...IDENTITY_FRAME, scale: 0.5, x: 0.25, y: -0.25 }, 400, 400, 1920, 1080);
    expect([contain.w, contain.h]).toEqual([540, 540]);
    expect([contain.cx, contain.cy]).toEqual([1920 / 2 + 480, 1080 / 2 - 270]);
    const cover = placeInFrame({ ...IDENTITY_FRAME, fit: "cover" }, 640, 360, 1920, 1080);
    expect([cover.w, cover.h]).toEqual([1920, 1080]);
    const cropped = placeInFrame({ ...IDENTITY_FRAME, crop: [0.25, 0, 0.25, 0] }, 1000, 500, 1000, 500);
    expect([cropped.sx, cropped.sw, cropped.w, cropped.h]).toEqual([250, 500, 500, 500]);
    const turned = placeInFrame({ ...IDENTITY_FRAME, rotation: 90, opacity: 0.5 }, 100, 100, 200, 200);
    expect(turned.rotation).toBeCloseTo(Math.PI / 2);
    expect(turned.alpha).toBe(0.5);
  });

  it("writes the adjust as a CSS filter for the 2D path", () => {
    expect(cssFilter(null)).toBe("none");
    expect(cssFilter({ brightness: 0.2, contrast: -0.1, saturation: -1 })).toBe("brightness(1.200) contrast(0.900) saturate(0.000)");
  });
});
