import { useEffect, useRef, useState } from "react";
import { FaFilm } from "react-icons/fa";
import { getExportThumbnail, getClipThumbnail } from "../api/deckclip";

export function ExportThumbnail({ filename, modified, source = false }: { filename: string; modified: string; source?: boolean }) {
  const element = useRef<HTMLDivElement>(null);
  const [image, setImage] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setImage(null);
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      void (source ? getClipThumbnail(filename) : getExportThumbnail(filename)).then((value) => { if (active) setImage(value); }).catch(() => {});
    });
    if (element.current) observer.observe(element.current);
    return () => { active = false; observer.disconnect(); };
  }, [filename, modified, source]);
  return <div ref={element} style={{ width: 96, height: 54, display: "flex", alignItems: "center", justifyContent: "center" }}>
    {image ? <img src={image} alt="" style={{ width: "100%", height: "100%", objectFit: "contain" }} /> : <FaFilm />}
  </div>;
}
