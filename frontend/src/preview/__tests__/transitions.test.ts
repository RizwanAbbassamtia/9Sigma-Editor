import { describe, expect, it } from "vitest";

import { dipTerms } from "../transitions/_dip";
import { cubeTerms } from "../transitions/_cube";
import { overshootCoefficient, pushTerms, whipTerms, type PushDirection } from "../transitions/_push";
import { blurSamples } from "../transitions/_zoomblur";
import { fragmentSource, speedPerSecond, type TransitionFrame } from "../transitions/common";
import { directionalWarpTerms } from "../transitions/directional_warp";
import { frameTime, taper, transitionEase, transitionSpeed } from "../transitions/easing";
import { burnTerms } from "../transitions/film_burn";
import { glitchTerms } from "../transitions/glitch_subtle";
import { normalizeTransitionId } from "../transitions/legacy";
import { leakTerms } from "../transitions/light_leak_warm";
import { morphTerms } from "../transitions/morph_blend";
import { TRANSITION_FILTERS, TRANSITION_SPECS, fadeOutColour, resolveTransition } from "../transitions/registry";
import { coverScale, spinTerms } from "../transitions/spin_zoom";
import { zoomThroughTerms } from "../transitions/zoom_through";
import { close, closeAll, fixture } from "./fixtures";

interface TransitionsFixture {
  specs: { id: string; easing: string; params: Record<string, unknown>; seeded: boolean; family: string; duration_s: number; fade_out_colour: number[] }[];
  frames: { id: string; n: number; fps: number; k: number; seed: number; size: [number, number]; t_raw: number; t: number; speed: number; taper: number; terms: Record<string, unknown> }[];
  legacy: Record<string, string>;
  easing: { name: string; x: number; value: number; speed: number }[];
  misc: {
    frame_time: { k: number; n: number; value: number }[];
    overshoot: { overshoot: number; c: number }[];
    blur_samples: { size: [number, number]; span: number; degrees: number; value: number }[];
    cover_scale: { size: [number, number]; degrees: number; value: number }[];
  };
}

const data = fixture<TransitionsFixture>("transitions.json");
const TOL = 1e-6;

function frameOf(item: TransitionsFixture["frames"][number]): TransitionFrame {
  const resolved = resolveTransition(item.id);
  return {
    width: item.size[0],
    height: item.size[1],
    t: transitionEase(resolved.easing, item.t_raw),
    tRaw: item.t_raw,
    durationS: item.n / item.fps,
    fps: item.fps,
    easing: resolved.easing,
    params: resolved.params,
    seed: item.seed,
  };
}

const num = (value: unknown) => value as number;
const nums = (value: unknown) => value as number[];

function checkTerms(item: TransitionsFixture["frames"][number], frame: TransitionFrame): void {
  const terms = item.terms;
  const label = `${item.id} k=${item.k}/${item.n} ${item.size.join("x")}`;
  const id = item.id;
  if (id === "film_dissolve") {
    close(num(TRANSITION_FILTERS.film_dissolve.values(frame).uGain), num(terms.gain), TOL, `${label} gain`);
  } else if (id === "dip_black" || id === "dip_white") {
    const dip = dipTerms(frame, id === "dip_white" ? [1, 1, 1] : [0, 0, 0]);
    expect(dip.phase, label).toBe({ a: 0, hold: 1, b: 2 }[terms.phase as string]);
    close(dip.weight, num(terms.weight), TOL, `${label} weight`);
    closeAll(dip.colour, nums(terms.colour), TOL, `${label} colour`);
  } else if (id === "blur_dissolve") {
    const blur = TRANSITION_FILTERS.blur_dissolve.blur!(frame);
    close(blur.a, num(terms.sigma_a), TOL, `${label} sigma a`);
    close(blur.b, num(terms.sigma_b), TOL, `${label} sigma b`);
  } else if (id === "zoom_through") {
    const z = zoomThroughTerms(frame);
    close(z.scaleA, num(terms.scale_a), TOL, `${label} scale a`);
    close(z.scaleB, num(terms.scale_b), TOL, `${label} scale b`);
    close(z.m, num(terms.m), TOL, `${label} m`);
    close(z.span, num(terms.span), TOL, `${label} span`);
    expect(z.samples, label).toBe(num(terms.samples));
  } else if (id.startsWith("push_")) {
    const p = pushTerms(frame, id.slice(5) as PushDirection);
    close(p.p, num(terms.p), TOL, `${label} p`);
    close(p.blur, num(terms.blur), 1e-6, `${label} blur`);
    expect(p.box, label).toBe(num(terms.box));
    close(overshootCoefficient(0.02), num(terms.c), 1e-12, `${label} c`);
  } else if (id.startsWith("whip_")) {
    const w = whipTerms(frame, id.slice(5) as PushDirection);
    close(w.p, num(terms.p), TOL, `${label} p`);
    close(w.blur, num(terms.blur), 1e-6, `${label} blur`);
    expect(w.box, label).toBe(num(terms.box));
  } else if (id.startsWith("luma_wipe_")) {
    const values = TRANSITION_FILTERS[id as keyof typeof TRANSITION_FILTERS].values(frame);
    close(num(values.uEdge), num(terms.edge), TOL, `${label} edge`);
    close(num(values.uFeather), num(terms.feather), TOL, `${label} feather`);
  } else if (id === "directional_warp") {
    const d = directionalWarpTerms(frame);
    closeAll(d.v, nums(terms.v), TOL, `${label} v`);
    close(d.smoothness, num(terms.smoothness), TOL, `${label} smoothness`);
    close(d.offset, num(terms.offset), TOL, `${label} offset`);
  } else if (id === "light_leak_warm") {
    const leak = leakTerms(frame);
    close(leak.shaped, num(terms.shaped), TOL, `${label} shaped`);
    close(leak.intensity, num(terms.intensity), TOL, `${label} intensity`);
    closeAll(leak.c1, nums(terms.c1), TOL, `${label} c1`);
    closeAll(leak.c2, nums(terms.c2), TOL, `${label} c2`);
    closeAll(leak.colour1, nums(terms.colour_1), TOL, `${label} colour`);
    expect(leak.skip, label).toBe(terms.skip);
  } else if (id === "film_burn") {
    const burn = burnTerms(frame);
    expect(burn.phase, label).toBe(terms.phase === "a" ? 0 : 1);
    close(burn.s, num(terms.s), TOL, `${label} s`);
    if (terms.phase === "a") {
      close(burn.e, num(terms.e), TOL, `${label} e`);
      close(burn.w, num(terms.w), TOL, `${label} w`);
      close(burn.char, num(terms.char), TOL, `${label} char`);
    } else {
      close(burn.weight, num(terms.weight), TOL, `${label} weight`);
    }
  } else if (id === "glitch_subtle") {
    const g = glitchTerms(frame);
    expect(g.count, label).toBe(num(terms.count));
    close(g.total, num(terms.total), TOL, `${label} total`);
    close(g.cut, num(terms.cut), TOL, `${label} cut`);
    expect(g.clean, label).toBe(terms.clean);
    if (!g.clean) {
      close(g.env, num(terms.env), TOL, `${label} env`);
      expect(g.step, label).toBe(num(terms.step));
      expect(g.split, label).toBe(num(terms.split));
      expect(g.bands, label).toEqual(terms.bands);
    }
  } else if (id === "spin_zoom") {
    const s = spinTerms(frame);
    close(s.scaleA, num(terms.scale_a), TOL, `${label} scale a`);
    close(s.degreesA, num(terms.degrees_a), TOL, `${label} degrees a`);
    close(s.scaleB, num(terms.scale_b), TOL, `${label} scale b`);
    close(s.degreesB, num(terms.degrees_b), TOL, `${label} degrees b`);
    close(s.blurScale, num(terms.blur_scale), TOL, `${label} blur scale`);
    close(s.blurDegrees, num(terms.blur_degrees), TOL, `${label} blur degrees`);
    close(s.mix, num(terms.mix), TOL, `${label} mix`);
    expect(s.samples, label).toBe(num(terms.samples));
  } else if (id === "morph_blend") {
    const m = morphTerms(frame);
    expect(m.small, label).toEqual(terms.small);
    close(m.sigmaSmall, num(terms.sigma_small), TOL, `${label} sigma`);
    close(m.amount, num(terms.amount), TOL, `${label} amount`);
  } else if (id === "cube_left" || id === "cube_right") {
    const c = cubeTerms(frame, id === "cube_left" ? "left" : "right");
    close(c.z0, num(terms.z0), TOL, `${label} z0`);
    close(c.phi, num(terms.phi), TOL, `${label} phi`);
    close(c.centreZ, num(terms.centre_z), TOL, `${label} centre`);
    const faces = terms.faces as { face: string; visible: boolean; dst: number[][] | null; brightness: number }[];
    faces.forEach((face, i) => {
      const mine = c.faces[i]!;
      expect(mine.visible, `${label} face ${face.face}`).toBe(face.visible);
      if (face.visible) {
        close(mine.brightness, face.brightness, TOL, `${label} brightness ${face.face}`);
        // the reference stores the corners as float32
        mine.dst!.forEach((corner, j) => closeAll(corner, face.dst![j]!, 2e-3, `${label} corner ${face.face}${j}`));
        // the inverse homography maps the corners back to the picture's corners
        const inverse = mine.inverse!;
        const src = [
          [-0.5, -0.5],
          [frame.width - 0.5, -0.5],
          [frame.width - 0.5, frame.height - 0.5],
          [-0.5, frame.height - 0.5],
        ];
        mine.dst!.forEach(([x, y], j) => {
          const w = inverse[2][0] * x! + inverse[2][1] * y! + inverse[2][2];
          const u = (inverse[0][0] * x! + inverse[0][1] * y! + inverse[0][2]) / w;
          const v = (inverse[1][0] * x! + inverse[1][1] * y! + inverse[1][2]) / w;
          closeAll([u, v], src[j]!, 1e-6, `${label} inverse ${face.face}${j}`);
        });
      }
    });
  }
}

describe("the transition table (render/transitions/registry.py)", () => {
  it("has every v2 id with the same easing, parameters, seed use and end fade colour", () => {
    expect(Object.keys(TRANSITION_SPECS).sort()).toEqual(data.specs.map((s) => s.id).sort());
    for (const reference of data.specs) {
      const resolved = resolveTransition(reference.id);
      expect(resolved.id).toBe(reference.id);
      expect(resolved.easing, reference.id).toBe(reference.easing);
      expect(resolved.params, reference.id).toEqual(reference.params);
      expect(resolved.spec.seeded, reference.id).toBe(reference.seeded);
      expect(resolved.spec.family, reference.id).toBe(reference.family);
      expect(resolved.spec.durationS, reference.id).toBe(reference.duration_s);
      closeAll(fadeOutColour(reference.id), reference.fade_out_colour, 1e-12, `${reference.id} fade colour`);
      expect(TRANSITION_FILTERS[resolved.id].id).toBe(reference.id);
    }
  });

  it("maps old xfade names like the renderer", () => {
    for (const [name, id] of Object.entries(data.legacy)) expect(normalizeTransitionId(name), name).toBe(id);
  });

  it("builds a shader source for every filter", () => {
    for (const filter of Object.values(TRANSITION_FILTERS)) {
      const source = fragmentSource(filter);
      expect(source.startsWith("#version 300 es")).toBe(true);
      expect(source).toContain("void main()");
      expect(source).not.toMatch(/\bhalf\b|\binput\b|\boutput\b|\bfilter\b|\bsample\b/);
    }
  });
});

describe("easing, speed and progress (render/transitions/easing.py)", () => {
  it("every curve and its speed", () => {
    for (const item of data.easing) {
      close(transitionEase(item.name, item.x), item.value, TOL, `${item.name}(${item.x})`);
      close(transitionSpeed(item.name, item.x), item.speed, 1e-6, `speed ${item.name}(${item.x})`);
    }
  });
  it("frame_time, overshoot, blur samples and cover scale", () => {
    for (const item of data.misc.frame_time) close(frameTime(item.k, item.n), item.value, 1e-15, `frame_time ${item.k}/${item.n}`);
    for (const item of data.misc.overshoot) close(overshootCoefficient(item.overshoot), item.c, 1e-12, `overshoot ${item.overshoot}`);
    for (const item of data.misc.blur_samples) expect(blurSamples(item.size[0], item.size[1], item.span, item.degrees)).toBe(item.value);
    for (const item of data.misc.cover_scale) close(coverScale(item.size[0], item.size[1], item.degrees), item.value, TOL, `cover ${item.degrees}`);
  });
});

describe("per-frame terms of every transition", () => {
  it("match the Python reference frame by frame", () => {
    for (const item of data.frames) {
      const frame = frameOf(item);
      const label = `${item.id} k=${item.k}/${item.n}`;
      close(frame.t, item.t, 1e-9, `${label} t`);
      close(speedPerSecond(frame), item.speed, 1e-6, `${label} speed`);
      close(taper(frame.t), item.taper, 1e-9, `${label} taper`);
      checkTerms(item, frame);
    }
  });
});
