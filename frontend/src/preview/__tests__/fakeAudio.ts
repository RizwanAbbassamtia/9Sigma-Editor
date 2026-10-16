/**
 * A Web Audio stand-in for the engine's tests: enough of AudioContext for the audio engine (gains,
 * buffer sources that record how they were started, decodeAudioData for PCM WAV files), and a
 * fetch whose answers the test hands out.
 */
import { parseWavHeader } from "../audio";

export class FakeParam {
  value = 1;
  setValueAtTime(value: number): void {
    this.value = value;
  }
  linearRampToValueAtTime(): void {}
  cancelScheduledValues(): void {}
  setTargetAtTime(): void {}
}

export class FakeNode {
  gain = new FakeParam();
  channelCount = 2;
  channelCountMode = "max";
  channelInterpretation = "speakers";
  connect(): void {}
  disconnect(): void {}
}

export class FakeBuffer {
  constructor(
    readonly numberOfChannels: number,
    readonly length: number,
    readonly sampleRate: number,
  ) {}
  get duration(): number {
    return this.length / this.sampleRate;
  }
  copyToChannel(): void {}
}

export interface StartCall {
  when: number;
  offset: number;
  duration: number | undefined;
  buffer: FakeBuffer | null;
}

export class FakeSource extends FakeNode {
  buffer: FakeBuffer | null = null;
  onended: (() => void) | null = null;
  constructor(private readonly log: StartCall[]) {
    super();
  }
  start(when = 0, offset = 0, duration?: number): void {
    this.log.push({ when, offset, duration, buffer: this.buffer });
  }
  stop(): void {}
}

/** Every context the engine makes, newest last. */
export const contexts: FakeContext[] = [];

export class FakeContext {
  state: "suspended" | "running" | "closed" = "suspended";
  currentTime = 0;
  readonly sampleRate = 48000;
  readonly destination = new FakeNode();
  readonly started: StartCall[] = [];
  /** the bytes handed to decodeAudioData */
  readonly decoded: ArrayBuffer[] = [];
  /** false: decodeAudioData refuses (the engine then reads the PCM itself) */
  decodes = true;

  constructor() {
    contexts.push(this);
  }
  resume(): Promise<void> {
    this.state = "running";
    return Promise.resolve();
  }
  suspend(): Promise<void> {
    this.state = "suspended";
    return Promise.resolve();
  }
  close(): Promise<void> {
    this.state = "closed";
    return Promise.resolve();
  }
  createGain(): FakeNode {
    return new FakeNode();
  }
  createBufferSource(): FakeSource {
    return new FakeSource(this.started);
  }
  createBuffer(channels: number, length: number, rate: number): FakeBuffer {
    return new FakeBuffer(channels, length, rate);
  }
  getOutputTimestamp(): { contextTime: number; performanceTime: number } {
    return { contextTime: this.currentTime, performanceTime: performance.now() };
  }
  decodeAudioData(bytes: ArrayBuffer): Promise<FakeBuffer> {
    this.decoded.push(bytes.slice(0));
    if (!this.decodes) return Promise.reject(new Error("cannot decode"));
    const info = parseWavHeader(bytes);
    if (!info) return Promise.reject(new Error("not a WAV"));
    const frames = Math.floor(Math.min(info.dataBytes, bytes.byteLength - info.dataOffset) / ((info.bitsPerSample / 8) * info.channels));
    return Promise.resolve(new FakeBuffer(info.channels, frames, info.sampleRate));
  }
}

/** A 16-bit PCM WAV of `seconds` of silence. */
export function wavBytes(seconds: number, channels = 1, rate = 48000): ArrayBuffer {
  const frames = Math.round(seconds * rate);
  const data = frames * channels * 2;
  const buffer = new ArrayBuffer(44 + data);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 36 + data, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, data, true);
  return buffer;
}

export interface HandCall {
  url: string;
  range: string | null;
  answer: (body: ArrayBuffer | null, status?: number) => void;
}

/** A fetch whose answers the test gives (`answer`), with every call recorded. */
export function handFetch() {
  const calls: HandCall[] = [];
  let open = 0;
  let mostOpen = 0;
  const fetchFn = ((url: string, init?: RequestInit) =>
    new Promise<Response>((resolve) => {
      open += 1;
      mostOpen = Math.max(mostOpen, open);
      const headers = (init?.headers ?? {}) as Record<string, string>;
      calls.push({
        url,
        range: headers.Range ?? null,
        answer: (body, status = 200) => {
          open -= 1;
          resolve(new Response(body, { status }));
        },
      });
    })) as unknown as typeof fetch;
  return { calls, fetchFn, mostOpen: () => mostOpen, open: () => open };
}

export async function flush(turns = 12): Promise<void> {
  for (let i = 0; i < turns; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}
