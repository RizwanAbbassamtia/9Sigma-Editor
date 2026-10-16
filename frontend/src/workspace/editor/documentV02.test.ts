/**
 * The v0.2 document edits (docs/CONTRACT-v0.2.md sections 3 and 4): layers and sound tracks
 * (add, move with neighbours, trim, split, remove), the scene's length without a narration (a
 * ripple), and the parser's new fields.
 */
import { describe, expect, it } from "vitest";

import { parseTimeline, type Timeline } from "../../types/timeline";
import {
  addAudioClip,
  addAudioTrack,
  addLayer,
  addLayerClip,
  moveLayerClip,
  removeLayer,
  rippleAfter,
  setSceneAdjust,
  setSceneClip,
  setSceneLength,
  sourceSpan,
  splitAudioClip,
  splitLayerClip,
  trimLayerClip,
  updateAudioClip,
  updateLayerClip,
  videoEnd,
} from "./document";

function scene(index: number, start: number, end: number, clip = false) {
  return {
    index,
    image: `06_images/scene_0${index + 1}.png`,
    start_s: start,
    end_s: end,
    motion: { preset: "hold", start_rect: [0, 0, 1920, 1080], end_rect: [0, 0, 1920, 1080] },
    transition_out: { type: "film_dissolve", duration_s: 0.6 },
    ...(clip ? { clip: { path: "01_media/walk.mp4", in_s: 1, speed: 2, keep_sound: true, duration_s: 10 } } : {}),
  };
}

function doc(): Timeline {
  const parsed = parseTimeline({
    project_id: "p",
    format: "long",
    aspect: "16:9",
    fps: 30,
    width: 1920,
    height: 1080,
    duration_s: 12,
    scenes: [scene(0, 0, 4), scene(1, 4, 8, true), scene(2, 8, 12)],
    popups: [{ id: "pop-001", scene: 2, text: "Hi", start_s: 9, end_s: 11, anim: "fade" }],
    markers: [{ id: "mark-001", t: 10, label: "x" }],
    captions: { enabled: true, cues: [{ id: "cue-0001", start_s: 8.5, end_s: 9.5, text: "a" }] },
  });
  if (!parsed) throw new Error("no timeline");
  return parsed;
}

const PICTURE = { path: "01_media/logo.png", kind: "picture" as const, name: "logo.png", duration_s: null };
const CLIP = { path: "01_media/b.mp4", kind: "clip" as const, name: "b.mp4", duration_s: 3 };
const SOUND = { path: "01_media/voice/intro.mp3", kind: "audio" as const, name: "intro.mp3", duration_s: 2.5 };

describe("the parser", () => {
  it("reads clips, frames, adjusts, layers and sound tracks with defaults", () => {
    const timeline = doc();
    expect(timeline.layers).toEqual([]);
    expect(timeline.audio_tracks).toEqual([]);
    expect(timeline.scenes[0]!.clip).toBeNull();
    expect(timeline.scenes[1]!.clip).toEqual({ path: "01_media/walk.mp4", in_s: 1, speed: 2, keep_sound: true, gain_db: 0, duration_s: 10 });
    const withLayers = parseTimeline({
      ...timeline,
      layers: [{ clips: [{ source: "01_media/a.png", start_s: 1, end_s: 3, frame: { scale: 0.5, crop: [0.1, 0, 0, 0], fit: "cover" } }] }, { name: "Titles" }],
      audio_tracks: [{ clips: [{ source: "01_media/v.mp3", start_s: 0, end_s: 2, gain_db: -6 }] }],
    });
    expect(withLayers?.layers.map((layer) => [layer.id, layer.name])).toEqual([
      ["layer-01", "Layer 1"],
      ["layer-02", "Titles"],
    ]);
    expect(withLayers?.layers[0]!.clips[0]!.id).toBe("lc-001");
    expect(withLayers?.layers[0]!.clips[0]!.frame).toMatchObject({ scale: 0.5, crop: [0.1, 0, 0, 0], fit: "cover", opacity: 1 });
    expect(withLayers?.audio_tracks[0]!.id).toBe("track-01");
    expect(withLayers?.audio_tracks[0]!.clips[0]!).toMatchObject({ id: "ac-001", gain_db: -6, speed: 1 });
  });
});

describe("layers", () => {
  it("adds a clip on the top layer at the playhead, cut to the room and the video", () => {
    let timeline = doc();
    const first = addLayerClip(timeline, null, PICTURE, 2);
    timeline = first.timeline;
    expect(first.note).toBeNull();
    expect(timeline.layers).toHaveLength(1);
    const clip = timeline.layers[0]!.clips[0]!;
    expect(clip.id).toBe(first.id);
    expect([clip.start_s, clip.end_s]).toEqual([2, 7]);
    expect(clip.frame.scale).toBeLessThan(1);
    // a clip keeps its own length, stopped by the next clip
    const second = addLayerClip(timeline, null, CLIP, 0);
    expect(second.note).toBeNull();
    const added = second.timeline.layers[0]!.clips.find((item) => item.id === second.id)!;
    expect([added.start_s, added.end_s]).toEqual([0, 2]);
    // no room inside another clip
    const third = addLayerClip(second.timeline, null, PICTURE, 3);
    expect(third.note).toMatch(/No room/);
    // cut at the end of the video
    const late = addLayerClip(second.timeline, null, PICTURE, 10);
    expect(late.timeline.layers[0]!.clips.find((item) => item.id === late.id)!.end_s).toBe(12);
  });

  it("moves a clip without passing its neighbours and trims it", () => {
    let timeline = doc();
    const a = addLayerClip(timeline, null, PICTURE, 0);
    timeline = a.timeline;
    const b = addLayerClip(timeline, null, PICTURE, 6);
    timeline = b.timeline;
    expect(timeline.layers[0]!.clips.map((clip) => [clip.start_s, clip.end_s])).toEqual([
      [0, 5],
      [6, 11],
    ]);
    const moved = moveLayerClip(timeline, a.id, 3);
    const movedClip = moved.layers[0]!.clips.find((clip) => clip.id === a.id)!;
    expect([movedClip.start_s, movedClip.end_s]).toEqual([1, 6]);
    const trimmed = trimLayerClip(moved, b.id, "start", 8);
    expect(trimmed.layers[0]!.clips.find((clip) => clip.id === b.id)!.start_s).toBe(8);
    const clipLayer = addLayerClip(trimmed, null, CLIP, 6);
    const cut = trimLayerClip(clipLayer.timeline, clipLayer.id, "start", 7);
    const cutClip = cut.layers[0]!.clips.find((clip) => clip.id === clipLayer.id)!;
    expect(cutClip.in_s).toBe(1);
    expect(sourceSpan(cutClip)).toEqual([1, 2]);
  });

  it("splits and removes", () => {
    let timeline = doc();
    const added = addLayerClip(timeline, null, CLIP, 2);
    timeline = added.timeline;
    const split = splitLayerClip(timeline, added.id, 3);
    expect(split.note).toBeNull();
    const clips = split.timeline.layers[0]!.clips;
    expect(clips.map((clip) => [clip.start_s, clip.end_s, clip.in_s])).toEqual([
      [2, 3, 0],
      [3, 5, 1],
    ]);
    expect(splitLayerClip(timeline, added.id, 2.01).note).toMatch(/edges/);
    const named = addLayer(split.timeline, "Titles");
    expect(named.timeline.layers[1]!.name).toBe("Titles");
    expect(removeLayer(named.timeline, named.id).layers).toHaveLength(1);
    expect(updateLayerClip(split.timeline, clips[0]!.id, { end_s: 4.5 }).layers[0]!.clips[0]!.end_s).toBe(3);
  });
});

describe("sound tracks", () => {
  it("adds a sound on a track with room, else a new track", () => {
    let timeline = doc();
    const first = addAudioClip(timeline, null, { ...SOUND, text: "Welcome", voice: "Rachel" }, 1);
    timeline = first.timeline;
    expect(timeline.audio_tracks).toHaveLength(1);
    expect(timeline.audio_tracks[0]!.clips[0]!).toMatchObject({ start_s: 1, end_s: 3.5, text: "Welcome", voice: "Rachel" });
    const second = addAudioClip(timeline, null, SOUND, 2);
    expect(second.timeline.audio_tracks).toHaveLength(2);
    const track = addAudioTrack(second.timeline, "Effects");
    expect(track.timeline.audio_tracks[2]!.name).toBe("Effects");
    const split = splitAudioClip(second.timeline, first.id, 2);
    expect(split.timeline.audio_tracks[0]!.clips.map((clip) => [clip.start_s, clip.end_s, clip.in_s])).toEqual([
      [1, 2, 0],
      [2, 3.5, 1],
    ]);
    const quieter = updateAudioClip(split.timeline, first.id, { gain_db: -6, fade_out_s: 0.5 });
    expect(quieter.audio_tracks[0]!.clips[0]!).toMatchObject({ gain_db: -6, fade_out_s: 0.5 });
  });

  it("refuses a sound before there are scenes", () => {
    const empty = { ...doc(), scenes: [], duration_s: 0, popups: [], markers: [], captions: { enabled: true, style: doc().captions.style, cues: [] } };
    expect(videoEnd(empty)).toBe(0);
    expect(addAudioClip(empty, null, SOUND, 0).note).toMatch(/Add a picture or clip first/);
  });
});

describe("scene length, clip and adjust", () => {
  it("ripples everything after a scene without narration", () => {
    const timeline = doc();
    const result = setSceneLength(timeline, 0, 6, [], "ripple");
    expect(result.note).toBeNull();
    const next = result.timeline;
    expect(next.scenes.map((item) => [item.start_s, item.end_s])).toEqual([
      [0, 6],
      [6, 10],
      [10, 14],
    ]);
    expect(next.duration_s).toBe(14);
    expect(next.popups[0]!).toMatchObject({ start_s: 11, end_s: 13 });
    expect(next.markers[0]!.t).toBe(12);
    expect(next.captions.cues[0]!).toMatchObject({ start_s: 10.5, end_s: 11.5 });
    const shorter = setSceneLength(next, 0, 2, [], "ripple").timeline;
    expect(shorter.scenes[2]!.end_s).toBe(10);
    expect(setSceneLength(next, 0, 0.1, [], "ripple").timeline.scenes[0]!.end_s).toBe(0.5);
  });

  it("rolls the next cut with a narration and refuses the last scene", () => {
    const timeline = doc();
    const rolled = setSceneLength(timeline, 0, 5, [], "roll");
    expect(rolled.timeline.scenes[0]!.end_s).toBe(5);
    expect(rolled.timeline.scenes[1]!.start_s).toBe(5);
    expect(setSceneLength(timeline, 2, 5, [], "roll").note).toMatch(/last scene/);
  });

  it("changes a clip's speed and a scene's adjust", () => {
    const timeline = doc();
    const faster = setSceneClip(timeline, 1, { speed: 0.5, in_s: 2 });
    expect(faster.scenes[1]!.clip).toMatchObject({ speed: 0.5, in_s: 2, keep_sound: true });
    expect(setSceneClip(timeline, 0, { speed: 2 }).scenes[0]!.clip).toBeNull();
    const adjusted = setSceneAdjust(timeline, 0, { saturation: -1 });
    expect(adjusted.scenes[0]!.adjust).toEqual({ brightness: 0, contrast: 0, saturation: -1 });
    expect(setSceneAdjust(adjusted, 0, null).scenes[0]!.adjust).toBeNull();
    const shifted = rippleAfter(adjusted, 8, 2);
    expect(shifted.scenes[2]!.end_s).toBe(14);
  });
});
