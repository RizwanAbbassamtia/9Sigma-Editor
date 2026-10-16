import { describe, expect, it } from "vitest";

import { MOTION_PRESETS } from "../../preview/motion";
import { applyContinuity, captionRulesFor, chunkCues, moveDirection, wrapLines, type FracRect, type ScenePlan } from "./builderRules";

// Expected values come from the Python reference (render/motion.py apply_continuity and
// render/captions.py chunk_cues / wrap_lines), run on the same inputs.

function plans(rows: Array<[string, string, boolean?, boolean?]>): ScenePlan[] {
  return rows.map(([preset, transition, motionLocked = false, transitionLocked = false]) => ({
    preset,
    start: [...MOTION_PRESETS[preset]!.start] as FracRect,
    end: [...MOTION_PRESETS[preset]!.end] as FracRect,
    transition,
    motionLocked,
    transitionLocked,
    notes: [],
  }));
}

function summary(list: ScenePlan[]) {
  return list.map((plan) => [plan.preset, plan.transition, plan.start, plan.end]);
}

describe("applyContinuity (render/motion.py)", () => {
  it("alternates neighbouring moves", () => {
    const list = plans([
      ["pan_right", "film_dissolve"],
      ["pan_right", "film_dissolve"],
      ["pan_right", "film_dissolve"],
    ]);
    const notes = applyContinuity(list);
    expect(summary(list)).toEqual([
      ["pan_right", "film_dissolve", [0, 0, 0.85, 1], [0.15, 0, 0.85, 1]],
      ["pan_left", "film_dissolve", [0.15, 0, 0.85, 1], [0, 0, 0.85, 1]],
      ["pan_right", "film_dissolve", [0, 0, 0.85, 1], [0.15, 0, 0.85, 1]],
    ]);
    expect(notes).toEqual(["Scene 2 now moves the other way to scene 1."]);
  });

  it("makes a zoom-through cut leave zoomed in and land wide", () => {
    const list = plans([
      ["hold", "zoom_through"],
      ["zoom_in", "film_dissolve"],
    ]);
    const notes = applyContinuity(list);
    expect(summary(list)).toEqual([
      ["slow_push", "zoom_through", [0, 0, 1, 1], [0.04, 0.04, 0.92, 0.92]],
      ["zoom_in", "film_dissolve", [0, 0, 1, 1], [0.1, 0.1, 0.8, 0.8]],
    ]);
    expect(notes).toEqual(["Scene 1 now ends zoomed in (slow_push) so the zoom-through cut carries its push on."]);
  });

  it("turns a whip to follow the camera, and the next move away from the same direction", () => {
    const list = plans([
      ["pan_right", "whip_right"],
      ["drift", "film_dissolve"],
    ]);
    const notes = applyContinuity(list);
    expect(summary(list)).toEqual([
      ["pan_right", "whip_left", [0, 0, 0.85, 1], [0.15, 0, 0.85, 1]],
      ["drift", "film_dissolve", [0.06, 0.03, 0.94, 0.94], [0.01, 0.03, 0.94, 0.94]],
    ]);
    expect(notes).toEqual(["The cut after scene 1 now whips left to follow the camera.", "Scene 2 now moves the other way to scene 1."]);
  });

  it("turns the move, not a locked whip", () => {
    const list = plans([
      ["pan_left", "whip_left", false, true],
      ["hold", "film_dissolve"],
    ]);
    expect(applyContinuity(list)).toEqual(["Scene 1 now pans the way the locked whip after it goes."]);
    expect(summary(list)[0]).toEqual(["pan_right", "whip_left", [0, 0, 0.85, 1], [0.15, 0, 0.85, 1]]);
  });

  it("flips the scene before a locked move", () => {
    const list = plans([
      ["zoom_in", "film_dissolve"],
      ["zoom_in", "film_dissolve", true],
    ]);
    expect(applyContinuity(list)).toEqual(["Scene 1 now moves the other way to scene 2."]);
    expect(summary(list)).toEqual([
      ["zoom_out", "film_dissolve", [0.1, 0.1, 0.8, 0.8], [0, 0, 1, 1]],
      ["zoom_in", "film_dissolve", [0, 0, 1, 1], [0.1, 0.1, 0.8, 0.8]],
    ]);
  });

  it("reads the old zoomin name as a zoom-through and keeps calm scenes calm", () => {
    const list = plans([
      ["drift", "zoomin"],
      ["slow_push", "film_dissolve"],
    ]);
    applyContinuity(list);
    expect(summary(list)).toEqual([
      ["slow_push", "zoomin", [0, 0, 1, 1], [0.04, 0.04, 0.92, 0.92]],
      ["slow_push", "film_dissolve", [0, 0, 1, 1], [0.04, 0.04, 0.92, 0.92]],
    ]);
  });

  it("knows which way a move goes", () => {
    expect(moveDirection([0, 0, 0.85, 1], [0.15, 0, 0.85, 1])).toEqual(["x", 1]);
    expect(moveDirection([0, 0, 1, 1], [0.1, 0.1, 0.8, 0.8])).toEqual(["z", 1]);
    expect(moveDirection([0, 0, 1, 1], [0, 0, 1, 1])).toBeNull();
  });
});

describe("caption chunking (render/captions.py)", () => {
  it("cuts words into cues at sentence ends and pauses", () => {
    const cues = chunkCues(
      [
        { id: "a", text: "The quick brown fox jumps over the lazy dog near the river bank today.", start_s: 0, end_s: 4.2, words: [] },
        {
          id: "b",
          text: "Then it rested.",
          start_s: 4.5,
          end_s: 5.6,
          words: [
            { text: "Then", start_s: 4.5, end_s: 4.8 },
            { text: "it", start_s: 4.85, end_s: 5.0 },
            { text: "rested.", start_s: 5.6, end_s: 6.1 },
          ],
        },
        { id: "c", text: "Short.", start_s: 6.2, end_s: 6.4, words: [] },
      ],
      { maxCharsPerLine: 20, maxLines: 2, pauseS: 0.35, spaceDependent: true },
    );
    expect(cues).toEqual([
      { start_s: 0, end_s: 2.063, text: "The quick brown fox\\Njumps over the" },
      { start_s: 2.063, end_s: 4.2, text: "lazy dog near the\\Nriver bank today." },
      { start_s: 4.5, end_s: 5, text: "Then it" },
      { start_s: 5.6, end_s: 6.1, text: "rested." },
      { start_s: 6.2, end_s: 6.6, text: "Short." },
    ]);
  });

  it("breaks a language without spaces by characters", () => {
    const cues = chunkCues([{ id: "j", text: "今日はとても良い天気ですね散歩に行きましょう", start_s: 0, end_s: 3, words: [] }], {
      maxCharsPerLine: 13,
      maxLines: 2,
      spaceDependent: false,
    });
    expect(cues).toEqual([{ start_s: 0, end_s: 3, text: "今日はとても良い天気で\\Nすね散歩に行きましょう" }]);
  });

  it("balances wrapped lines", () => {
    expect(wrapLines("one two three four five six seven eight nine ten", 12, 2)).toEqual(["one two three four five", "six seven eight nine ten"]);
    expect(wrapLines("short", 12, 2)).toEqual(["short"]);
  });

  it("uses the language rules of config/captions.yaml", () => {
    expect(captionRulesFor("Japanese")).toMatchObject({ font: "Noto Sans JP", maxCharsPerLine: 13, spaceDependent: false });
    expect(captionRulesFor("Arabic")).toMatchObject({ rtl: true, font: "Noto Naskh Arabic" });
    expect(captionRulesFor("English")).toMatchObject({ font: "Noto Sans", maxCharsPerLine: 42, maxLines: 2 });
  });
});
