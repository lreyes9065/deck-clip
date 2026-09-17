import { ButtonItem, Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { getRecentStatuses } from "../api/deckclip";
import type { RecentStatus } from "../types";

// Small focusable chunks allow controller scrolling through long errors without
// relying on a nested scrollbar or a single truncated Field description.
function DetailText({ text }: { text: string }) {
  const chunks = text.match(/[\s\S]{1,600}/g) ?? [];
  return <>{chunks.map((chunk, index) => <PanelSectionRow key={index}>
    <Field focusable label={index === 0 ? "Details" : "Details (continued)"}
      description={<div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{chunk}</div>} />
  </PanelSectionRow>)}</>;
}

export function RecentStatusesPage() {
  const [entries, setEntries] = useState<RecentStatus[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await getRecentStatuses();
        if (active) { setEntries(next); setError(""); setLoaded(true); }
      } catch (reason) {
        if (active) { setError(`Could not load statuses: ${String(reason)}`); setLoaded(true); }
      } finally {
        if (active) timer = setTimeout(() => void refresh(), 2000);
      }
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  return <PanelSection title="Recent export statuses">
    <PanelSectionRow><Field focusable label="Latest five export batches"
      description="The latest five batches are saved locally across restarts. Details may contain local filenames; check before sharing screenshots. Extremely long FFmpeg errors retain their final 128 KiB, with an explicit notice." /></PanelSectionRow>
    {error && <PanelSectionRow><Field focusable label="Status unavailable" description={error} /></PanelSectionRow>}
    {!entries.length && <PanelSectionRow><Field label={loaded ? "No exports in this session yet." : "Loading…"} /></PanelSectionRow>}
    {entries.map((entry) => <PanelSection key={entry.id} title={`${new Date(entry.started_at).toLocaleString()} • ${entry.state}`}>
      <PanelSectionRow><ButtonItem layout="below" onClick={() => setExpanded((current) => ({ ...current, [entry.id]: !current[entry.id] }))}>
        {expanded[entry.id] ? "Hide details" : "Show full details"} • {entry.clips.length} clip{entry.clips.length === 1 ? "" : "s"}
      </ButtonItem></PanelSectionRow>
      {expanded[entry.id] && <DetailText text={[
        `Status: ${entry.state} (${Math.round(entry.progress)}%)`,
        `Started: ${entry.started_at}`,
        entry.finished_at ? `Finished: ${entry.finished_at}` : "Still in progress",
        `Output folder: ${entry.output_dir}`,
        entry.free_bytes !== undefined ? `Free space after failure: ${(entry.free_bytes / 1024 ** 3).toFixed(2)} GiB` : "",
        ...entry.clips.map((clip) => `${clip.display_name}: ${clip.state} (${Math.round(clip.progress)}%)\nStage: ${clip.stage ?? "Not started"}${clip.output ? `\nSaved: ${clip.output}` : ""}`),
        entry.error ? `Error: ${entry.error}` : "",
        entry.details ?? "",
      ].filter(Boolean).join("\n\n")} />}
    </PanelSection>)}
  </PanelSection>;
}
