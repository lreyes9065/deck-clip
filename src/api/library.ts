import { listClips } from "./deckclip";
import type { Clip } from "../types";

const listeners = new Set<(clips: Clip[]) => void>();
let pending: Promise<Clip[]> | null = null;

export function subscribeLibrary(listener: (clips: Clip[]) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

// Opening the panel and a manual rescan share one request when they overlap.
// Publish results so Settings can update a still-mounted library screen too.
export function refreshLibrary(): Promise<Clip[]> {
  if (!pending) {
    pending = listClips().then((clips) => {
      listeners.forEach((listener) => listener(clips));
      return clips;
    }).finally(() => { pending = null; });
  }
  return pending;
}
