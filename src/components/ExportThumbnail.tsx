import { useEffect, useRef, useState } from "react";
import { FaFilm } from "react-icons/fa";
import { getExportThumbnail, getClipThumbnail } from "../api/deckclip";
import { thumbnailRetryDelay } from "../utils/retry";

export function ExportThumbnail({ filename, modified, source = false }: { filename: string; modified: string; source?: boolean }) {
  const element = useRef<HTMLDivElement>(null);
  const [image, setImage] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;
    setImage(null);
    const load = async (attempt: number) => {
      try {
        const result = await (source ? getClipThumbnail(filename) : getExportThumbnail(filename));
        if (!active) return;
        if (result.status === "busy") {
          // The backend's preview queue was full; ask again shortly.
          const delay = thumbnailRetryDelay(attempt);
          if (delay !== null) retryTimer = window.setTimeout(() => void load(attempt + 1), delay);
          return;
        }
        setImage(result.image);
      } catch {
        // Unavailable previews keep the placeholder icon.
      }
    };
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      void load(0);
    });
    if (element.current) observer.observe(element.current);
    return () => { active = false; observer.disconnect(); window.clearTimeout(retryTimer); };
  }, [filename, modified, source]);
  return <div ref={element} style={{ width: 96, height: 54, display: "flex", alignItems: "center", justifyContent: "center" }}>
    {image ? <img src={image} alt="" style={{ width: "100%", height: "100%", objectFit: "contain" }} /> : <FaFilm />}
  </div>;
}
