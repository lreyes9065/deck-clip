import { ButtonItem, Field, PanelSection, PanelSectionRow, TextField, Toggle } from "@decky/ui";
import { ALL_CLIPS, UNKNOWN_CLIPS } from "../constants";
import type { Clip } from "../types";
import { formatDuration } from "../utils/formatting";

type Props = {
  activeGroup: string;
  activeName: string;
  clips: Clip[];
  names: Record<string, string>;
  query: string;
  selected: Record<string, boolean>;
  selectedCount: number;
  exporting: boolean;
  visibleCount: number;
  onBack: () => void;
  onExport: () => void;
  onName: (id: string, value: string) => void;
  onQuery: (value: string) => void;
  onSelect: (id: string, value: boolean) => void;
  onShowMore: () => void;
};

export function ClipsPage(props: Props) {
  return (
    <PanelSection title={props.activeName}>
      <PanelSectionRow><ButtonItem layout="below" onClick={props.onBack}>‹ Back to games</ButtonItem></PanelSectionRow>
      <PanelSectionRow>
        <TextField label="Filter by game, date, time, or duration" value={props.query} onChange={(event) => props.onQuery(event.target.value)} />
      </PanelSectionRow>
      {props.clips.slice(0, props.visibleCount).map((clip) => (
        <PanelSectionRow key={clip.id}>
          <div>
            <Field
              label={props.activeGroup === ALL_CLIPS || props.activeGroup === UNKNOWN_CLIPS ? clip.game_name : new Date(clip.recorded_at).toLocaleString()}
              description={props.activeGroup === ALL_CLIPS || props.activeGroup === UNKNOWN_CLIPS
                ? `${new Date(clip.recorded_at).toLocaleString()} • ${formatDuration(clip.duration_seconds)}`
                : formatDuration(clip.duration_seconds)}
              bottomSeparator="none"
            >
              <Toggle value={Boolean(props.selected[clip.id])} onChange={(value) => props.onSelect(clip.id, value)} />
            </Field>
            {props.selected[clip.id] && (
              <TextField label="Optional filename" value={props.names[clip.id] ?? ""} onChange={(event) => props.onName(clip.id, event.target.value)} />
            )}
          </div>
        </PanelSectionRow>
      ))}
      {!props.clips.length && <PanelSectionRow><div>No matching clips.</div></PanelSectionRow>}
      {props.visibleCount < props.clips.length && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={props.onShowMore}>Load more ({props.clips.length - props.visibleCount} remaining)</ButtonItem>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!props.selectedCount || props.exporting} onClick={props.onExport}>
          {props.exporting ? "Exporting…" : `Export ${props.selectedCount || "selected"} clip${props.selectedCount === 1 ? "" : "s"}`}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
