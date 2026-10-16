import { describe, expect, it } from "vitest";

import { buildDescription, cueLines, cutFrames, cutLimit, cutProgressAt, frameAt, frameForTime } from "../description";
import type { PreviewBundle, PreviewTimeline } from "../types";
import { close, closeAll, fixture } from "./fixtures";

interface LayoutCase {
  name: string;
  timeline: PreviewTimeline;
  total_frames: number;
  scenes: {
    number: number;
    index: number;
    frames: number;
    lead_in: number;
    tail_out: number;
    body_start: number;
    body: number;
    region: number[];
    image_size: [number, number];
    samples: { k: number; rect: number[] }[];
  }[];
  cuts: { number: number; frames: number; a: number; b: number | null; transition: string | null; easing: string | null; fade_colour: number[] | null; seed: number }[];
  drops: [number, number][];
  frames: (
    | { n: number; kind: "scene"; scene: number; clip_frame: number }
    | { n: number; kind: "cut"; cut: number; k: number; frames: number; a: number; a_frame: number; b: number; b_frame: number; t_raw: number; t: number }
    | { n: number; kind: "fade"; cut: number; k: number; frames: number; a: number; a_frame: number; weight: number }
  )[];
}

interface LayoutFixture {
  settings: { min_frames: number; max_share: number };
  cases: LayoutCase[];
}

const data = fixture<LayoutFixture>("layout.json");

function bundleOf(item: LayoutCase): PreviewBundle {
  return {
    timeline: item.timeline,
    pictures: item.scenes.map((scene) => ({ scene: scene.index, path: item.timeline.scenes[scene.index]!.image, width: scene.image_size[0], height: scene.image_size[1] })),
  };
}

describe("frame layout (render/assembly.py plan_layout)", () => {
  for (const item of data.cases) {
    it(`${item.name}: scenes, cuts and every output frame`, () => {
      const desc = buildDescription(bundleOf(item), { settings: { minFrames: data.settings.min_frames, maxShare: data.settings.max_share } });
      expect(desc.totalFrames).toBe(item.total_frames);
      expect(desc.scenes.map((s) => s.number)).toEqual(item.scenes.map((s) => s.number));
      desc.scenes.forEach((scene, i) => {
        const reference = item.scenes[i]!;
        expect(scene.index).toBe(reference.index);
        expect(scene.clipFrames, `scene ${reference.number} frames`).toBe(reference.frames);
        expect(scene.leadIn, `scene ${reference.number} lead in`).toBe(reference.lead_in);
        expect(scene.tailOut, `scene ${reference.number} tail out`).toBe(reference.tail_out);
        expect(scene.bodyStart, `scene ${reference.number} body start`).toBe(reference.body_start);
        expect(scene.body, `scene ${reference.number} body`).toBe(reference.body);
        closeAll(scene.path.bounds ?? [], reference.region, 1e-9, `scene ${reference.number} region`);
        for (const sample of reference.samples) {
          closeAll(scene.path.frameRect(sample.k, scene.clipFrames, desc.fps), sample.rect, 1e-6, `scene ${reference.number} k=${sample.k}`);
        }
      });
      expect(desc.cuts.length).toBe(item.cuts.length);
      desc.cuts.forEach((cut, i) => {
        const reference = item.cuts[i]!;
        expect(cut.number).toBe(reference.number);
        expect(cut.frames).toBe(reference.frames);
        expect(desc.scenes[cut.from]!.number).toBe(reference.a);
        expect(cut.to === null ? null : desc.scenes[cut.to]!.number).toBe(reference.b);
        expect(cut.transition?.id ?? null).toBe(reference.transition);
        expect(cut.transition?.easing ?? null).toBe(reference.easing);
        if (reference.fade_colour) closeAll(cut.fadeColour ?? [], reference.fade_colour, 1e-9, "fade colour");
        else expect(cut.fadeColour).toBeNull();
        expect(cut.seed).toBe(reference.seed);
      });
      for (const expected of item.frames) {
        const state = frameAt(desc, expected.n);
        const label = `${item.name} frame ${expected.n}`;
        expect(state.kind, label).toBe(expected.kind);
        if (expected.kind === "scene" && state.kind === "scene") {
          expect(state.a.scene.number, label).toBe(expected.scene);
          expect(state.a.clipFrame, label).toBe(expected.clip_frame);
        } else if (expected.kind === "cut" && state.kind === "cut") {
          expect(state.cut.number, label).toBe(expected.cut);
          expect(state.k, label).toBe(expected.k);
          expect(state.a.scene.number, label).toBe(expected.a);
          expect(state.a.clipFrame, label).toBe(expected.a_frame);
          expect(state.b.scene.number, label).toBe(expected.b);
          expect(state.b.clipFrame, label).toBe(expected.b_frame);
          close(state.tRaw, expected.t_raw, 1e-12, `${label} t_raw`);
          close(state.t, expected.t, 1e-9, `${label} t`);
        } else if (expected.kind === "fade" && state.kind === "fade") {
          expect(state.cut.number, label).toBe(expected.cut);
          expect(state.k, label).toBe(expected.k);
          expect(state.a.clipFrame, label).toBe(expected.a_frame);
          close(state.weight, expected.weight, 1e-12, `${label} weight`);
        }
      }
    });
  }

  it("draws the frame of the video's own frame times at any display rate", () => {
    const item = data.cases[0]!;
    const desc = buildDescription(bundleOf(item));
    expect(frameForTime(desc, 0)).toBe(0);
    expect(frameForTime(desc, 1 / 30)).toBe(1);
    expect(frameForTime(desc, 0.0333)).toBe(0);
    expect(frameForTime(desc, 0.03333333)).toBe(1);
    expect(frameForTime(desc, 1e9)).toBe(desc.totalFrames - 1);
    const cut = desc.cuts[0]!;
    const t0 = cut.startFrame / desc.fps;
    for (let k = 0; k < cut.frames; k++) close(cutProgressAt(cut, desc.fps, t0 + k / desc.fps), (k + 0.5) / cut.frames, 1e-9, `cut progress ${k}`);
  });

  it("takes loudnorm's gain curve only for the sound it was measured for", () => {
    const item = data.cases[0]!;
    const timeline = { ...item.timeline, music: null, render: { preview_gain_db: 6 } };
    const curve = { step_s: 0.1, gain_db: [5.5, 6.5] };
    const plain = buildDescription({ ...bundleOf(item), timeline, preview_gain_curve: curve });
    expect(plain.audio.gainDb).toBe(6);
    expect(plain.audio.gainCurve).toEqual({ stepS: 0.1, gainDb: [5.5, 6.5] });
    // music whose stem is not current: the raw track plays, and the curve was not measured for it
    const raw = buildDescription({ ...bundleOf(item), timeline: { ...timeline, music: { path: "music/calm.mp3" } }, media: { stem_url: null }, preview_gain_curve: curve });
    expect(raw.audio.gainCurve).toBeNull();
    const broken = buildDescription({ ...bundleOf(item), timeline, preview_gain_curve: { step_s: 0, gain_db: [1] } });
    expect(broken.audio.gainCurve).toBeNull();
    expect(buildDescription({ ...bundleOf(item), timeline }).audio.gainCurve).toBeNull();
  });

  it("plays the raw music track from the bundle's music_url while the stem is not current", () => {
    const item = data.cases[0]!;
    const projectId = item.timeline.project_id;
    // the renderer's music path: absolute, in the channel's music folder (never a project file)
    const music = { path: "F:\\sge_tmp\\m5\\parity\\env\\music\\parity-bed.wav", start_s: 2, gain_db: -20, fade_in_s: 1, fade_out_s: 2 };
    const timeline = { ...item.timeline, music };
    const served = "/api/projects/p1/media/music?v=abc123";
    const raw = buildDescription({ ...bundleOf(item), timeline, media: { stem_url: null, music_url: served } });
    expect(raw.audio.music).toEqual({ url: served, startS: 2, gainDb: -20, fadeInS: 1, fadeOutS: 2 });
    // no music_url (an older server, or the track is not on this PC): no music rather than a
    // request the project files route refuses
    const older = buildDescription({ ...bundleOf(item), timeline, media: { stem_url: null } });
    expect(older.audio.music).toBeNull();
    for (const path of ["C:/music/a.wav", "\\\\nas\\music\\a.wav", "/home/me/a.wav"]) {
      expect(buildDescription({ ...bundleOf(item), timeline: { ...timeline, music: { ...music, path } } }).audio.music, path).toBeNull();
    }
    // a track kept inside the project folder still comes through the project files route
    const inside = buildDescription({ ...bundleOf(item), timeline: { ...timeline, music: { ...music, path: "music/calm.mp3" } } });
    expect(inside.audio.music?.url).toBe(`/api/projects/${projectId}/files/music/calm.mp3`);
    // with a current stem the stem plays and the raw track is not used
    const stem = buildDescription({ ...bundleOf(item), timeline, media: { stem_url: "/api/projects/p1/files/07_edit/music_ducked.wav?v=k", music_url: served } });
    expect(stem.audio.music).toBeNull();
    expect(stem.audio.stemUrl).toBe("/api/projects/p1/files/07_edit/music_ducked.wav?v=k");
  });

  it("caps cuts like the renderer", () => {
    expect(cutLimit(4.0, 120, 30, 0.25)).toBe(30);
    expect(cutLimit(1.0, 30, 30, 0.25)).toBe(8);
    expect(cutLimit(100, 3000, 30, 0.25)).toBe(90);
    expect(cutLimit(1.0, 5, 30, 0.25)).toBe(2);
    expect(cutFrames(0.7, 30, 2, 100)).toBe(21);
    expect(cutFrames(0.03, 30, 2, 100)).toBe(0);
    expect(cutFrames(0.75, 30, 2, 100)).toBe(22);
    expect(cutFrames(0.85, 30, 2, 100)).toBe(26);
  });

  it("splits caption cues on \\N and keeps them as the ASS writer does", () => {
    expect(cueLines("one two\\Nthree")).toEqual(["one two", "three"]);
    expect(cueLines("a {b}\nc")).toEqual(["a (b)", "c"]);
  });
});
