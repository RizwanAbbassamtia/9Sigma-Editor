/**
 * `luma_wipe_*` (render/transitions/luma_wipe.py): a soft-edged wipe driven by a gradient `G`
 * (0 where B appears first): `alpha = smoothstep(0, 1, (t (1 + f) - G) / f)`, `out = mix(A, B, alpha)`.
 * G: left `1 - u`, right `u`, up `1 - v`, down `v`, diag `(u + v) / 2` (pixel centres, v down).
 */
import { num, type TransitionFilter, type TransitionFrame } from "./common";
import { smoothstep } from "./easing";

export type WipeDirection = "left" | "right" | "up" | "down" | "diag";

const CODES: Record<WipeDirection, number> = { left: 0, right: 1, up: 2, down: 3, diag: 4 };

export function wipeFeather(frame: TransitionFrame): number {
  return Math.min(Math.max(num(frame.params, "feather", 0.12), 1e-3), 1);
}

export function wipeGradient(direction: WipeDirection, u: number, v: number): number {
  switch (direction) {
    case "left":
      return 1 - u;
    case "right":
      return u;
    case "up":
      return 1 - v;
    case "down":
      return v;
    default:
      return (u + v) * 0.5;
  }
}

/** The wipe's alpha at pixel centre (u, v) (for tests and the Canvas 2D fallback). */
export function wipeAlpha(frame: TransitionFrame, direction: WipeDirection, u: number, v: number): number {
  const feather = wipeFeather(frame);
  const edge = frame.t * (1 + feather);
  return smoothstep(0, 1, (edge - wipeGradient(direction, u, v)) / feather);
}

export function wipeFilter(id: string, direction: WipeDirection): TransitionFilter {
  return {
    id,
    uniforms: { uEdge: { type: "f32" }, uFeather: { type: "f32" }, uDirection: { type: "i32" } },
    values(frame) {
      const feather = wipeFeather(frame);
      return { uEdge: frame.t * (1 + feather), uFeather: feather, uDirection: CODES[direction] };
    },
    glsl: /* glsl */ `
void main() {
  float u = vUV.x;
  float v = vUV.y;
  float g = uDirection == 0 ? 1.0 - u : uDirection == 1 ? u : uDirection == 2 ? 1.0 - v : uDirection == 3 ? v : (u + v) * 0.5;
  float alpha = smoothstep(0.0, 1.0, (uEdge - g) / uFeather);
  vec3 a = texture(uA, vUV).rgb;
  vec3 b = texture(uB, vUV).rgb;
  finalColor = vec4(mix(a, b, alpha), 1.0);
}
`,
    fallback: "wipe",
    exactInFallback: false,
  };
}
