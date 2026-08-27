import { ButtonItem, Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { GameFilter } from "../components/GameFilter";
import { UNKNOWN_CLIPS } from "../constants";
import type { GameGroup } from "../types";

type Option = { data: string; label: string };
type Props = {
  disabled: boolean;
  displayedGames: GameGroup[];
  filter: string | null;
  filterOptions: Option[];
  message: string;
  unknownCount: number;
  onClearFilter: () => void;
  onFilter: (value: string) => void;
  onManageExports: () => void;
  onOpenGame: (id: string) => void;
  onRefresh: () => void;
};

export function LibraryPage(props: Props) {
  return (
    <PanelSection>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={props.disabled} onClick={props.onManageExports}>Manage exported clips</ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <GameFilter options={props.filterOptions} value={props.filter} onChange={props.onFilter} onClear={props.onClearFilter} />
      </PanelSectionRow>
      <PanelSectionRow><Field label={props.filter ? "Selected game" : "Recently recorded"} /></PanelSectionRow>
      {props.displayedGames.map((game) => (
        <PanelSectionRow key={game.id}>
          <ButtonItem layout="below" onClick={() => props.onOpenGame(game.id)}>
            {game.name} ({game.clips.length})
          </ButtonItem>
        </PanelSectionRow>
      ))}
      {props.filter === UNKNOWN_CLIPS && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => props.onOpenGame(UNKNOWN_CLIPS)}>
            Unknown / Unmatched ({props.unknownCount})
          </ButtonItem>
        </PanelSectionRow>
      )}
      {props.message && <PanelSectionRow><div>{props.message}</div></PanelSectionRow>}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={props.disabled} onClick={props.onRefresh}>Refresh library</ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
