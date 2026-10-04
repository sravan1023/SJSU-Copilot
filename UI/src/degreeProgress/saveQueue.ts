// Autosave for Degree Progress: a debounced, single-flight save queue.
//
// schedule(value) waits `delay` ms after the last change, then saves the
// latest value. Only one save runs at a time; a change made while a save is
// in flight is saved right after it, so the last edit always wins and two
// saves never race. A failed save keeps the latest value for retry().
// flush() saves anything pending now, for leaving the page.

export type SaveState = "idle" | "pending" | "saving" | "saved" | "error";

export interface SaveQueue<T> {
  schedule(value: T): void;
  /** Save anything pending now. Resolves true unless the save failed. */
  flush(): Promise<boolean>;
  /** After a failure: save the latest value again. */
  retry(): Promise<boolean>;
  state(): SaveState;
  /** True while a change hasn't reached the server yet (or failed to). */
  unsaved(): boolean;
  onState(listener: (state: SaveState) => void): () => void;
  dispose(): void;
}

export function createSaveQueue<T>(save: (value: T) => Promise<unknown>, delay = 800): SaveQueue<T> {
  let latest: T | undefined;
  let dirty = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let inFlight: Promise<void> | null = null;
  let current: SaveState = "idle";
  const listeners = new Set<(s: SaveState) => void>();

  const set = (s: SaveState) => {
    if (current === s) return;
    current = s;
    for (const l of listeners) l(s);
  };

  const start = (): Promise<void> => {
    if (inFlight) return inFlight;
    if (!dirty) return Promise.resolve();
    const value = latest as T;
    dirty = false;
    set("saving");
    inFlight = (async () => {
      try {
        await save(value);
        set(dirty ? "pending" : "saved");
      } catch {
        dirty = true;
        set("error");
      } finally {
        inFlight = null;
      }
      // A change arrived during the save and its timer already fired: save it now.
      if (dirty && current !== "error" && !timer) await start();
    })();
    return inFlight;
  };

  const flush = async (): Promise<boolean> => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    while (inFlight || (dirty && current !== "error")) {
      await (inFlight ?? start());
    }
    return current !== "error";
  };

  return {
    schedule(value: T) {
      latest = value;
      dirty = true;
      set("pending");
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void start();
      }, delay);
    },
    flush,
    async retry() {
      if (current === "error") set("pending");
      return flush();
    },
    state: () => current,
    unsaved: () => dirty || inFlight !== null || timer !== null,
    onState(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    dispose() {
      if (timer) clearTimeout(timer);
      timer = null;
      listeners.clear();
    },
  };
}
