// Decky's modal flow can remount the panel. Keep its location and invalidation
// counter for this plugin session, without persisting selections or filenames.
let snapshot = { managingExports: false, exportsRevision: 0 };
const listeners = new Set<() => void>();
export const getNavigation = () => snapshot;
export const subscribeNavigation = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
export function showExports(managingExports: boolean) {
  snapshot = { ...snapshot, managingExports };
  listeners.forEach((listener) => listener());
}
export function invalidateExports() {
  snapshot = { ...snapshot, exportsRevision: snapshot.exportsRevision + 1 };
  listeners.forEach((listener) => listener());
}
