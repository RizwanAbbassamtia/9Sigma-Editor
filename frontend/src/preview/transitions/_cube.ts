/**
 * `cube_left` / `cube_right` (render/transitions/cube.py): two faces of a box turning a quarter
 * turn about its vertical axis, seen by a pinhole camera. Units are frame heights; the face is
 * `a x 1` (`a = W / H`) and the box is `a` deep.
 *   z0 = (2.5 - 1.5 perspective) max(a, 1);  phi = +-(pi/2) t
 *   centre = (0, 0, z0 + a/2 + 0.35 max(a, 1) sin(pi t))
 *   project: X = W/2 + z0 x / z H,  Y = H/2 + z0 y / z H
 * A face is drawn when its rotated normal points at the camera, mapped onto its projected quad
 * by a homography and darkened by `1 - shade (1 - cos)`. The CPU works out each face's inverse
 * homography (output pixel -> picture pixel, OpenCV pixel coordinates); the shader applies it.
 */
import { num, type TransitionFilter, type TransitionFrame } from "./common";

type Vec3 = [number, number, number];
type Corner = [number, number, number];

const FRONT: Corner[] = [
  [-1, -1, -1],
  [1, -1, -1],
  [1, 1, -1],
  [-1, 1, -1],
];
const RIGHT: Corner[] = [
  [1, -1, -1],
  [1, -1, 1],
  [1, 1, 1],
  [1, 1, -1],
];
const LEFT: Corner[] = [
  [-1, -1, 1],
  [-1, -1, -1],
  [-1, 1, -1],
  [-1, 1, 1],
];

function rotate(point: Vec3, phi: number): Vec3 {
  const [x, y, z] = point;
  const c = Math.cos(phi);
  const s = Math.sin(phi);
  return [x * c + z * s, y, -x * s + z * c];
}

export interface CubeFace {
  visible: boolean;
  /** projected corners (TL, TR, BR, BL) in OpenCV pixel coordinates */
  dst: [number, number][] | null;
  brightness: number;
  /** rows of the 3x3 map output pixel -> picture pixel (OpenCV coordinates) */
  inverse: [Vec3, Vec3, Vec3] | null;
}

export interface CubeTerms {
  z0: number;
  phi: number;
  centreZ: number;
  faces: [CubeFace, CubeFace];
}

/** Solves `src_i -> dst_i` (four points) for the 3x3 homography (h33 = 1), rows. */
export function perspectiveTransform(src: readonly [number, number][], dst: readonly [number, number][]): [Vec3, Vec3, Vec3] | null {
  const a: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i]!;
    const [u, v] = dst[i]!;
    a.push([x, y, 1, 0, 0, 0, -x * u, -y * u]);
    b.push(u);
    a.push([0, 0, 0, x, y, 1, -x * v, -y * v]);
    b.push(v);
  }
  const n = 8;
  for (let col = 0; col < n; col++) {
    let pivot = col;
    for (let row = col + 1; row < n; row++) if (Math.abs(a[row]![col]!) > Math.abs(a[pivot]![col]!)) pivot = row;
    if (Math.abs(a[pivot]![col]!) < 1e-12) return null;
    [a[col], a[pivot]] = [a[pivot]!, a[col]!];
    [b[col], b[pivot]] = [b[pivot]!, b[col]!];
    for (let row = 0; row < n; row++) {
      if (row === col) continue;
      const factor = a[row]![col]! / a[col]![col]!;
      if (factor === 0) continue;
      for (let k = col; k < n; k++) a[row]![k]! -= factor * a[col]![k]!;
      b[row]! -= factor * b[col]!;
    }
  }
  const h = b.map((value, i) => value / a[i]![i]!);
  return [
    [h[0]!, h[1]!, h[2]!],
    [h[3]!, h[4]!, h[5]!],
    [h[6]!, h[7]!, 1],
  ];
}

/** The inverse of a 3x3 matrix given as rows. */
export function invert3(m: [Vec3, Vec3, Vec3]): [Vec3, Vec3, Vec3] | null {
  const [[a, b, c], [d, e, f], [g, h, i]] = m;
  const A = e * i - f * h;
  const B = -(d * i - f * g);
  const C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (Math.abs(det) < 1e-18) return null;
  const inv = 1 / det;
  return [
    [A * inv, -(b * i - c * h) * inv, (b * f - c * e) * inv],
    [B * inv, (a * i - c * g) * inv, -(a * f - c * d) * inv],
    [C * inv, -(a * h - b * g) * inv, (a * e - b * d) * inv],
  ];
}

function face(
  width: number,
  height: number,
  corners: Corner[],
  normal: Vec3,
  phi: number,
  centreZ: number,
  z0: number,
  aspect: number,
  shade: number,
): CubeFace {
  const hidden: CubeFace = { visible: false, dst: null, brightness: 1, inverse: null };
  const n = rotate(normal, phi);
  const world = corners.map(([cx, cy, cz]) => {
    const [x, y, z] = rotate([(cx * aspect) / 2, cy * 0.5, (cz * aspect) / 2], phi);
    return [x, y, z + centreZ] as Vec3;
  });
  const mid: Vec3 = [0, 1, 2].map((i) => world.reduce((sum, p) => sum + p[i]!, 0) / 4) as Vec3;
  const facing = n[0] * mid[0] + n[1] * mid[1] + n[2] * mid[2];
  if (facing >= -1e-6 || world.some((p) => p[2] <= 1e-3)) return hidden;
  const dst = world.map(([x, y, z]) => [width / 2 + ((z0 * x) / z) * height - 0.5, height / 2 + ((z0 * y) / z) * height - 0.5] as [number, number]);
  let twice = 0;
  for (let i = 0; i < 4; i++) {
    const [x0, y0] = dst[i]!;
    const [x1, y1] = dst[(i + 1) % 4]!;
    twice += x0 * y1 - x1 * y0;
  }
  if (0.5 * Math.abs(twice) < 1) return hidden;
  const src: [number, number][] = [
    [-0.5, -0.5],
    [width - 0.5, -0.5],
    [width - 0.5, height - 0.5],
    [-0.5, height - 0.5],
  ];
  const forward = perspectiveTransform(src, dst);
  const inverse = forward ? invert3(forward) : null;
  if (!inverse) return hidden;
  const distance = Math.sqrt(mid[0] * mid[0] + mid[1] * mid[1] + mid[2] * mid[2]);
  const cosine = Math.min(1, Math.max(0, -facing / Math.max(distance, 1e-9)));
  const brightness = 1 - shade * (1 - cosine);
  // the reference only darkens a face below 0.999 (cv2.convertScaleAbs)
  return { visible: true, dst, brightness: brightness < 0.999 ? brightness : 1, inverse };
}

export function cubeTerms(frame: TransitionFrame, direction: "left" | "right"): CubeTerms {
  const t = frame.t;
  const p = frame.params;
  const aspect = frame.width / frame.height;
  const size = Math.max(aspect, 1);
  const distance = Object.prototype.hasOwnProperty.call(p, "perspective")
    ? 2.5 - 1.5 * Math.min(Math.max(num(p, "perspective", 0.7), 0), 1)
    : num(p, "distance", 1.45);
  const z0 = Math.max(distance, 0.5) * size;
  const lift = Math.max(num(p, "zoom_out", 0.35), 0) * size * Math.sin(Math.PI * t);
  const shade = Math.min(Math.max(num(p, "shade", 0.45), 0), 1);
  const sign = direction === "left" ? 1 : -1;
  const phi = sign * (Math.PI / 2) * t;
  const centreZ = z0 + aspect / 2 + lift;
  const [side, sideNormal]: [Corner[], Vec3] = direction === "left" ? [RIGHT, [1, 0, 0]] : [LEFT, [-1, 0, 0]];
  return {
    z0,
    phi,
    centreZ,
    faces: [
      face(frame.width, frame.height, FRONT, [0, 0, -1], phi, centreZ, z0, aspect, shade),
      face(frame.width, frame.height, side, sideNormal, phi, centreZ, z0, aspect, shade),
    ],
  };
}

const FACE_GLSL = /* glsl */ `
vec3 drawFace(sampler2D picture, vec3 row0, vec3 row1, vec3 row2, float brightness, vec2 p) {
  // OpenCV pixel coordinates: centres on integers
  vec3 P = vec3(p - 0.5, 1.0);
  float w = dot(row2, P);
  if (w <= 0.0) return vec3(0.0);
  vec2 src = vec2(dot(row0, P), dot(row1, P)) / w;
  if (src.x < -2.0 || src.y < -2.0 || src.x > uSize.x + 1.0 || src.y > uSize.y + 1.0) return vec3(0.0);
  return cubicConstant(picture, src) * brightness;
}
void main() {
  vec2 p = pixel();
  vec3 colour = vec3(0.0);
  if (uFaces.x > 0.5) colour += drawFace(uA, uA0, uA1, uA2, uFaces.y, p);
  if (uFaces.z > 0.5) colour += drawFace(uB, uB0, uB1, uB2, uFaces.w, p);
  finalColor = vec4(clamp(colour, 0.0, 1.0), 1.0);
}
`;

const ZERO: Vec3 = [0, 0, 0];

export function cubeFilter(id: "cube_left" | "cube_right", direction: "left" | "right"): TransitionFilter {
  return {
    id,
    uniforms: {
      uA0: { type: "vec3<f32>" },
      uA1: { type: "vec3<f32>" },
      uA2: { type: "vec3<f32>" },
      uB0: { type: "vec3<f32>" },
      uB1: { type: "vec3<f32>" },
      uB2: { type: "vec3<f32>" },
      uFaces: { type: "vec4<f32>" },
    },
    values(frame) {
      const { faces } = cubeTerms(frame, direction);
      const [fa, fb] = faces;
      return {
        uA0: fa.inverse?.[0] ?? ZERO,
        uA1: fa.inverse?.[1] ?? ZERO,
        uA2: fa.inverse?.[2] ?? ZERO,
        uB0: fb.inverse?.[0] ?? ZERO,
        uB1: fb.inverse?.[1] ?? ZERO,
        uB2: fb.inverse?.[2] ?? ZERO,
        uFaces: [fa.visible ? 1 : 0, fa.brightness, fb.visible ? 1 : 0, fb.brightness],
      };
    },
    glsl: FACE_GLSL,
    fallback: "dissolve",
    exactInFallback: false,
  };
}
