import { ButtonItem, Field, PanelSection, PanelSectionRow, Toggle } from "@decky/ui";
import type { ExportedFile, TransferStatus } from "../types";
import { formatSize } from "../utils/formatting";
import { ExportThumbnail } from "../components/ExportThumbnail";

type Props = {
  exports: ExportedFile[];
  message: string;
  selected: Record<string, boolean>;
  transfer: TransferStatus | null;
  onBack: () => void;
  onSelect: (filename: string, value: boolean) => void;
  onSendSelected: () => void;
  onStopTransfer: () => void;
  onDeleteSelected: () => void;
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
        <PanelSectionRow><div>{props.transfer.state === "downloaded" ? "All downloads completed. You can stop sharing." : `${props.transfer.completed_files ?? 0} of ${props.transfer.file_count ?? 1} clips downloaded. Keep ClipPort open and both devices on the same trusted Wi-Fi network.`}</div></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={props.onStopTransfer}>Stop sharing</ButtonItem></PanelSectionRow>
      </> : <>
        {props.exports.map((item) => (
          <PanelSectionRow key={item.filename}>
            <Field icon={<ExportThumbnail filename={item.filename} modified={item.modified_at} />} label={item.filename} description={`${formatSize(item.size_bytes)} • ${new Date(item.modified_at).toLocaleString()}`}>
              <Toggle value={Boolean(props.selected[item.filename])} onChange={(value) => props.onSelect(item.filename, value)} />
            </Field>
          </PanelSectionRow>
        ))}
        <PanelSectionRow><ButtonItem layout="below" disabled={!selectedCount} onClick={props.onSendSelected}>Send selected clips ({selectedCount})</ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" disabled={!selectedCount} onClick={props.onDeleteSelected}>Delete selected exports ({selectedCount})</ButtonItem></PanelSectionRow>
      </>}
      {!props.transfer && !props.exports.length && <PanelSectionRow><div>No exported MP4 files found.</div></PanelSectionRow>}
      {props.message && <PanelSectionRow><div>{props.message}</div></PanelSectionRow>}
    </PanelSection>
  );
}
