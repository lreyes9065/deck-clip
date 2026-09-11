import { Button, ButtonItem, Field, Focusable, PanelSection, PanelSectionRow, Toggle } from "@decky/ui";
import { Fragment } from "react";
import { FaCheck, FaTimes, FaTrash } from "react-icons/fa";
import type { ExportedFile, TransferStatus } from "../types";
import { formatSize } from "../utils/formatting";

type Props = {
  confirmTrash: string | null;
  exports: ExportedFile[];
  message: string;
  selected: Record<string, boolean>;
  transfer: TransferStatus | null;
  onBack: () => void;
  onCancelTrash: () => void;
  onConfirmTrash: (filename: string) => void;
  onRefresh: () => void;
  onSelect: (filename: string, value: boolean) => void;
  onSendSelected: () => void;
  onStartTrash: (filename: string) => void;
  onStopTransfer: () => void;
};

export function ExportManagerPage(props: Props) {
  const selectedCount = props.exports.filter((item) => props.selected[item.filename]).length;
  return (
    <PanelSection title="Exported clips">
      <PanelSectionRow><ButtonItem layout="below" onClick={props.onBack}>‹ Back to games</ButtonItem></PanelSectionRow>
      {props.transfer?.qr && props.transfer.url ? <>
        <PanelSectionRow>
          <Field label="Scan with your iPhone camera" description={`${props.transfer.file_count ?? 1} clip${(props.transfer.file_count ?? 1) === 1 ? "" : "s"} • expires ${props.transfer.expires_at ? new Date(props.transfer.expires_at).toLocaleTimeString() : "soon"}`} />
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ background: "white", padding: "28px", margin: "0 auto", width: "240px" }}>
            <div style={{ display: "grid", gridTemplateColumns: `repeat(${props.transfer.qr.length}, 1fr)` }}>
              {props.transfer.qr.flatMap((row, rowIndex) => Array.from(row).map((value, columnIndex) => (
                <div key={`${rowIndex}-${columnIndex}`} style={{ background: value === "1" ? "black" : "white", aspectRatio: "1" }} />
              )))}
            </div>
          </div>
        </PanelSectionRow>
        <PanelSectionRow><div>{props.transfer.state === "downloaded" ? "All downloads completed. You can stop sharing." : `${props.transfer.completed_files ?? 0} of ${props.transfer.file_count ?? 1} clips downloaded. Keep DeckClip open and both devices on the same trusted Wi-Fi network.`}</div></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={props.onStopTransfer}>Stop sharing</ButtonItem></PanelSectionRow>
      </> : <>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!selectedCount} onClick={props.onSendSelected}>
            Send selected clips ({selectedCount})
          </ButtonItem>
        </PanelSectionRow>
        {props.exports.map((item) => (
        <Fragment key={item.filename}>
          <PanelSectionRow>
            <Field label={item.filename} description={`${formatSize(item.size_bytes)} • ${new Date(item.modified_at).toLocaleString()}`} bottomSeparator="none">
              <Toggle value={Boolean(props.selected[item.filename])} onChange={(value) => props.onSelect(item.filename, value)} />
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Focusable flow-children="horizontal" style={{ display: "flex", justifyContent: "flex-end", gap: "8px", width: "100%" }}>
              {props.confirmTrash === item.filename ? <>
                <Button aria-label="Confirm move to Trash" style={{ width: "46px", minWidth: "46px", padding: 0 }} onClick={() => props.onConfirmTrash(item.filename)}><FaCheck /></Button>
                <Button aria-label="Cancel" style={{ width: "46px", minWidth: "46px", padding: 0 }} onClick={props.onCancelTrash}><FaTimes /></Button>
              </> : <>
                <Button aria-label="Move to Trash" style={{ width: "46px", minWidth: "46px", padding: 0 }} onClick={() => props.onStartTrash(item.filename)}><FaTrash /></Button>
              </>}
            </Focusable>
          </PanelSectionRow>
        </Fragment>
        ))}
      </>}
      {!props.transfer && !props.exports.length && <PanelSectionRow><div>No exported MP4 files found.</div></PanelSectionRow>}
      {props.message && <PanelSectionRow><div>{props.message}</div></PanelSectionRow>}
      <PanelSectionRow><ButtonItem layout="below" onClick={props.onRefresh}>Refresh exports</ButtonItem></PanelSectionRow>
    </PanelSection>
  );
}
