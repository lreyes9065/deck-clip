import { useEffect, useRef, useState } from "react";
import { getTransferStatus, startTransfer, stopTransfer } from "../api/deckclip";
import type { TransferStatus } from "../types";

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
    let active = true;
    let polling = false;
    const timer = window.setInterval(async () => {
      if (busy.current || polling || !active) return;
      polling = true;
      const request = revision.current;
      try {
        const status = await getTransferStatus();
        if (!active || request !== revision.current) return;
        if (status.state === "inactive" || status.state === "expired") {
          setTransfer(null);
          window.clearInterval(timer);
        } else {
          setTransfer((current) => ({ ...current, ...status, qr: current?.qr }));
        }
      } catch (error) {
        if (!active || request !== revision.current) return;
        onError(`Lost transfer status: ${String(error)}`);
        window.clearInterval(timer);
      } finally { polling = false; }
    }, 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [onError, transfer?.state, transfer?.url]);

  return { begin, stop, transfer };
}
