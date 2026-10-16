/**
 * Transitions without WebGL2 (design 5.7): dissolves and dips stay exact (the film dissolve's
 * linear-light mix through the same 256 x 256 table as `mix_linear`), pushes and wipes are close
 * (no motion blur; the wipe's soft edge as a gradient), everything else is a plain dissolve and the
 * monitor shows "Simplified preview".
 */
import { dipTerms } from "../transitions/_dip";
import { pushTerms, stripLayout, whipTerms, type PushDirection } from "../transitions/_push";
import { wipeFeather, type WipeDirection } from "../transitions/_wipe";
import { num, type TransitionFrame } from "../transitions/common";
import { smoothstep } from "../transitions/easing";
import type { ResolvedTransition } from "../transitions/registry";

export type Ctx2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;
export type Source2D = HTMLCanvasElement | OffscreenCanvas;

/** True when Canvas 2D draws this transition only as a stand-in (a dissolve). */
export function simplifiedIn2D(transition: Pick<ResolvedTransition, "filter">): boolean {
  const filter = transition.filter;
  return !(filter.exactInFallback || filter.fallback === "push" || filter.fallback === "wipe");
}

const LIN = new Float64Array(256);
for (let i = 0; i < 256; i++) LIN[i] = (i / 255) ** 2.2;

/** `T[a * 256 + b] = clamp(round(255 gain ((1 - w) a^2.2 + w b^2.2)^(1/2.2)))` (common.py `_linear_table`). */
export function linearTable(weight: number, gain: number): Uint8Array {
  const table = new Uint8Array(65536);
  const w = Math.min(Math.max(weight, 0), 1);
  for (let a = 0; a < 256; a++) {
    const base = (1 - w) * LIN[a]!;
    for (let b = 0; b < 256; b++) {
      const value = Math.round((base + w * LIN[b]!) ** (1 / 2.2) * 255 * gain);
      table[a * 256 + b] = value < 0 ? 0 : value > 255 ? 255 : value;
    }
  }
  return table;
}

function mixLinearInto(ctx: Ctx2D, a: Source2D, b: Source2D, width: number, height: number, weight: number, gain: number): void {
  const aCtx = a.getContext("2d") as Ctx2D;
  const bCtx = b.getContext("2d") as Ctx2D;
  const pa = aCtx.getImageData(0, 0, width, height);
  const pb = bCtx.getImageData(0, 0, width, height).data;
  const table = linearTable(weight, gain);
  const out = pa.data;
  for (let i = 0; i < out.length; i += 4) {
    out[i] = table[(out[i]! << 8) | pb[i]!]!;
    out[i + 1] = table[(out[i + 1]! << 8) | pb[i + 1]!]!;
    out[i + 2] = table[(out[i + 2]! << 8) | pb[i + 2]!]!;
    out[i + 3] = 255;
  }
  ctx.putImageData(pa, 0, 0);
}

function fill(ctx: Ctx2D, colour: [number, number, number], alpha: number, width: number, height: number): void {
  ctx.globalAlpha = alpha;
  ctx.fillStyle = `rgb(${Math.round(colour[0] * 255)}, ${Math.round(colour[1] * 255)}, ${Math.round(colour[2] * 255)})`;
  ctx.fillRect(0, 0, width, height);
  ctx.globalAlpha = 1;
}

/** Draws one cut frame from A and B (both already drawn at the output size). */
export function drawCut2D(ctx: Ctx2D, a: Source2D, b: Source2D, transition: ResolvedTransition, frame: TransitionFrame, scratch: Source2D): void {
  const { width, height } = frame;
  const filter = transition.filter;
  const t = frame.t;
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  switch (filter.fallback) {
    case "dip": {
      const terms = dipTerms(frame, transition.id === "dip_white" ? [1, 1, 1] : [0, 0, 0]);
      if (terms.phase === 0) {
        ctx.drawImage(a, 0, 0);
        fill(ctx, terms.colour, terms.weight, width, height);
      } else if (terms.phase === 1) {
        fill(ctx, terms.colour, 1, width, height);
      } else {
        fill(ctx, terms.colour, 1, width, height);
        ctx.globalAlpha = terms.weight;
        ctx.drawImage(b, 0, 0);
      }
      break;
    }
    case "linear-dissolve": {
      const gain = transition.id === "film_dissolve" ? 1 + num(transition.params, ["luma_lift", "lift"], 0.03) * Math.sin(Math.PI * t) : 1;
      mixLinearInto(ctx, a, b, width, height, t, gain);
      break;
    }
    case "push": {
      const kind = transition.id.startsWith("whip_") ? "whip" : "push";
      const direction = transition.id.split("_")[1] as PushDirection;
      const layout = stripLayout(frame, direction, kind);
      const terms = kind === "push" ? pushTerms(frame, direction) : whipTerms(frame, direction);
      const horizontal = direction === "left" || direction === "right";
      fill(ctx, [0, 0, 0], 1, width, height);
      // the motion blur as a few copies spread over the shutter's travel (an even average)
      const copies = terms.box >= 2 ? Math.min(terms.box, 16) : 1;
      const spread = terms.box >= 2 ? terms.box - 1 : 0;
      for (let i = 0; i < copies; i++) {
        const offset = copies > 1 ? -spread / 2 + (i * spread) / (copies - 1) : 0;
        const dx = horizontal ? offset : 0;
        const dy = horizontal ? 0 : offset;
        ctx.globalAlpha = 1 / (i + 1);
        ctx.drawImage(a, layout.a[0] + dx, layout.a[1] + dy);
        ctx.drawImage(b, layout.b[0] + dx, layout.b[1] + dy);
      }
      ctx.globalAlpha = 1;
      break;
    }
    case "wipe": {
      const direction = transition.id.replace("luma_wipe_", "") as WipeDirection;
      const feather = wipeFeather(frame);
      const edge = t * (1 + feather);
      const sctx = scratch.getContext("2d") as Ctx2D;
      sctx.globalCompositeOperation = "source-over";
      sctx.globalAlpha = 1;
      sctx.clearRect(0, 0, width, height);
      sctx.drawImage(b, 0, 0);
      // alpha = smoothstep(0, 1, (edge - G) / feather) along the wipe's direction
      // the diagonal's gradient G = (u + v) / 2 runs along (1/W, 1/H): its end is n / |n|^2
      const nx = 1 / (2 * width);
      const ny = 1 / (2 * height);
      const norm = nx * nx + ny * ny;
      const [x0, y0, x1, y1] =
        direction === "left"
          ? [width, 0, 0, 0]
          : direction === "right"
            ? [0, 0, width, 0]
            : direction === "up"
              ? [0, height, 0, 0]
              : direction === "down"
                ? [0, 0, 0, height]
                : [0, 0, nx / norm, ny / norm];
      const gradient = sctx.createLinearGradient(x0, y0, x1, y1);
      const steps = 24;
      for (let i = 0; i <= steps; i++) {
        const g = i / steps;
        gradient.addColorStop(g, `rgba(0,0,0,${smoothstep(0, 1, (edge - g) / feather)})`);
      }
      sctx.globalCompositeOperation = "destination-in";
      sctx.fillStyle = gradient;
      sctx.fillRect(0, 0, width, height);
      sctx.globalCompositeOperation = "source-over";
      ctx.drawImage(a, 0, 0);
      ctx.drawImage(scratch, 0, 0);
      break;
    }
    default: {
      ctx.drawImage(a, 0, 0);
      ctx.globalAlpha = t;
      ctx.drawImage(b, 0, 0);
    }
  }
  ctx.restore();
}
