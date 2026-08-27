import { definePlugin, toaster } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FaFilm } from "react-icons/fa";
import { listClips, listExports, trashExport } from "./api/deckclip";
import { ExportProgress } from "./components/ExportProgress";
import { ALL_CLIPS, PAGE_SIZE, UNKNOWN_CLIPS } from "./constants";
import { useExportJob } from "./hooks/useExportJob";
import { usePhoneTransfer } from "./hooks/usePhoneTransfer";
import { ClipsPage } from "./pages/ClipsPage";
import { ExportManagerPage } from "./pages/ExportManagerPage";
import { LibraryPage } from "./pages/LibraryPage";
import type { Clip, ExportedFile, GameGroup } from "./types";
import { formatDuration } from "./utils/formatting";
import { loadGameFilter, saveGameFilter } from "./utils/gameFilter";

function Content() {
  const [clips, setClips] = useState<Clip[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [gameFilter, setGameFilter] = useState<string | null>(loadGameFilter);
  const [clipQuery, setClipQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [managingExports, setManagingExports] = useState(false);
  const [exports, setExports] = useState<ExportedFile[]>([]);
  const [confirmTrash, setConfirmTrash] = useState<string | null>(null);
  const [message, setMessage] = useState("Looking for clips…");
  const reportError = useCallback((value: string) => setMessage(value), []);
  const exportJob = useExportJob(reportError);
  const phoneTransfer = usePhoneTransfer(reportError);

  const refresh = async () => {
    try {
      const found = await listClips();
      setClips(found);
      setSelected({});
      setNames({});
      setMessage(found.length ? "" : "No Steam Game Recording clips found.");
    } catch (error) { setMessage(`Could not load clips: ${String(error)}`); }
  };
  const refreshExports = async () => {
    try { setExports(await listExports()); setMessage(""); }
    catch (error) { setMessage(`Could not load exports: ${String(error)}`); }
  };

  useEffect(() => { void refresh(); }, []);
  useEffect(() => { setClipQuery(""); setVisibleCount(PAGE_SIZE); }, [activeGroup]);

  const games = useMemo<GameGroup[]>(() => {
    const grouped = new Map<string, GameGroup>();
    for (const clip of clips) {
      if (clip.game_name.startsWith("Steam app ") || clip.game_name === "Steam clip") continue;
      const group = grouped.get(clip.app_id);
      if (group) group.clips.push(clip);
      else grouped.set(clip.app_id, { id: clip.app_id, name: clip.game_name, clips: [clip] });
    }
    return Array.from(grouped.values());
  }, [clips]);
  const unknownClips = useMemo(
    () => clips.filter((clip) => clip.game_name.startsWith("Steam app ") || clip.game_name === "Steam clip"),
    [clips],
  );
  const gameOptions = useMemo(() => [
    ...games.map((game) => ({ data: game.id, label: `${game.name} (${game.clips.length})` })),
    ...(unknownClips.length ? [{ data: UNKNOWN_CLIPS, label: `Unknown / Unmatched (${unknownClips.length})` }] : []),
  ], [games, unknownClips.length]);
  useEffect(() => {
    if (!clips.length || !gameFilter) return;
    const valid = gameFilter === UNKNOWN_CLIPS ? unknownClips.length > 0 : games.some((game) => game.id === gameFilter);
    if (!valid) { saveGameFilter(null); setGameFilter(null); }
  }, [clips.length, gameFilter, games, unknownClips.length]);

  const displayedGames = gameFilter === UNKNOWN_CLIPS ? [] : gameFilter ? games.filter((game) => game.id === gameFilter) : games.slice(0, 3);
  const activeName = activeGroup === ALL_CLIPS ? "All Clips"
    : activeGroup === UNKNOWN_CLIPS ? "Unknown / Unmatched"
      : games.find((game) => game.id === activeGroup)?.name ?? "Clips";
  const activeClips = useMemo(() => {
    const source = activeGroup === ALL_CLIPS ? clips
      : activeGroup === UNKNOWN_CLIPS ? unknownClips
        : games.find((game) => game.id === activeGroup)?.clips ?? [];
    const query = clipQuery.trim().toLocaleLowerCase();
    return query ? source.filter((clip) => `${clip.game_name} ${new Date(clip.recorded_at).toLocaleString()} ${formatDuration(clip.duration_seconds)}`.toLocaleLowerCase().includes(query)) : source;
  }, [activeGroup, clipQuery, clips, games, unknownClips]);
  const chosen = useMemo(() => clips.filter((clip) => selected[clip.id]), [clips, selected]);

  const openExports = () => { setManagingExports(true); setConfirmTrash(null); void refreshExports(); };
  const moveToTrash = async (filename: string) => {
    try {
      await trashExport(filename);
      setConfirmTrash(null);
      await refreshExports();
      toaster.toast({ title: "Moved to Trash", body: filename });
    } catch (error) { setMessage(`Could not move export to Trash: ${String(error)}`); }
  };
  const beginTransfer = async (filename: string) => {
    try { setMessage(""); await phoneTransfer.begin(filename); }
    catch (error) { setMessage(`Could not start phone transfer: ${String(error)}`); }
  };
  const exportNow = async () => {
    if (!chosen.length) return;
    setMessage("");
    try { await exportJob.begin(chosen.map((clip) => ({ id: clip.id, name: names[clip.id]?.trim() || undefined }))); }
    catch (error) { setMessage(`Could not start export: ${String(error)}`); }
  };

  return <>
    {managingExports ? (
      <ExportManagerPage
        confirmTrash={confirmTrash} exports={exports} message={message} transfer={phoneTransfer.transfer}
        onBack={() => { void phoneTransfer.stop(); setManagingExports(false); setConfirmTrash(null); }}
        onCancelTrash={() => setConfirmTrash(null)} onConfirmTrash={(filename) => void moveToTrash(filename)}
        onRefresh={() => void refreshExports()} onSend={(filename) => void beginTransfer(filename)}
        onStartTrash={setConfirmTrash} onStopTransfer={() => void phoneTransfer.stop()}
      />
    ) : activeGroup === null ? (
      <LibraryPage
        disabled={exportJob.exporting} displayedGames={displayedGames} filter={gameFilter} filterOptions={gameOptions}
        message={message} unknownCount={unknownClips.length}
        onClearFilter={() => { saveGameFilter(null); setGameFilter(null); }}
        onFilter={(value) => { saveGameFilter(value); setGameFilter(value); }}
        onManageExports={openExports} onOpenGame={setActiveGroup} onRefresh={() => void refresh()}
      />
    ) : (
      <ClipsPage
        activeGroup={activeGroup} activeName={activeName} clips={activeClips} names={names} query={clipQuery}
        selected={selected} selectedCount={chosen.length} exporting={exportJob.exporting} visibleCount={visibleCount}
        onBack={() => setActiveGroup(null)} onExport={() => void exportNow()}
        onName={(id, value) => setNames((current) => ({ ...current, [id]: value }))}
        onQuery={(value) => { setClipQuery(value); setVisibleCount(PAGE_SIZE); }}
        onSelect={(id, value) => setSelected((current) => ({ ...current, [id]: value }))}
        onShowMore={() => setVisibleCount((count) => count + PAGE_SIZE)}
      />
    )}
    {exportJob.job && <ExportProgress job={exportJob.job} />}
  </>;
}

export default definePlugin(() => ({
  name: "DeckClip",
  titleView: <div className={staticClasses.Title}>DeckClip</div>,
  content: <Content />,
  icon: <FaFilm />,
  onDismount() {},
}));
