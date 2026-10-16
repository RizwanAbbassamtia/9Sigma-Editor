import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PopupPreviewRequest } from "../types/preview";
import { createPopupPreviewer, fontUrl, mediaThumbUrl, peaksUrl, previewKeys, timelineBundleUrl, voiceSegmentUrl } from "./preview";

const REQUEST: PopupPreviewRequest = { text: "Hello", style: "", position: "top-left", anim: "pop", aspect: "16:9", height: 540 };

describe("preview URLs", () => {
  it("follow the contract's routes", () => {
    expect(timelineBundleUrl("p 1")).toBe("/api/projects/p%201/timeline");
    expect(mediaThumbUrl("p1", "06_images/scene_01.png")).toBe("/api/projects/p1/media/thumb?path=06_images%2Fscene_01.png&h=128");
    expect(mediaThumbUrl("p1", "a.png", 1079.6)).toBe("/api/projects/p1/media/thumb?path=a.png&h=1080");
    // the route makes 16 to 2160 pixels (a portrait monitor at 2x asks for about 1920)
    expect(mediaThumbUrl("p1", "a.png", 1708)).toBe("/api/projects/p1/media/thumb?path=a.png&h=1708");
    expect(mediaThumbUrl("p1", "a.png", 3000)).toBe("/api/projects/p1/media/thumb?path=a.png&h=2160");
    expect(mediaThumbUrl("p1", "a.png", 4)).toBe("/api/projects/p1/media/thumb?path=a.png&h=16");
    expect(mediaThumbUrl("p1", "a.png", Number.NaN)).toBe("/api/projects/p1/media/thumb?path=a.png&h=128");
    expect(peaksUrl("p1", "music")).toBe("/api/projects/p1/media/peaks/music.json");
    expect(voiceSegmentUrl("p1", "s 7")).toBe("/api/projects/p1/media/voice/segments/s%207.wav");
    expect(fontUrl("NotoSans[wdth,wght].ttf")).toBe("/api/fonts/NotoSans%5Bwdth%2Cwght%5D.ttf");
  });

  it("keeps the bundle under the project so a render refreshes it", () => {
    expect(previewKeys.bundle("p1").slice(0, 3)).toEqual(["projects", "detail", "p1"]);
  });
});

describe("createPopupPreviewer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks once per popup for calls within 150 ms, with the latest text", async () => {
    const fetcher = vi.fn(async (_projectId: string, request: PopupPreviewRequest) => ({ png_url: `/${request.text}.png`, x: 1, y: 2, w: 3, h: 4 }));
    const preview = createPopupPreviewer("p1", 150, fetcher);
    const first = preview("pop-001", REQUEST);
    await vi.advanceTimersByTimeAsync(100);
    const second = preview("pop-001", { ...REQUEST, text: "Hello there" });
    const other = preview("pop-002", { ...REQUEST, text: "Other" });
    expect(fetcher).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(150);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0]![1].text).toBe("Hello there");
    await expect(first).resolves.toMatchObject({ png_url: "/Hello there.png" });
    await expect(second).resolves.toMatchObject({ png_url: "/Hello there.png" });
    await expect(other).resolves.toMatchObject({ png_url: "/Other.png" });
  });

  it("passes a failure to every caller", async () => {
    const fetcher = vi.fn(async () => {
      throw new Error("offline");
    });
    const preview = createPopupPreviewer("p1", 150, fetcher);
    const first = preview("pop-001", REQUEST);
    const second = preview("pop-001", REQUEST);
    const settled = Promise.allSettled([first, second]);
    await vi.advanceTimersByTimeAsync(200);
    const results = await settled;
    expect(results.map((result) => result.status)).toEqual(["rejected", "rejected"]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
