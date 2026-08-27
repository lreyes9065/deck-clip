import { callable } from "@decky/api";
import type { Clip, ExportedFile, ExportItem, Job, TransferStatus } from "../types";

export const listClips = callable<[], Clip[]>("list_clips");
export const startExport = callable<[items: ExportItem[]], { job_id: string }>("start_export");
export const getExportStatus = callable<[jobId: string], Job>("get_export_status");
export const listExports = callable<[], ExportedFile[]>("list_exports");
export const trashExport = callable<[filename: string], { filename: string }>("trash_export");
export const startTransfer = callable<[filename: string], TransferStatus>("start_transfer");
export const getTransferStatus = callable<[], TransferStatus>("get_transfer_status");
export const stopTransfer = callable<[], { state: string }>("stop_transfer");
