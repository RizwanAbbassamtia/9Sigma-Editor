import { describe, expect, it } from "vitest";

import { DEFAULT_SETTINGS } from "../types";
import {
  NOTO_SANS_METRICS,
  assEmPx,
  baselineSnap,
  captionEvents,
  captionGeometry,
  cueTokens,
  eventAt,
  paintCaption,
  parseFontMetrics,
  popScale,
  popupFrame,
  popupPosition,
  popupRequest,
  shareOut,
  wordSlices,
  CAPTION_LOOKS,
} from "../text";
import { close, fixture } from "./fixtures";

interface PopupsFixture {
  look: { fade_s: number; slide_s: number; slide_px: number; margin_px: number };
  cases: {
    anim: "slide_left" | "pop" | "fade";
    start_s: number;
    end_s: number;
    frame: [number, number];
    fps: number;
    x0: number;
    y0: number;
    samples: { n: number; visible: boolean; x: number; y: number; x_px: number; y_px: number; alpha: number }[];
  }[];
  positions: { position: string; frame: [number, number]; popup: [number, number]; reserved: number; margin: number; xy: [number, number] }[];
}

const popups = fixture<PopupsFixture>("popups.json");

describe("popup motion (render/ffmpeg.py _popup_exprs and the fades)", () => {
  it("matches the overlay frame by frame", () => {
    const settings = { popupFadeS: popups.look.fade_s, popupSlideS: popups.look.slide_s, popupSlidePx: popups.look.slide_px };
    for (const item of popups.cases) {
      for (const sample of item.samples) {
        const state = popupFrame({ anim: item.anim, startS: item.start_s, endS: item.end_s }, sample.n, item.fps, { width: item.frame[0], height: item.frame[1] }, { x: item.x0, y: item.y0 }, settings);
        const label = `${item.anim} ${item.start_s}-${item.end_s} n=${sample.n}`;
        expect(state.visible, label).toBe(sample.visible);
        close(state.x, sample.x, 1e-6, `${label} x`);
        close(state.y, sample.y, 1e-6, `${label} y`);
        expect(state.xPx, label).toBe(sample.x_px);
        expect(state.yPx, label).toBe(sample.y_px);
        close(state.alpha, sample.alpha, 1e-6, `${label} alpha`);
      }
    }
  });

  it("places popups like render/popups.py", () => {
    for (const item of popups.positions) {
      expect(popupPosition(item.position, item.popup, item.frame, item.margin, item.reserved), `${item.position} ${item.frame}`).toEqual(item.xy);
    }
  });

  it("asks for the PNG at the timeline's frame height", () => {
    const request = popupRequest(
      { key: "pop-001", index: 0, scene: 0, text: "Hello there", style: "", position: "top-left", anim: "pop", startS: 1, endS: 3, png: "" },
      "16:9",
      1080,
    );
    expect(request).toEqual({ text: "Hello there", style: "", position: "top-left", anim: "pop", aspect: "16:9", height: 1080 });
    expect(popupRequest({ key: "x", index: 0, scene: 0, text: "a", style: "", position: "center", anim: "fade", startS: 0, endS: 1, png: "" }, "9:16", 5000).height).toBe(2160);
  });
});

describe("captions (libass geometry and the M5 looks)", () => {
  it("sizes the font the way libass does (ascent + descent = the ASS size)", () => {
    close(assEmPx(48, NOTO_SANS_METRICS), (48 * 1000) / 1362, 1e-9, "em");
    const geometry = captionGeometry(2, { size: 48, outline: 2 }, { width: 960, height: 540 }, "16:9", NOTO_SANS_METRICS, DEFAULT_SETTINGS);
    close(geometry.scale, 0.5, 1e-12, "scale");
    close(geometry.lineHeight, 24, 1e-12, "line height");
    close(geometry.marginV, 30, 1e-12, "margin");
    close(geometry.borderPx, 1, 1e-12, "border");
    // the last line's descent sits on the margin
    close(geometry.baselines[1]! + geometry.descentPx, 540 - 30, 1e-9, "bottom");
    close(geometry.baselines[1]! - geometry.baselines[0]!, 24, 1e-9, "line step");
    const portrait = captionGeometry(1, { size: 48 }, { width: 304, height: 540 }, "9:16", NOTO_SANS_METRICS, DEFAULT_SETTINGS);
    close(portrait.scale, 540 / 1920, 1e-12, "portrait uses PlayResY 1920");
  });

  it("reads the metrics of a font file", () => {
    // a minimal sfnt: three tables (head, hhea, OS/2)
    const buffer = new ArrayBuffer(12 + 3 * 16 + 54 + 36 + 96);
    const view = new DataView(buffer);
    view.setUint16(4, 3);
    const tables: [string, number][] = [
      ["OS/2", 12 + 48 + 54 + 36],
      ["head", 12 + 48],
      ["hhea", 12 + 48 + 54],
    ];
    tables.forEach(([tag, offset], i) => {
      for (let c = 0; c < 4; c++) view.setUint8(12 + i * 16 + c, tag.charCodeAt(c));
      view.setUint32(12 + i * 16 + 8, offset);
    });
    view.setUint16(12 + 48 + 18, 2048);
    view.setInt16(12 + 48 + 54 + 4, 1900);
    view.setInt16(12 + 48 + 54 + 6, -500);
    view.setInt16(12 + 48 + 54 + 36 + 74, 2100);
    view.setInt16(12 + 48 + 54 + 36 + 76, 600);
    expect(parseFontMetrics(buffer)).toEqual({ unitsPerEm: 2048, winAscent: 2100, winDescent: 600, hheaAscent: 1900, hheaDescent: -500 });
  });

  it("draws the outline and the bold like libass and places the text to a fraction of a pixel", () => {
    // a 2D context that records what is drawn (jsdom has no canvas)
    const calls: { op: string; text: string; x: number; y: number; lineWidth: number }[] = [];
    const state = { dy: 0, lineWidth: 1 };
    const ctx = {
      font: "",
      textBaseline: "alphabetic",
      textAlign: "left",
      lineJoin: "round",
      lineCap: "round",
      direction: "ltr",
      get lineWidth(): number {
        return state.lineWidth;
      },
      set lineWidth(value: number) {
        state.lineWidth = value;
      },
      strokeStyle: "",
      fillStyle: "",
      globalAlpha: 1,
      save() {},
      restore() {},
      translate(_x: number, y: number): void {
        state.dy += y;
      },
      measureText: (text: string) => ({ width: text.length * 10 }),
      strokeText(text: string, x: number, y: number): void {
        calls.push({ op: "stroke", text, x, y: y + state.dy, lineWidth: state.lineWidth });
      },
      fillText(text: string, x: number, y: number): void {
        calls.push({ op: "fill", text, x, y: y + state.dy, lineWidth: state.lineWidth });
      },
    } as unknown as CanvasRenderingContext2D;
    const style = { size: 48, outline: 2, bold: true, preset: "bold-outline" };
    const cue = { key: "cue-0001", startS: 0, endS: 2, text: "Hello there" } as never;
    const event = { startS: 0, endS: 2, kind: "cue", active: null, pop: false } as const;
    const painted = paintCaption(ctx, 960, 540, { cue, event, t: 1, style, aspect: "16:9", font: null, settings: DEFAULT_SETTINGS });
    const geometry = captionGeometry(1, style, { width: 960, height: 540 }, "16:9", NOTO_SANS_METRICS, DEFAULT_SETTINGS);
    const e = geometry.emboldenPx;
    close(e, geometry.emPx / 64, 1e-12, "em / 64");
    const exact = geometry.baselines[0]! - e / 2;
    // the outline goes around the emboldened glyph: 2 x border + em / 64 wide, moved right and up
    const outline = calls.find((call) => call.op === "stroke" && call.lineWidth > 1)!;
    close(outline.lineWidth, 2 * geometry.borderPx + e, 1e-12, "outline width");
    const fill = calls.find((call) => call.op === "fill")!;
    close(fill.x - outline.x, 0, 1e-12, "fill and outline together");
    close(fill.x, geometry.centreX - 55 + e / 2, 1e-9, "moved right by e / 2");
    // the line is drawn on a whole pixel and the canvas moved back by the rest
    close(fill.y, Math.round(exact), 1e-9, "on a whole pixel");
    close(fill.y + painted.offsetY, exact, 1e-9, "where libass puts it");
    expect(Math.abs(painted.offsetY)).toBeLessThanOrEqual(0.5);
    close(baselineSnap(504.5), 0.5, 1e-12, "halves go down like the browser's");
    close(baselineSnap(504.7), 0.3, 1e-9, "snap");
  });

  it("splits cues into words and separators", () => {
    expect(cueTokens("Hello  big\\Nworld")).toEqual([
      ["Hello", true],
      ["  ", false],
      ["big", true],
      ["\\N", false],
      ["world", true],
    ]);
    expect(cueTokens("日本", false)).toEqual([
      ["日", true],
      ["本", true],
    ]);
  });

  it("slices words like word_slices and shares time out by length", () => {
    const cue = { startS: 1.0, endS: 3.0 };
    const words = [
      { w: "a", start_s: 0.9, end_s: 1.2 },
      { w: "bb", start_s: 1.5, end_s: 2.0 },
      { w: "c", start_s: 2.4, end_s: 3.4 },
    ];
    expect(wordSlices(cue, words)).toEqual([
      [1.0, 1.5, 0],
      [1.5, 2.4, 1],
      [2.4, 3.0, 2],
    ]);
    expect(shareOut(["ab", "c"], 0, 3)).toEqual([
      { w: "ab", start_s: 0, end_s: 2 },
      { w: "c", start_s: 2, end_s: 3 },
    ]);
  });

  it("writes the events of each look", () => {
    const cue = { key: "cue-0001", index: 0, startS: 1, endS: 3, lines: ["one two"], text: "one two" };
    const words = [
      { w: "one", start_s: 1.2, end_s: 1.6 },
      { w: "two", start_s: 1.7, end_s: 2.5 },
    ];
    expect(captionEvents(cue, words, { preset: "bold-outline" })).toEqual([{ startS: 1, endS: 3, kind: "cue", active: null, pop: false }]);
    expect(captionEvents(cue, words, { preset: "active-word" })).toEqual([
      { startS: 1, endS: 1.2, kind: "cue", active: null, pop: false },
      { startS: 1.2, endS: 1.7, kind: "highlight", active: 0, pop: false },
      { startS: 1.7, endS: 3, kind: "highlight", active: 1, pop: false },
    ]);
    const single = captionEvents(cue, words, { preset: "single-word-pop" });
    expect(single.map((e) => [e.kind, e.active, e.pop])).toEqual([
      ["word", 0, true],
      ["word", 1, true],
    ]);
    expect(eventAt(single, 1.69)?.active).toBe(0);
    expect(eventAt(single, 1.7)?.active).toBe(1);
    const look = CAPTION_LOOKS["single-word-pop"]!;
    close(popScale(single[0]!, 1.2, look), 1, 1e-12, "pop at start");
    close(popScale(single[0]!, 1.26, look), 1.06, 1e-9, "pop halfway");
    close(popScale(single[0]!, 1.5, look), 1.12, 1e-12, "pop done");
    expect(captionEvents(cue, words, { preset: "boxed" })).toHaveLength(1);
    expect(captionEvents(cue, undefined, { preset: "active-word" }).length).toBe(2);
    // one word at a time in the active-word look: each word in the active colour, no pop
    const coloured = captionEvents(cue, words, { preset: "active-word", layout: "single-word" });
    expect(coloured.map((e) => [e.kind, e.active, e.pop, e.coloured === true])).toEqual([
      ["word", 0, false, true],
      ["word", 1, false, true],
    ]);
    expect(captionEvents(cue, words, { preset: "boxed", layout: "Single word" }).map((e) => [e.kind, e.coloured === true])).toEqual([
      ["word", false],
      ["word", false],
    ]);
  });
});
