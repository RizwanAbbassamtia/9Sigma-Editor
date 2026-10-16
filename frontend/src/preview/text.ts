/**
 * Text in the preview (docs/M5-CONTRACT.md section 4, design 5.3):
 *
 * **Captions** are drawn on Canvas 2D and uploaded as a texture, the way libass draws the ASS file
 * of render/captions.py: the Noto font from `/api/fonts` (FontFace, Chromium's HarfBuzz shaping),
 * lines split on `\N` and never re-wrapped, bottom-centre (`\an2`) with `MarginV = 60` in the ASS
 * reference resolution (1920 x 1080, or 1080 x 1920 for 9:16), scaled by `frame height / PlayResY`.
 * libass sizes a font so that its ascent plus descent (the OS/2 Windows metrics) equal the ASS size
 * (`FT_SIZE_REQUEST_TYPE_REAL_DIM`), so the em size here is `size x unitsPerEm / (winAscent +
 * winDescent)`, read from the font file itself. The outline (`\bord`, scaled) is stroked first with
 * round joins, then the fill. A bold style emboldens the regular outlines as libass does with
 * FreeType's `FT_Outline_Embolden` (em / 64): the glyph grows by that much to the right and
 * upwards while its left and bottom edges stay, and the outline goes around the emboldened glyph.
 * The looks of `captions.style.preset`: active-word (the word being spoken in `active_colour`),
 * single words with a 100 -> 112 % pop over 0.12 s, and boxed (a see-through box per line).
 *
 * **Popups** are the backend's Pillow PNGs (`POST /popups/preview`, debounced 150 ms), placed and
 * animated with the overlay maths of render/ffmpeg.py (`_popup_exprs` and the `fade` filters).
 * Until a PNG arrives, an approximation in the same box is drawn and the monitor says so.
 */
import { assTime, defaultFileUrl } from "./description";
import { pyRound } from "./motion";
import type { CueDesc, PopupDesc, PopupPreviewRequest, PopupPreviewResult, PreviewCaptionStyle, PreviewWord, RenderSettings } from "./types";

// Font files and metrics --------------------------------------------------------------------

export interface FontMetrics {
  unitsPerEm: number;
  winAscent: number;
  winDescent: number;
  hheaAscent: number;
  hheaDescent: number;
}

/** `head`, `hhea` and `OS/2` of a TrueType/OpenType file (null when it is not one). */
export function parseFontMetrics(buffer: ArrayBuffer): FontMetrics | null {
  const view = new DataView(buffer);
  if (view.byteLength < 12) return null;
  const tables = view.getUint16(4);
  const find = (tag: string): number | null => {
    for (let i = 0; i < tables; i++) {
      const at = 12 + i * 16;
      if (at + 16 > view.byteLength) return null;
      const name = String.fromCharCode(view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2), view.getUint8(at + 3));
      if (name === tag) return view.getUint32(at + 8);
    }
    return null;
  };
  const head = find("head");
  const hhea = find("hhea");
  const os2 = find("OS/2");
  if (head === null || hhea === null) return null;
  const unitsPerEm = view.getUint16(head + 18);
  const hheaAscent = view.getInt16(hhea + 4);
  const hheaDescent = view.getInt16(hhea + 6);
  let winAscent = 0;
  let winDescent = 0;
  if (os2 !== null && os2 + 78 <= view.byteLength) {
    // libass reads them as signed shorts
    winAscent = view.getInt16(os2 + 74);
    winDescent = view.getInt16(os2 + 76);
  }
  return { unitsPerEm: unitsPerEm || 1000, winAscent, winDescent, hheaAscent, hheaDescent };
}

/** Noto Sans when the file could not be read (unitsPerEm 1000, win 1069 + 293). */
export const NOTO_SANS_METRICS: FontMetrics = { unitsPerEm: 1000, winAscent: 1069, winDescent: 293, hheaAscent: 1069, hheaDescent: -293 };

/** libass's ascent and descent in font units (GDI's: the OS/2 Windows metrics when they are set). */
export function assAscentDescent(metrics: FontMetrics): [number, number] {
  if (metrics.winAscent + metrics.winDescent !== 0) return [metrics.winAscent, metrics.winDescent];
  return [metrics.hheaAscent, -metrics.hheaDescent];
}

/** The em size in pixels libass uses for an ASS font size (`REAL_DIM`: ascent + descent = size). */
export function assEmPx(assSizePx: number, metrics: FontMetrics): number {
  const [ascent, descent] = assAscentDescent(metrics);
  const cell = ascent + descent;
  return cell > 0 ? (assSizePx * metrics.unitsPerEm) / cell : assSizePx;
}

export interface LoadedFont {
  family: string;
  /** the name the canvas uses (the default instance: what libass draws) */
  cssFamily: string;
  /** the same file with the whole weight axis (Pillow's Bold instance for popups) */
  cssVariable: string;
  metrics: FontMetrics;
}

/** Loads the Noto fonts of the bundle into the page (once per URL). */
export class FontLibrary {
  private readonly fonts = new Map<string, Promise<LoadedFont | null>>();

  constructor(private readonly fetchFn: typeof fetch = (input, init) => fetch(input, init)) {}

  load(family: string, url: string): Promise<LoadedFont | null> {
    const key = `${family}|${url}`;
    let job = this.fonts.get(key);
    if (!job) {
      job = this.fetchFont(family, url);
      this.fonts.set(key, job);
    }
    return job;
  }

  private async fetchFont(family: string, url: string): Promise<LoadedFont | null> {
    try {
      const response = await this.fetchFn(url, { credentials: "same-origin" });
      if (!response.ok) return null;
      const buffer = await response.arrayBuffer();
      const metrics = parseFontMetrics(buffer) ?? NOTO_SANS_METRICS;
      const safe = family.replace(/[^A-Za-z0-9 ]+/g, "");
      const cssFamily = `CCS ${safe}`;
      const cssVariable = `CCS ${safe} VF`;
      const set = fontSet();
      if (set && typeof FontFace !== "undefined") {
        const regular = new FontFace(cssFamily, buffer.slice(0), { weight: "400" });
        const variable = new FontFace(cssVariable, buffer.slice(0), { weight: "100 900" });
        await Promise.all([regular.load(), variable.load()]);
        set.add(regular);
        set.add(variable);
      }
      return { family, cssFamily, cssVariable, metrics };
    } catch {
      return null;
    }
  }
}

function fontSet(): FontFaceSet | null {
  if (typeof document !== "undefined" && document.fonts) return document.fonts;
  const scope = globalThis as unknown as { fonts?: FontFaceSet };
  return scope.fonts ?? null;
}

/** The font family files the backend serves (`/api/fonts/{file}`), by family name. */
export const FONT_FILES: Readonly<Record<string, string>> = {
  "Noto Sans": "NotoSans[wdth,wght].ttf",
  "Noto Sans Devanagari": "NotoSansDevanagari[wdth,wght].ttf",
  "Noto Naskh Arabic": "NotoNaskhArabic[wght].ttf",
  "Noto Sans Arabic": "NotoSansArabic[wdth,wght].ttf",
  "Noto Sans JP": "NotoSansJP[wght].ttf",
  "Noto Sans KR": "NotoSansKR[wght].ttf",
};

export function fontUrl(family: string, fonts: readonly { family: string; url: string }[] | undefined): string | null {
  const listed = fonts?.find((font) => font.family === family);
  if (listed) return listed.url;
  const file = FONT_FILES[family];
  return file ? `/api/fonts/${encodeURIComponent(file)}` : null;
}

// Caption looks (render/captions.py CaptionLook) ------------------------------------------------

export interface CaptionLook {
  id: string;
  layout: "phrase" | "single_word";
  activeColour: string;
  popScale: number;
  popS: number;
  box: boolean;
  boxColour: string;
  boxOpacity: number;
  boxPaddingPx: number;
}

export const CAPTION_LOOKS: Readonly<Record<string, CaptionLook>> = {
  "bold-outline": { id: "bold-outline", layout: "phrase", activeColour: "#FFE34D", popScale: 1, popS: 0, box: false, boxColour: "#000000", boxOpacity: 0.55, boxPaddingPx: 12 },
  "active-word": { id: "active-word", layout: "phrase", activeColour: "#FFE34D", popScale: 1, popS: 0, box: false, boxColour: "#000000", boxOpacity: 0.55, boxPaddingPx: 12 },
  "single-word-pop": { id: "single-word-pop", layout: "single_word", activeColour: "#FFE34D", popScale: 1.12, popS: 0.12, box: false, boxColour: "#000000", boxOpacity: 0.55, boxPaddingPx: 12 },
  boxed: { id: "boxed", layout: "phrase", activeColour: "#FFE34D", popScale: 1, popS: 0, box: true, boxColour: "#000000", boxOpacity: 0.55, boxPaddingPx: 12 },
};

export function captionPreset(style: PreviewCaptionStyle): string {
  const key = String(style.preset ?? "")
    .trim()
    .toLowerCase()
    .replace(/[_ ]/g, "-");
  return Object.prototype.hasOwnProperty.call(CAPTION_LOOKS, key) ? key : "bold-outline";
}

export function captionLook(style: PreviewCaptionStyle): CaptionLook {
  return CAPTION_LOOKS[captionPreset(style)]!;
}

/** Whether the language breaks lines by character (Japanese: Noto Sans JP), not at spaces. */
export function spaceDependent(style: PreviewCaptionStyle): boolean {
  return (style.font ?? "Noto Sans") !== "Noto Sans JP";
}

/** The cue in pieces: `[piece, isWord]`, separators (spaces, `\N`) kept (`cue_tokens`). */
export function cueTokens(text: string, spaced = true): [string, boolean][] {
  const pieces: [string, boolean][] = [];
  const cleaned = (text ?? "").replace(/\r/g, "").replace(/\n/g, "\\N");
  cleaned.split("\\N").forEach((line, number) => {
    if (number) pieces.push(["\\N", false]);
    if (spaced) {
      for (const part of line.split(/( +)/)) if (part) pieces.push([part, !part.startsWith(" ")]);
    } else {
      for (const char of line) pieces.push([char, !/\s/.test(char)]);
    }
  });
  return pieces;
}

/** Each word from its start to the next word's start (the last to the cue's end), inside the cue (`word_slices`). */
export function wordSlices(cue: { startS: number; endS: number }, words: readonly PreviewWord[]): [number, number, number][] {
  const slices: [number, number, number][] = [];
  words.forEach((word, index) => {
    const start = Math.min(Math.max(word.start_s, cue.startS), cue.endS);
    const following = index + 1 < words.length ? words[index + 1]!.start_s : cue.endS;
    const end = Math.min(Math.max(following, start), cue.endS);
    if (end - start > 1e-6) slices.push([pyRound(start, 3), pyRound(end, 3), index]);
  });
  return slices;
}

/** The words of a cue shared out by length when the timing has none (`_share_out`). */
export function shareOut(words: readonly string[], start: number, end: number): PreviewWord[] {
  const total = words.reduce((sum, word) => sum + word.length, 0) || 1;
  const span = Math.max(end - start, 0);
  let clock = start;
  return words.map((word) => {
    const length = (span * word.length) / total;
    const item = { w: word, start_s: pyRound(clock, 3), end_s: pyRound(clock + length, 3) };
    clock += length;
    return item;
  });
}

/** One ASS event of a cue: the whole cue (`active` null), the cue with one word coloured, or one word alone. */
export interface CaptionEvent {
  startS: number;
  endS: number;
  kind: "cue" | "highlight" | "word";
  /** the word shown in the active colour (or alone) */
  active: number | null;
  /** single-word-pop: the word grows 100 -> 112 % over the first 0.12 s */
  pop: boolean;
  /** a single word drawn in the active colour (active-word shown one word at a time) */
  coloured?: boolean;
}

/** Whether captions show one word at a time (models/timeline.py `CaptionStyle.single_words`). */
export function singleWords(style: PreviewCaptionStyle): boolean {
  const layout = String(style.layout ?? "")
    .trim()
    .toLowerCase()
    .replace(/[- ]/g, "_");
  return layout === "single_word" || captionPreset(style) === "single-word-pop";
}

/** The events `render/captions.py` `caption_events` writes for one cue in the style's look. */
export function captionEvents(cue: CueDesc, words: readonly PreviewWord[] | undefined, style: PreviewCaptionStyle): CaptionEvent[] {
  const look = captionLook(style);
  const preset = captionPreset(style);
  if (!cue.lines.join("").trim()) return [];
  const single = singleWords(style) || look.layout === "single_word";
  const highlight = preset === "active-word";
  const plain: CaptionEvent = { startS: cue.startS, endS: cue.endS, kind: "cue", active: null, pop: false };
  if (!single && !highlight) return [plain];
  const spaced = spaceDependent(style);
  const tokens = cueTokens(cue.text, spaced)
    .filter(([, isWord]) => isWord)
    .map(([piece]) => piece);
  const timed = words && words.length ? words : shareOut(tokens, cue.startS, cue.endS);
  if (!timed.length) return [plain];
  const slices = wordSlices(cue, timed);
  const events: CaptionEvent[] = [];
  if (single) {
    const pop = preset === "single-word-pop" && look.popS > 0 && Math.abs(look.popScale - 1) > 1e-6;
    for (const [start, end, index] of slices) {
      events.push(highlight ? { startS: start, endS: end, kind: "word", active: index, pop, coloured: true } : { startS: start, endS: end, kind: "word", active: index, pop });
    }
    return events;
  }
  if (slices.length && slices[0]![0] > cue.startS + 1e-6) events.push({ ...plain, endS: slices[0]![0] });
  for (const [start, end, index] of slices) events.push({ startS: start, endS: end, kind: "highlight", active: index, pop: false });
  return events;
}

/** The event on screen at `t` (ASS times are centiseconds; an event shows from start up to its end). */
export function eventAt(events: readonly CaptionEvent[], t: number): CaptionEvent | null {
  let found: CaptionEvent | null = null;
  for (const event of events) {
    if (assTime(event.startS) <= t + 1e-9 && t < assTime(event.endS) - 1e-9) found = event;
  }
  return found;
}

/** The pop scale of a single word at `t` (libass `\t(0,120,\fscx112\fscy112)`, linear). */
export function popScale(event: CaptionEvent, t: number, look: CaptionLook): number {
  if (!event.pop) return 1;
  const ms = Math.round(look.popS * 1000);
  const into = (t - assTime(event.startS)) * 1000;
  const k = ms > 0 ? Math.min(Math.max(into / ms, 0), 1) : 1;
  return 1 + (Math.round(look.popScale * 100) / 100 - 1) * k;
}

// Caption geometry --------------------------------------------------------------------------

export interface CaptionGeometry {
  /** frame height / PlayResY */
  scale: number;
  emPx: number;
  lineHeight: number;
  ascentPx: number;
  descentPx: number;
  /** baseline of each line, top to bottom */
  baselines: number[];
  centreX: number;
  /** the libass border in pixels (`\bord` x scale) */
  borderPx: number;
  /**
   * synthetic bold: how much thicker the strokes are (libass: em / 64, FT_Outline_Embolden, which
   * keeps the glyph's left and bottom edges: the glyph also moves right and up by half of it)
   */
  emboldenPx: number;
  marginV: number;
  /** the bottom of the text block (the alignment point of `\an2`) */
  bottom: number;
}

/** The ASS reference resolution of an aspect: 1920 x 1080, or 1080 x 1920 for 9:16. */
export function assPlayRes(aspect: string): [number, number] {
  return aspect === "9:16" ? [1080, 1920] : [1920, 1080];
}

export function captionGeometry(
  lineCount: number,
  style: PreviewCaptionStyle,
  frame: { width: number; height: number },
  aspect: string,
  metrics: FontMetrics,
  settings: Pick<RenderSettings, "captionMarginVPx">,
  scaleFactor = 1,
): CaptionGeometry {
  const [, playResY] = assPlayRes(aspect);
  const scale = frame.height / playResY;
  const size = Math.max(8, Number(style.size ?? 48)) * scale * scaleFactor;
  const [ascent, descent] = assAscentDescent(metrics);
  const cell = ascent + descent > 0 ? ascent + descent : 1;
  const emPx = assEmPx(size, metrics);
  const lineHeight = size;
  const ascentPx = (size * ascent) / cell;
  const descentPx = (size * descent) / cell;
  const marginV = settings.captionMarginVPx * scale;
  const bottom = frame.height - marginV;
  const baselines: number[] = [];
  const top = bottom - lineHeight * lineCount;
  for (let i = 0; i < lineCount; i++) baselines.push(top + ascentPx + i * lineHeight);
  const look = captionLook(style);
  const border = look.box ? 0 : Math.max(0, Number(style.outline ?? 2)) * scale;
  return {
    scale,
    emPx,
    lineHeight,
    ascentPx,
    descentPx,
    baselines,
    centreX: frame.width / 2,
    borderPx: border,
    emboldenPx: style.bold === false ? 0 : emPx / 64,
    marginV,
    bottom,
  };
}

// Caption painting --------------------------------------------------------------------------

type Canvas2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

export interface CaptionPaintInput {
  cue: CueDesc;
  event: CaptionEvent;
  t: number;
  style: PreviewCaptionStyle;
  aspect: string;
  font: LoadedFont | null;
  settings: Pick<RenderSettings, "captionMarginVPx">;
}

function cssColour(value: string | undefined, fallback: string): string {
  const text = (value ?? "").trim();
  return /^#?[0-9a-fA-F]{6}$/.test(text) ? (text.startsWith("#") ? text : `#${text}`) : fallback;
}

/** A caption drawn on its canvas (see {@link paintCaption}). */
export interface PaintedCaption {
  /** the box drawn, in frame pixels */
  box: [number, number, number, number] | null;
  /**
   * How far to move the canvas down when it is drawn (-0.5 to 0.5 pixels). The browser puts a
   * line of text on a whole pixel (a baseline asked for at 504.7 lands on 505) where libass places
   * it to an eighth of a pixel, so the first line is drawn on the whole pixel below or above its
   * place and the canvas is moved back by the rest.
   */
  offsetY: number;
}

/** The amount a browser moves a text baseline to put it on a whole pixel (it rounds halves up). */
export function baselineSnap(baseline: number): number {
  return Math.round(baseline) - baseline;
}

/** Draws one caption state on a transparent canvas the size of the frame. */
export function paintCaption(ctx: Canvas2D, width: number, height: number, input: CaptionPaintInput): PaintedCaption {
  const { cue, event, style, aspect, font, settings } = input;
  const look = captionLook(style);
  const metrics = font?.metrics ?? NOTO_SANS_METRICS;
  const family = font ? `'${font.cssFamily}'` : "'Noto Sans', sans-serif";
  const spaced = spaceDependent(style);
  const tokens = cueTokens(cue.text, spaced);
  // single words: only that word; otherwise the cue's lines with the active word coloured
  const lines: { text: string; parts: { text: string; active: boolean }[] }[] = [];
  if (event.kind === "word" && event.active !== null) {
    const word = (tokens.filter(([, isWord]) => isWord)[event.active]?.[0] ?? "").replace(/\{/g, "(").replace(/\}/g, ")");
    lines.push({ text: word, parts: [{ text: word, active: event.coloured === true }] });
  } else {
    let current: { text: string; parts: { text: string; active: boolean }[] } = { text: "", parts: [] };
    let wordNumber = -1;
    for (const [piece, isWord] of tokens) {
      if (piece === "\\N") {
        lines.push(current);
        current = { text: "", parts: [] };
        continue;
      }
      if (isWord) wordNumber += 1;
      const active = isWord && event.kind === "highlight" && wordNumber === event.active;
      const safe = piece.replace(/\{/g, "(").replace(/\}/g, ")");
      current.parts.push({ text: safe, active });
      current.text += safe;
    }
    lines.push(current);
  }
  const scaleFactor = popScale(event, input.t, look);
  const geometry = captionGeometry(lines.length, style, { width, height }, aspect, metrics, settings, scaleFactor);
  const primary = cssColour(style.primary, "#FFFFFF");
  const outline = cssColour(style.outline_colour, "#000000");
  const activeColour = cssColour(style.active_colour ?? look.activeColour, look.activeColour);
  // the first line on a whole pixel (the browser would round it there), the rest via `offsetY`
  const snap = baselineSnap((geometry.baselines[0] ?? 0) - embolden(geometry) / 2);
  ctx.save();
  ctx.translate(0, snap);
  ctx.font = `400 ${geometry.emPx}px ${family}`;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  const direction = style.rtl ? "rtl" : "ltr";
  (ctx as CanvasRenderingContext2D).direction = direction;
  const measured = lines.map((line) => ({ line, width: ctx.measureText(line.text).width }));
  let box: [number, number, number, number] | null = null;
  const extend = (x0: number, y0: number, x1: number, y1: number) => {
    box = box ? [Math.min(box[0], x0), Math.min(box[1], y0), Math.max(box[2], x1), Math.max(box[3], y1)] : [x0, y0, x1, y1];
  };
  if (look.box) {
    const pad = look.boxPaddingPx * geometry.scale;
    ctx.beginPath();
    measured.forEach(({ width: lineWidth }, i) => {
      const left = geometry.centreX - lineWidth / 2;
      const baseline = geometry.baselines[i]!;
      ctx.rect(left - pad, baseline - geometry.ascentPx - pad, lineWidth + 2 * pad, geometry.lineHeight + 2 * pad);
      extend(left - pad, baseline - geometry.ascentPx - pad, left + lineWidth + pad, baseline + geometry.descentPx + pad);
    });
    ctx.globalAlpha = look.boxOpacity;
    ctx.fillStyle = cssColour(look.boxColour, "#000000");
    ctx.fill("nonzero");
    ctx.globalAlpha = 1;
  }
  measured.forEach(({ line, width: lineWidth }, i) => {
    const baseline = geometry.baselines[i]!;
    let x = direction === "rtl" ? geometry.centreX + lineWidth / 2 : geometry.centreX - lineWidth / 2;
    extend(geometry.centreX - lineWidth / 2 - geometry.borderPx, baseline - geometry.ascentPx - geometry.borderPx, geometry.centreX + lineWidth / 2 + geometry.borderPx, baseline + geometry.descentPx + geometry.borderPx);
    if (direction === "rtl" || line.parts.length <= 1) {
      // one run keeps the shaping of the whole line (Arabic joins across words)
      const anyActive = line.parts.some((part) => part.active);
      drawRun(ctx, line.text, direction === "rtl" ? x - lineWidth : x, baseline, geometry, outline, anyActive && line.parts.length === 1 ? activeColour : primary);
      return;
    }
    // outline for the whole line first, then the fill run by run (each run where the line's
    // own prefix ends, so kerning inside the line is kept)
    if (geometry.borderPx > 0) strokeOutline(ctx, line.text, x, baseline, geometry, outline);
    let prefix = "";
    for (const part of line.parts) {
      const at = x + (prefix ? ctx.measureText(prefix).width : 0);
      fillRun(ctx, part.text, at, baseline, geometry, part.active ? activeColour : primary);
      prefix += part.text;
    }
  });
  ctx.restore();
  return { box, offsetY: -snap };
}

/** The synthetic bold in pixels (0 when it is too thin to draw). */
function embolden(geometry: CaptionGeometry): number {
  return geometry.emboldenPx > 0.05 ? geometry.emboldenPx : 0;
}

/**
 * The outline (`\bord`) around the emboldened glyph: FT_Outline_Embolden grows the glyph by `e`
 * to the right and upwards (half of it a move, half of it a widening on every side), so the
 * outline is stroked `2 x border + e` wide around the text moved by `e / 2` right and up.
 */
function strokeOutline(ctx: Canvas2D, text: string, x: number, baseline: number, geometry: CaptionGeometry, outline: string): void {
  const e = embolden(geometry);
  ctx.strokeStyle = outline;
  ctx.lineWidth = 2 * geometry.borderPx + e;
  ctx.strokeText(text, x + e / 2, baseline - e / 2);
}

function drawRun(ctx: Canvas2D, text: string, x: number, baseline: number, geometry: CaptionGeometry, outline: string, fill: string): void {
  if (geometry.borderPx > 0) strokeOutline(ctx, text, x, baseline, geometry, outline);
  fillRun(ctx, text, x, baseline, geometry, fill);
}

/** The fill of a run, emboldened like FT_Outline_Embolden (moved by `e / 2` right and up, `e` wider). */
function fillRun(ctx: Canvas2D, text: string, x: number, baseline: number, geometry: CaptionGeometry, fill: string): void {
  const e = embolden(geometry);
  ctx.fillStyle = fill;
  ctx.fillText(text, x + e / 2, baseline - e / 2);
  if (e > 0) {
    ctx.strokeStyle = fill;
    ctx.lineWidth = e;
    ctx.strokeText(text, x + e / 2, baseline - e / 2);
  }
}

// Popups (render/ffmpeg.py _popup_exprs and the fade filters) ------------------------------------

/** A number as FFmpeg's expression gets it (`fmt`: four decimals). */
export function fmt4(value: number): number {
  return pyRound(value, 4);
}

export interface PopupFrame {
  /** shown at this frame (enable window and the PNG stream both running) */
  visible: boolean;
  /** overlay position before the even-pixel rounding (frame pixels) */
  x: number;
  y: number;
  /** where the overlay really lands (yuv420: floored to even pixels) */
  xPx: number;
  yPx: number;
  alpha: number;
}

/**
 * The popup at output frame `n` of a `frame`-sized render, top-left `(x0, y0)` from the backend:
 * enable `between(t, start, end)`, slide/pop `travel x max(0, 1 - (t - start) / slide_s)^2`, the
 * alpha fades of the looped PNG (its frames shifted by `setpts=PTS+start/TB`, whole frames), the
 * overlay floored to even pixels.
 */
export function popupFrame(
  popup: Pick<PopupDesc, "anim" | "startS" | "endS">,
  n: number,
  fps: number,
  frame: { width: number; height: number },
  origin: { x: number; y: number },
  settings: Pick<RenderSettings, "popupFadeS" | "popupSlideS" | "popupSlidePx">,
): PopupFrame {
  const scale = Math.min(frame.width, frame.height) / 1080;
  const t = n / fps;
  const start = fmt4(popup.startS);
  const end = fmt4(popup.endS);
  const enabled = start <= t && t <= end;
  const travel = Math.max(1, settings.popupSlidePx * scale);
  const slide = fmt4(Math.max(settings.popupSlideS, 0.01));
  const ease = Math.max(0, 1 - (t - start) / slide) ** 2;
  let x = origin.x;
  let y = origin.y;
  if (popup.anim === "slide_left") x = origin.x + fmt4(travel) * ease;
  else if (popup.anim === "pop") y = origin.y + fmt4(travel / 2) * ease;
  const duration = Math.max(popup.endS - popup.startS, 0.2);
  const fade = fmt4(Math.min(settings.popupFadeS, duration / 3));
  const outStart = fmt4(duration - Math.min(settings.popupFadeS, duration / 3));
  const shift = Math.trunc(start * fps);
  const k = n - shift;
  const local = k / fps;
  const last = Math.ceil(fmt4(duration) * fps) - 1;
  const present = k >= 0 && k <= last;
  const alphaIn = fade > 0 ? Math.min(Math.max(local / fade, 0), 1) : 1;
  const alphaOut = fade > 0 ? 1 - Math.min(Math.max((local - outStart) / fade, 0), 1) : 1;
  return {
    visible: enabled && present,
    x,
    y,
    xPx: Math.trunc(x) & ~1,
    yPx: Math.trunc(y) & ~1,
    alpha: present ? alphaIn * alphaOut : 0,
  };
}

export const POPUP_POSITIONS = ["top-left", "top-right", "bottom-left", "bottom-right", "center"] as const;

/** Top-left corner of a popup in the frame (render/popups.py `popup_position`). */
export function popupPosition(
  position: string,
  popup: [number, number],
  frame: [number, number],
  marginPx: number,
  bottomReservedPx = 0,
): [number, number] {
  const [popupW, popupH] = popup;
  const [frameW, frameH] = frame;
  let where = (position || "bottom-left").trim().toLowerCase().replace(/_/g, "-");
  if (!(POPUP_POSITIONS as readonly string[]).includes(where)) where = "bottom-left";
  if (where === "center") return [Math.floor((frameW - popupW) / 2), Math.floor((frameH - popupH) / 2)];
  const x = where.endsWith("left") ? marginPx : frameW - popupW - marginPx;
  const y = where.startsWith("top") ? marginPx : frameH - popupH - marginPx - bottomReservedPx;
  return [Math.max(0, x), Math.max(0, y)];
}

/** The caption band popups keep clear of (pipeline/stages/edit.py `popup_assets`). */
export function popupReservedPx(style: PreviewCaptionStyle, hasCues: boolean, captionsOn: boolean, frame: [number, number], settings: Pick<RenderSettings, "captionMarginVPx">): number {
  if (!captionsOn || !hasCues) return 0;
  const scale = Math.max(0.1, Math.min(frame[0], frame[1]) / 1080);
  return pyRound((Number(style.size ?? 48) * Number(style.max_lines ?? 2) * 1.4 + settings.captionMarginVPx) * scale);
}

/** The popup request for a frame of the timeline's size. */
export function popupRequest(popup: PopupDesc, aspect: string, frameHeight: number): PopupPreviewRequest {
  return {
    text: popup.text,
    style: popup.style,
    position: popup.position,
    anim: popup.anim,
    aspect: aspect === "9:16" ? "9:16" : "16:9",
    height: Math.min(Math.max(Math.round(frameHeight), 90), 2160),
  };
}

export const POPUP_DEBOUNCE_MS = 150;

export interface PopupArt {
  /** the frame the PNG and its position belong to */
  frameHeight: number;
  x: number;
  y: number;
  w: number;
  h: number;
  image: ImageBitmap | HTMLCanvasElement | OffscreenCanvas;
  /** true while it is the engine's own approximation ("Drawing...") */
  approximate: boolean;
  key: string;
}

/** Popup PNGs from the backend (debounced 150 ms per popup), with an approximation meanwhile. */
export class PopupLibrary {
  private readonly art = new Map<string, PopupArt>();
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly wanted = new Map<string, string>();
  private destroyed = false;

  constructor(
    private readonly request: ((key: string, body: PopupPreviewRequest) => Promise<PopupPreviewResult | null>) | null,
    private readonly fetchFn: typeof fetch,
    private readonly onChange: () => void,
  ) {}

  /** The art for a popup now (the real PNG, or the approximation while it loads). */
  get(popup: PopupDesc, aspect: string, frame: [number, number], font: LoadedFont | null, reserved: number): PopupArt | null {
    const body = popupRequest(popup, aspect, frame[1]);
    const signature = JSON.stringify(body);
    const key = popup.key;
    const current = this.art.get(key);
    if (this.wanted.get(key) !== signature) {
      this.wanted.set(key, signature);
      this.schedule(key, body, signature);
    }
    if (current && current.key === signature) return current;
    if (current && !current.approximate) return current;
    const approx = approximatePopup(popup, frame, font, reserved);
    if (approx) this.art.set(key, { ...approx, key: signature });
    return approx ? { ...approx, key: signature } : null;
  }

  isDrawing(): boolean {
    for (const art of this.art.values()) if (art.approximate) return true;
    return false;
  }

  private schedule(key: string, body: PopupPreviewRequest, signature: string): void {
    const timer = this.timers.get(key);
    if (timer !== undefined) clearTimeout(timer);
    if (!this.request) return;
    // the first picture of a popup is asked for at once; edits wait 150 ms for the typing to stop
    const held = this.art.get(key);
    const delay = held && !held.approximate ? POPUP_DEBOUNCE_MS : 0;
    this.timers.set(
      key,
      setTimeout(() => {
        this.timers.delete(key);
        void this.fetchArt(key, body, signature);
      }, delay),
    );
  }

  private async fetchArt(key: string, body: PopupPreviewRequest, signature: string): Promise<void> {
    try {
      const result = await this.request!(key, body);
      if (!result || this.destroyed || this.wanted.get(key) !== signature) return;
      const response = await this.fetchFn(result.png_url, { credentials: "same-origin" });
      if (!response.ok) return;
      const image = await createImageBitmap(await response.blob(), { premultiplyAlpha: "premultiply" });
      if (this.destroyed || this.wanted.get(key) !== signature) {
        image.close();
        return;
      }
      const old = this.art.get(key);
      if (old && !old.approximate && old.image instanceof ImageBitmap) old.image.close();
      this.art.set(key, { frameHeight: body.height, x: result.x, y: result.y, w: result.w || image.width, h: result.h || image.height, image, approximate: false, key: signature });
      this.onChange();
    } catch {
      /* the approximation stays */
    }
  }

  destroy(): void {
    this.destroyed = true;
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
    for (const art of this.art.values()) if (!art.approximate && art.image instanceof ImageBitmap) art.image.close();
    this.art.clear();
  }
}

function makeCanvas(width: number, height: number): HTMLCanvasElement | OffscreenCanvas | null {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(Math.max(1, width), Math.max(1, height));
  if (typeof document !== "undefined") {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, width);
    canvas.height = Math.max(1, height);
    return canvas;
  }
  return null;
}

/** The popup drawn here the way render/popups.py lays it out (until the backend's PNG arrives). */
export function approximatePopup(popup: PopupDesc, frame: [number, number], font: LoadedFont | null, reserved: number): Omit<PopupArt, "key"> | null {
  const [frameW, frameH] = frame;
  const scale = Math.max(0.1, Math.min(frameW, frameH) / 1080);
  const fontPx = Math.max(8, pyRound(64 * scale));
  const padding = Math.max(2, pyRound(24 * scale));
  const radius = Math.max(2, pyRound(18 * scale));
  const probe = makeCanvas(4, 4);
  const probeCtx = probe?.getContext("2d") as Canvas2D | null;
  if (!probe || !probeCtx) return null;
  const family = font ? `'${font.cssVariable}'` : "'Noto Sans', sans-serif";
  probeCtx.font = `700 ${fontPx}px ${family}`;
  const widthOf = (text: string) => probeCtx.measureText(text).width;
  const text = popup.text;
  const maxText = Math.max(fontPx * 4, Math.trunc(frameW * 0.8) - 2 * padding);
  let lines = [text];
  const words = text.split(" ");
  if (words.length > 1 && widthOf(text) > maxText) {
    let best = widthOf(text);
    for (let cut = 1; cut < words.length; cut++) {
      const first = words.slice(0, cut).join(" ");
      const second = words.slice(cut).join(" ");
      const widest = Math.max(widthOf(first), widthOf(second));
      if (widest < best) {
        best = widest;
        lines = [first, second];
      }
    }
  }
  const metrics = font?.metrics ?? NOTO_SANS_METRICS;
  const ascent = (fontPx * metrics.hheaAscent) / metrics.unitsPerEm;
  const descent = (fontPx * -metrics.hheaDescent) / metrics.unitsPerEm;
  const lineHeight = Math.ceil(ascent) + Math.ceil(descent);
  const gap = pyRound(fontPx * 0.15);
  const textWidth = Math.max(...lines.map(widthOf));
  const width = Math.ceil(textWidth + 2 * padding);
  const height = lineHeight * lines.length + gap * (lines.length - 1) + 2 * padding;
  const canvas = makeCanvas(width, height);
  const ctx = canvas?.getContext("2d") as Canvas2D | null;
  if (!canvas || !ctx) return null;
  ctx.fillStyle = "rgba(31, 56, 100, 0.92)";
  ctx.beginPath();
  ctx.roundRect(0, 0, width, height, radius);
  ctx.fill();
  ctx.font = `700 ${fontPx}px ${family}`;
  ctx.fillStyle = "#FFFFFF";
  ctx.textBaseline = "alphabetic";
  let y = padding;
  for (const line of lines) {
    const lineWidth = widthOf(line);
    ctx.fillText(line, padding + (textWidth - lineWidth) / 2, y + Math.ceil(ascent));
    y += lineHeight + gap;
  }
  // until the renderer's own picture of the popup arrives, the stand-in says so
  const tag = Math.max(8, Math.round(fontPx * 0.28));
  ctx.font = `600 ${tag}px ${family}`;
  ctx.fillStyle = "rgba(255, 255, 255, 0.75)";
  ctx.textAlign = "right";
  ctx.textBaseline = "top";
  ctx.fillText("Drawing...", width - Math.max(3, padding / 3), Math.max(2, padding / 4));
  const [x0, y0] = popupPosition(popup.position, [width, height], frame, pyRound(60 * scale), reserved);
  return { frameHeight: frameH, x: x0, y: y0, w: width, h: height, image: canvas, approximate: true };
}

// The missing-picture slate -----------------------------------------------------------------------

/** A navy slate with a picture icon and "Picture missing (scene N)". */
export function paintSlate(ctx: Canvas2D, width: number, height: number, sceneNumber: number, font: LoadedFont | null): void {
  ctx.save();
  ctx.fillStyle = "#1F2A44";
  ctx.fillRect(0, 0, width, height);
  const unit = Math.min(width, height) / 540;
  const iconW = 90 * unit;
  const iconH = 66 * unit;
  const cx = width / 2;
  const cy = height / 2 - 24 * unit;
  ctx.strokeStyle = "#8EA3C9";
  ctx.lineWidth = Math.max(1, 4 * unit);
  ctx.lineJoin = "round";
  ctx.strokeRect(cx - iconW / 2, cy - iconH / 2, iconW, iconH);
  ctx.beginPath();
  ctx.moveTo(cx - iconW / 2 + 8 * unit, cy + iconH / 2 - 8 * unit);
  ctx.lineTo(cx - 8 * unit, cy - 2 * unit);
  ctx.lineTo(cx + 8 * unit, cy + 14 * unit);
  ctx.lineTo(cx + 22 * unit, cy + 2 * unit);
  ctx.lineTo(cx + iconW / 2 - 8 * unit, cy + iconH / 2 - 8 * unit);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(cx + 22 * unit, cy - 14 * unit, 7 * unit, 0, Math.PI * 2);
  ctx.stroke();
  const family = font ? `'${font.cssVariable}'` : "'Noto Sans', sans-serif";
  ctx.font = `600 ${Math.round(26 * unit)}px ${family}`;
  ctx.fillStyle = "#E6ECF7";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillText(`Picture missing (scene ${sceneNumber})`, cx, cy + iconH / 2 + 18 * unit);
  ctx.restore();
}

export { defaultFileUrl };
