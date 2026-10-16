/**
 * Helper passes of the GPU compositor (GLSL ES 3.00 fragment shaders over a full-frame quad):
 *
 * - `fast_gaussian` of render/transitions/common.py: sigmas up to 3 px are a separable Gaussian
 *   at full size (OpenCV's kernel size `round(6 sigma + 1) | 1`); larger ones run on a copy
 *   area-scaled down by a power of two `f = 2^floor(log2(sigma / 1.5))` with the rest
 *   `sqrt(max(sigma^2 - f^2 / 4, (f / 2)^2)) / f` and are read back bilinearly;
 * - the luma, smoothing and Sobel passes of `morph_blend`'s flow field on a quarter-size grid.
 *
 * Borders are mirrored like OpenCV's BORDER_REFLECT_101.
 */
const HEADER = /* glsl */ `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
in vec2 vUV;
out vec4 finalColor;
`;

/** The common helpers without the two-picture samplers (only `reflect101` and the noise are used). */
const HELPERS = /* glsl */ `
float reflect101(float q, float size) {
  float period = 2.0 * size - 2.0;
  float x = q - 0.5;
  if (period <= 0.0) return 0.5;
  x = mod(x, period);
  if (x < 0.0) x += period;
  if (x > size - 1.0) x = period - x;
  return x + 0.5;
}
`;

/**
 * A scene's picture under the camera (the Ken Burns quad). The renderer resizes the picture with a
 * Lanczos filter at full size and the parity proof scales that down by area, so each preview pixel
 * should be the average of the picture over the pixel's footprint. Four bilinear taps at a quarter
 * of the footprint either side of the centre approximate that average on the mip level where the
 * footprint is at most two texels wide (`kenBurnsSampling`), instead of the GPU's trilinear blend
 * of two mip levels, which is noticeably softer between 1x and 2x minification.
 */
export const KB_FRAGMENT = /* glsl */ `${HEADER}
uniform sampler2D uTexture;
uniform vec2 uStep;
uniform float uLod;
void main() {
  vec2 d = 0.25 * uStep;
  vec4 acc = textureLod(uTexture, vUV + vec2(-d.x, -d.y), uLod);
  acc += textureLod(uTexture, vUV + vec2(d.x, -d.y), uLod);
  acc += textureLod(uTexture, vUV + vec2(-d.x, d.y), uLod);
  acc += textureLod(uTexture, vUV + vec2(d.x, d.y), uLod);
  finalColor = 0.25 * acc;
}
`;

/**
 * The supersampling of the Ken Burns quad: `step` is one output pixel in texture coordinates
 * (`uvSpan` = the camera window as a share of the picture, over `output` pixels) and `lod` the
 * mip level on which that pixel is at most two texels of the decoded `texture` wide.
 */
export function kenBurnsSampling(uvSpan: [number, number], output: [number, number], texture: [number, number]): { step: [number, number]; lod: number } {
  const stepU = uvSpan[0] / Math.max(output[0], 1);
  const stepV = uvSpan[1] / Math.max(output[1], 1);
  const texels = Math.max(stepU * texture[0], stepV * texture[1], 1e-6);
  return { step: [stepU, stepV], lod: Math.max(0, Math.log2(texels) - 1) };
}

/** Area average of `uSource` over `uFactor x uFactor` source pixels per output pixel. */
export const DOWNSAMPLE_FRAGMENT = /* glsl */ `${HEADER}
uniform sampler2D uSource;
uniform vec2 uSourceSize;
uniform int uFactor;
void main() {
  vec2 centre = vUV * uSourceSize;
  if (uFactor <= 1) {
    finalColor = texture(uSource, vUV);
    return;
  }
  float f = float(uFactor);
  vec2 corner = centre - 0.5 * f;
  int half2 = uFactor / 2;
  vec4 acc = vec4(0.0);
  for (int j = 0; j < 16; j++) {
    if (j >= half2) break;
    for (int i = 0; i < 16; i++) {
      if (i >= half2) break;
      // a bilinear tap between four texels = the exact mean of that 2 x 2 block
      vec2 p = corner + vec2(2.0 * float(i) + 1.0, 2.0 * float(j) + 1.0);
      acc += texture(uSource, p / uSourceSize);
    }
  }
  finalColor = acc / float(half2 * half2);
}
`;

/** One direction of a separable Gaussian (`uDirection` (1, 0) or (0, 1)). */
export const BLUR_FRAGMENT = /* glsl */ `${HEADER}
uniform sampler2D uSource;
uniform vec2 uSourceSize;
uniform vec2 uDirection;
uniform float uSigma;
uniform int uRadius;
${HELPERS}
void main() {
  vec2 p = vUV * uSourceSize;
  vec4 acc = vec4(0.0);
  float total = 0.0;
  float twoSigma2 = 2.0 * uSigma * uSigma;
  int count = 2 * uRadius + 1;
  for (int i = 0; i < 129; i++) {
    if (i >= count) break;
    int k = i - uRadius;
    float w = exp(-float(k * k) / twoSigma2);
    vec2 q = p + uDirection * float(k);
    // mirrored at the edges like BORDER_REFLECT_101 (the kernel is never wider than the picture
    // here, so one reflection is enough)
    if (q.x < 0.5) q.x = 1.0 - q.x;
    else if (q.x > uSourceSize.x - 0.5) q.x = 2.0 * uSourceSize.x - 1.0 - q.x;
    if (q.y < 0.5) q.y = 1.0 - q.y;
    else if (q.y > uSourceSize.y - 0.5) q.y = 2.0 * uSourceSize.y - 1.0 - q.y;
    acc += w * texture(uSource, q / uSourceSize);
    total += w;
  }
  finalColor = acc / total;
}
`;

/** morph_blend pass 1: the luma of A (red) and B (green) averaged over each small pixel. */
export const LUMA_FRAGMENT = /* glsl */ `${HEADER}
uniform sampler2D uA;
uniform sampler2D uB;
uniform vec2 uSourceSize;
uniform vec2 uTargetSize;
float luma(vec3 c) {
  float y = dot(c, vec3(0.299, 0.587, 0.114));
  return floor(y * 255.0 + 0.5) / 255.0;
}
void main() {
  vec2 scale = uSourceSize / uTargetSize;
  vec2 corner = (vUV * uTargetSize - 0.5) * scale;
  vec3 a = vec3(0.0);
  vec3 b = vec3(0.0);
  for (int j = 0; j < 4; j++) {
    for (int i = 0; i < 4; i++) {
      vec2 uv = (corner + (vec2(float(i), float(j)) + 0.5) * (scale / 4.0)) / uSourceSize;
      a += texture(uA, uv).rgb;
      b += texture(uB, uv).rgb;
    }
  }
  a /= 16.0;
  b /= 16.0;
  finalColor = vec4(luma(a), luma(b), 0.0, 1.0);
}
`;

/** morph_blend pass 3: Sobel / 8 of LA + LB -> V = amount (LB - LA) g / sqrt(|g|^2 + eps^2), encoded. */
export const FIELD_FRAGMENT = /* glsl */ `${HEADER}
uniform sampler2D uLuma;
uniform vec2 uSmallSize;
uniform float uEps;
${HELPERS}
vec2 at(float dx, float dy) {
  vec2 p = vUV * uSmallSize + vec2(dx, dy);
  p = vec2(reflect101(p.x, uSmallSize.x), reflect101(p.y, uSmallSize.y));
  return texture(uLuma, p / uSmallSize).rg;
}
float total(float dx, float dy) {
  vec2 l = at(dx, dy);
  return l.r + l.g;
}
void main() {
  float gx = (total(1.0, -1.0) + 2.0 * total(1.0, 0.0) + total(1.0, 1.0) - total(-1.0, -1.0) - 2.0 * total(-1.0, 0.0) - total(-1.0, 1.0)) * 0.125;
  float gy = (total(-1.0, 1.0) + 2.0 * total(0.0, 1.0) + total(1.0, 1.0) - total(-1.0, -1.0) - 2.0 * total(0.0, -1.0) - total(1.0, -1.0)) * 0.125;
  vec2 here = at(0.0, 0.0);
  float diff = here.g - here.r;
  vec2 v = diff * vec2(gx, gy) / sqrt(gx * gx + gy * gy + uEps * uEps);
  // V / (2 amount) + 0.5; the composite multiplies back by 2 amount
  finalColor = vec4(v * 0.5 + 0.5, 0.0, 1.0);
}
`;

/** OpenCV's Gaussian kernel size for an 8-bit picture: `round(6 sigma + 1) | 1`. */
export function kernelRadius(sigma: number, floatImage = false): number {
  const size = Math.round(sigma * (floatImage ? 8 : 6) + 1) | 1;
  return Math.min(Math.max((size - 1) / 2, 1), 64);
}

export interface BlurPlan {
  /** 1 = blur at full size */
  factor: number;
  /** the sigma used on the (scaled) copy, in its own pixels */
  sigma: number;
  radius: number;
}

/** How `fast_gaussian` blurs: none (null), at full size, or on a smaller copy. */
export function blurPlan(sigma: number): BlurPlan | null {
  if (sigma < 0.35) return null;
  if (sigma <= 3) return { factor: 1, sigma, radius: kernelRadius(sigma) };
  let factor = 2 ** Math.floor(Math.log2(sigma / 1.5));
  factor = Math.max(1, Math.min(factor, 32));
  const rest = Math.sqrt(Math.max(sigma * sigma - (factor * factor) / 4, (0.5 * factor) ** 2)) / factor;
  return { factor, sigma: rest, radius: kernelRadius(rest) };
}

