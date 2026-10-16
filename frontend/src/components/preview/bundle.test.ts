import { describe, expect, it } from "vitest";

import { parseTimeline, type Timeline } from "../../types/timeline";
import { bundleFromTimeline, fontsFor, projectFile, withCuts, withOverlays } from "./bundle";

function timeline(): Timeline {
  const parsed = parseTimeline({
    project_id: "p1",
    format: "long",
    aspect: "16:9",
    fps: 30,
    width: 1920,
    height: 1080,
    duration_s: 10,
    voice: { path: "05_voice/voice.wav", start_s: 0 },
    music: { path: "D:/music/calm.mp3", stem: "07_edit/music_ducked.wav", envelope: "07_edit/music_envelope.json" },
    scenes: [
      { index: 0, image: "06_images/scene_01.png", start_s: 0, end_s: 5, motion: { preset: "hold", start_rect: [0, 0, 1920, 1080], end_rect: [0, 0, 1920, 1080] }, transition_out: { type: "film_dissolve", duration_s: 0.6 } },
      { index: 1, image: "06_images/scene_02.png", start_s: 5, end_s: 10, motion: { preset: "hold", start_rect: [0, 0, 1920, 1080], end_rect: [0, 0, 1920, 1080] }, transition_out: { type: "dip_black", duration_s: 1 } },
    ],
    popups: [{ scene: 0, text: "Hello", start_s: 0.3, end_s: 2, anim: "pop" }],
    captions: { enabled: false, cues: [{ start_s: 0, end_s: 1, text: "Hello there" }] },
    render: { preview_gain_db: -2.5 },
  });
  if (!parsed) throw new Error("fixture timeline did not parse");
  return parsed;
}

describe("bundleFromTimeline", () => {
  it("draws a saved timeline without the server's bundle", () => {
    const sizes = new Map([["06_images/scene_01.png", { width: 2560, height: 1440 }]]);
    const bundle = bundleFromTimeline("p1", timeline(), sizes);
    expect(bundle.pictures).toEqual([
      { scene: 0, path: "06_images/scene_01.png", width: 2560, height: 1440, thumb_url: "/api/projects/p1/media/thumb?path=06_images%2Fscene_01.png&h=128" },
      { scene: 1, path: "06_images/scene_02.png", width: 0, height: 0, thumb_url: "/api/projects/p1/media/thumb?path=06_images%2Fscene_02.png&h=128" },
    ]);
    expect(bundle.media.voice_url).toBe("/api/projects/p1/files/05_voice/voice.wav");
    expect(bundle.media.stem_url).toBe("/api/projects/p1/files/07_edit/music_ducked.wav");
    expect(bundle.media.envelope_url).toBe("/api/projects/p1/files/07_edit/music_envelope.json");
    expect(bundle.preview_gain_db).toBe(-2.5);
    expect(bundle.sentences).toEqual([]);
    expect(bundle.virtual).toBe(false);
  });

  it("only serves files inside the project", () => {
    expect(projectFile("p1", "C:\\Users\\me\\Music\\song.mp3")).toBeNull();
    expect(projectFile("p1", "")).toBeNull();
    expect(projectFile("p1", "/api/projects/p1/files/x.wav")).toBe("/api/projects/p1/files/x.wav");
  });

  it("lists Noto Sans and the caption font once", () => {
    expect(fontsFor(["Noto Sans JP", "Noto Sans", "Unknown"]).map((font) => font.family)).toEqual(["Noto Sans", "Noto Sans JP"]);
  });
});

describe("local patches", () => {
  it("puts changed cuts in and keeps the same bundle when nothing changed", () => {
    const bundle = bundleFromTimeline("p1", timeline());
    const same = withCuts(bundle, [
      { type: "film_dissolve", duration_s: 0.6 },
      { type: "dip_black", duration_s: 1 },
    ]);
    expect(same).toBe(bundle);
    const changed = withCuts(bundle, [
      { type: "whip_left", duration_s: 0.4 },
      { type: "dip_black", duration_s: 1 },
    ]);
    expect(changed).not.toBe(bundle);
    expect(changed.timeline.scenes[0]!.transition_out).toEqual({ type: "whip_left", duration_s: 0.4 });
    expect(changed.timeline.scenes[1]).toBe(bundle.timeline.scenes[1]);
  });

  it("switches popups and captions", () => {
    const bundle = bundleFromTimeline("p1", timeline());
    const off = withOverlays(bundle, { popups: false, captions: false });
    expect(off.timeline.popups).toEqual([]);
    expect(off.timeline.captions.enabled).toBe(false);
    const captionsOn = withOverlays(bundle, { popups: true, captions: true });
    expect(captionsOn.timeline.captions.enabled).toBe(true);
    expect(captionsOn.timeline.popups).toBe(bundle.timeline.popups);
    expect(withOverlays(bundle, { popups: true, captions: false })).toBe(bundle);
  });
});
