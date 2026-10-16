/**
 * What every transition filter shares: the per-frame context (the Python `FrameContext` of
 * render/transitions/common.py), the parameter readers with the same aliases and fallbacks, the
 * filter interface the engine draws with, and the GLSL building blocks of TRANSITION-PARITY.md
 * section 4 (linear-light mix, pixel centres, mirrored sampling).
 */
import { NOISE_GLSL } from "./noise";
import { transitionSpeed } from "./easing";

export type TransitionParams = Readonly<Record<string, unknown>>;

/** One frame of a cut, as the transition functions see it. */
export interface TransitionFrame {
  /** output size in pixels (the preview canvas) */
  width: number;
  height: number;
  /** eased progress */
  t: number;
  /** linear progress (k + 0.5) / n */
  tRaw: number;
  /** the cut's duration in seconds (n / fps) */
  durationS: number;
  fps: number;
  easing: string;
  params: TransitionParams;
  /** the 1-based number of the scene the cut leaves */
  seed: number;
}

/** `min(W, H) / 1080`: parameters in pixels are "pixels at 1080p". */
export function frameScale(frame: Pick<TransitionFrame, "width" | "height">): number {
  return Math.max(Math.min(frame.width, frame.height) / 1080, 1e-3);
}

export function px(frame: Pick<TransitionFrame, "width" | "height">, valueAt1080p: number): number {
  return valueAt1080p * frameScale(frame);
}

/** How fast eased progress moves, per second, at this frame. */
export function speedPerSecond(frame: TransitionFrame): number {
  return transitionSpeed(frame.easing, frame.tRaw) / Math.max(frame.durationS, 1e-3);
}

// Parameters (FrameContext.num / flag / span / text / colour) ------------------------------

function first(params: TransitionParams, names: string | readonly string[]): unknown {
  for (const name of typeof names === "string" ? [names] : names) {
    if (Object.prototype.hasOwnProperty.call(params, name) && params[name] !== null && params[name] !== undefined) {
      return params[name];
    }
  }
  return undefined;
}

function pyFloat(value: unknown): number | null {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value.trim());
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

/** A number param; `names` may list aliases (the catalogue's name first). */
export function num(params: TransitionParams, names: string | readonly string[], fallback: number): number {
  const value = first(params, names);
  if (value === undefined || typeof value === "boolean") return fallback;
  const parsed = pyFloat(value);
  if (parsed === null) return fallback;
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function flag(params: TransitionParams, names: string | readonly string[], fallback: boolean): boolean {
  const value = first(params, names);
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") return !["", "0", "false", "no", "off"].includes(value.trim().toLowerCase());
  return fallback;
}

/** A `[low, high]` param (a single number gives `[n, n]`). */
export function span(params: TransitionParams, names: string | readonly string[], fallback: [number, number]): [number, number] {
  const value = first(params, names);
  let low: number | null;
  let high: number | null;
  if (Array.isArray(value) && value.length === 2) {
    low = pyFloat(value[0]);
    high = pyFloat(value[1]);
  } else if (value !== undefined && typeof value !== "boolean") {
    low = high = pyFloat(value);
  } else {
    return fallback;
  }
  if (low === null || high === null || !Number.isFinite(low) || !Number.isFinite(high)) return fallback;
  return [Math.min(low, high), Math.max(low, high)];
}

export function text(params: TransitionParams, names: string | readonly string[], fallback: string): string {
  const value = first(params, names);
  return typeof value === "string" && value ? value : fallback;
}

export type RGB = [number, number, number];

/** A 0-1 RGB triple from `[r, g, b]` (0-1 or 0-255) or `#rrggbb` (`parse_rgb`). */
export function parseRgb(value: unknown, fallback: RGB): RGB {
  if (typeof value === "string") {
    const hex = value.trim().replace(/^#+/, "");
    if (hex.length === 6 && /^[0-9a-fA-F]{6}$/.test(hex)) {
      return [parseInt(hex.slice(0, 2), 16) / 255, parseInt(hex.slice(2, 4), 16) / 255, parseInt(hex.slice(4, 6), 16) / 255];
    }
    return fallback;
  }
  if (Array.isArray(value) && value.length === 3) {
    const numbers = value.map((v) => pyFloat(v));
    if (numbers.some((n) => n === null)) return fallback;
    let values = numbers as number[];
    if (Math.max(...values) > 1) values = values.map((n) => n / 255);
    return values.map((n) => Math.min(Math.max(n, 0), 1)) as RGB;
  }
  return fallback;
}

export function colour(params: TransitionParams, names: string | readonly string[], fallback: RGB): RGB {
  const value = first(params, names);
  return value === undefined ? fallback : parseRgb(value, fallback);
}

// The filter interface ----------------------------------------------------------------------

/** Pixi uniform types the filters use. */
export type UniformType = "f32" | "i32" | "u32" | "vec2<f32>" | "vec3<f32>" | "vec4<f32>" | "mat3x3<f32>";

export interface UniformSpec {
  type: UniformType;
  /** array length (GLSL `uniform vec4 uBands[5]`) */
  size?: number;
}

export type UniformValue = number | readonly number[];
export type UniformValues = Record<string, UniformValue>;

/** How the Canvas 2D fallback draws a transition (design 5.7). */
export type FallbackKind = "linear-dissolve" | "dissolve" | "dip" | "push" | "wipe";

/** Extra work before the composite pass. */
export interface BlurRequest {
  /** Gaussian sigma in output pixels for A and for B (0 = none) */
  a: number;
  b: number;
}

export interface TransitionFilter {
  id: string;
  /** declarations of the filter's own uniforms (added to the shared header) */
  uniforms: Readonly<Record<string, UniformSpec>>;
  /** GLSL ES 3.00: functions and `void main()` writing `finalColor` */
  glsl: string;
  /** uniform values for one frame: pure maths, tested against the Python reference */
  values(frame: TransitionFrame): UniformValues;
  /** Gaussian blur of the two pictures before the composite (blur_dissolve) */
  blur?(frame: TransitionFrame): BlurRequest;
  /**
   * A pass drawn first into a frame-size texture that the main pass reads as `uPass` (same
   * uniforms): zoom_through and spin_zoom draw the sharp mix there and blur it once, as the
   * reference does.
   */
  prePass?: { glsl: string };
  /** a luma flow field on a quarter-size grid before the composite (morph_blend) */
  morph?(frame: TransitionFrame): { small: [number, number]; sigmaSmall: number; amount: number; eps: number };
  fallback: FallbackKind;
  /** true when the Canvas 2D fallback draws it exactly (dissolves, dips) */
  exactInFallback: boolean;
}

// GLSL ------------------------------------------------------------------------------------

/** The vertex shader of every full-frame pass (Pixi mesh pipeline). */
export const PASS_VERTEX = /* glsl */ `#version 300 es
precision highp float;
in vec2 aPosition;
in vec2 aUV;
out vec2 vUV;
uniform mat3 uProjectionMatrix;
uniform mat3 uWorldTransformMatrix;
uniform mat3 uTransformMatrix;
void main() {
  mat3 mvp = uProjectionMatrix * uWorldTransformMatrix * uTransformMatrix;
  gl_Position = vec4((mvp * vec3(aPosition, 1.0)).xy, 0.0, 1.0);
  vUV = aUV;
}
`;

/**
 * Shared header: `uv` is the pixel centre with the origin at the top-left (`vUV`), `pixel()` the
 * same in pixels, `sampleA/B(p)` read a picture at pixel position `p` (mirrored at the edge by the
 * texture's address mode), `mixLinear` blends in linear light (gamma 2.2) with a gain.
 */
export const HEADER_GLSL = /* glsl */ `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
in vec2 vUV;
out vec4 finalColor;
uniform sampler2D uA;
uniform sampler2D uB;
uniform vec2 uSize;
uniform float uT;
uniform float uTRaw;
uniform uint uSeed;
`;

export const COMMON_GLSL = /* glsl */ `
const float PI = 3.141592653589793;
vec2 pixel() { return vUV * uSize; }
vec2 frameCentre() { return 0.5 * uSize; }
vec3 sampleA(vec2 p) { return texture(uA, p / uSize).rgb; }
vec3 sampleB(vec2 p) { return texture(uB, p / uSize).rgb; }
vec3 linearLight(vec3 c) { return pow(max(c, vec3(0.0)), vec3(2.2)); }
vec3 encodeLight(vec3 l) { return pow(max(l, vec3(0.0)), vec3(1.0 / 2.2)); }
vec3 mixLinear(vec3 a, vec3 b, float w, float gain) {
  return clamp(gain * encodeLight((1.0 - w) * linearLight(a) + w * linearLight(b)), 0.0, 1.0);
}
mat2 turn(float degreesCw) {
  float a = radians(degreesCw);
  float c = cos(a);
  float s = sin(a);
  // y-down screen coordinates: a positive angle turns clockwise
  return mat2(c, s, -s, c);
}
/* Pixel position q (centres at +0.5) mirrored back into [0, size) like BORDER_REFLECT_101. */
float reflect101(float q, float size) {
  float period = 2.0 * size - 2.0;
  float x = q - 0.5;
  if (period <= 0.0) return 0.5;
  x = mod(x, period);
  if (x < 0.0) x += period;
  if (x > size - 1.0) x = period - x;
  return x + 0.5;
}
/* OpenCV's cubic convolution weights (INTER_CUBIC, A = -0.75) for a fraction t. */
vec4 cubicWeights(float t) {
  const float A = -0.75;
  float w0 = ((A * (t + 1.0) - 5.0 * A) * (t + 1.0) + 8.0 * A) * (t + 1.0) - 4.0 * A;
  float w1 = ((A + 2.0) * t - (A + 3.0)) * t * t + 1.0;
  float w2 = ((A + 2.0) * (1.0 - t) - (A + 3.0)) * (1.0 - t) * (1.0 - t) + 1.0;
  return vec4(w0, w1, w2, 1.0 - w0 - w1 - w2);
}
/* OpenCV's cubic at OpenCV pixel position q in 9 filtered reads: the two middle weights of each
   axis are positive, so those two taps share one bilinear read. Outside the picture the texture's
   mirrored addressing stands in for BORDER_REFLECT_101 (one pixel apart, at the very edge). */
vec3 cubicFast(sampler2D picture, vec2 q) {
  vec2 i0 = floor(q);
  vec2 t = q - i0;
  vec4 wx = cubicWeights(t.x);
  vec4 wy = cubicWeights(t.y);
  vec2 middle = vec2(wx.y + wx.z, wy.y + wy.z);
  vec2 shift = vec2(wx.z / middle.x, wy.z / middle.y);
  vec2 a = (i0 - 0.5) / uSize;
  vec2 b = (i0 + shift + 0.5) / uSize;
  vec2 c = (i0 + 2.5) / uSize;
  vec3 top = wx.x * texture(picture, vec2(a.x, a.y)).rgb + middle.x * texture(picture, vec2(b.x, a.y)).rgb + wx.w * texture(picture, vec2(c.x, a.y)).rgb;
  vec3 centre = wx.x * texture(picture, vec2(a.x, b.y)).rgb + middle.x * texture(picture, vec2(b.x, b.y)).rgb + wx.w * texture(picture, vec2(c.x, b.y)).rgb;
  vec3 bottom = wx.x * texture(picture, vec2(a.x, c.y)).rgb + middle.x * texture(picture, vec2(b.x, c.y)).rgb + wx.w * texture(picture, vec2(c.x, c.y)).rgb;
  return clamp(wy.x * top + middle.y * centre + wy.w * bottom, 0.0, 1.0);
}
/* Bicubic read at OpenCV pixel position q, outside pixels mirrored (BORDER_REFLECT_101). */
vec3 cubicReflect(sampler2D picture, vec2 q) {
  vec2 i0 = floor(q);
  vec2 t = q - i0;
  vec4 wx = cubicWeights(t.x);
  vec4 wy = cubicWeights(t.y);
  vec3 acc = vec3(0.0);
  for (int j = 0; j < 4; j++) {
    float y = i0.y + float(j - 1);
    y = reflect101(y + 0.5, uSize.y) - 0.5;
    vec3 row = vec3(0.0);
    for (int i = 0; i < 4; i++) {
      float x = i0.x + float(i - 1);
      x = reflect101(x + 0.5, uSize.x) - 0.5;
      row += wx[i] * texture(picture, (vec2(x, y) + 0.5) / uSize).rgb;
    }
    acc += wy[j] * row;
  }
  return clamp(acc, 0.0, 1.0);
}
/* Bicubic read of a picture at OpenCV pixel position q (pixel centres on integers); pixels outside
   the picture are black (BORDER_CONSTANT), as warpPerspective draws them. */
vec3 cubicConstant(sampler2D picture, vec2 q) {
  vec2 i0 = floor(q);
  vec2 t = q - i0;
  vec4 wx = cubicWeights(t.x);
  vec4 wy = cubicWeights(t.y);
  vec3 acc = vec3(0.0);
  for (int j = 0; j < 4; j++) {
    float y = i0.y + float(j - 1);
    if (y < 0.0 || y > uSize.y - 1.0) continue;
    vec3 row = vec3(0.0);
    for (int i = 0; i < 4; i++) {
      float x = i0.x + float(i - 1);
      if (x < 0.0 || x > uSize.x - 1.0) continue;
      row += wx[i] * texture(picture, (vec2(x, y) + 0.5) / uSize).rgb;
    }
    acc += wy[j] * row;
  }
  return clamp(acc, 0.0, 1.0);
}
${NOISE_GLSL}
`;

/** The GLSL declaration of one uniform. */
export function uniformDeclaration(name: string, spec: UniformSpec): string {
  const glslType: Record<UniformType, string> = {
    f32: "float",
    i32: "int",
    u32: "uint",
    "vec2<f32>": "vec2",
    "vec3<f32>": "vec3",
    "vec4<f32>": "vec4",
    "mat3x3<f32>": "mat3",
  };
  return `uniform ${glslType[spec.type]} ${name}${spec.size && spec.size > 1 ? `[${spec.size}]` : ""};`;
}

/** The whole fragment source of a filter (or of its pre-pass). */
export function fragmentSource(filter: TransitionFilter, part: "main" | "pre" = "main"): string {
  const declarations = Object.entries(filter.uniforms)
    .map(([name, spec]) => uniformDeclaration(name, spec))
    .join("\n");
  const body = part === "pre" && filter.prePass ? filter.prePass.glsl : filter.glsl;
  return `${HEADER_GLSL}${declarations}\n${COMMON_GLSL}\n${body}`;
}
