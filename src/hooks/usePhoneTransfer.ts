import { useEffect, useState } from "react";
import { getTransferStatus, startTransfer, stopTransfer } from "../api/deckclip";
import type { TransferStatus } from "../types";

export function usePhoneTransfer(onError: (message: string) => void) {
  const [transfer, setTransfer] = useState<TransferStatus | null>(null);
  const begin = async (filename: string) => setTransfer(await startTransfer(filename));
  const stop = async () => {
    try { await stopTransfer(); }
    finally { setTransfer(null); }
  };

  useEffect(() => {
    if (!transfer || transfer.state === "inactive" || transfer.state === "expired") return;
    const timer = window.setInterval(async () => {
      try {
        const status = await getTransferStatus();
        if (status.state === "inactive" || status.state === "expired") {
          setTransfer(null);
          window.clearInterval(timer);
        } else {
          setTransfer((current) => ({ ...current, ...status, qr: current?.qr }));
        }
      } catch (error) {
        onError(`Lost transfer status: ${String(error)}`);
        window.clearInterval(timer);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [onError, transfer?.state]);

  return { begin, stop, transfer };
}
