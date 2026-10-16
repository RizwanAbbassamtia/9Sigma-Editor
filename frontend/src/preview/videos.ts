/**
 * v0.2 (docs/CONTRACT-v0.2.md section 7): the video elements behind clip scenes and clip
 * layers. One hidden element per file; while paused it is seeked to the frame the playhead
 * asks for, while playing it runs at the clip's speed and is pulled back in line when it
 * drifts more than a sixth of a second. Elements the playhead has left behind are released.
 */

export const DRIFT_S = 0.17;
export const SEEK_EPSILON_S = 0.02;
export const MAX_ELEMENTS = 6;

interface Entry {
  url: string;
  element: HTMLVideoElement;
  lastUsed: number;
  /** a seek asked for while the element was still seeking */
  pending: number | null;
  failed: boolean;
}

export class VideoStore {
  private readonly entries = new Map<string, Entry>();
  private clock = 0;
  private destroyed = false;

  constructor(private readonly onFrame: () => void) {}

  /** The element for `url`, made (and loading) when it is not there yet. */
  want(url: string): HTMLVideoElement | null {
    if (this.destroyed || typeof document === "undefined") return null;
    let entry = this.entries.get(url);
    if (!entry) {
      const element = document.createElement("video");
      element.preload = "auto";
      element.muted = true;
      element.playsInline = true;
      element.loop = false;
      element.src = url;
      entry = { url, element, lastUsed: 0, pending: null, failed: false };
      const held = entry;
      element.addEventListener("loadeddata", () => this.onFrame());
      element.addEventListener("seeked", () => {
        if (held.pending !== null) {
          const next = held.pending;
          held.pending = null;
          held.element.currentTime = next;
        } else this.onFrame();
      });
      element.addEventListener("error", () => {
        held.failed = true;
      });
      this.entries.set(url, entry);
      element.load();
    }
    entry.lastUsed = ++this.clock;
    return entry.element;
  }

  /** The element when it has a frame to show; null while it loads or when it failed. */
  ready(url: string): HTMLVideoElement | null {
    const entry = this.entries.get(url);
    if (!entry || entry.failed) return null;
    entry.lastUsed = ++this.clock;
    return entry.element.readyState >= 2 && entry.element.videoWidth > 0 ? entry.element : null;
  }

  /**
   * Brings the element to `target` seconds of its file: a seek while paused (one at a time),
   * play at `rate` while playing. `volume` null keeps it muted (the clip's sound is off).
   */
  sync(url: string, target: number, playing: boolean, rate: number, volume: number | null): void {
    const element = this.want(url);
    const entry = this.entries.get(url);
    if (!element || !entry || entry.failed) return;
    const safe = Math.max(0, Number.isFinite(target) ? target : 0);
    const speed = Math.min(16, Math.max(0.0625, rate || 1));
    if (element.playbackRate !== speed) element.playbackRate = speed;
    const muted = volume === null || volume <= 0;
    if (element.muted !== muted) element.muted = muted;
    if (!muted) element.volume = Math.min(1, volume);
    if (playing) {
      const duration = Number.isFinite(element.duration) ? element.duration : Infinity;
      if (safe >= duration - 0.05) {
        // the clip ran out: its last frame holds
        if (!element.paused) element.pause();
        if (Number.isFinite(duration) && Math.abs(element.currentTime - duration) > SEEK_EPSILON_S) element.currentTime = duration;
        return;
      }
      if (element.paused) {
        element.currentTime = safe;
        void element.play().catch(() => undefined);
      } else if (Math.abs(element.currentTime - safe) > DRIFT_S) {
        element.currentTime = safe;
      }
      return;
    }
    if (!element.paused) element.pause();
    if (Math.abs(element.currentTime - safe) <= SEEK_EPSILON_S) return;
    if (element.seeking) entry.pending = safe;
    else element.currentTime = safe;
  }

  pauseAll(): void {
    for (const entry of this.entries.values()) {
      if (!entry.element.paused) entry.element.pause();
    }
  }

  /** Keeps the elements in `keep` (plus the most recently used up to a few); releases the rest. */
  trim(keep: ReadonlySet<string>): void {
    const others = [...this.entries.values()].filter((entry) => !keep.has(entry.url)).sort((a, b) => b.lastUsed - a.lastUsed);
    const room = Math.max(MAX_ELEMENTS - keep.size, 0);
    for (const entry of others.slice(room)) this.release(entry);
  }

  private release(entry: Entry): void {
    try {
      entry.element.pause();
      entry.element.removeAttribute("src");
      entry.element.load();
    } catch {
      /* already gone */
    }
    this.entries.delete(entry.url);
  }

  destroy(): void {
    this.destroyed = true;
    for (const entry of [...this.entries.values()]) this.release(entry);
  }
}
