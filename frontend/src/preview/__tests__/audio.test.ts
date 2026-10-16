import { describe, expect, it } from "vitest";

import { ByteLru, VOICE_UPMIX_GAIN, clipsBetween, createVoiceBus, curveGainDb, curvePoints, dbToGain, decodePcm, envelopeGainDb, parseWavHeader, timelineSamples, voiceClips, voicePlacements } from "../audio";
import type { PreviewBundle } from "../types";
import { close, fixture, hasFixture } from "./fixtures";

interface AudioFixture {
  placements: {
    name: string;
    segments: { sentence_id: string; src_start_s: number; src_end_s: number; at_s: number }[] | null;
    start_s: number;
    file_samples: number | null;
    frames: number;
    fps: number;
    total: number;
    placements: [number, number, number][];
  }[];
  envelope: { step_s: number; gain_db: number[]; times: number[]; values: number[] };
}

describe("voice placement (render/music.py voice_placements)", () => {
  it.runIf(hasFixture("audio.json"))("places the voice sample-exact like the renderer", () => {
    const data = fixture<AudioFixture>("audio.json");
    for (const item of data.placements) {
      expect(timelineSamples(item.frames, item.fps), item.name).toBe(item.total);
      const placements = voicePlacements(item.segments, item.start_s, item.file_samples, item.total);
      expect(placements.map((p) => [p.srcStart, p.srcEnd, p.at]), item.name).toEqual(item.placements);
    }
  });

  it.runIf(hasFixture("audio.json"))("reads the music envelope like envelope_gain_at", () => {
    const data = fixture<AudioFixture>("audio.json");
    const envelope = { step_s: data.envelope.step_s, gain_db: data.envelope.gain_db };
    data.envelope.times.forEach((t, i) => close(envelopeGainDb(envelope, t), data.envelope.values[i]!, 1e-9, `envelope at ${t}`));
  });

  it("schedules one clip per sentence", () => {
    const bundle: PreviewBundle = {
      timeline: {
        project_id: "p",
        aspect: "16:9",
        fps: 30,
        width: 1920,
        height: 1080,
        duration_s: 10,
        scenes: [],
        voice: {
          path: "05_voice/voice.wav",
          start_s: 0,
          segments: [
            { sentence_id: "s2", src_start_s: 2.6, src_end_s: 5.0, at_s: 0 },
            { sentence_id: "s1", src_start_s: 0.2, src_end_s: 2.3, at_s: 2.5 },
          ],
        },
      },
      sentences: [
        { id: "s1", text: "one", start_s: 0.1, end_s: 2.3 },
        { id: "s2", text: "two", start_s: 2.6, end_s: 5.0 },
      ],
    };
    const clips = voiceClips(bundle, 300, 30, (id) => `/seg/${id}.wav`, null);
    expect(clips.map((c) => c.id)).toEqual(["s2", "s1"]);
    close(clips[0]!.atS, 0, 1e-12, "s2 at");
    close(clips[0]!.offsetS, 0, 1e-12, "s2 offset");
    close(clips[0]!.durationS, 2.4, 1e-9, "s2 length");
    close(clips[1]!.atS, 2.5, 1e-12, "s1 at");
    close(clips[1]!.offsetS, 0.1, 1e-9, "s1 starts 0.1 s into its sentence");
    close(clips[1]!.durationS, 2.1, 1e-9, "s1 length");
    expect(clipsBetween(clips, 2.35, 2.55).map((c) => c.id)).toEqual(["s2", "s1"]);
    expect(clipsBetween(clips, 2.45, 2.55).map((c) => c.id)).toEqual(["s1"]);

    // the storyboard's animatic before the voice exists: estimated times, nothing to play
    expect(voiceClips({ ...bundle, timing_estimated: true }, 300, 30, (id) => `/seg/${id}.wav`, null)).toEqual([]);
    expect(voiceClips({ ...bundle, timeline: { ...bundle.timeline, voice: { path: "", start_s: 0, segments: null } } }, 300, 30, (id) => `/seg/${id}.wav`, null)).toEqual([]);

    const whole = voiceClips({ ...bundle, timeline: { ...bundle.timeline, voice: { path: "v.wav", start_s: 0.5 } } }, 300, 30, (id) => `/seg/${id}.wav`, "/voice.wav");
    expect(whole.map((c) => [c.id, c.atS])).toEqual([
      ["s1", 0.6],
      ["s2", 3.1],
    ]);
  });
});

describe("audio helpers", () => {
  it("plays the voice at the level the renderer mixes it (mono, centre at -3 dB on each speaker)", () => {
    const gain = () => ({ channelCount: 2, channelCountMode: "max", channelInterpretation: "speakers", gain: { value: 1 } });
    const bus = createVoiceBus({ createGain: gain } as unknown as BaseAudioContext);
    expect(bus.channelCount).toBe(1);
    expect(bus.channelCountMode).toBe("explicit");
    expect(bus.channelInterpretation).toBe("speakers");
    close(bus.gain.value, VOICE_UPMIX_GAIN, 0, "voice gain");
    close(20 * Math.log10(VOICE_UPMIX_GAIN), -3.0103, 1e-4, "voice gain in dB");
  });

  it("follows loudnorm's measured gain between the middles of its 100 ms steps", () => {
    const curve = { stepS: 0.1, gainDb: [5, 6, 8] };
    close(curveGainDb(curve, 0), 5, 1e-12, "held before the first middle");
    close(curveGainDb(curve, 0.05), 5, 1e-12, "first middle");
    close(curveGainDb(curve, 0.1), 5.5, 1e-12, "between");
    close(curveGainDb(curve, 0.2), 7, 1e-12, "between 2");
    close(curveGainDb(curve, 0.25), 8, 1e-12, "last middle");
    close(curveGainDb(curve, 99), 8, 1e-12, "held after the last");
    // the ramps the master gain is given: the middles after `from` up to `to`
    expect(curvePoints(curve, 0, 1)).toEqual([[0.05, 5], [0.15000000000000002, 6], [0.25, 8]]);
    expect(curvePoints(curve, 0.05, 0.2)).toEqual([[0.15000000000000002, 6]]);
    expect(curvePoints(curve, 0.3, 9)).toEqual([]);
  });

  it("dB and the LRU", () => {
    close(dbToGain(-6), 0.501187233627, 1e-9, "-6 dB");
    const lru = new ByteLru<string>(100);
    lru.set("a", "A", 40);
    lru.set("b", "B", 40);
    lru.get("a");
    lru.set("c", "C", 40);
    expect(lru.has("a")).toBe(true);
    expect(lru.has("b")).toBe(false);
    lru.set("d", "D", 40, new Set(["a"]));
    expect(lru.has("a")).toBe(true);
    expect(lru.has("c")).toBe(false);
    expect(lru.bytes).toBe(80);
  });

  it("reads a 16-bit PCM WAV", () => {
    const frames = 4;
    const buffer = new ArrayBuffer(44 + frames * 4);
    const view = new DataView(buffer);
    const write = (offset: number, text: string) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
    write(0, "RIFF");
    view.setUint32(4, 36 + frames * 4, true);
    write(8, "WAVE");
    write(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 2, true);
    view.setUint32(24, 48000, true);
    view.setUint32(28, 48000 * 4, true);
    view.setUint16(32, 4, true);
    view.setUint16(34, 16, true);
    write(36, "data");
    view.setUint32(40, frames * 4, true);
    view.setInt16(44, 16384, true);
    view.setInt16(46, -32768, true);
    const info = parseWavHeader(buffer)!;
    expect(info).toEqual({ format: 1, channels: 2, sampleRate: 48000, bitsPerSample: 16, dataOffset: 44, dataBytes: 16 });
    const channels = decodePcm(buffer.slice(44), info);
    expect(channels.length).toBe(2);
    close(channels[0]![0]!, 0.5, 1e-12, "left");
    close(channels[1]![0]!, -1, 1e-12, "right");
  });
});
