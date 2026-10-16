/**
 * v0.2 (docs/CONTRACT-v0.2.md section 7): the sound clips of the sound tracks, played through
 * audio elements kept in step with the preview clock (started at their place while playing,
 * pulled back in line when they drift, silent while paused). Their gain, fades and the
 * master gain of the mix are applied as the renderer applies them; the music dip is not.
 */
import { dbToGain } from "./audio";
import type { SoundClipDesc } from "./types";

export const SOUND_DRIFT_S = 0.2;

export class SoundPlayer {
  private clips: SoundClipDesc[] = [];
  private readonly elements = new Map<string, HTMLAudioElement>();
  /** the master gain of the mix (loudnorm's offset), as a factor */
  master = 1;
  private destroyed = false;

  setClips(clips: readonly SoundClipDesc[]): void {
    if (this.destroyed || typeof document === "undefined") return;
    this.clips = [...clips];
    const wanted = new Set(clips.map((clip) => clip.key));
    for (const [key, element] of [...this.elements]) {
      if (wanted.has(key)) continue;
      element.pause();
      element.removeAttribute("src");
      element.load();
      this.elements.delete(key);
    }
    for (const clip of clips) {
      const held = this.elements.get(clip.key);
      if (held && held.dataset.url === clip.url) continue;
      if (held) {
        held.pause();
        held.removeAttribute("src");
      }
      const element = document.createElement("audio");
      element.preload = "auto";
      element.src = clip.url;
      element.dataset.url = clip.url;
      this.elements.set(clip.key, element);
    }
  }

  /** Volume of `clip` at `t` (its gain and fades), 0 to 1. */
  gainAt(clip: SoundClipDesc, t: number): number {
    const into = t - clip.startS;
    const left = clip.endS - t;
    let fade = 1;
    if (clip.fadeInS > 0 && into < clip.fadeInS) fade = Math.min(fade, Math.max(0, into / clip.fadeInS));
    if (clip.fadeOutS > 0 && left < clip.fadeOutS) fade = Math.min(fade, Math.max(0, left / clip.fadeOutS));
    return Math.min(1, Math.max(0, dbToGain(clip.gainDb) * fade * this.master));
  }

  /** Brings every clip in line with the video time `t`. */
  sync(t: number, playing: boolean): void {
    if (this.destroyed) return;
    for (const clip of this.clips) {
      const element = this.elements.get(clip.key);
      if (!element) continue;
      const active = t >= clip.startS && t < clip.endS;
      if (!active || !playing) {
        if (!element.paused) element.pause();
        continue;
      }
      const target = clip.inS + (t - clip.startS) * clip.speed;
      element.volume = this.gainAt(clip, t);
      const rate = Math.min(16, Math.max(0.0625, clip.speed || 1));
      if (element.playbackRate !== rate) element.playbackRate = rate;
      if (element.paused) {
        element.currentTime = target;
        void element.play().catch(() => undefined);
      } else if (Math.abs(element.currentTime - target) > SOUND_DRIFT_S) {
        element.currentTime = target;
      }
    }
  }

  pauseAll(): void {
    for (const element of this.elements.values()) {
      if (!element.paused) element.pause();
    }
  }

  destroy(): void {
    this.destroyed = true;
    for (const element of this.elements.values()) {
      element.pause();
      element.removeAttribute("src");
      element.load();
    }
    this.elements.clear();
    this.clips = [];
  }
}
