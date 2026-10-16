/**
 * Radial and rotational motion blur about the frame centre (`zoom_rotate_blur` and
 * `blur_samples` of render/transitions/common.py, TRANSITION-PARITY.md section 4): the mean of N
 * samples magnified by `k q^j` and turned by `theta + j d`. The reference averages by repeated
 * halving; the shader loops over the N samples directly (the same mean).
 */

export const BLUR_SAMPLE_SPACING_PX = 2;
export const MAX_BLUR_SAMPLES = 128;

/** How many samples keep neighbours at most 2 px apart at the frame corner (1 = no blur). */
export function blurSamples(width: number, height: number, scaleSpan: number, degreesSpan: number): number {
  const radius = 0.5 * Math.hypot(width, height);
  const travel = radius * Math.abs(scaleSpan) + radius * ((Math.abs(degreesSpan) * Math.PI) / 180);
  if (travel < 1) return 1;
  const needed = travel / BLUR_SAMPLE_SPACING_PX + 1;
  const steps = Math.min(Math.max(1, Math.ceil(Math.log2(needed))), Math.round(Math.log2(MAX_BLUR_SAMPLES)));
  return 2 ** steps;
}

/** `(q, d)`: the step in scale and in degrees between neighbouring samples. */
export function blurStep(samples: number, scaleSpan: number, degreesSpan: number): [number, number] {
  if (samples < 2) return [1, 0];
  return [(1 + scaleSpan) ** (1 / (samples - 1)), degreesSpan / (samples - 1)];
}

/**
 * Pre-pass GLSL: the reference's sharp picture, `mix(zoom_rotate(A, kA, degA), zoom_rotate(B, kB,
 * degB), m)` with OpenCV's bicubic and mirrored borders.
 */
export const SHARP_ZOOM_GLSL = /* glsl */ `
vec3 zoomTurn(sampler2D picture, vec2 p, float k, float degrees) {
  vec2 c = frameCentre();
  vec2 q = c + turn(-degrees) * (p - c) / k;
  return cubicFast(picture, q - 0.5);
}
void main() {
  vec2 p = pixel();
  vec3 colour;
  if (uMix <= 0.0) colour = zoomTurn(uA, p, uScaleA, uDegA);
  else if (uMix >= 1.0) colour = zoomTurn(uB, p, uScaleB, uDegB);
  else colour = mix(zoomTurn(uA, p, uScaleA, uDegA), zoomTurn(uB, p, uScaleB, uDegB), uMix);
  finalColor = vec4(colour, 1.0);
}
`;

/**
 * Main-pass GLSL: `zoom_rotate_blur` of the sharp picture (`uPass`): the mean of N copies magnified
 * by `q^j` and turned by `j d` about the centre (mirrored at the edges).
 */
export const RADIAL_BLUR_GLSL = /* glsl */ `
uniform sampler2D uPass;
void main() {
  vec2 c = frameCentre();
  vec2 v = pixel() - c;
  int n = max(uSamples, 1);
  vec3 acc = vec3(0.0);
  float qj = 1.0;
  mat2 turnStep = turn(-uD);
  mat2 rotation = mat2(1.0);
  for (int j = 0; j < 128; j++) {
    if (j >= n) break;
    vec2 q = c + rotation * v / qj;
    acc += texture(uPass, q / uSize).rgb;
    qj *= uQ;
    rotation = turnStep * rotation;
  }
  finalColor = vec4(acc / float(n), 1.0);
}
`;
