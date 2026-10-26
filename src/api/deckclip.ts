import { callable } from "@decky/api";
import type { ActiveExport, Clip, ExportedFile, ExportItem, Job, TransferStatus, RecentStatus, ThumbnailResult } from "../types";
import { timed } from "../utils/backendCall";

// Every backend call has a timeout (see utils/backendCall.ts). Long work
// (exports, transfers) starts with a quick call and is then polled, so no
// single call needs to stay open for long.
const QUICK_MS = 5000;
export const LIST_TIMEOUT_MS = 15000;

const call = <A extends unknown[], R>(method: string, timeoutMs: number) =>
  timed<A, R>(method, callable<A, R>(method), timeoutMs);

export const listClips = call<[], Clip[]>("list_clips", LIST_TIMEOUT_MS);
// Re-scans the library before queueing, so it gets the list timeout plus a margin.
export const startExport = call<[items: ExportItem[]], { job_id: string }>("start_export", LIST_TIMEOUT_MS + 5000);
export const getExportStatus = call<[jobId: string], Job>("get_export_status", QUICK_MS);
export const getActiveExport = call<[], ActiveExport | null>("get_active_export", QUICK_MS);
export const getRecentStatuses = call<[], RecentStatus[]>("get_recent_statuses", QUICK_MS);
export const listExports = call<[], ExportedFile[]>("list_exports", LIST_TIMEOUT_MS);
export const deleteExports = call<[filenames: string[]], { deleted: string[]; failed: string[] }>("delete_exports", LIST_TIMEOUT_MS);
// Previews queue behind each other in the backend (each FFmpeg run is capped at 10 s).
export const getExportThumbnail = call<[filename: string], ThumbnailResult>("get_export_thumbnail", 20000);
export const getClipThumbnail = call<[clipId: string], ThumbnailResult>("get_clip_thumbnail", 20000);
export const startTransfer = call<[filenames: string[]], TransferStatus>("start_transfer", 10000);
export const getTransferStatus = call<[], TransferStatus>("get_transfer_status", QUICK_MS);
export const stopTransfer = call<[], { state: string }>("stop_transfer", QUICK_MS);
