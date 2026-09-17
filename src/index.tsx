import { definePlugin, routerHook, toaster, useQuickAccessVisible } from "@decky/api";
import { ConfirmModal, showModal, DialogButton, Focusable, Navigation, staticClasses } from "@decky/ui";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { FaFilm } from "react-icons/fa";
import { BsGearFill } from "react-icons/bs";
import { listExports, deleteExports } from "./api/deckclip";
import { refreshLibrary, subscribeLibrary } from "./api/library";
import { ExportProgress } from "./components/ExportProgress";
import { PAGE_SIZE, UNKNOWN_CLIPS } from "./constants";
import { useExportJob } from "./hooks/useExportJob";
import { usePhoneTransfer } from "./hooks/usePhoneTransfer";
import { ClipsPage } from "./pages/ClipsPage";
import { ExportManagerPage } from "./pages/ExportManagerPage";
import { HelpSettingsPage } from "./pages/HelpSettingsPage";
import { LibraryPage } from "./pages/LibraryPage";
import type { Clip, ExportedFile, GameGroup, Job } from "./types";
import { formatDuration } from "./utils/formatting";
import { loadGameFilter, saveGameFilter } from "./utils/gameFilter";
import { getNavigation, subscribeNavigation, showExports, invalidateExports } from "./utils/navigation";

const HELP_ROUTE = "/deckclip/help";

function Content() {
  const quickAccessVisible = useQuickAccessVisible();
  const [clips, setClips] = useState<Clip[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [gameFilter, setGameFilter] = useState<string | null>(loadGameFilter);
  const [clipQuery, setClipQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const { managingExports, exportsRevision } = useSyncExternalStore(subscribeNavigation, getNavigation);
  const [exports, setExports] = useState<ExportedFile[]>([]);
  const [selectedExports, setSelectedExports] = useState<Record<string, boolean>>({});
  const [message, setMessage] = useState("Looking for clips…");
  const reportError = useCallback((value: string) => setMessage(value), []);
  const finishExport = useCallback((job: Job) => {
    // Preserve failed/unprocessed clips and any selections outside this job.
    const completed = new Set(job.clips.filter((clip) => clip.state === "complete").map((clip) => clip.id));
    setSelected((current) => Object.fromEntries(Object.entries(current).filter(([id]) => !completed.has(id))));
  }, []);
  const exportJob = useExportJob(reportError, finishExport);
  const phoneTransfer = usePhoneTransfer(reportError);

  useEffect(() => subscribeLibrary((found) => {
      const available = new Set(found.map((clip) => clip.id));
      setClips(found);
      setSelected((current) => Object.fromEntries(Object.entries(current).filter(([id]) => available.has(id))));
      setNames((current) => Object.fromEntries(Object.entries(current).filter(([id]) => available.has(id))));
      setMessage(found.length ? "" : "No Steam Game Recording clips found.");
  }), []);

  // Mounting the plugin while the menu is open and reopening the menu both
  // refresh the library. Ignore late failures after closing or unmounting.
  useEffect(() => {
    if (!quickAccessVisible) return;
    let active = true;
    void refreshLibrary().catch((error) => {
      if (active) setMessage(`Could not load clips: ${String(error)}`);
    });
    return () => { active = false; };
  }, [quickAccessVisible]);
  const refreshExports = useCallback(async () => {
    try { setExports(await listExports()); setSelectedExports({}); setMessage(""); }
    catch (error) { setMessage(`Could not load exports: ${String(error)}`); }
  }, []);
  useEffect(() => {
    if (managingExports && quickAccessVisible) void refreshExports();
  }, [managingExports, quickAccessVisible, exportsRevision, refreshExports]);

  useEffect(() => { setClipQuery(""); setVisibleCount(PAGE_SIZE); }, [activeGroup]);

  const navigateGame = (group: string | null) => {
    // Selection belongs to the current game view, including failed exports.
    // An already-started backend job keeps its own submitted clip list.
    setSelected({});
    setActiveGroup(group);
  };

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
  const activeName = activeGroup === UNKNOWN_CLIPS ? "Unknown / Unmatched"
      : games.find((game) => game.id === activeGroup)?.name ?? "Clips";
  const activeClips = useMemo(() => {
    const source = activeGroup === UNKNOWN_CLIPS ? unknownClips
        : games.find((game) => game.id === activeGroup)?.clips ?? [];
    const query = clipQuery.trim().toLocaleLowerCase();
    return query ? source.filter((clip) => `${clip.game_name} ${new Date(clip.recorded_at).toLocaleString()} ${formatDuration(clip.duration_seconds)}`.toLocaleLowerCase().includes(query)) : source;
  }, [activeGroup, clipQuery, clips, games, unknownClips]);
  const chosen = useMemo(() => clips.filter((clip) => selected[clip.id]), [clips, selected]);

  const openExports = () => showExports(true);
  const closeExports = async () => {
    if (await phoneTransfer.stop()) showExports(false);
  };
  const removeExports = async (filenames: string[]) => {
    try {
      const result = await deleteExports(filenames);
      invalidateExports();
      setMessage(result.failed.length ? `${result.deleted.length} deleted; could not delete: ${result.failed.join(", ")}` : `${result.deleted.length} exports permanently deleted.`);
      toaster.toast({ title: "Exports deleted", body: `${result.deleted.length} permanently deleted` });
    } catch (error) { setMessage(`Could not delete exports: ${String(error)}`); }
  };
  const confirmDelete = (filenames: string[]) => {
    if (!filenames.length) return;
    showModal(<ConfirmModal strTitle={`Delete ${filenames.length} exported video${filenames.length === 1 ? "" : "s"}?`}
      strDescription={`Permanently delete: ${filenames.join(", ")}. This cannot be undone. Steam recordings will not be touched.`}
      strOKButtonText="Delete permanently" strCancelButtonText="Cancel" bDestructiveWarning
      onOK={() => void removeExports(filenames)} />);
  };
  const beginTransfer = async () => {
    const filenames = exports.filter((item) => selectedExports[item.filename]).map((item) => item.filename);
    if (!filenames.length) return;
    try { setMessage(""); await phoneTransfer.begin(filenames); }
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
        exports={exports} message={message} selected={selectedExports} transfer={phoneTransfer.transfer}
        onBack={() => void closeExports()}
        onSendSelected={() => void beginTransfer()}
        onSelect={(filename, value) => setSelectedExports((current) => ({ ...current, [filename]: value }))}
        onDeleteSelected={() => confirmDelete(exports.filter((item) => selectedExports[item.filename]).map((item) => item.filename))} onStopTransfer={() => void phoneTransfer.stop()}
      />
    ) : activeGroup === null ? (
      <LibraryPage
        disabled={exportJob.exporting} displayedGames={displayedGames} filter={gameFilter} filterOptions={gameOptions}
        message={message} unknownCount={unknownClips.length}
        onClearFilter={() => { saveGameFilter(null); setGameFilter(null); }}
        onFilter={(value) => { saveGameFilter(value); setGameFilter(value); }}
        onManageExports={openExports} onOpenGame={navigateGame}
      />
    ) : (
      <ClipsPage
        activeGroup={activeGroup} activeName={activeName} clips={activeClips} names={names} query={clipQuery}
        selected={selected} selectedCount={chosen.length} exporting={exportJob.exporting} visibleCount={visibleCount}
        onBack={() => navigateGame(null)} onExport={() => void exportNow()}
        onName={(id, value) => setNames((current) => ({ ...current, [id]: value }))}
        onQuery={(value) => { setClipQuery(value); setVisibleCount(PAGE_SIZE); }}
        onSelect={(id, value) => setSelected((current) => ({ ...current, [id]: value }))}
        onShowMore={() => setVisibleCount((count) => count + PAGE_SIZE)}
      />
    )}
    {exportJob.job && <ExportProgress job={exportJob.job} />}
  </>;
}

export default definePlugin(() => {
  // Match child routes too: SidebarNavigation owns topic navigation.
  routerHook.addRoute(HELP_ROUTE, HelpSettingsPage);
  const openHelp = () => {
    Navigation.Navigate(`${HELP_ROUTE}/iphone`);
    Navigation.CloseSideMenus();
  };
  return {
    name: "Decky ClipPort",
    titleView: (
      <Focusable flow-children="horizontal" style={{ alignItems: "center", display: "flex", justifyContent: "space-between", width: "100%" }}>
        <div className={staticClasses.Title}>ClipPort</div>
        <DialogButton aria-label="Open Help and Settings" onOKActionDescription="Help & Settings" style={{ height: "28px", width: "40px", minWidth: 0, padding: "10px 12px" }} onClick={openHelp}>
          <BsGearFill style={{ marginTop: "-4px", display: "block" }} />
        </DialogButton>
      </Focusable>
    ),
    content: <Content />,
    icon: <FaFilm />,
    onDismount() { routerHook.removeRoute(HELP_ROUTE); },
  };
});
