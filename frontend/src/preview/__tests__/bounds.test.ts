import { describe, expect, it } from "vitest";

import { clipTransitions, popupAnimFor, popupWindow, sceneBounds } from "../bounds";
import { closeAll, fixture } from "./fixtures";

interface BoundsFixture {
  scene_bounds: {
    name: string;
    scenes: { sentence_ids: string[]; est_duration_s: number }[];
    timing: { duration_s: number; sentences: { id: string; start_s: number; end_s: number }[] };
    tail_s: number;
    bounds: [number, number][];
  }[];
  clip_transitions: { bounds: [number, number][]; durations: number[]; max_share: number; min_s: number; result: number[] }[];
  popup_window: {
    key_start_s: number;
    scene_start_s: number;
    scene_end_s: number;
    options: Record<string, number>;
    window: [number, number] | null;
  }[];
  popup_anim: { style: string | null; anim: string }[];
}

const data = fixture<BoundsFixture>("bounds.json");

describe("timeline builder rules (render/timeline_builder.py)", () => {
  it("scene_bounds", () => {
    for (const item of data.scene_bounds) {
      const bounds = sceneBounds(item.scenes, item.timing, item.tail_s);
      expect(bounds.length, item.name).toBe(item.bounds.length);
      bounds.forEach((pair, i) => closeAll(pair, item.bounds[i]!, 1e-9, `${item.name} scene ${i + 1}`));
    }
  });

  it("clip_transitions", () => {
    for (const item of data.clip_transitions) {
      closeAll(clipTransitions(item.bounds, item.durations, { maxShare: item.max_share, minS: item.min_s }), item.result, 1e-9, "clip");
    }
  });

  it("popup_window", () => {
    for (const item of data.popup_window) {
      const window = popupWindow(item.key_start_s, item.scene_start_s, item.scene_end_s, item.options);
      if (item.window === null) expect(window).toBeNull();
      else closeAll(window!, item.window, 1e-9, "window");
    }
  });

  it("popup_anim_for", () => {
    for (const item of data.popup_anim) expect(popupAnimFor(item.style)).toBe(item.anim);
  });
});
