import { toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { getExportStatus, startExport } from "../api/deckclip";
import type { ExportItem, Job } from "../types";

export function useExportJob(onError: (message: string) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const begin = async (items: ExportItem[]) => {
    setJob(null);
    const result = await startExport(items);
    setJobId(result.job_id);
  };

  useEffect(() => {
    if (!jobId) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getExportStatus(jobId);
        setJob(next);
        if (next.state === "complete" || next.state === "failed") {
          window.clearInterval(timer);
          setJobId(null);
          toaster.toast({
            title: next.state === "complete" ? "DeckClip export complete" : "DeckClip export failed",
            body: next.state === "complete" ? `Clips saved to ${next.output_dir}` : (next.error ?? "See clip details."),
          });
        }
      } catch (error) {
        window.clearInterval(timer);
        setJobId(null);
        onError(`Lost export status: ${String(error)}`);
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [jobId, onError]);

  useEffect(() => {
    if (job?.state !== "complete") return;
    const timer = window.setTimeout(() => setJob(null), 5000);
    return () => window.clearTimeout(timer);
  }, [job?.state]);

  return { begin, exporting: Boolean(jobId), job };
}
