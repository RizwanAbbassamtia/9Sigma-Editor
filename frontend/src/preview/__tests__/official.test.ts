/**
 * The backend's own fixtures: tools/make_preview_fixtures.py writes src/preview/fixtures/*.json
 * from the code the renderer runs (docs/M5-CONTRACT.md section 4), and the engine has to agree
 * with them to 1e-6. Each block is skipped when its file is not there (the generator has not run).
 * The engine's wider fixtures (whole frame layouts, transition terms, depth layers) are the
 * engine_*.json files of the same folder, made by the same tool (see fixtures.ts).
 */
import { describe, expect, it } from "vitest";

import { dbToGain, envelopeGainDb } from "../audio";
import { clipTransitions, sceneBounds } from "../bounds";
import { buildDescription, cueLines, cutFrames, cutLimit, cutProgressAt } from "../description";
import { MOTION_PRESETS, MotionPath, coverRegion, ease, rectInPixels, shakeOffsets } from "../motion";
import { CAPTION_LOOKS, captionEvents, cueTokens, popupFrame } from "../text";
import { frameTime, taper, transitionEase, transitionSpeed } from "../transitions/easing";
import { normalizeTransitionId } from "../transitions/legacy";
import { TRANSITION_SPECS, fadeOutColour, resolveTransition } from "../transitions/registry";
import type { CueDesc, CutDesc, PreviewBundle } from "../types";
import { close, closeAll } from "./fixtures";

const FILES = import.meta.glob("../fixtures/*.json", { eager: true, import: "default" }) as Record<string, unknown>;

function official<T>(name: string): T | null {
  return (FILES[`../fixtures/${name}.json`] as T | undefined) ?? null;
}

const TOL = 1e-6;

// ease.json -----------------------------------------------------------------------------------

interface EaseFixture {
  t: number[];
  values: Record<string, number[]>;
  transition: { values: Record<string, number[]>; speed: Record<string, number[]>; speed_t: number[]; taper: number[] };
}

const easeData = official<EaseFixture>("ease");

describe.skipIf(!easeData)("official ease.json", () => {
  it("camera curves (render/motion.py ease)", () => {
    for (const [name, values] of Object.entries(easeData!.values)) {
      closeAll(
        easeData!.t.map((t) => ease(name, t)),
        values,
        TOL,
        name,
      );
    }
  });

  it("transition curves, their speed and the taper (render/transitions/easing.py)", () => {
    const curves = easeData!.transition;
    for (const [name, values] of Object.entries(curves.values)) {
      closeAll(
        easeData!.t.map((t) => transitionEase(name, t)),
        values,
        TOL,
        name,
      );
    }
    for (const [name, values] of Object.entries(curves.speed)) {
      closeAll(
        curves.speed_t.map((t) => transitionSpeed(name, t)),
        values,
        TOL,
        `${name} speed`,
      );
    }
    closeAll(
      curves.speed_t.map((t) => taper(t)),
      curves.taper,
      TOL,
      "taper",
    );
  });
});

// motion.json ---------------------------------------------------------------------------------

interface MotionCase {
  name: string;
  preset: string;
  ease: string | null;
  easing: string;
  handheld: number;
  image: [number, number];
  frame: [number, number];
  cover: number[];
  start_rect: number[];
  end_rect: number[];
  seed: number;
  fps: number;
  frames: number;
  t: number[];
  eased: number[];
  rects: number[][];
  shake?: number[][];
}

interface MotionFixture {
  handheld: { amplitude: number };
  presets: { id: string; label: string; energy: string; start_rect: number[]; end_rect: number[]; easing: string; handheld: boolean }[];
  cases: MotionCase[];
}

const motionData = official<MotionFixture>("motion");

describe.skipIf(!motionData)("official motion.json", () => {
  it("has the same presets", () => {
    expect(Object.keys(MOTION_PRESETS)).toEqual(motionData!.presets.map((preset) => preset.id));
    for (const preset of motionData!.presets) {
      const spec = MOTION_PRESETS[preset.id]!;
      expect(spec.label, preset.id).toBe(preset.label);
      expect(spec.energy, preset.id).toBe(preset.energy);
      expect(spec.easing, preset.id).toBe(preset.easing);
      expect(spec.handheld, preset.id).toBe(preset.handheld);
      closeAll(spec.start, preset.start_rect, 1e-12, `${preset.id} start`);
      closeAll(spec.end, preset.end_rect, 1e-12, `${preset.id} end`);
    }
  });

  for (const item of motionData?.cases ?? []) {
    it(`${item.name}: the window of every clip frame`, () => {
      const cover = coverRegion(item.image[0], item.image[1], item.frame[0], item.frame[1]);
      closeAll(cover, item.cover, TOL, "cover region");
      const spec = MOTION_PRESETS[item.preset]!;
      const fromPreset = rectInPixels(spec.start, item.image[0], item.image[1], item.frame[0], item.frame[1]);
      // the cases built from the preset's fractions (the others give their own rectangles)
      if (fromPreset.every((value, i) => Math.abs(value - item.start_rect[i]!) <= TOL)) {
        closeAll(rectInPixels(spec.end, item.image[0], item.image[1], item.frame[0], item.frame[1]), item.end_rect, TOL, "end rect");
      }
      const path = MotionPath.forPreset(item.preset, item.start_rect, item.end_rect, {
        seed: item.seed,
        bounds: cover,
        easing: item.ease,
        handheldAmplitude: motionData!.handheld.amplitude,
      });
      expect(path.easing).toBe(item.easing);
      close(path.handheld, item.handheld, 1e-12, "handheld");
      closeAll(
        item.t.map((t) => path.eased(t)),
        item.eased,
        TOL,
        "eased",
      );
      item.rects.forEach((rect, k) => closeAll(path.frameRect(k, item.frames, item.fps), rect, TOL, `${item.name} k=${k}`));
      item.shake?.forEach((pair, k) => closeAll(shakeOffsets(k / item.fps, item.seed), pair, TOL, `${item.name} shake k=${k}`));
    });
  }
});

// bounds.json ---------------------------------------------------------------------------------

interface BoundsCase {
  name: string;
  aspect: string;
  fps: number;
  tail_s: number;
  max_share: number;
  min_frames: number;
  timing: { duration_s: number; sentences: { id: string; start_s: number; end_s: number }[] };
  scenes: { sentence_ids: string[]; est_duration_s: number; transition: string; transition_s: number }[];
  bounds: [number, number][];
  transitions_s: number[];
  timeline_transitions: string[];
  layout: {
    total_frames: number;
    starts: number[];
    lengths: number[];
    kept: number[];
    cut_n: Record<string, number>;
    fade_n: number;
    clips: { index: number; start: number; length: number; n_in: number; h_in: number; n_out: number; tail_out: number; tail: number; frames: number; first_frame: number }[];
    cuts: { after: number; before: number | null; first_frame: number; frames: number; h: number; transition: string; fade_colour: number[] | null }[];
  };
}

const boundsData = official<{ cases: BoundsCase[] }>("bounds");

function boundsBundle(item: BoundsCase): PreviewBundle {
  const portrait = item.aspect === "9:16";
  return {
    timeline: {
      project_id: item.name,
      aspect: item.aspect,
      fps: item.fps,
      width: portrait ? 1080 : 1920,
      height: portrait ? 1920 : 1080,
      duration_s: item.bounds[item.bounds.length - 1]![1],
      scenes: item.bounds.map(([start, end], i) => ({
        index: i,
        image: `06_images/scene_${String(i + 1).padStart(2, "0")}.png`,
        start_s: start,
        end_s: end,
        motion: { preset: "hold", start_rect: [0, 0, 16, 9], end_rect: [0, 0, 16, 9] },
        // the storyboard's own name (an old xfade name in places): the engine maps it as the model does
        transition_out: { type: item.scenes[i]!.transition, duration_s: item.transitions_s[i]! },
      })),
    },
  };
}

describe.skipIf(!boundsData)("official bounds.json", () => {
  for (const item of boundsData?.cases ?? []) {
    it(`${item.name}: scene bounds, cut lengths and the renderer's frame layout`, () => {
      const bounds = sceneBounds(item.scenes, item.timing, item.tail_s);
      closeAll(bounds.flat(), item.bounds.flat(), 1e-9, "bounds");
      const lengths = clipTransitions(
        item.bounds,
        item.scenes.map((scene) => scene.transition_s),
        { maxShare: item.max_share, minS: item.min_frames / Math.max(item.fps, 1) },
      );
      closeAll(lengths, item.transitions_s, 1e-9, "transitions_s");
      item.scenes.forEach((scene, i) => expect(normalizeTransitionId(scene.transition), `scene ${i + 1}`).toBe(item.timeline_transitions[i]));

      const layout = item.layout;
      const desc = buildDescription(boundsBundle(item), { settings: { minFrames: item.min_frames, maxShare: item.max_share } });
      expect(desc.totalFrames).toBe(layout.total_frames);
      expect(desc.scenes.map((scene) => scene.index)).toEqual(layout.kept);
      layout.clips.forEach((clip, i) => {
        const scene = desc.scenes[i]!;
        const label = `${item.name} clip ${clip.index}`;
        expect(scene.index, label).toBe(clip.index);
        expect(scene.startFrame, `${label} start`).toBe(clip.start);
        expect(scene.startFrame, `${label} start`).toBe(layout.starts[clip.index]);
        expect(scene.length, `${label} length`).toBe(clip.length);
        expect(scene.length, `${label} length`).toBe(layout.lengths[clip.index]);
        expect(scene.leadIn, `${label} h_in`).toBe(clip.h_in);
        expect(scene.tailOut, `${label} tail_out`).toBe(clip.tail_out);
        expect(scene.clipFrames, `${label} frames`).toBe(clip.frames);
        expect(scene.startFrame - scene.leadIn, `${label} first frame`).toBe(clip.first_frame);
        // the body is the clip less its incoming cut and the frames that feed the outgoing cut (or the end fade)
        expect(scene.body, `${label} body`).toBe(clip.frames - clip.n_in - clip.tail);
      });
      expect(desc.cuts.length).toBe(layout.cuts.length);
      layout.cuts.forEach((reference, i) => {
        const cut: CutDesc = desc.cuts[i]!;
        const label = `${item.name} cut after ${reference.after}`;
        expect(desc.scenes[cut.from]!.index, label).toBe(reference.after);
        expect(cut.to === null ? null : desc.scenes[cut.to]!.index, label).toBe(reference.before);
        expect(cut.startFrame, `${label} first frame`).toBe(reference.first_frame);
        expect(cut.frames, `${label} frames`).toBe(reference.frames);
        if (reference.before === null) {
          expect(cut.transition, label).toBeNull();
          expect(cut.frames, `${label} fade`).toBe(layout.fade_n);
          closeAll(cut.fadeColour ?? [], reference.fade_colour ?? [], 1e-9, `${label} fade colour`);
        } else {
          expect(cut.transition?.id, label).toBe(reference.transition);
          expect(Math.floor(cut.frames / 2), `${label} h`).toBe(reference.h);
          expect(cut.frames, `${label} cut_n`).toBe(layout.cut_n[String(reference.after)]);
        }
      });
      const nonZero = Object.values(layout.cut_n).filter((n) => n > 0).length;
      expect(desc.cuts.filter((cut) => cut.to !== null).length).toBe(nonZero);
      if (!layout.fade_n) expect(desc.cuts.some((cut) => cut.to === null)).toBe(false);
    });
  }
});

// cuts.json -----------------------------------------------------------------------------------

interface CutsFixture {
  fps: number;
  n: number;
  h: number;
  transitions: {
    id: string;
    easing: string;
    seeded: boolean;
    default_duration_s: number;
    params: Record<string, unknown>;
    fade_colour: number[];
    frames: { k: number; t_raw: number; t: number }[];
  }[];
  legacy: Record<string, string>;
  lengths: { duration_s: number; fps: number; shorter_s: number; shorter_frames: number; max_share: number; min_frames: number; longest: number; n: number }[];
  preview_time: { T0: number; T: number; D: number; fps: number; t_raw: number }[];
}

const cutsData = official<CutsFixture>("cuts");

describe.skipIf(!cutsData)("official cuts.json", () => {
  it("every transition id: easing, seed, defaults, end colour and its frames", () => {
    expect(Object.keys(TRANSITION_SPECS).sort()).toEqual(cutsData!.transitions.map((row) => row.id).sort());
    for (const row of cutsData!.transitions) {
      const resolved = resolveTransition(row.id);
      expect(resolved.id).toBe(row.id);
      expect(resolved.easing, row.id).toBe(row.easing);
      expect(resolved.spec.seeded, row.id).toBe(row.seeded);
      close(resolved.spec.durationS, row.default_duration_s, 1e-12, `${row.id} duration`);
      expect(JSON.parse(JSON.stringify(resolved.params)), row.id).toEqual(row.params);
      closeAll(fadeOutColour(row.id), row.fade_colour, 1e-9, `${row.id} fade colour`);
      expect(row.frames.length).toBe(cutsData!.n);
      for (const frame of row.frames) {
        close(frameTime(frame.k, cutsData!.n), frame.t_raw, 1e-12, `${row.id} t_raw ${frame.k}`);
        close(transitionEase(resolved.easing, frame.t_raw), frame.t, TOL, `${row.id} t ${frame.k}`);
      }
    }
  });

  it("maps every old name as the timeline model does", () => {
    for (const [name, target] of Object.entries(cutsData!.legacy)) expect(normalizeTransitionId(name), name).toBe(target);
  });

  it("caps cut lengths like the renderer", () => {
    for (const row of cutsData!.lengths) {
      const longest = cutLimit(row.shorter_s, row.shorter_frames, row.fps, row.max_share);
      expect(longest, JSON.stringify(row)).toBe(row.longest);
      expect(cutFrames(row.duration_s, row.fps, row.min_frames, longest), JSON.stringify(row)).toBe(row.n);
    }
  });

  it("reads a cut's progress between frames", () => {
    for (const row of cutsData!.preview_time) {
      const cut = { startFrame: Math.round(row.T0 * row.fps), durationS: row.D } as CutDesc;
      close(cutProgressAt(cut, row.fps, row.T), row.t_raw, 1e-9, `T=${row.T}`);
    }
  });
});

// popups.json ---------------------------------------------------------------------------------

interface PopupsFixture {
  look: { fade_s: number; slide_s: number; slide_px: number; margin_px: number };
  cases: {
    anim: "slide_left" | "fade" | "pop";
    scale: number;
    start_s: number;
    end_s: number;
    x0: number;
    y0: number;
    fps: number;
    frames: { n: number; t: number; visible: boolean; k: number | null; alpha: number; x: number; y: number }[];
  }[];
}

const popupsData = official<PopupsFixture>("popups");

describe.skipIf(!popupsData)("official popups.json", () => {
  it("every frame around each popup: shown, alpha and position", () => {
    const look = popupsData!.look;
    const settings = { popupFadeS: look.fade_s, popupSlideS: look.slide_s, popupSlidePx: look.slide_px };
    for (const item of popupsData!.cases) {
      const frame = { width: 1920 * item.scale, height: 1080 * item.scale };
      for (const expected of item.frames) {
        const label = `${item.anim} x${item.scale} ${item.start_s}-${item.end_s} n=${expected.n}`;
        const got = popupFrame({ anim: item.anim, startS: item.start_s, endS: item.end_s }, expected.n, item.fps, frame, { x: item.x0, y: item.y0 }, settings);
        expect(got.visible, label).toBe(expected.visible);
        close(got.visible ? got.alpha : 0, expected.alpha, TOL, `${label} alpha`);
        close(got.x, expected.x, TOL, `${label} x`);
        close(got.y, expected.y, TOL, `${label} y`);
      }
    }
  });
});

// ducking.json --------------------------------------------------------------------------------

interface DuckingFixture {
  settings: { step_s: number };
  gain_db: number[];
  read: { t: number[]; gain_db: number[]; gain: number[] };
}

const duckingData = official<DuckingFixture>("ducking");

describe.skipIf(!duckingData)("official ducking.json", () => {
  it("reads the music envelope like render/music.py", () => {
    const envelope = { step_s: duckingData!.settings.step_s, gain_db: duckingData!.gain_db };
    const read = duckingData!.read;
    closeAll(
      read.t.map((t) => envelopeGainDb(envelope, t)),
      read.gain_db,
      TOL,
      "gain dB",
    );
    closeAll(
      read.t.map((t) => dbToGain(envelopeGainDb(envelope, t))),
      read.gain,
      TOL,
      "gain",
    );
  });
});

// captions.json -------------------------------------------------------------------------------

interface CaptionsFixture {
  presets: { id: string; layout: string; active_colour: string; pop_scale: number; pop_s: number; box: boolean; box_colour: string; box_opacity: number; box_padding_px: number }[];
  cases: {
    name: string;
    language: string;
    space_dependent: boolean;
    cues: { start_s: number; end_s: number; text: string }[];
    tokens: [string, boolean][][];
    words: { text: string; start_s: number; end_s: number }[][];
    events: Record<string, { cue: number; start_s: number; end_s: number; active: number | null; text: string }[]>;
  }[];
}

const captionsData = official<CaptionsFixture>("captions");

describe.skipIf(!captionsData)("official captions.json", () => {
  it("has the same looks", () => {
    for (const preset of captionsData!.presets) {
      const look = CAPTION_LOOKS[preset.id]!;
      expect(look, preset.id).toBeDefined();
      expect(look.layout, preset.id).toBe(preset.layout);
      expect(look.activeColour.toUpperCase(), preset.id).toBe(preset.active_colour.toUpperCase());
      close(look.popScale, preset.pop_scale, 1e-12, `${preset.id} pop scale`);
      close(look.popS, preset.pop_s, 1e-12, `${preset.id} pop seconds`);
      expect(look.box, preset.id).toBe(preset.box);
      expect(look.boxColour.toUpperCase(), preset.id).toBe(preset.box_colour.toUpperCase());
      close(look.boxOpacity, preset.box_opacity, 1e-12, `${preset.id} box opacity`);
      close(look.boxPaddingPx, preset.box_padding_px, 1e-12, `${preset.id} box padding`);
    }
  });

  for (const item of captionsData?.cases ?? []) {
    it(`${item.name}: cue pieces and the events of every look`, () => {
      const font = item.space_dependent ? "Noto Sans" : "Noto Sans JP";
      item.cues.forEach((cue, i) => expect(cueTokens(cue.text, item.space_dependent), `${item.name} cue ${i}`).toEqual(item.tokens[i]));
      for (const [preset, expected] of Object.entries(item.events)) {
        const got: { cue: number; start_s: number; end_s: number; active: number | null }[] = [];
        item.cues.forEach((cue, i) => {
          const desc: CueDesc = { key: `cue-${i}`, index: i, startS: cue.start_s, endS: cue.end_s, lines: cueLines(cue.text), text: cue.text };
          const words = item.words[i]!.map((word) => ({ w: word.text, start_s: word.start_s, end_s: word.end_s }));
          for (const event of captionEvents(desc, words, { preset, font })) got.push({ cue: i, start_s: event.startS, end_s: event.endS, active: event.active });
        });
        expect(got.length, `${item.name} ${preset}`).toBe(expected.length);
        got.forEach((event, j) => {
          const reference = expected[j]!;
          const label = `${item.name} ${preset} event ${j}`;
          expect(event.cue, label).toBe(reference.cue);
          close(event.start_s, reference.start_s, 1e-9, `${label} start`);
          close(event.end_s, reference.end_s, 1e-9, `${label} end`);
          expect(event.active, label).toBe(reference.active);
        });
      }
    });
  }
});
