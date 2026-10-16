/**
 * The preview's sound (docs/M5-CONTRACT.md section 4, design 5.4): a Web Audio graph with
 *
 * - the voice as one buffer per sentence (`/media/voice/segments/{sid}.wav`), each scheduled with
 *   `AudioBufferSourceNode.start(when, offset)` where the renderer places it: the sentence clips of
 *   `voice.segments`, or the whole voice file from `voice.start_s` (render/music.py
 *   `voice_placements`, sample-exact at 48 kHz). Buffers are decoded for a window from 10 s behind
 *   to 60 s ahead of the playhead and kept in an LRU of about 40 MB. A seek while paused decodes
 *   the clip under the playhead and the music piece there first, then the rest, a few at a time
 *   (`loadOrder`), and a press of Play waits up to 300 ms for them (`ready`), so the sound starts
 *   with the picture and nothing under the playhead is skipped (docs/M5-CONTRACT.md 8.7.4);
 * - the music stem (`07_edit/music_ducked.wav`, 16-bit PCM) on a second bus, streamed in 5 s pieces
 *   with HTTP Range requests (a 10-minute stem never sits in memory whole), each decoded by the
 *   browser's own decoder off the main thread with a WAV header put in front of its bytes; without
 *   a stem the raw track (`media.music_url`) plays through a media element with the envelope
 *   applied live;
 * - a master gain so the preview plays as loud as the final file: `preview_gain_db`, or, once the
 *   backend has measured it, the gain the renderer's one-pass `loudnorm` gives the mix every
 *   100 ms (`preview_gain_curve`), which is up to about 1 dB away from one gain at the start of a
 *   video and in short ones;
 * - 60 ms scrub grains with 10 ms fades while "Scrub audio" is on.
 *
 * The pure parts (placement, the envelope, the LRU) are tested in __tests__/audio.test.ts.
 */
import { pyRound } from "./motion";
import type { GainCurve, PreviewBundle, RenderDescription, VoiceClip } from "./types";

export const AUDIO_RATE = 48000;
export const BUFFER_BUDGET_BYTES = 40 * 1024 * 1024;
export const WINDOW_BEHIND_S = 10;
export const WINDOW_AHEAD_S = 60;
export const SCHEDULE_AHEAD_S = 2.5;
/** Seconds of music per piece of the stem: short, so the piece at a new playhead comes quickly. */
export const STEM_CHUNK_S = 5;
export const GRAIN_S = 0.06;
export const GRAIN_FADE_S = 0.01;
/** Voice clips and music pieces loading at once (the one under the playhead goes first). */
export const MAX_LOADS = 3;
/** A clip starting this soon after the playhead counts as under it (Play waits for it too). */
export const READY_LOOKAHEAD_S = 0.25;
/** A clip or piece that could not be loaded is asked for again after this long, a few times. */
export const RETRY_AFTER_MS = 3000;
export const MAX_TRIES = 3;
/**
 * The sound device is kept running while the preview is in use and stopped (the context
 * suspended) after this long without a play, a seek or a skim: starting it again takes 0.4 to
 * 1.1 s on this laptop, which a press of Play would otherwise wait for or lose sound to.
 */
export const IDLE_SUSPEND_MS = 60_000;
/** The longest Play waits for the sound device to start (without it no sound can start at all). */
export const DEVICE_WAIT_MS = 1500;
/**
 * The voice's level on each of the two speakers, as the renderer mixes it. The voice is mono
 * (a stereo file is first averaged, `aformat=...:channel_layouts=mono`) and FFmpeg turns it into
 * stereo with the centre at -3 dB (swresample's `center_mix_level`, 1/sqrt(2)), while Web Audio
 * would copy a mono buffer to both speakers at full level. Without this the preview's voice would
 * be 3 dB louder against the music, and 3 dB louder overall, than the finished video.
 */
export const VOICE_UPMIX_GAIN = Math.SQRT1_2;

// Placement (render/music.py voice_placements) --------------------------------------------

export interface Placement {
  /** samples of the voice file (48 kHz) */
  srcStart: number;
  srcEnd: number;
  /** sample of the timeline where it starts */
  at: number;
}

export interface SegmentLike {
  sentence_id: string;
  src_start_s: number;
  src_end_s: number;
  at_s: number;
}

/** Audio samples of the scenes part: the video's frames at `rate`. */
export function timelineSamples(totalFrames: number, fps: number, rate = AUDIO_RATE): number {
  return pyRound((totalFrames * rate) / Math.max(Math.trunc(fps), 1));
}

/**
 * Where the voice file's samples land on the timeline, in timeline order, never overlapping and
 * never past `total`: the whole file from `startS` (delayed in whole milliseconds, as `adelay`),
 * or each segment at `at_s` (a segment that starts before the previous one has finished loses
 * its overlapping start).
 */
export function voicePlacements(
  segments: readonly SegmentLike[] | null | undefined,
  startS: number,
  fileSamples: number | null,
  total: number,
  rate = AUDIO_RATE,
): (Placement & { segment: SegmentLike | null })[] {
  if (!segments) {
    const delay = Math.floor((pyRound(startS * 1000) * rate) / 1000);
    const length = fileSamples !== null ? Math.trunc(fileSamples) : total;
    const end = Math.min(length, Math.max(total - delay, 0));
    return end > 0 ? [{ srcStart: 0, srcEnd: end, at: delay, segment: null }] : [];
  }
  const ordered = segments
    .map((segment, position) => ({ segment, position }))
    .sort((a, b) => a.segment.at_s - b.segment.at_s || a.segment.src_start_s - b.segment.src_start_s || a.position - b.position)
    .map((item) => item.segment);
  const placements: (Placement & { segment: SegmentLike | null })[] = [];
  let cursor = 0;
  for (const segment of ordered) {
    let start = pyRound(segment.src_start_s * rate);
    let end = pyRound(segment.src_end_s * rate);
    if (fileSamples !== null) end = Math.min(end, Math.trunc(fileSamples));
    let at = pyRound(segment.at_s * rate);
    if (at < cursor) {
      start += cursor - at;
      at = cursor;
    }
    if (at >= total) break;
    end = Math.min(end, start + (total - at));
    if (end <= start) continue;
    placements.push({ srcStart: start, srcEnd: end, at, segment });
    cursor = at + (end - start);
  }
  return placements;
}

/**
 * The voice clips the preview schedules: one per sentence buffer. With segments, each placement
 * plays part of its sentence's file (offset = where the clip starts inside the sentence); without
 * them every sentence plays at `voice.start_s + sentence.start_s` (the whole file's placement).
 */
export function voiceClips(
  bundle: PreviewBundle,
  totalFrames: number,
  fps: number,
  segmentUrl: (sentenceId: string) => string,
  wholeVoiceUrl: string | null,
): VoiceClip[] {
  const timeline = bundle.timeline;
  const total = timelineSamples(totalFrames, fps);
  const sentences = new Map((bundle.sentences ?? []).map((sentence) => [sentence.id, sentence]));
  const clips: VoiceClip[] = [];
  // an animatic before the voice exists: the times are estimates and there is nothing to play
  if (bundle.timing_estimated || timeline.voice?.path === "") return clips;
  const segments = timeline.voice?.segments ?? null;
  if (segments) {
    for (const place of voicePlacements(segments, 0, null, total)) {
      const id = place.segment!.sentence_id;
      const sentence = sentences.get(id);
      const sentenceStart = sentence ? pyRound(sentence.start_s * AUDIO_RATE) : place.srcStart;
      clips.push({
        id,
        url: segmentUrl(id),
        offsetS: Math.max(place.srcStart - sentenceStart, 0) / AUDIO_RATE,
        durationS: (place.srcEnd - place.srcStart) / AUDIO_RATE,
        atS: place.at / AUDIO_RATE,
      });
    }
    return clips;
  }
  const [whole] = voicePlacements(null, timeline.voice?.start_s ?? 0, null, total);
  if (!whole) return clips;
  const ordered = [...(bundle.sentences ?? [])].sort((a, b) => a.start_s - b.start_s);
  if (!ordered.length) {
    if (wholeVoiceUrl) clips.push({ id: "voice", url: wholeVoiceUrl, offsetS: 0, durationS: (whole.srcEnd - whole.srcStart) / AUDIO_RATE, atS: whole.at / AUDIO_RATE });
    return clips;
  }
  for (const sentence of ordered) {
    const start = pyRound(sentence.start_s * AUDIO_RATE);
    const end = Math.min(pyRound(sentence.end_s * AUDIO_RATE), whole.srcEnd);
    if (end <= start) continue;
    clips.push({ id: sentence.id, url: segmentUrl(sentence.id), offsetS: 0, durationS: (end - start) / AUDIO_RATE, atS: (whole.at + start) / AUDIO_RATE });
  }
  return clips;
}

// The music envelope (07_edit/music_envelope.json) -------------------------------------------

export interface MusicEnvelope {
  step_s: number;
  gain_db: number[];
}

/** The gain (dB) at `t`: value i is the gain at i * step_s, interpolated in dB, held after the last. */
export function envelopeGainDb(envelope: MusicEnvelope | null, t: number): number {
  if (!envelope || !envelope.gain_db.length) return 0;
  const step = envelope.step_s > 0 ? envelope.step_s : 0.01;
  const position = Math.max(t, 0) / step;
  const index = Math.floor(position);
  const values = envelope.gain_db;
  if (index >= values.length - 1) return values[values.length - 1]!;
  const fraction = position - index;
  return values[index]! + (values[index + 1]! - values[index]!) * fraction;
}

export function dbToGain(db: number): number {
  return 10 ** (db / 20);
}

// loudnorm's gain curve (07_edit/preview/audio.json, measured by render/ffmpeg.py) --------------

/**
 * The gain (dB) at video time `t`: value i covers `i * stepS` to `(i + 1) * stepS` and sits at
 * the middle of that step; between two middles it is interpolated in dB, before the first and
 * after the last it holds.
 */
export function curveGainDb(curve: GainCurve, t: number): number {
  const values = curve.gainDb;
  if (!values.length) return 0;
  const position = t / curve.stepS - 0.5;
  if (position <= 0) return values[0]!;
  const index = Math.floor(position);
  if (index >= values.length - 1) return values[values.length - 1]!;
  const fraction = position - index;
  return values[index]! + (values[index + 1]! - values[index]!) * fraction;
}

/** The middles of the curve's steps after `from` up to `to` (seconds) with their gain (dB). */
export function curvePoints(curve: GainCurve, from: number, to: number): [number, number][] {
  const points: [number, number][] = [];
  const step = curve.stepS;
  const first = Math.max(0, Math.floor(from / step - 0.5) + 1);
  for (let index = first; index < curve.gainDb.length; index++) {
    const at = (index + 0.5) * step;
    if (at <= from) continue;
    if (at > to) break;
    points.push([at, curve.gainDb[index]!]);
  }
  return points;
}

// LRU of decoded buffers --------------------------------------------------------------------

export class ByteLru<V> {
  private readonly items = new Map<string, { value: V; bytes: number }>();
  private total = 0;

  constructor(readonly budget: number) {}

  get size(): number {
    return this.items.size;
  }

  get bytes(): number {
    return this.total;
  }

  get(key: string): V | undefined {
    const item = this.items.get(key);
    if (!item) return undefined;
    this.items.delete(key);
    this.items.set(key, item);
    return item.value;
  }

  has(key: string): boolean {
    return this.items.has(key);
  }

  /** Adds an entry; evicts the least recently used ones (never those in `keep`) over budget. */
  set(key: string, value: V, bytes: number, keep: ReadonlySet<string> = new Set()): void {
    const old = this.items.get(key);
    if (old) {
      this.total -= old.bytes;
      this.items.delete(key);
    }
    this.items.set(key, { value, bytes });
    this.total += bytes;
    for (const [other, item] of this.items) {
      if (this.total <= this.budget) break;
      if (other === key || keep.has(other)) continue;
      this.items.delete(other);
      this.total -= item.bytes;
    }
  }

  delete(key: string): void {
    const item = this.items.get(key);
    if (!item) return;
    this.total -= item.bytes;
    this.items.delete(key);
  }

  clear(): void {
    this.items.clear();
    this.total = 0;
  }
}

/** The clips that sound inside [from, to). */
export function clipsBetween(clips: readonly VoiceClip[], from: number, to: number): VoiceClip[] {
  return clips.filter((clip) => clip.atS < to && clip.atS + clip.durationS > from);
}

/** The music piece that plays at `t`. */
export function stemPieceAt(t: number): number {
  return Math.floor(Math.max(t, 0) / STEM_CHUNK_S);
}

export type LoadItem = { kind: "clip"; url: string } | { kind: "stem"; index: number };

/**
 * What to decode around `t`, most urgent first: the voice clips under the playhead (or starting
 * within READY_LOOKAHEAD_S) and the music piece there, the next piece when `t` is near its end,
 * then the rest of the window (10 s behind to 60 s ahead), those ahead first and nearest first.
 */
export function loadOrder(clips: readonly VoiceClip[], t: number, stem: boolean, durationS: number): LoadItem[] {
  const items: LoadItem[] = [];
  const seen = new Set<string>();
  const addClip = (clip: VoiceClip) => {
    if (seen.has(clip.url)) return;
    seen.add(clip.url);
    items.push({ kind: "clip", url: clip.url });
  };
  clipsBetween(clips, t, t + READY_LOOKAHEAD_S).forEach(addClip);
  if (stem) {
    const piece = stemPieceAt(t);
    items.push({ kind: "stem", index: piece });
    const next = (piece + 1) * STEM_CHUNK_S;
    if (next - t < SCHEDULE_AHEAD_S && next < durationS) items.push({ kind: "stem", index: piece + 1 });
  }
  const distance = (clip: VoiceClip) => (clip.atS >= t ? clip.atS - t : 2 * (t - (clip.atS + clip.durationS)));
  clipsBetween(clips, t - WINDOW_BEHIND_S, t + WINDOW_AHEAD_S)
    .sort((a, b) => distance(a) - distance(b))
    .forEach(addClip);
  return items;
}

// WAV (the stem is 16-bit PCM; decoded here so it can be streamed in pieces) ------------------

export interface WavInfo {
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
  format: number;
  dataOffset: number;
  dataBytes: number;
}

/** Reads the RIFF header of a PCM WAV (the first few KB are enough). */
export function parseWavHeader(bytes: ArrayBuffer): WavInfo | null {
  const view = new DataView(bytes);
  if (view.byteLength < 12) return null;
  const tag = (offset: number) => String.fromCharCode(view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3));
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
  let offset = 12;
  let fmt: Omit<WavInfo, "dataOffset" | "dataBytes"> | null = null;
  while (offset + 8 <= view.byteLength) {
    const id = tag(offset);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === "fmt " && body + 16 <= view.byteLength) {
      fmt = {
        format: view.getUint16(body, true),
        channels: view.getUint16(body + 2, true),
        sampleRate: view.getUint32(body + 4, true),
        bitsPerSample: view.getUint16(body + 14, true),
      };
    } else if (id === "data") {
      if (!fmt) return null;
      return { ...fmt, dataOffset: body, dataBytes: size };
    }
    offset = body + size + (size % 2);
  }
  return null;
}

/**
 * A RIFF header for `dataBytes` of PCM in the stem's format: put in front of a range of the stem,
 * the browser's decoder (`decodeAudioData`, off the main thread) reads the piece.
 */
export function wavHeader(info: Pick<WavInfo, "channels" | "sampleRate" | "bitsPerSample" | "format">, dataBytes: number): ArrayBuffer {
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const write = (offset: number, text: string) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  const channels = Math.max(info.channels, 1);
  const blockAlign = channels * (info.bitsPerSample / 8);
  write(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, info.format === 3 ? 3 : 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, info.sampleRate, true);
  view.setUint32(28, info.sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, info.bitsPerSample, true);
  write(36, "data");
  view.setUint32(40, dataBytes, true);
  return header;
}

/** A range of the stem as a WAV file of its own (a new buffer: `bytes` stays usable). */
export function withWavHeader(info: Pick<WavInfo, "channels" | "sampleRate" | "bitsPerSample" | "format">, bytes: ArrayBuffer): ArrayBuffer {
  const out = new Uint8Array(44 + bytes.byteLength);
  out.set(new Uint8Array(wavHeader(info, bytes.byteLength)), 0);
  out.set(new Uint8Array(bytes), 44);
  return out.buffer;
}

/** 16-bit (or 32-bit float) PCM bytes -> planar float channels. */
export function decodePcm(bytes: ArrayBuffer, info: Pick<WavInfo, "channels" | "bitsPerSample" | "format">): Float32Array[] {
  const channels = Math.max(info.channels, 1);
  const width = info.bitsPerSample / 8;
  const frames = Math.floor(bytes.byteLength / (width * channels));
  const out = Array.from({ length: channels }, () => new Float32Array(frames));
  const view = new DataView(bytes);
  const float = info.format === 3 && info.bitsPerSample === 32;
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      const at = (i * channels + c) * width;
      out[c]![i] = float ? view.getFloat32(at, true) : info.bitsPerSample === 16 ? view.getInt16(at, true) / 32768 : 0;
    }
  }
  return out;
}

// The engine ----------------------------------------------------------------------------------

export interface AudioEngineOptions {
  fetch?: typeof fetch;
  onError?: (message: string) => void;
}

/**
 * The bus every voice clip (and scrub grain) plays through: one channel (a stereo clip is
 * averaged, `0.5 x (L + R)`, as the renderer's mono step does) at `VOICE_UPMIX_GAIN`, so each
 * speaker gets the voice at the level of the finished video.
 */
export function createVoiceBus(ctx: BaseAudioContext): GainNode {
  const bus = ctx.createGain();
  bus.channelCount = 1;
  bus.channelCountMode = "explicit";
  bus.channelInterpretation = "speakers";
  bus.gain.value = VOICE_UPMIX_GAIN;
  return bus;
}

interface Scheduled {
  key: string;
  node: AudioBufferSourceNode;
}

/**
 * The Web Audio side of the preview. All times are on the video clock (seconds); `now()` is the
 * audio clock the picture follows while playing.
 */
export class AudioEngine {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private voiceBus: GainNode | null = null;
  private musicBus: GainNode | null = null;
  private desc: RenderDescription | null = null;
  private readonly cache = new ByteLru<AudioBuffer>(BUFFER_BUDGET_BYTES);
  private readonly loading = new Map<string, Promise<AudioBuffer | null>>();
  /** loads that failed: when and how often (asked again after RETRY_AFTER_MS, MAX_TRIES times) */
  private readonly failed = new Map<string, { at: number; tries: number }>();
  /** what is still to be decoded around the playhead, most urgent first */
  private queue: LoadItem[] = [];
  /** loads of the queue in progress (at most MAX_LOADS) */
  private active = 0;
  private scheduled: Scheduled[] = [];
  private playing = false;
  /** video time `t0` sounds at context time `c0` */
  private t0 = 0;
  private c0 = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private stem: { url: string; info: WavInfo | null; header: Promise<WavInfo | null> | null } | null = null;
  private musicElement: HTMLAudioElement | null = null;
  private musicSource: MediaElementAudioSourceNode | null = null;
  private envelope: { url: string; data: MusicEnvelope | null; loading: Promise<MusicEnvelope | null> | null } | null = null;
  private volume = 1;
  /** video time up to which the master gain's ramps along the loudness curve are scheduled */
  private gainPlannedTo = 0;
  /** bumped by every play and pause: a play that resolves late after a pause does nothing */
  private playToken = 0;
  /** stops the sound device after a while without use (see `wake`) */
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly fetchFn: typeof fetch;
  private destroyed = false;

  constructor(private readonly options: AudioEngineOptions = {}) {
    this.fetchFn = options.fetch ?? ((input, init) => fetch(input, init));
  }

  /** Creates the AudioContext (call from a user gesture where possible). */
  ensureContext(): AudioContext | null {
    if (this.ctx || this.destroyed) return this.ctx;
    const Ctor: typeof AudioContext | undefined =
      typeof window !== "undefined" ? (window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext) : undefined;
    if (!Ctor) return null;
    try {
      this.ctx = new Ctor({ latencyHint: "playback", sampleRate: AUDIO_RATE });
    } catch {
      try {
        this.ctx = new Ctor();
      } catch {
        return null;
      }
    }
    const ctx = this.ctx;
    this.master = ctx.createGain();
    this.voiceBus = createVoiceBus(ctx);
    this.musicBus = ctx.createGain();
    this.voiceBus.connect(this.master);
    this.musicBus.connect(this.master);
    this.master.connect(ctx.destination);
    this.applyMasterGain();
    return ctx;
  }

  get context(): AudioContext | null {
    return this.ctx;
  }

  /**
   * Opens the audio context ahead of the first play: on Windows creating it opens the sound
   * device and can hold the page for a few hundred milliseconds, which would freeze the picture
   * just after the first press of Play. It then keeps running (silent) while the preview is in
   * use, so a press of Play does not wait for the device to start again (see `wake`).
   */
  prepare(): void {
    if (this.ctx || this.destroyed || this.playing) return;
    if (this.ensureContext()) this.wake();
  }

  /**
   * The preview is in use (opened, a seek, a skim): the sound device runs, and stops after
   * IDLE_SUSPEND_MS without use unless something plays.
   */
  private wake(): void {
    const ctx = this.ctx;
    if (!ctx || this.destroyed) return;
    if (ctx.state === "suspended") void ctx.resume().catch(() => undefined);
    if (this.idleTimer !== null) clearTimeout(this.idleTimer);
    this.idleTimer = this.playing
      ? null
      : setTimeout(() => {
          this.idleTimer = null;
          if (!this.playing && this.ctx && this.ctx.state === "running") void this.ctx.suspend().catch(() => undefined);
        }, IDLE_SUSPEND_MS);
  }

  setDescription(desc: RenderDescription): void {
    const previous = this.desc;
    this.desc = desc;
    if (!previous || previous.audio.stemUrl !== desc.audio.stemUrl) this.stem = desc.audio.stemUrl ? { url: desc.audio.stemUrl, info: null, header: null } : null;
    if (!previous || previous.audio.envelopeUrl !== desc.audio.envelopeUrl) this.envelope = desc.audio.envelopeUrl ? { url: desc.audio.envelopeUrl, data: null, loading: null } : null;
    if (!previous || previous.audio.music?.url !== desc.audio.music?.url) this.dropMusicElement();
    this.applyMasterGain();
    if (this.playing) this.reschedule(this.now());
  }

  setVolume(volume: number): void {
    this.volume = Math.min(Math.max(volume, 0), 2);
    this.applyMasterGain();
  }

  /** The master gain (dB) at video time `t`: the loudness curve's, else the one gain. */
  private gainDbAt(t: number): number {
    const audio = this.desc?.audio;
    if (!audio) return 0;
    return audio.gainCurve ? curveGainDb(audio.gainCurve, t) : audio.gainDb;
  }

  private applyMasterGain(): void {
    if (!this.master || !this.desc) return;
    if (this.playing && this.ctx) {
      this.planGain(this.now());
      return;
    }
    const param = this.master.gain;
    param.cancelScheduledValues(0);
    param.value = dbToGain(this.gainDbAt(this.t0)) * this.volume;
  }

  /** While playing: the master gain from video time `t` on, along the loudness curve. */
  private planGain(t: number): void {
    const ctx = this.ctx;
    const master = this.master;
    if (!ctx || !master || !this.desc) return;
    const param = master.gain;
    param.cancelScheduledValues(0);
    param.setValueAtTime(dbToGain(this.gainDbAt(t)) * this.volume, Math.max(ctx.currentTime, this.c0 + (t - this.t0)));
    this.gainPlannedTo = t;
    this.extendGain(t);
  }

  /** Schedules the curve's ramps up to a little past the sound already scheduled. */
  private extendGain(t: number): void {
    const curve = this.desc?.audio.gainCurve;
    const master = this.master;
    if (!curve || !master || !this.playing) return;
    const until = t + SCHEDULE_AHEAD_S + 0.5;
    if (until <= this.gainPlannedTo) return;
    for (const [at, db] of curvePoints(curve, this.gainPlannedTo, until)) {
      master.gain.linearRampToValueAtTime(dbToGain(db) * this.volume, this.c0 + (at - this.t0));
    }
    this.gainPlannedTo = until;
  }

  /** The audio clock in video seconds (only meaningful while playing). */
  now(): number {
    return this.nowAt(performance.now());
  }

  /**
   * The audio clock at `perfMs` on the performance clock (an animation frame's time): what the
   * speakers play at that moment (output latency included) when the browser can say, else the
   * context's own time.
   */
  nowAt(perfMs: number): number {
    const ctx = this.ctx;
    if (!ctx || !this.playing) return this.t0;
    let contextTime = ctx.currentTime;
    const stamp = typeof ctx.getOutputTimestamp === "function" ? ctx.getOutputTimestamp() : null;
    if (stamp && typeof stamp.contextTime === "number" && typeof stamp.performanceTime === "number" && stamp.performanceTime > 0) {
      contextTime = stamp.contextTime + (perfMs - stamp.performanceTime) / 1000;
    }
    return this.t0 + Math.max(0, contextTime - this.c0);
  }

  /** True when the audio clock is running (a suspended context cannot drive the picture). */
  get running(): boolean {
    return this.playing && this.ctx !== null && this.ctx.state === "running";
  }

  /**
   * Starts the sound at video time `at` (a number, or the picture's clock read once the audio
   * context is running, so the sound joins the picture where it is by then).
   */
  async play(at: number | (() => number)): Promise<void> {
    const ctx = this.ensureContext();
    if (!ctx) return;
    const token = ++this.playToken;
    if (this.idleTimer !== null) clearTimeout(this.idleTimer);
    this.idleTimer = null;
    if (ctx.state !== "running") {
      try {
        await ctx.resume();
      } catch {
        /* blocked until a user gesture: the picture runs on the performance clock */
      }
    }
    if (token !== this.playToken || ctx.state !== "running") return;
    const t = typeof at === "function" ? at() : at;
    this.stopSources();
    this.playing = true;
    this.t0 = t;
    this.c0 = ctx.currentTime + 0.03;
    this.planGain(t);
    this.preload(t);
    this.reschedule(t);
    if (this.timer === null) this.timer = setInterval(() => this.tick(), 250);
  }

  pause(t: number): void {
    this.playToken += 1;
    this.playing = false;
    this.t0 = t;
    this.stopSources();
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    if (this.musicElement) this.musicElement.pause();
    this.applyMasterGain();
    if (!this.destroyed) this.wake();
  }

  /**
   * Decodes what lies around `t` (the window from 10 s behind to 60 s ahead): the clips under the
   * playhead and the music piece there first, a few at a time (`loadOrder`, MAX_LOADS). A new
   * call replaces what was still waiting.
   */
  preload(t: number): void {
    const desc = this.desc;
    if (!desc || this.destroyed) return;
    // decoding needs a context; a seek or a skim also wakes the sound device for the next Play
    if (!this.ctx) this.prepare();
    else this.wake();
    this.queue = loadOrder(desc.audio.clips, t, this.stem !== null, desc.durationS);
    this.pump();
    void this.envelopeData();
  }

  /** Starts the next loads of the queue while fewer than MAX_LOADS are running. */
  private pump(): void {
    while (this.active < MAX_LOADS && this.queue.length && !this.destroyed) {
      const item = this.queue.shift()!;
      const key = item.kind === "clip" ? item.url : this.stemKey(item.index);
      if (key === null || this.cache.has(key) || this.loading.has(key) || !this.mayTry(key)) continue;
      const job = item.kind === "clip" ? this.buffer(item.url) : this.stemPiece(item.index);
      this.active += 1;
      void job.finally(() => {
        this.active -= 1;
        this.pump();
      });
    }
  }

  /**
   * Gets the sound at `t` ready to start: the voice clips under the playhead (or starting within
   * READY_LOOKAHEAD_S) and the music piece there decoded, waited for at most `timeoutMs`, and the
   * sound device running, waited for at most `deviceMs` (no sound at all can start without it).
   * Resolves true when all of it is ready, false when a wait ran out (the picture starts anyway).
   */
  async ready(t: number, timeoutMs: number, deviceMs = DEVICE_WAIT_MS): Promise<boolean> {
    const desc = this.desc;
    const ctx = this.ensureContext();
    if (!ctx || !desc) return false;
    this.preload(t);
    const device = ctx.state === "running" ? Promise.resolve() : ctx.resume().catch(() => undefined);
    const sound: Promise<unknown>[] = [];
    for (const clip of clipsBetween(desc.audio.clips, t, t + READY_LOOKAHEAD_S)) sound.push(this.buffer(clip.url));
    if (this.stem) sound.push(this.stemPiece(stemPieceAt(t)));
    const timers: ReturnType<typeof setTimeout>[] = [];
    const after = (ms: number) => new Promise<false>((resolve) => timers.push(setTimeout(() => resolve(false), Math.max(ms, 0))));
    const started = performance.now();
    try {
      const decoded = sound.length ? await Promise.race([Promise.all(sound).then(() => true), after(timeoutMs)]) : true;
      const left = deviceMs - (performance.now() - started);
      const running = ctx.state === "running" || (await Promise.race([device.then(() => ctx.state === "running"), after(left)]));
      return decoded && running;
    } finally {
      timers.forEach(clearTimeout);
    }
  }

  private mayTry(key: string): boolean {
    const failure = this.failed.get(key);
    return !failure || (failure.tries < MAX_TRIES && performance.now() - failure.at >= RETRY_AFTER_MS);
  }

  private noteFailure(key: string): void {
    const failure = this.failed.get(key);
    this.failed.set(key, { at: performance.now(), tries: (failure?.tries ?? 0) + 1 });
  }

  private stemKey(index: number): string | null {
    return this.stem ? `stem:${this.stem.url}:${index}` : null;
  }

  private keepSet(t: number): Set<string> {
    const desc = this.desc;
    if (!desc) return new Set();
    const keep = new Set(clipsBetween(desc.audio.clips, t - 2, t + 20).map((clip) => clip.url));
    for (let index = stemPieceAt(t - 2); index <= stemPieceAt(t + 20); index++) {
      const key = this.stemKey(index);
      if (key) keep.add(key);
    }
    return keep;
  }

  private async buffer(url: string): Promise<AudioBuffer | null> {
    const cached = this.cache.get(url);
    if (cached) return cached;
    const pending = this.loading.get(url);
    if (pending) return pending;
    if (!this.mayTry(url)) return null;
    const ctx = this.ensureContext();
    if (!ctx) return null;
    const job = (async () => {
      try {
        const response = await this.fetchFn(url, { credentials: "same-origin" });
        if (!response.ok) throw new Error(`${response.status}`);
        const bytes = await response.arrayBuffer();
        const decoded = await ctx.decodeAudioData(bytes);
        this.cache.set(url, decoded, decoded.length * decoded.numberOfChannels * 4, this.keepSet(this.now()));
        this.failed.delete(url);
        return decoded;
      } catch {
        this.noteFailure(url);
        return null;
      } finally {
        this.loading.delete(url);
      }
    })();
    this.loading.set(url, job);
    const buffer = await job;
    if (buffer && this.playing) this.reschedule(this.now());
    return buffer;
  }

  private tick(): void {
    if (!this.playing) return;
    const t = this.now();
    this.preload(t);
    this.reschedule(t);
    this.updateMusicElement(t);
  }

  private stopSources(): void {
    for (const item of this.scheduled) {
      try {
        item.node.onended = null;
        item.node.stop();
        item.node.disconnect();
      } catch {
        /* already stopped */
      }
    }
    this.scheduled = [];
  }

  /** Schedules every voice clip and stem piece that starts before `t + 2.5 s` and is not yet scheduled. */
  private reschedule(t: number): void {
    const ctx = this.ctx;
    const desc = this.desc;
    if (!ctx || !desc || !this.playing || !this.voiceBus || !this.musicBus) return;
    const horizon = t + SCHEDULE_AHEAD_S;
    const end = desc.durationS;
    this.extendGain(t);
    for (const clip of clipsBetween(desc.audio.clips, t, Math.min(horizon, end))) {
      const key = `v:${clip.id}:${clip.atS}`;
      if (this.scheduled.some((item) => item.key === key)) continue;
      const buffer = this.cache.get(clip.url);
      if (!buffer) continue;
      this.startPiece(key, buffer, this.voiceBus, clip.atS, clip.offsetS, Math.min(clip.durationS, end - clip.atS), t);
    }
    this.scheduleStem(t, horizon);
  }

  private startPiece(key: string, buffer: AudioBuffer, bus: AudioNode, atS: number, offsetS: number, durationS: number, t: number): void {
    const ctx = this.ctx!;
    // the scheduling clock: what the context will play 10 ms from now, on the video clock
    const soonest = Math.max(t, this.t0 + (ctx.currentTime + 0.01 - this.c0));
    const late = Math.max(soonest - atS, 0);
    const remaining = durationS - late;
    if (remaining <= 0.002) return;
    const offset = offsetS + late;
    if (offset >= buffer.duration) return;
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(bus);
    const when = this.c0 + (atS + late - this.t0);
    node.start(Math.max(when, ctx.currentTime), offset, Math.min(remaining, buffer.duration - offset));
    const item = { key, node };
    node.onended = () => {
      this.scheduled = this.scheduled.filter((other) => other !== item);
      try {
        node.disconnect();
      } catch {
        /* gone */
      }
    };
    this.scheduled.push(item);
  }

  // The stem, in pieces -------------------------------------------------------------------

  private async stemHeader(): Promise<WavInfo | null> {
    const stem = this.stem;
    if (!stem) return null;
    if (stem.info) return stem.info;
    stem.header ??= (async () => {
      try {
        const response = await this.fetchFn(stem.url, { headers: { Range: "bytes=0-8191" }, credentials: "same-origin" });
        if (!response.ok) return null;
        const info = parseWavHeader(await response.arrayBuffer());
        stem.info = info && (info.bitsPerSample === 16 || (info.format === 3 && info.bitsPerSample === 32)) ? info : null;
        return stem.info;
      } catch {
        return null;
      }
    })();
    return stem.header;
  }

  /**
   * A range of the stem's PCM as an AudioBuffer: decoded by the browser (off the main thread)
   * with a WAV header in front; read here only when the browser cannot.
   */
  private async decodePiece(ctx: BaseAudioContext, info: WavInfo, bytes: ArrayBuffer): Promise<AudioBuffer | null> {
    try {
      const decoded = await ctx.decodeAudioData(withWavHeader(info, bytes));
      if (decoded.length > 0) return decoded;
    } catch {
      /* read below */
    }
    const channels = decodePcm(bytes, info);
    const frames = channels[0]?.length ?? 0;
    if (!frames) return null;
    const buffer = ctx.createBuffer(channels.length, frames, info.sampleRate);
    channels.forEach((data, c) => buffer.copyToChannel(data as Float32Array<ArrayBuffer>, c));
    return buffer;
  }

  private async stemPiece(index: number): Promise<AudioBuffer | null> {
    const stem = this.stem;
    const ctx = this.ctx;
    if (!stem || !ctx) return null;
    const key = `stem:${stem.url}:${index}`;
    const cached = this.cache.get(key);
    if (cached) return cached;
    const pending = this.loading.get(key);
    if (pending) return pending;
    if (!this.mayTry(key)) return null;
    const job = (async () => {
      try {
        const info = await this.stemHeader();
        if (!info) return null;
        const frameBytes = (info.bitsPerSample / 8) * info.channels;
        const framesPerPiece = Math.round(STEM_CHUNK_S * info.sampleRate);
        const first = info.dataOffset + index * framesPerPiece * frameBytes;
        const last = Math.min(first + framesPerPiece * frameBytes, info.dataOffset + info.dataBytes) - 1;
        if (last < first) return null;
        const response = await this.fetchFn(stem.url, { headers: { Range: `bytes=${first}-${last}` }, credentials: "same-origin" });
        if (!response.ok) throw new Error(`${response.status}`);
        const buffer = await this.decodePiece(ctx, info, await response.arrayBuffer());
        if (!buffer) return null;
        this.cache.set(key, buffer, buffer.length * buffer.numberOfChannels * 4, this.keepSet(this.now()));
        this.failed.delete(key);
        return buffer;
      } catch {
        this.noteFailure(key);
        return null;
      } finally {
        this.loading.delete(key);
      }
    })();
    this.loading.set(key, job);
    const buffer = await job;
    if (buffer && this.playing) this.reschedule(this.now());
    return buffer;
  }

  private scheduleStem(t: number, horizon: number): void {
    if (!this.stem || !this.musicBus) {
      this.updateMusicElement(t);
      return;
    }
    const firstPiece = Math.floor(Math.max(t, 0) / STEM_CHUNK_S);
    const lastPiece = Math.floor(Math.max(horizon, 0) / STEM_CHUNK_S);
    for (let index = firstPiece; index <= lastPiece + 1; index++) {
      const key = `s:${index}`;
      if (this.scheduled.some((item) => item.key === key)) continue;
      const buffer = this.cache.get(`stem:${this.stem.url}:${index}`);
      if (!buffer) {
        if (index * STEM_CHUNK_S < (this.desc?.durationS ?? 0)) void this.stemPiece(index);
        continue;
      }
      if (index * STEM_CHUNK_S > horizon) continue;
      this.startPiece(key, buffer, this.musicBus, index * STEM_CHUNK_S, 0, buffer.duration, t);
    }
  }

  // Without a stem: the raw track through a media element, the envelope applied live --------

  /** The music envelope (fetched once; asked again after a failure like the clips are). */
  private async envelopeData(): Promise<MusicEnvelope | null> {
    const envelope = this.envelope;
    if (!envelope) return null;
    if (envelope.data) return envelope.data;
    if (envelope.loading) return envelope.loading;
    const key = `envelope:${envelope.url}`;
    if (!this.mayTry(key)) return null;
    envelope.loading = (async () => {
      try {
        const response = await this.fetchFn(envelope.url, { credentials: "same-origin" });
        if (!response.ok) throw new Error(`${response.status}`);
        const raw = (await response.json()) as { step_s?: unknown; gain_db?: unknown };
        const values = Array.isArray(raw.gain_db) ? raw.gain_db.map((v) => (typeof v === "number" && Number.isFinite(v) ? v : 0)) : [];
        envelope.data = { step_s: typeof raw.step_s === "number" && raw.step_s > 0 ? raw.step_s : 0.01, gain_db: values };
        return envelope.data;
      } catch {
        this.noteFailure(key);
        return null;
      } finally {
        envelope.loading = null;
      }
    })();
    return envelope.loading;
  }

  private dropMusicElement(): void {
    if (this.musicElement) {
      this.musicElement.pause();
      this.musicElement.removeAttribute("src");
      this.musicElement.load();
    }
    try {
      this.musicSource?.disconnect();
    } catch {
      /* gone */
    }
    this.musicElement = null;
    this.musicSource = null;
  }

  private updateMusicElement(t: number): void {
    const music = this.desc?.audio.music;
    const ctx = this.ctx;
    if (!music || this.stem || !ctx || !this.musicBus || typeof Audio === "undefined") return;
    if (!this.musicElement) {
      const element = new Audio();
      element.crossOrigin = "anonymous";
      element.loop = true;
      element.preload = "auto";
      element.src = music.url;
      this.musicElement = element;
      try {
        this.musicSource = ctx.createMediaElementSource(element);
        this.musicSource.connect(this.musicBus);
      } catch {
        this.musicSource = null;
      }
    }
    const element = this.musicElement;
    const duration = this.desc?.durationS ?? 0;
    const fadeIn = music.fadeInS > 0 ? Math.min(t / Math.min(music.fadeInS, Math.max(duration, 1e-3)), 1) : 1;
    const fadeOut = music.fadeOutS > 0 && music.fadeOutS < duration ? Math.min(Math.max((duration - t) / music.fadeOutS, 0), 1) : 1;
    const envelope = this.envelope?.data ?? null;
    const gain = dbToGain(music.gainDb + envelopeGainDb(envelope, t)) * fadeIn * fadeOut;
    this.musicBus.gain.setTargetAtTime(gain, ctx.currentTime, 0.02);
    if (!this.playing) {
      element.pause();
      return;
    }
    const target = music.startS + t;
    const length = Number.isFinite(element.duration) && element.duration > 0 ? element.duration : null;
    const wanted = length ? target % length : target;
    if (element.paused || Math.abs(element.currentTime - wanted) > 0.25) {
      try {
        element.currentTime = wanted;
      } catch {
        /* not seekable yet */
      }
      void element.play().catch(() => undefined);
    }
  }

  // Scrubbing ---------------------------------------------------------------------------------

  /** A 60 ms grain of the voice at `t` with 10 ms fades (when its buffer is ready). */
  grain(t: number): void {
    const ctx = this.ensureContext();
    const desc = this.desc;
    if (!ctx || !desc || !this.voiceBus || this.playing) return;
    if (ctx.state !== "running") void ctx.resume().catch(() => undefined);
    const clip = desc.audio.clips.find((item) => item.atS <= t && t < item.atS + item.durationS);
    if (!clip) return;
    const buffer = this.cache.get(clip.url);
    if (!buffer) {
      void this.buffer(clip.url);
      return;
    }
    const offset = clip.offsetS + (t - clip.atS);
    if (offset >= buffer.duration) return;
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    const envelope = ctx.createGain();
    const start = ctx.currentTime + 0.005;
    envelope.gain.setValueAtTime(0, start);
    envelope.gain.linearRampToValueAtTime(1, start + GRAIN_FADE_S);
    envelope.gain.setValueAtTime(1, start + GRAIN_S - GRAIN_FADE_S);
    envelope.gain.linearRampToValueAtTime(0, start + GRAIN_S);
    node.connect(envelope);
    envelope.connect(this.voiceBus);
    node.start(start, offset, GRAIN_S);
    node.onended = () => {
      try {
        node.disconnect();
        envelope.disconnect();
      } catch {
        /* gone */
      }
    };
  }

  /** Bytes of decoded audio held (for the memory budget). */
  get heldBytes(): number {
    return this.cache.bytes;
  }

  destroy(): void {
    this.destroyed = true;
    if (this.idleTimer !== null) clearTimeout(this.idleTimer);
    this.idleTimer = null;
    this.pause(this.t0);
    this.dropMusicElement();
    this.cache.clear();
    this.loading.clear();
    this.queue = [];
    const ctx = this.ctx;
    this.ctx = null;
    if (ctx) void ctx.close().catch(() => undefined);
  }

  /** For tests and the parity harness: report problems in plain English. */
  protected reportError(message: string): void {
    this.options.onError?.(message);
  }
}
