import { listClips } from "./deckclip";
import type { Clip } from "../types";
import { sharedRequest } from "../utils/backendCall";

const listeners = new Set<(clips: Clip[]) => void>();

export function subscribeLibrary(listener: (clips: Clip[]) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

// Opening the panel and a manual rescan share one request when they overlap.
// Publish results so Settings can update a still-mounted library screen too.
// listClips times out after 15 s; a timed-out or failed scan is cleared so
// the next open or rescan asks the backend again.
const library = sharedRequest(() => listClips(), (clips) => {
  listeners.forEach((listener) => listener(clips));
});

export function refreshLibrary(): Promise<Clip[]> {
  return library.run();
}
