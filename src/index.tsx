import {
  ButtonItem,
  Field,
  PanelSection,
  PanelSectionRow,
  ProgressBar,
  TextField,
  Toggle,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useMemo, useState } from "react";
import { FaFilm } from "react-icons/fa";

type Clip = {
  id: string;
  app_id: string;
  game_name: string;
  recorded_at: string;
  duration_seconds: number | null;
  session_count: number;
};
type ExportItem = { id: string; name?: string };
type JobClip = { id: string; display_name: string; progress: number; state: string; output?: string; error?: string };
type Job = { state: string; progress: number; output_dir: string; clips: JobClip[]; error?: string };

const listClips = callable<[], Clip[]>("list_clips");
const startExport = callable<[items: ExportItem[]], { job_id: string }>("start_export");
const getExportStatus = callable<[jobId: string], Job>("get_export_status");

const formatDuration = (seconds: number | null) => {
  if (seconds === null) return "duration unknown";
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
};

function Content() {
  const [clips, setClips] = useState<Clip[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [message, setMessage] = useState("Looking for recent clips…");

  const refresh = async () => {
    try {
      const found = await listClips();
      setClips(found);
      setSelected(Object.fromEntries(found.map((clip) => [clip.id, true])));
      setMessage(found.length ? "" : "No Steam Game Recording clips found.");
    } catch (error) {
      setMessage(`Could not load clips: ${String(error)}`);
    }
  };

  useEffect(() => { void refresh(); }, []);
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
        setMessage(`Lost export status: ${String(error)}`);
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [jobId]);

  const chosen = useMemo(() => clips.filter((clip) => selected[clip.id]), [clips, selected]);
  const exportNow = async () => {
    if (!chosen.length) return;
    setJob(null);
    setMessage("");
    try {
      const result = await startExport(chosen.map((clip) => ({ id: clip.id, name: names[clip.id]?.trim() || undefined })));
      setJobId(result.job_id);
    } catch (error) {
      setMessage(`Could not start export: ${String(error)}`);
    }
  };

  return (
    <>
      <PanelSection title="Recent clips">
        {clips.map((clip) => (
          <PanelSectionRow key={clip.id}>
            <Field
              label={clip.game_name}
              description={`${new Date(clip.recorded_at).toLocaleString()} • ${formatDuration(clip.duration_seconds)}`}
              bottomSeparator="none"
            >
              <Toggle value={Boolean(selected[clip.id])} onChange={(value) => setSelected({ ...selected, [clip.id]: value })} />
            </Field>
            {selected[clip.id] && (
              <TextField
                label="Optional filename"
                value={names[clip.id] ?? ""}
                onChange={(event) => setNames({ ...names, [clip.id]: event.target.value })}
              />
            )}
          </PanelSectionRow>
        ))}
        {message && <PanelSectionRow><div>{message}</div></PanelSectionRow>}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!chosen.length || Boolean(jobId)} onClick={() => void exportNow()}>
            {jobId ? "Exporting…" : `Export ${chosen.length || "selected"} clip${chosen.length === 1 ? "" : "s"}`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={Boolean(jobId)} onClick={() => void refresh()}>Refresh clips</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      {job && (
        <PanelSection title="Export progress">
          <PanelSectionRow><ProgressBar nProgress={job.progress} /></PanelSectionRow>
          {job.clips.map((item) => (
            <PanelSectionRow key={item.id}>
              <Field label={item.display_name} description={item.error ?? `${Math.round(item.progress)}% • ${item.state}`}>
                <ProgressBar nProgress={item.progress} />
              </Field>
            </PanelSectionRow>
          ))}
          {job.state === "complete" && <PanelSectionRow><div>Clips saved to {job.output_dir}</div></PanelSectionRow>}
        </PanelSection>
      )}
    </>
  );
}

export default definePlugin(() => ({
  name: "DeckClip",
  titleView: <div className={staticClasses.Title}>DeckClip</div>,
  content: <Content />,
  icon: <FaFilm />,
  onDismount() {},
}));
