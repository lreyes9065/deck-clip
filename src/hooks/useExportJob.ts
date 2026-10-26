import { toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { getActiveExport, getExportStatus, startExport } from "../api/deckclip";
import type { ExportItem, Job } from "../types";
import { startPoller } from "../utils/backendCall";

const POLL_MS = 500;
// Consecutive failed or timed-out status checks before giving up. Polls back
// off (1 s, 2 s, 4 s, 5 s...), so this is roughly 20 s of an unresponsive backend.
const MAX_POLL_FAILURES = 6;
const TERMINAL = ["complete", "failed", "cancelled", "interrupted"];

export function useExportJob(onError: (message: string) => void, onFinished: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [warning, setWarning] = useState("");
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

  // The panel unmounts whenever the Quick Access menu closes. On mount, pick up
  // any export the backend is still running and resume polling it.
  useEffect(() => {
    let active = true;
    void getActiveExport().then((found) => {
      if (!active || !found) return;
      setJob(found.job);
      setJobId((current) => current ?? found.job_id);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!jobId) return;
    // One status call at a time; each times out, so polling never stalls.
    const poller = startPoller({
      intervalMs: POLL_MS,
      poll: async () => {
        const next = await getExportStatus(jobId);
        if (poller.stopped) return "stop";
        setJob(next);
        if (!TERMINAL.includes(next.state)) return "continue";
        setJobId(null);
        onFinished(next);
        toaster.toast({
          title: next.state === "complete" ? "ClipPort export complete" : "ClipPort export failed",
          body: next.state === "complete" ? `Clips saved to ${next.output_dir}` : (next.error ?? "See clip details."),
        });
        return "stop";
      },
      onError: (error, failures) => {
        if (failures < MAX_POLL_FAILURES) {
          setWarning(`Export status is not responding (${String(error)}). Retrying…`);
          return "retry";
        }
        setWarning("");
        setJobId(null);
        // The export may still be running: reopening ClipPort resumes it.
        onError(`Lost export status: ${String(error)}. Close and reopen ClipPort to check on it.`);
        return "stop";
      },
      onRecover: () => setWarning(""),
    });
    return () => { poller.stop(); setWarning(""); };
  }, [jobId, onError, onFinished]);

  useEffect(() => {
    if (job?.state !== "complete") return;
    const timer = window.setTimeout(() => setJob(null), 5000);
    return () => window.clearTimeout(timer);
  }, [job?.state]);

  return { begin, exporting: starting || Boolean(jobId), job, warning };
}
