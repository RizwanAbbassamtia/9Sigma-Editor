import { describe, expect, it } from "vitest";

import { fbm, hash01, hashU32, rand } from "../transitions/noise";
import { close, fixture } from "./fixtures";

interface NoiseFixture {
  hash: { x: number; y: number; seed: number; u32: number; h01: number; rand: number }[];
  fbm: { size: [number, number]; cells: number; octaves: number; seed: number; points: [number, number, number][] }[];
}

const data = fixture<NoiseFixture>("noise.json");

describe("integer hash and value noise (render/transitions/noise.py)", () => {
  it("hash_u32, hash01 and rand are bit-exact", () => {
    for (const item of data.hash) {
      expect(hashU32(item.x, item.y, item.seed), `${item.x},${item.y},${item.seed}`).toBe(item.u32);
      expect(hash01(item.x, item.y, item.seed)).toBe(item.h01);
      expect(rand(item.x, item.y, item.seed)).toBe(item.rand);
    }
  });

  it("fbm at sample pixels", () => {
    for (const item of data.fbm) {
      for (const [x, y, value] of item.points) {
        close(fbm(x, y, item.size[0], item.size[1], item.cells, item.octaves, item.seed), value, 2e-6, `fbm ${x},${y}`);
      }
    }
  });
});
