/**
 * The transition table (backend/sigma_editor/render/transitions/registry.py): every v2 id, its
 * easing, its default parameters, whether it reads the seed, and the GLSL filter that draws it.
 * The catalogue (`/transitions/catalogue.json`, config/transitions.yaml) wins for `easing` and
 * `params` when it has the entry, exactly as `registry.resolve` does.
 */
import blurDissolve from "./blur_dissolve";
import { parseRgb, type RGB, type TransitionFilter, type TransitionParams } from "./common";
import crosswarp from "./crosswarp";
import cubeLeft from "./cube_left";
import cubeRight from "./cube_right";
import dipBlack from "./dip_black";
import dipWhite from "./dip_white";
import directionalWarp from "./directional_warp";
import { knownTransitionEasing } from "./easing";
import filmBurn from "./film_burn";
import filmDissolve from "./film_dissolve";
import glitchSubtle from "./glitch_subtle";
import { CATALOGUE_LEGACY, normalizeTransitionId, type TransitionId } from "./legacy";
import lightLeakWarm from "./light_leak_warm";
import lumaWipeDiag from "./luma_wipe_diag";
import lumaWipeDown from "./luma_wipe_down";
import lumaWipeLeft from "./luma_wipe_left";
import lumaWipeRight from "./luma_wipe_right";
import lumaWipeUp from "./luma_wipe_up";
import morphBlend from "./morph_blend";
import pushDown from "./push_down";
import pushLeft from "./push_left";
import pushRight from "./push_right";
import pushUp from "./push_up";
import spinZoom from "./spin_zoom";
import whipDown from "./whip_down";
import whipLeft from "./whip_left";
import whipRight from "./whip_right";
import whipUp from "./whip_up";
import zoomThrough from "./zoom_through";

export interface TransitionSpec {
  id: TransitionId;
  label: string;
  family: string;
  easing: string;
  durationS: number;
  energy: "calm" | "medium" | "high";
  params: TransitionParams;
  seeded: boolean;
}

function spec(
  id: TransitionId,
  label: string,
  family: string,
  easing: string,
  durationS: number,
  energy: TransitionSpec["energy"],
  params: TransitionParams,
  seeded = false,
): TransitionSpec {
  return { id, label, family, easing, durationS, energy, params, seeded };
}

const DIRS = ["left", "right", "up", "down"] as const;

export const TRANSITION_SPECS: Readonly<Record<TransitionId, TransitionSpec>> = Object.fromEntries(
  [
    spec("film_dissolve", "Film dissolve", "dissolve", "ease_in_out_cubic", 0.7, "calm", { luma_lift: 0.03 }),
    spec("dip_black", "Dip to black", "dips", "ease_in_out_cubic", 0.8, "calm", { color: [0, 0, 0], hold: 0.08 }),
    spec("dip_white", "Dip to white", "dips", "ease_in_out_cubic", 0.8, "calm", { color: [255, 255, 255], hold: 0.08 }),
    spec("blur_dissolve", "Blur dissolve", "blur", "ease_in_out_cubic", 0.7, "calm", { max_blur_px: 24 }),
    spec("zoom_through", "Zoom through", "zoom", "ease_in_out_cubic", 0.7, "medium", {
      a_scale_end: 1.25,
      b_scale_start: 1.15,
      crossfade_window: 0.4,
      radial_blur: true,
    }),
    ...DIRS.map((d) =>
      spec(`push_${d}` as TransitionId, `Push ${d}`, "push_whip", "ease_out_quart", 0.5, "medium", { direction: d, overshoot: 0.02, motion_blur: 0.5 }),
    ),
    ...DIRS.map((d) => spec(`whip_${d}` as TransitionId, `Whip ${d}`, "push_whip", "ease_in_out_cubic", 0.35, "high", { direction: d, motion_blur: 1.0 })),
    ...[...DIRS, "diag" as const].map((d) =>
      spec(`luma_wipe_${d}` as TransitionId, d === "diag" ? "Diagonal wipe" : `Wipe ${d}`, "luma_wipe", "ease_in_out_cubic", 0.6, "medium", {
        direction: d,
        feather: 0.12,
      }),
    ),
    spec("crosswarp", "Cross warp", "warps", "ease_in_out_cubic", 0.7, "medium", {}),
    spec("directional_warp", "Directional warp", "warps", "ease_in_out_cubic", 0.45, "medium", { direction: [-1.0, 1.0], smoothness: 0.5 }),
    spec("light_leak_warm", "Warm light leak", "leaks", "linear", 0.8, "medium", { peak_t: 0.5, color: [255, 176, 96], strength: 0.85 }, true),
    spec("film_burn", "Film burn", "leaks", "linear", 0.8, "high", { color: [255, 240, 220], edge_color: [255, 140, 40] }, true),
    spec("glitch_subtle", "Subtle glitch", "glitch", "snap", 0.3, "high", { glitch_frames: [6, 8], max_shift_px: 6, slices: [3, 5] }, true),
    spec("spin_zoom", "Spin zoom", "spin", "ease_in_out_cubic", 0.45, "high", { max_angle_deg: 12, a_scale_end: 1.4, b_angle_start_deg: -8 }),
    spec("morph_blend", "Morph blend", "warps", "ease_in_out_cubic", 0.8, "calm", { max_displacement_px: 18 }),
    spec("cube_left", "Cube turn left", "cube", "ease_in_out_cubic", 0.45, "medium", { direction: "left", perspective: 0.7 }),
    spec("cube_right", "Cube turn right", "cube", "ease_in_out_cubic", 0.45, "medium", { direction: "right", perspective: 0.7 }),
  ].map((item) => [item.id, item]),
) as Record<TransitionId, TransitionSpec>;

export const TRANSITION_FILTERS: Readonly<Record<TransitionId, TransitionFilter>> = {
  film_dissolve: filmDissolve,
  dip_black: dipBlack,
  dip_white: dipWhite,
  blur_dissolve: blurDissolve,
  zoom_through: zoomThrough,
  push_left: pushLeft,
  push_right: pushRight,
  push_up: pushUp,
  push_down: pushDown,
  whip_left: whipLeft,
  whip_right: whipRight,
  whip_up: whipUp,
  whip_down: whipDown,
  luma_wipe_left: lumaWipeLeft,
  luma_wipe_right: lumaWipeRight,
  luma_wipe_up: lumaWipeUp,
  luma_wipe_down: lumaWipeDown,
  luma_wipe_diag: lumaWipeDiag,
  crosswarp,
  directional_warp: directionalWarp,
  light_leak_warm: lightLeakWarm,
  film_burn: filmBurn,
  glitch_subtle: glitchSubtle,
  spin_zoom: spinZoom,
  morph_blend: morphBlend,
  cube_left: cubeLeft,
  cube_right: cubeRight,
};

/** One entry of /transitions/catalogue.json (only what the engine reads). */
export interface CatalogueEntry {
  id: string;
  easing?: string | null;
  params?: Record<string, unknown> | null;
}

/** /transitions/catalogue.json (only what the engine reads). */
export interface TransitionCatalogue {
  transitions?: CatalogueEntry[];
  legacy?: Record<string, string>;
}

export interface ResolvedTransition {
  id: TransitionId;
  easing: string;
  params: TransitionParams;
  spec: TransitionSpec;
  filter: TransitionFilter;
}

/**
 * The engine id with its easing and parameters settled: the spec's defaults, overridden by the
 * catalogue's entry (when given), then by `params`.
 */
export function resolveTransition(
  name: string | null | undefined,
  catalogue?: TransitionCatalogue | null,
  overrides?: { params?: TransitionParams; easing?: string | null },
): ResolvedTransition {
  const id = normalizeTransitionId(name, catalogue?.legacy ?? CATALOGUE_LEGACY);
  const base = TRANSITION_SPECS[id];
  const entry = catalogue?.transitions?.find((item) => item.id === id) ?? null;
  const params = { ...base.params, ...(entry?.params ?? {}), ...(overrides?.params ?? {}) };
  const easing = knownTransitionEasing(overrides?.easing || entry?.easing || base.easing, base.easing);
  return { id, easing, params, spec: base, filter: TRANSITION_FILTERS[id] };
}

export const FADE_OUT_COLOURS: Readonly<Partial<Record<TransitionId, RGB>>> = { dip_white: [1, 1, 1] };

/** The colour the last scene fades to: white for `dip_white`, the dip's `color`, else black. */
export function fadeOutColour(name: string | null | undefined, catalogue?: TransitionCatalogue | null): RGB {
  const resolved = resolveTransition(name, catalogue);
  const fallback = FADE_OUT_COLOURS[resolved.id] ?? [0, 0, 0];
  if (resolved.id !== "dip_black" && resolved.id !== "dip_white") return fallback;
  const params = resolved.params;
  for (const key of ["color", "colour"]) {
    const value = params[key];
    if (value !== null && value !== undefined) return parseRgb(value, fallback);
  }
  return fallback;
}

export function transitionIds(): TransitionId[] {
  return Object.keys(TRANSITION_SPECS) as TransitionId[];
}
