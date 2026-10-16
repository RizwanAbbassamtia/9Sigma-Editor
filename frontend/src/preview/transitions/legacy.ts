/**
 * Old FFmpeg `xfade` names -> Transitions v2 ids, exactly as the renderer maps them
 * (`models/timeline.py` `normalize_transition_id`: the contract's own table first, then the
 * `legacy:` table of config/transitions.yaml, anything else `film_dissolve`).
 */

export const TRANSITION_IDS = [
  "film_dissolve",
  "dip_black",
  "dip_white",
  "blur_dissolve",
  "zoom_through",
  "push_left",
  "push_right",
  "push_up",
  "push_down",
  "whip_left",
  "whip_right",
  "whip_up",
  "whip_down",
  "luma_wipe_left",
  "luma_wipe_right",
  "luma_wipe_up",
  "luma_wipe_down",
  "luma_wipe_diag",
  "crosswarp",
  "directional_warp",
  "light_leak_warm",
  "film_burn",
  "glitch_subtle",
  "spin_zoom",
  "morph_blend",
  "cube_left",
  "cube_right",
] as const;

export type TransitionId = (typeof TRANSITION_IDS)[number];
export const DEFAULT_TRANSITION: TransitionId = "film_dissolve";

/** `models/timeline.py` LEGACY_TRANSITIONS (checked before the catalogue). */
export const CONTRACT_LEGACY: Readonly<Record<string, TransitionId>> = {
  fade: "film_dissolve",
  wipeleft: "luma_wipe_left",
  slideleft: "push_left",
  zoomin: "zoom_through",
  fadeblack: "dip_black",
  fadewhite: "dip_white",
};

/** The `legacy:` table of config/transitions.yaml (also served in /transitions/catalogue.json). */
export const CATALOGUE_LEGACY: Readonly<Record<string, string>> = {
  fade: "film_dissolve",
  dissolve: "film_dissolve",
  fadegrays: "film_dissolve",
  fadeblack: "dip_black",
  fadewhite: "dip_white",
  wipeleft: "luma_wipe_left",
  wiperight: "luma_wipe_right",
  wipeup: "luma_wipe_up",
  wipedown: "luma_wipe_down",
  smoothleft: "luma_wipe_left",
  smoothright: "luma_wipe_right",
  smoothup: "luma_wipe_up",
  smoothdown: "luma_wipe_down",
  diagtl: "luma_wipe_diag",
  diagtr: "luma_wipe_diag",
  diagbl: "luma_wipe_diag",
  diagbr: "luma_wipe_diag",
  slideleft: "push_left",
  slideright: "push_right",
  slideup: "push_up",
  slidedown: "push_down",
  zoomin: "zoom_through",
  circleopen: "film_dissolve",
  circleclose: "film_dissolve",
  radial: "film_dissolve",
  pixelize: "film_dissolve",
  hlslice: "film_dissolve",
  vuslice: "film_dissolve",
  distance: "film_dissolve",
};

export function isTransitionId(value: string): value is TransitionId {
  return (TRANSITION_IDS as readonly string[]).includes(value);
}

/**
 * A v2 transition id for `value`: v2 ids pass through, old xfade names map to their v2 look,
 * anything else becomes `film_dissolve`. `catalogueLegacy` replaces the built-in copy of the
 * catalogue's table when the app has loaded /transitions/catalogue.json.
 */
export function normalizeTransitionId(value: unknown, catalogueLegacy?: Readonly<Record<string, string>> | null): TransitionId {
  const text = String(value ?? "").trim();
  if (isTransitionId(text)) return text;
  const key = text.toLowerCase().replace(/-/g, "_").replace(/ /g, "_");
  if (isTransitionId(key)) return key;
  if (Object.prototype.hasOwnProperty.call(CONTRACT_LEGACY, key)) return CONTRACT_LEGACY[key]!;
  const resolved = text ? catalogueResolve(text, catalogueLegacy ?? CATALOGUE_LEGACY) : null;
  return resolved && isTransitionId(resolved) ? resolved : DEFAULT_TRANSITION;
}

function has(table: Readonly<Record<string, string>>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(table, key);
}

/** `models/transitions.py` `TransitionCatalog.resolve`: an id, an old name or a loosely written id. */
export function catalogueResolve(name: string, legacy: Readonly<Record<string, string>>): string | null {
  if (!name) return null;
  if (isTransitionId(name)) return name;
  if (has(legacy, name)) return legacy[name]!;
  const clean = name.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (isTransitionId(clean)) return clean;
  const squashed = clean.replace(/_/g, "");
  if (has(legacy, squashed)) return legacy[squashed]!;
  if (has(legacy, clean)) return legacy[clean]!;
  return null;
}
