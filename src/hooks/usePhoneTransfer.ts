import { useEffect, useRef, useState } from "react";
import { getTransferStatus, startTransfer, stopTransfer } from "../api/deckclip";
import type { TransferStatus } from "../types";
import { startPoller } from "../utils/backendCall";

const POLL_MS = 1000;
const MAX_POLL_FAILURES = 4;

export function usePhoneTransfer(onError: (message: string) => void) {
  const [transfer, setTransfer] = useState<TransferStatus | null>(null);
  const revision = useRef(0);
  const busy = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; revision.current++; }; }, []);
  const begin = async (filenames: string[]) => {
    if (busy.current) return;
    busy.current = true;
    const request = ++revision.current;
    try {
      const next = await startTransfer(filenames);
      if (mounted.current && request === revision.current) setTransfer(next);
    } finally { busy.current = false; }
  };
  const stop = async () => {
    if (busy.current) return false;
    busy.current = true;
    ++revision.current;
    try { await stopTransfer(); if (mounted.current) setTransfer(null); return true; }
    catch (error) { if (mounted.current) onError(`Could not stop sharing: ${String(error)}. Retry Stop sharing.`); return false; }
    finally { busy.current = false; }
  };

  useEffect(() => {
    if (!transfer || transfer.state === "inactive" || transfer.state === "expired") return;
    // One status call at a time; each times out, so polling never stalls.
    const poller = startPoller({
      intervalMs: POLL_MS,
      poll: async () => {
        // Starting or stopping is in progress; check again next tick.
        if (busy.current) return "continue";
        const request = revision.current;
        const status = await getTransferStatus();
        if (poller.stopped || request !== revision.current) return "continue";
        if (status.state === "inactive" || status.state === "expired") {
          setTransfer(null);
          return "stop";
        }
        setTransfer((current) => ({ ...current, ...status, qr: current?.qr }));
        return "continue";
      },
      onError: (error, failures) => {
        if (failures < MAX_POLL_FAILURES) return "retry";
        onError(`Lost transfer status: ${String(error)}. The QR link may still work; use Stop sharing to end it.`);
        return "stop";
      },
    });
    return () => poller.stop();
  }, [onError, transfer?.state, transfer?.url]);

  return { begin, stop, transfer };
}
