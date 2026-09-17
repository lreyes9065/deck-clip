import { SidebarNavigation, Field, PanelSection, PanelSectionRow, ButtonItem } from "@decky/ui";
import { useState } from "react";
import { refreshLibrary } from "../api/library";
import { RecentStatusesPage } from "./RecentStatusesPage";

const stepStyle = { margin: "0 0 10px", lineHeight: 1.35 } as const;

function IPhoneHelp() {
  return <>
      <PanelSection title="iPhone: Save directly to Photos">
      <PanelSectionRow>
        <Field
          label="One-time Shortcut setup"
          description="Set this up before starting a QR transfer. Keep the exact name DeckClip Save to Photos for compatibility with existing Shortcuts; the plugin is now called ClipPort."
        />
      </PanelSectionRow>
      <PanelSectionRow><Field focusable label="Create the Shortcut" description={<>
        <p style={stepStyle}><strong>1.</strong> Open Shortcuts, tap +, and name it <strong>DeckClip Save to Photos</strong>.</p>
        <p style={stepStyle}><strong>2.</strong> Add <strong>Get Contents of URL</strong>. Tap URL → Select Variable → <strong>Shortcut Input</strong>.</p>
        <p style={stepStyle}><strong>3.</strong> Add <strong>Get Dictionary Value</strong>. Enter <strong>files</strong>. It should say “Get Value for files in Contents of URL.”</p>
        <p style={stepStyle}><strong>4.</strong> Add <strong>Repeat with Each</strong> using <strong>Dictionary Value</strong>.</p>
      </>} /></PanelSectionRow>
      <PanelSectionRow><Field focusable label="Inside the Repeat section" description={<>
        <p style={stepStyle}><strong>5.</strong> Inside Repeat, add <strong>Get Dictionary Value</strong>. Enter <strong>url</strong> and use <strong>Repeat Item</strong>.</p>
        <p style={stepStyle}><strong>6.</strong> Inside Repeat, add <strong>Get Contents of URL</strong> using the preceding <strong>Dictionary Value</strong>.</p>
        <p style={stepStyle}><strong>7.</strong> Inside Repeat, add <strong>Save to Photo Album</strong> using the preceding <strong>Contents of URL</strong>.</p>
        <p style={stepStyle}><strong>8.</strong> Confirm those final three actions are above <strong>End Repeat</strong>, then test from a ClipPort QR page.</p>
      </>} /></PanelSectionRow>
      </PanelSection>
  </>;
}

function TransferHelp() {
  const [refreshing, setRefreshing] = useState(false);
  const [message, setMessage] = useState("");
  const rescan = async () => {
    setRefreshing(true);
    setMessage("");
    try {
      const clips = await refreshLibrary();
      setMessage(`Library refreshed. ${clips.length} clips found.`);
    } catch (error) {
      setMessage(`Could not refresh library: ${String(error)}`);
    } finally { setRefreshing(false); }
  };
  return <>
      <PanelSection title="Library">
        <PanelSectionRow><Field focusable label="Automatic refresh" description="The library refreshes when you open ClipPort. If a recording seems to be missing, you can rescan here." /></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" disabled={refreshing} onClick={() => void rescan()}>{refreshing ? "Refreshing…" : "Rescan library"}</ButtonItem></PanelSectionRow>
        {message && <PanelSectionRow><Field focusable label={message} /></PanelSectionRow>}
      </PanelSection>
      <PanelSection title="Transfer safety">
      <PanelSectionRow>
        <Field
          focusable label="Trusted local networks only"
          description="QR links are random and expire after 10 minutes, but local HTTP traffic is not encrypted. Never share a QR screenshot."
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <Field
          focusable label="Export management"
          description="ClipPort only manages MP4 files in /home/deck/Videos/DeckClip. Steam source recordings remain read-only."
        />
      </PanelSectionRow>
      </PanelSection>
  </>;
}

export function HelpSettingsPage() {
  // Follow Decky's own settings pattern: distinct child routes let Steam
  // select the content and manage focus. B exits through native navigation.
  return <SidebarNavigation
    title="ClipPort Help & Settings"
    showTitle
    pages={[
      { title: "iPhone setup", route: "/deckclip/help/iphone", content: <IPhoneHelp /> },
      { title: "Transfers & files", route: "/deckclip/help/transfers", content: <TransferHelp /> },
      { title: "Recent statuses", route: "/deckclip/help/statuses", content: <RecentStatusesPage /> },
    ]}
  />;
}
