/**
 * The engine's wider JSON fixtures: tools/make_preview_fixtures.py writes them to
 * `src/preview/fixtures/engine_<name>.json` (the numbers of backend/sigma_editor; see
 * docs/design/PREVIEW-ENGINE.md, "Tests"), next to the seven contract fixtures that
 * official.test.ts reads. `fixture("motion.json")` reads `engine_motion.json`. Loaded with Vite's
 * glob import so the test files need no Node types.
 */
const FILES = import.meta.glob("../fixtures/engine_*.json", { eager: true, import: "default" }) as Record<string, unknown>;

export function fixture<T = Record<string, unknown>>(name: string): T {
  const value = FILES[`../fixtures/engine_${name}`];
  if (value === undefined) throw new Error(`fixture engine_${name} is missing (run tools/make_preview_fixtures.py)`);
  return value as T;
}

export function hasFixture(name: string): boolean {
  return FILES[`../fixtures/engine_${name}`] !== undefined;
}

/** |a - b| <= tol, with a readable message. */
export function close(actual: number, expected: number, tol: number, label: string): void {
  if (!(Math.abs(actual - expected) <= tol) && !(Number.isNaN(actual) && Number.isNaN(expected))) {
    throw new Error(`${label}: got ${actual}, expected ${expected} (tolerance ${tol})`);
  }
}

export function closeAll(actual: readonly number[], expected: readonly number[], tol: number, label: string): void {
  if (actual.length !== expected.length) throw new Error(`${label}: length ${actual.length}, expected ${expected.length}`);
  actual.forEach((value, i) => close(value, expected[i]!, tol, `${label}[${i}]`));
}
