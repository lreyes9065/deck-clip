import { callable } from "@decky/api";
import type { Clip, ExportedFile, ExportItem, Job, TransferStatus, RecentStatus } from "../types";

export const listClips = callable<[], Clip[]>("list_clips");
export const startExport = callable<[items: ExportItem[]], { job_id: string }>("start_export");
export const getExportStatus = callable<[jobId: string], Job>("get_export_status");
export const getRecentStatuses = callable<[], RecentStatus[]>("get_recent_statuses");
export const listExports = callable<[], ExportedFile[]>("list_exports");
export const deleteExports = callable<[filenames: string[]], { deleted: string[]; failed: string[] }>("delete_exports");
export const getExportThumbnail = callable<[filename: string], string | null>("get_export_thumbnail");
export const startTransfer = callable<[filenames: string[]], TransferStatus>("start_transfer");
export const getTransferStatus = callable<[], TransferStatus>("get_transfer_status");
export const stopTransfer = callable<[], { state: string }>("stop_transfer");
