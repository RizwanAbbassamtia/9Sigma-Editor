import { describe, expect, it } from "vitest";

import { parseMusicStem, parsePopupPreview, parseTimelineBundle, parseWaveformPeaks } from "./preview";
import { fillIds, parseEditPayload, parseTimeline } from "./timeline";

const SCENE = {
  index: 0,
  image: "06_images/scene_01.png",
  start_s: 0,
  end_s: 4,
  motion: { preset: "zoom_in", start_rect: [0, 0, 1920, 1080], end_rect: [192, 108, 1536, 864] },
  transition_out: { type: "film_dissolve", duration_s: 0.6 },
};

const OLD_TIMELINE = {
  version: 1,
  project_id: "p1",
  format: "long",
  aspect: "16:9",
  fps: 30,
  width: 1920,
  height: 1080,
  duration_s: 4.8,
  voice: { path: "05_voice/voice.wav", start_s: 0 },
  music: { path: null },
  scenes: [SCENE],
  popups: [{ scene: 0, text: "Hi", start_s: 0.3, end_s: 2, anim: "pop" }],
  captions: { enabled: true, style: { font: "Noto Sans" }, cues: [{ start_s: 0, end_s: 1, text: "Hi there" }] },
};

describe("timeline.json from before M5", () => {
  it("reads with the M5 defaults", () => {
    const timeline = parseTimeline(OLD_TIMELINE)!;
    expect(timeline.revision).toBe(1);
    expect(timeline.markers).toEqual([]);
    expect(timeline.render).toEqual({ preview_gain_db: null });
    expect(timeline.voice.segments).toBeNull();
    expect(timeline.music.stem).toBeNull();
    expect(timeline.music.envelope).toBeNull();
    const scene = timeline.scenes[0]!;
    expect(scene.sentence_ids).toEqual([]);
    expect(scene.edited).toEqual([]);
    expect(scene.motion.ease).toBeNull();
    expect(timeline.captions.style).toMatchObject({ preset: "bold-outline", active_colour: "#FFE34D", layout: "phrase" });
    // ids are filled as the model fills them when it reads a timeline
    expect(timeline.popups[0]).toMatchObject({ id: "pop-001", anchor: null, edited: false, locked: false });
    expect(timeline.captions.cues[0]).toMatchObject({ id: "cue-0001", edited: false });
  });
});

describe("timeline.json with the M5 fields", () => {
  it("keeps them", () => {
    const timeline = parseTimeline({
      ...OLD_TIMELINE,
      revision: 7,
      voice: {
        path: "05_voice/voice.wav",
        start_s: 0,
        segments: [
          { sentence_id: "s2", src_start_s: 2, src_end_s: 3, at_s: 0 },
          { sentence_id: "s1", src_start_s: 0, src_end_s: 2, at_s: 1 },
          { sentence_id: "", src_start_s: 0, src_end_s: 2, at_s: 1 },
          { sentence_id: "s3", src_start_s: 4, src_end_s: 4, at_s: 3 },
        ],
      },
      music: { path: "x.mp3", stem: "07_edit/music_ducked.wav", envelope: "07_edit/music_envelope.json" },
      scenes: [{ ...SCENE, sentence_ids: ["s1", "s2"], edited: ["motion"], motion: { ...SCENE.motion, ease: "snap" } }],
      popups: [{ id: "intro", scene: 0, text: "Hi", start_s: 0.3, end_s: 2, anim: "pop", anchor: { sentence_id: "s1", offset_s: 0.3 }, edited: true, locked: true }],
      captions: {
        enabled: true,
        style: { font: "Noto Sans", preset: "active-word", active_colour: "#00FF00", layout: "single_word" },
        cues: [{ id: "c1", start_s: 0, end_s: 1, text: "Hi", edited: true }],
      },
      markers: [{ id: "m1", t: 2.5, label: "Reveal", colour: "#FF0000", chapter: true }, { t: 3 }, { label: "no time" }],
      render: { preview_gain_db: -3.2 },
    })!;
    expect(timeline.revision).toBe(7);
    // a clip without a sentence or without length is dropped (the model refuses them)
    expect(timeline.voice.segments).toEqual([
      { sentence_id: "s2", src_start_s: 2, src_end_s: 3, at_s: 0 },
      { sentence_id: "s1", src_start_s: 0, src_end_s: 2, at_s: 1 },
    ]);
    expect(timeline.music).toMatchObject({ stem: "07_edit/music_ducked.wav", envelope: "07_edit/music_envelope.json" });
    expect(timeline.scenes[0]).toMatchObject({ sentence_ids: ["s1", "s2"], edited: ["motion"] });
    expect(timeline.scenes[0]!.motion.ease).toBe("snap");
    expect(timeline.popups[0]).toMatchObject({ id: "intro", anchor: { sentence_id: "s1", offset_s: 0.3 }, edited: true, locked: true });
    expect(timeline.captions.style).toMatchObject({ preset: "active-word", active_colour: "#00FF00", layout: "single_word" });
    expect(timeline.captions.cues[0]).toMatchObject({ id: "c1", edited: true });
    expect(timeline.markers).toEqual([
      { id: "m1", t: 2.5, label: "Reveal", colour: "#FF0000", chapter: true },
      { id: "mark-002", t: 3, label: "", colour: "#60A5FA", chapter: false },
    ]);
    expect(timeline.render.preview_gain_db).toBe(-3.2);
  });

  it("drops an unknown curve or caption look", () => {
    const timeline = parseTimeline({
      ...OLD_TIMELINE,
      scenes: [{ ...SCENE, motion: { ...SCENE.motion, ease: "bouncy" } }],
      captions: { style: { preset: "rainbow", layout: "zigzag" }, cues: [] },
    })!;
    expect(timeline.scenes[0]!.motion.ease).toBeNull();
    expect(timeline.captions.style.preset).toBe("bold-outline");
    expect(timeline.captions.style.layout).toBe("phrase");
  });
});

describe("fillIds", () => {
  it("fills empty and repeated ids with positional ones, skipping ids in use", () => {
    const filled = fillIds([{ id: "" }, { id: "pop-001" }, { id: "pop-001" }, { id: "x" }], "pop", 3);
    expect(filled.map((item) => item.id)).toEqual(["pop-002", "pop-001", "pop-003", "x"]);
  });
});

describe("GET /api/projects/{id}/timeline", () => {
  it("parses the bundle", () => {
    const bundle = parseTimelineBundle({
      timeline: { ...OLD_TIMELINE, revision: 3 },
      revision: 4,
      sentences: [{ id: "s1", text: "Hi there.", start_s: 0.1, end_s: 1.2, words: [{ w: "Hi", start_s: 0.1, end_s: 0.4 }, { w: "there.", start_s: 0.5, end_s: 1.2 }] }],
      sections: [{ id: "intro", name: "The hook", start_scene: 0, end_scene: 0 }],
      pictures: [{ scene: 0, path: "06_images/scene_01.png", width: 2560, height: 1440, thumb_url: "/api/projects/p1/media/thumb?path=x&h=128" }, { scene: 1, path: "" }],
      media: { voice_url: "/v.wav", stem_url: null, envelope_url: null, fonts: [{ family: "Noto Sans", url: "/api/fonts/NotoSans.ttf" }, { family: "" }] },
      preview_gain_db: -1.5,
      virtual: true,
      timing_estimated: false,
      cue_words: { "cue-0001": [{ w: "Hi", start_s: 0.1, end_s: 0.4 }] },
    })!;
    expect(bundle.revision).toBe(4);
    expect(bundle.timeline.revision).toBe(4);
    expect(bundle.sentences[0]!.words).toEqual([
      { w: "Hi", start_s: 0.1, end_s: 0.4 },
      { w: "there.", start_s: 0.5, end_s: 1.2 },
    ]);
    expect(bundle.sections).toEqual([{ id: "intro", name: "The hook", start_scene: 0, end_scene: 0 }]);
    expect(bundle.pictures).toHaveLength(1);
    expect(bundle.media).toEqual({ voice_url: "/v.wav", stem_url: null, envelope_url: null, fonts: [{ family: "Noto Sans", url: "/api/fonts/NotoSans.ttf" }], music_url: null });
    expect(bundle.preview_gain_db).toBe(-1.5);
    expect(bundle.virtual).toBe(true);
    expect(bundle.cue_words["cue-0001"]).toEqual([{ w: "Hi", start_s: 0.1, end_s: 0.4 }]);
  });

  it("gives null without a timeline", () => {
    expect(parseTimelineBundle({ revision: 1 })).toBeNull();
    expect(parseTimelineBundle(null)).toBeNull();
  });

  it("falls back to the timeline's own gain", () => {
    const bundle = parseTimelineBundle({ timeline: { ...OLD_TIMELINE, render: { preview_gain_db: -4 } } })!;
    expect(bundle.preview_gain_db).toBe(-4);
    expect(bundle.media.voice_url).toBeNull();
  });
});

describe("the other preview answers", () => {
  it("parses peaks, popup pictures and the music stem", () => {
    expect(parseWaveformPeaks({ step_s: 0.01, min: [-0.5, -0.2], max: [0.5, 0.3], duration_s: 0.02, source: "voice" })).toEqual({
      step_s: 0.01,
      min: [-0.5, -0.2],
      max: [0.5, 0.3],
      duration_s: 0.02,
      source: "voice",
    });
    expect(parseWaveformPeaks({ min: [], max: [] })).toBeNull();
    expect(parsePopupPreview({ png_url: "/p.png", x: 60, y: 40, w: 300, h: 90 })).toEqual({ png_url: "/p.png", x: 60, y: 40, w: 300, h: 90 });
    expect(parsePopupPreview({ x: 1 })).toBeNull();
    expect(parseMusicStem({ stem_url: "/s.wav", envelope_url: "/e.json", preview_gain_db: -2 })).toEqual({ stem_url: "/s.wav", envelope_url: "/e.json", preview_gain_db: -2 });
    expect(parseMusicStem(undefined)).toEqual({ stem_url: null, envelope_url: null, preview_gain_db: null });
  });

  it("reads the stem and gain from the edit review payload", () => {
    const payload = parseEditPayload({ timeline: OLD_TIMELINE, stem_url: "/api/projects/p1/files/07_edit/music_ducked.wav", preview_gain_db: -1 })!;
    expect(payload.stem_url).toBe("/api/projects/p1/files/07_edit/music_ducked.wav");
    expect(payload.envelope_url).toBeNull();
    expect(payload.preview_gain_db).toBe(-1);
  });
});
