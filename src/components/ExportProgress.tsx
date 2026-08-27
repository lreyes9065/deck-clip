import { Field, PanelSection, PanelSectionRow, ProgressBar } from "@decky/ui";
import type { Job } from "../types";

export function ExportProgress({ job }: { job: Job }) {
  return (
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
  );
}
