/**
 * Transition packs and camera motion styles. Mirrors backend/sigma_editor/models/transitions.py
 * (Pydantic v2, the source of truth): the pack a new edit picks its transitions from and the
 * camera move style (Settings > Render).
 */

export const TRANSITION_PACKS = ["cinematic", "clean", "dynamic"] as const;
export type TransitionPack = (typeof TRANSITION_PACKS)[number];

export const DEFAULT_TRANSITION_PACK: TransitionPack = "cinematic";

export const TRANSITION_PACK_LABELS: Record<TransitionPack, string> = {
  cinematic: "Cinematic",
  clean: "Clean",
  dynamic: "Dynamic",
};

export const TRANSITION_PACK_DESCRIPTIONS: Record<TransitionPack, string> = {
  cinematic: "Film dissolves, dips to black and soft wipes. Calm and warm; the default.",
  clean: "Short, plain cuts and dissolves. Good for explainers and tutorials.",
  dynamic: "Pushes, whips, zooms and glitches. Lively, for fast-paced videos.",
};

export const MOTION_STYLES = ["ken_burns", "parallax"] as const;
export type MotionStyle = (typeof MOTION_STYLES)[number];

export const DEFAULT_MOTION_STYLE: MotionStyle = "ken_burns";

export const MOTION_STYLE_LABELS: Record<MotionStyle, string> = {
  ken_burns: "Pan and zoom (Ken Burns)",
  parallax: "2.5D parallax",
};

export const MOTION_STYLE_DESCRIPTIONS: Record<MotionStyle, string> = {
  ken_burns: "The camera slowly pans and zooms over each picture.",
  parallax: "The picture is split into near and far layers that move at different speeds. Needs the depth model add-on; without it the pan and zoom move is used.",
};

export function isTransitionPack(value: unknown): value is TransitionPack {
  return typeof value === "string" && (TRANSITION_PACKS as readonly string[]).includes(value);
}

export function isMotionStyle(value: unknown): value is MotionStyle {
  return typeof value === "string" && (MOTION_STYLES as readonly string[]).includes(value);
}
