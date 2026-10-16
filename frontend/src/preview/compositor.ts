/**
 * What a compositor draws each frame, shared by the WebGL2 one (gpu/compositor.ts, PixiJS 8) and
 * the Canvas 2D fallback (canvas2d/compositor.ts).
 */
import type { ParallaxPlan } from "./parallax";
import type { Picture } from "./pictures";
import type { FrameState, SceneDesc } from "./types";

export interface SceneSource {
  /** the decoded picture; null while it loads */
  picture: Picture | null;
  /** the picture could not be loaded: the slate is drawn */
  missing: boolean;
  /** 2.5D parallax when the scene asks for it and its depth map is in */
  parallax: ParallaxPlan | null;
}

export type OverlayImage = ImageBitmap | HTMLCanvasElement | OffscreenCanvas;

export interface Overlay {
  key: string;
  /** changes when the image is redrawn (the GPU uploads it again) */
  version: number;
  image: OverlayImage;
  /** where it lands in output pixels */
  x: number;
  y: number;
  w: number;
  h: number;
  alpha: number;
  /** image pixels are premultiplied (ImageBitmaps made with premultiplyAlpha) */
  premultiplied: boolean;
}

/**
 * The "Picture missing" slate of the scene in one of the two scene slots (a cut shows two). The
 * engine keeps one canvas per slot and redraws it when the slot's scene changes (`version`), so
 * an animatic of a hundred scenes still holds two slates and two textures.
 */
export interface SlateImage {
  canvas: HTMLCanvasElement | OffscreenCanvas;
  /** changes whenever the canvas is drawn again (the GPU uploads it again) */
  version: number;
}

export interface DrawInput {
  state: FrameState;
  width: number;
  height: number;
  source(scene: SceneDesc): SceneSource;
  /** the slate of scene `sceneNumber` in slot `slot` (0: the frame's scene or a cut's outgoing one, 1: a cut's incoming one) */
  slate(sceneNumber: number, slot: 0 | 1): SlateImage;
  overlays: readonly Overlay[];
}

export interface Compositor {
  readonly kind: "webgl2" | "canvas2d";
  resize(width: number, height: number): void;
  draw(input: DrawInput): void;
  /** frees what is held for pictures no longer in the store */
  release(paths: Iterable<string>): void;
  /** true when this compositor draws the transition as a simpler stand-in */
  simplifies(transitionId: string): boolean;
  /** a transition the engine will need soon (compile its shader ahead of time) */
  prepare?(transitionId: string): void;
  /**
   * compile the shaders of a transition (and the passes it uses) now: the engine calls it while
   * the preview is idle, so a cut that comes up while playing does not wait for its first link
   */
  warm?(transitionId: string): void;
  /** upload a picture that has just loaded, rather than on the first frame that shows it */
  preload?(picture: Picture): void;
  /** upload a parallax plan's plates that have just been made */
  preloadPlates?(plan: ParallaxPlan): void;
  /** frees what is held for a parallax plan's plates (the plan left the scenes around the playhead) */
  releasePlates?(plan: ParallaxPlan): void;
  destroy(): void;
}
