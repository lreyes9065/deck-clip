// Backoff for preview requests the backend reported as busy (its queue was
// full). Exponential from 250 ms, capped at 5 s, with jitter so a screen of
// previews does not retry in lockstep.
export const MAX_THUMBNAIL_ATTEMPTS = 10;

export function thumbnailRetryDelay(attempt: number, random: () => number = Math.random): number | null {
  if (attempt >= MAX_THUMBNAIL_ATTEMPTS) return null;
  return Math.min(5000, 250 * 2 ** attempt) + Math.floor(random() * 250);
}
