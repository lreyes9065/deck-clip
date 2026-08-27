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
type GameGroup = { id: string; name: string; clips: Clip[] };
type ExportedFile = { filename: string; size_bytes: number; modified_at: string };

const ALL_CLIPS = "__all__";
const UNKNOWN_CLIPS = "__unknown__";
const PAGE_SIZE = 25;

const listClips = callable<[], Clip[]>("list_clips");
const startExport = callable<[items: ExportItem[]], { job_id: string }>("start_export");
const getExportStatus = callable<[jobId: string], Job>("get_export_status");
const listExports = callable<[], ExportedFile[]>("list_exports");
const trashExport = callable<[filename: string], { filename: string }>("trash_export");

const formatDuration = (seconds: number | null) => {
  if (seconds === null) return "duration unknown";
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
};
const formatSize = (bytes: number) => {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
};

function Content() {
  const [clips, setClips] = useState<Clip[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [gameQuery, setGameQuery] = useState("");
  const [clipQuery, setClipQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [managingExports, setManagingExports] = useState(false);
  const [exports, setExports] = useState<ExportedFile[]>([]);
  const [confirmTrash, setConfirmTrash] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [message, setMessage] = useState("Looking for clips…");

  const refresh = async () => {
    try {
      const found = await listClips();
      setClips(found);
      setSelected({});
      setNames({});
      setMessage(found.length ? "" : "No Steam Game Recording clips found.");
    } catch (error) {
      setMessage(`Could not load clips: ${String(error)}`);
    }
  };
  const refreshExports = async () => {
    try {
      setExports(await listExports());
      setMessage("");
    } catch (error) {
      setMessage(`Could not load exports: ${String(error)}`);
    }
  };
  const openExports = () => {
    setManagingExports(true);
    setConfirmTrash(null);
    void refreshExports();
  };
  const moveToTrash = async (filename: string) => {
    try {
      await trashExport(filename);
      setConfirmTrash(null);
      await refreshExports();
      toaster.toast({ title: "Moved to Trash", body: filename });
    } catch (error) {
      setMessage(`Could not move export to Trash: ${String(error)}`);
    }
  };

  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    setClipQuery("");
    setVisibleCount(PAGE_SIZE);
  }, [activeGroup]);
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
  const games = useMemo<GameGroup[]>(() => {
    const grouped = new Map<string, GameGroup>();
    for (const clip of clips) {
      if (clip.game_name.startsWith("Steam app ") || clip.game_name === "Steam clip") continue;
      const key = clip.app_id;
      const group = grouped.get(key);
      if (group) group.clips.push(clip);
      else grouped.set(key, { id: key, name: clip.game_name, clips: [clip] });
    }
    return Array.from(grouped.values());
  }, [clips]);
  const unknownClips = useMemo(
    () => clips.filter((clip) => clip.game_name.startsWith("Steam app ") || clip.game_name === "Steam clip"),
    [clips],
  );
  const filteredGames = useMemo(() => {
    const query = gameQuery.trim().toLocaleLowerCase();
    if (!query) return games;
    return games.filter((game) => game.name.toLocaleLowerCase().includes(query) || game.id.includes(query));
  }, [games, gameQuery]);
  const activeName = activeGroup === ALL_CLIPS
    ? "All Clips"
    : activeGroup === UNKNOWN_CLIPS
      ? "Unknown / Unmatched"
      : games.find((game) => game.id === activeGroup)?.name ?? "Clips";
  const activeClips = useMemo(() => {
    const source = activeGroup === ALL_CLIPS
      ? clips
      : activeGroup === UNKNOWN_CLIPS
        ? unknownClips
        : games.find((game) => game.id === activeGroup)?.clips ?? [];
    const query = clipQuery.trim().toLocaleLowerCase();
    if (!query) return source;
    return source.filter((clip) => {
      const searchable = `${clip.game_name} ${new Date(clip.recorded_at).toLocaleString()} ${formatDuration(clip.duration_seconds)}`;
      return searchable.toLocaleLowerCase().includes(query);
    });
  }, [activeGroup, clipQuery, clips, games, unknownClips]);
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
      {managingExports ? (
        <PanelSection title="Exported clips">
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => { setManagingExports(false); setConfirmTrash(null); }}>‹ Back to games</ButtonItem>
          </PanelSectionRow>
          {exports.map((item) => (
            <PanelSectionRow key={item.filename}>
              <Field
                label={item.filename}
                description={`${formatSize(item.size_bytes)} • ${new Date(item.modified_at).toLocaleString()}`}
                bottomSeparator="none"
              />
              {confirmTrash === item.filename ? (
                <div style={{ display: "flex", gap: "8px" }}>
                  <ButtonItem layout="below" onClick={() => void moveToTrash(item.filename)}>Confirm move to Trash</ButtonItem>
                  <ButtonItem layout="below" onClick={() => setConfirmTrash(null)}>Cancel</ButtonItem>
                </div>
              ) : (
                <ButtonItem layout="below" onClick={() => setConfirmTrash(item.filename)}>Move to Trash</ButtonItem>
              )}
            </PanelSectionRow>
          ))}
          {!exports.length && <PanelSectionRow><div>No exported MP4 files found.</div></PanelSectionRow>}
          {message && <PanelSectionRow><div>{message}</div></PanelSectionRow>}
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => void refreshExports()}>Refresh exports</ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      ) : activeGroup === null ? (
        <PanelSection title="Choose a game">
          <PanelSectionRow>
            <TextField
              label="Search games or App IDs"
              value={gameQuery}
              onChange={(event) => setGameQuery(event.target.value)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => setActiveGroup(ALL_CLIPS)}>
              All Clips ({clips.length})
            </ButtonItem>
          </PanelSectionRow>
          {filteredGames.map((game) => (
            <PanelSectionRow key={game.id}>
              <ButtonItem
                layout="below"
                onClick={() => setActiveGroup(game.id)}
              >
                {game.name} ({game.clips.length})
              </ButtonItem>
            </PanelSectionRow>
          ))}
          {unknownClips.length > 0 && (
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => setActiveGroup(UNKNOWN_CLIPS)}>
                Unknown / Unmatched ({unknownClips.length})
              </ButtonItem>
            </PanelSectionRow>
          )}
          {!filteredGames.length && gameQuery && <PanelSectionRow><div>No matching games.</div></PanelSectionRow>}
          {message && <PanelSectionRow><div>{message}</div></PanelSectionRow>}
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={Boolean(jobId)} onClick={openExports}>Manage exported clips</ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={Boolean(jobId)} onClick={() => void refresh()}>Refresh library</ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      ) : (
        <PanelSection title={activeName}>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => setActiveGroup(null)}>‹ Back to games</ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <TextField
              label="Filter by game, date, time, or duration"
              value={clipQuery}
              onChange={(event) => { setClipQuery(event.target.value); setVisibleCount(PAGE_SIZE); }}
            />
          </PanelSectionRow>
          {activeClips.slice(0, visibleCount).map((clip) => (
            <PanelSectionRow key={clip.id}>
              <div>
                <Field
                  label={activeGroup === ALL_CLIPS || activeGroup === UNKNOWN_CLIPS ? clip.game_name : new Date(clip.recorded_at).toLocaleString()}
                  description={activeGroup === ALL_CLIPS || activeGroup === UNKNOWN_CLIPS
                    ? `${new Date(clip.recorded_at).toLocaleString()} • ${formatDuration(clip.duration_seconds)}`
                    : formatDuration(clip.duration_seconds)}
                  bottomSeparator="none"
                >
                  <Toggle
                    value={Boolean(selected[clip.id])}
                    onChange={(value) => setSelected({ ...selected, [clip.id]: value })}
                  />
                </Field>
                {selected[clip.id] && (
                  <TextField
                    label="Optional filename"
                    value={names[clip.id] ?? ""}
                    onChange={(event) => setNames({ ...names, [clip.id]: event.target.value })}
                  />
                )}
              </div>
            </PanelSectionRow>
          ))}
          {!activeClips.length && <PanelSectionRow><div>No matching clips.</div></PanelSectionRow>}
          {visibleCount < activeClips.length && (
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => setVisibleCount(visibleCount + PAGE_SIZE)}>
                Load more ({activeClips.length - visibleCount} remaining)
              </ButtonItem>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={!chosen.length || Boolean(jobId)} onClick={() => void exportNow()}>
              {jobId ? "Exporting…" : `Export ${chosen.length || "selected"} clip${chosen.length === 1 ? "" : "s"}`}
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
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
