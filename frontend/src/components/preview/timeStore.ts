/**
 * The playhead as a tiny external store. The engine reports the time on every drawn frame
 * (30 to 60 times a second); only the parts that show it subscribe, each at its own grain
 * (tenths for the clock, whole seconds for the screen-reader text), so the rest of the review
 * page never re-renders with the playhead.
 */
import { useSyncExternalStore } from "react";

export interface TimeStore {
  get(): number;
  set(seconds: number): void;
  subscribe(listener: () => void): () => void;
}

export function createTimeStore(initial = 0): TimeStore {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set(seconds) {
      const next = Number.isFinite(seconds) ? seconds : 0;
      if (next === value) return;
      value = next;
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** The store's time cut to `grain` seconds (0.1 = tenths); re-renders only when that changes. */
export function useStoreTime(store: TimeStore, grain = 0.1): number {
  const steps = useSyncExternalStore(
    store.subscribe,
    () => Math.floor(store.get() / grain + 1e-6),
    () => Math.floor(store.get() / grain + 1e-6),
  );
  return steps * grain;
}
