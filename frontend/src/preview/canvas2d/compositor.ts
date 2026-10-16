/**
 * The Canvas 2D fallback (design 5.7), used when WebGL2 is blocked or a lost context does not come
 * back: Ken Burns stays exact (`drawImage` with the camera window as the source rectangle, high
 * quality smoothing), parallax scenes use their Ken Burns move, cuts are drawn by
 * canvas2d/transitions.ts and popups and captions are drawn on top.
 */
import type { Compositor, DrawInput } from "../compositor";
import type { TransitionFrame } from "../transitions/common";
import { resolveTransition } from "../transitions/registry";
import type { FrameState, SceneRef } from "../types";
import { drawCut2D, simplifiedIn2D, type Ctx2D, type Source2D } from "./transitions";

function makeCanvas(width: number, height: number): Source2D {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(width, height);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

export class Canvas2DCompositor implements Compositor {
  readonly kind = "canvas2d" as const;
  private readonly ctx: CanvasRenderingContext2D;
  private width = 2;
  private height = 2;
  private a: Source2D = makeCanvas(2, 2);
  private b: Source2D = makeCanvas(2, 2);
  private scratch: Source2D = makeCanvas(2, 2);

  constructor(private readonly canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) throw new Error("Canvas 2D is not available");
    this.ctx = ctx;
  }

  resize(width: number, height: number): void {
    const w = Math.max(2, Math.round(width));
    const h = Math.max(2, Math.round(height));
    if (w === this.width && h === this.height) return;
    this.width = w;
    this.height = h;
    this.canvas.width = w;
    this.canvas.height = h;
    this.a = makeCanvas(w, h);
    this.b = makeCanvas(w, h);
    this.scratch = makeCanvas(w, h);
  }

  simplifies(transitionId: string): boolean {
    return simplifiedIn2D(resolveTransition(transitionId));
  }

  release(): void {
    /* the bitmaps belong to the picture store */
  }

  private drawScene(ctx: Ctx2D, ref: SceneRef, input: DrawInput, slot: 0 | 1): void {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, this.width, this.height);
    const source = input.source(ref.scene);
    if (source.missing) {
      ctx.drawImage(input.slate(ref.scene.number, slot).canvas, 0, 0, this.width, this.height);
      return;
    }
    const picture = source.picture;
    if (!picture) return;
    const bitmap = picture.bitmap;
    const fx = bitmap.width / picture.imageWidth;
    const fy = bitmap.height / picture.imageHeight;
    const [x, y, w, h] = ref.rect;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bitmap, x * fx, y * fy, w * fx, h * fy, 0, 0, this.width, this.height);
  }

  draw(input: DrawInput): void {
    this.resize(input.width, input.height);
    const ctx = this.ctx;
    const state: FrameState = input.state;
    ctx.save();
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    if (state.kind === "empty") {
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, this.width, this.height);
    } else if (state.kind === "scene") {
      this.drawScene(ctx, state.a, input, 0);
    } else if (state.kind === "fade") {
      this.drawScene(ctx, state.a, input, 0);
      const [r, g, b] = state.colour;
      ctx.globalAlpha = Math.min(Math.max(state.weight, 0), 1);
      ctx.fillStyle = `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
      ctx.fillRect(0, 0, this.width, this.height);
      ctx.globalAlpha = 1;
    } else if (state.t <= 0) {
      this.drawScene(ctx, state.a, input, 0);
    } else if (state.t >= 1) {
      this.drawScene(ctx, state.b, input, 1);
    } else {
      const transition = state.cut.transition!;
      this.drawScene(this.a.getContext("2d") as Ctx2D, state.a, input, 0);
      this.drawScene(this.b.getContext("2d") as Ctx2D, state.b, input, 1);
      const frame: TransitionFrame = {
        width: this.width,
        height: this.height,
        t: state.t,
        tRaw: state.tRaw,
        durationS: state.cut.durationS,
        fps: state.cut.frames / Math.max(state.cut.durationS, 1e-9),
        easing: transition.easing,
        params: transition.params,
        seed: state.cut.seed,
      };
      drawCut2D(ctx, this.a, this.b, transition, frame, this.scratch);
    }
    for (const overlay of input.overlays) {
      if (overlay.alpha <= 0) continue;
      ctx.globalAlpha = Math.min(overlay.alpha, 1);
      ctx.drawImage(overlay.image as CanvasImageSource, overlay.x, overlay.y, overlay.w, overlay.h);
    }
    ctx.restore();
  }

  snapshot(): ImageData | null {
    return this.ctx.getImageData(0, 0, this.width, this.height);
  }

  destroy(): void {
    /* nothing held besides canvases */
  }
}
