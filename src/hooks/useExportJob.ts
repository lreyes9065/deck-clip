import { toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { getExportStatus, startExport } from "../api/deckclip";
import type { ExportItem, Job } from "../types";

export function useExportJob(onError: (message: string) => void, onFinished: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pending = useRef(false);

  const begin = async (items: ExportItem[]) => {
    if (pending.current || jobId) return;
    pending.current = true;
    setStarting(true);
    try {
      const result = await startExport(items);
      setJob(null);
      setJobId(result.job_id);
    } finally { pending.current = false; setStarting(false); }
  };

  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let polling = false;
    const timer = window.setInterval(async () => {
      if (!active || polling) return;
      polling = true;
      try {
        const next = await getExportStatus(jobId);
        if (!active) return;
        setJob(next);
        if (["complete", "failed", "cancelled", "interrupted"].includes(next.state)) {
          active = false;
          window.clearInterval(timer);
          setJobId(null);
          onFinished(next);
          toaster.toast({
            title: next.state === "complete" ? "ClipPort export complete" : "ClipPort export failed",
            body: next.state === "complete" ? `Clips saved to ${next.output_dir}` : (next.error ?? "See clip details."),
          });
        }
      } catch (error) {
        if (!active) return;
        active = false;
        window.clearInterval(timer);
        setJobId(null);
        onError(`Lost export status: ${String(error)}`);
      } finally {
        polling = false;
      }
    }, 500);
    return () => { active = false; window.clearInterval(timer); };
  }, [jobId, onError, onFinished]);

  useEffect(() => {
    if (job?.state !== "complete") return;
    const timer = window.setTimeout(() => setJob(null), 5000);
    return () => window.clearTimeout(timer);
  }, [job?.state]);

  return { begin, exporting: starting || Boolean(jobId), job };
}
