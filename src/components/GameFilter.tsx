import { Button, Dropdown, Focusable } from "@decky/ui";
import type { SingleDropdownOption } from "@decky/ui";

type Props = {
  options: SingleDropdownOption[];
  value: string | null;
  onChange: (value: string) => void;
  onClear: () => void;
};

export function GameFilter({ options, value, onChange, onClear }: Props) {
  return (
    <div style={{ width: "100%" }}>
      <div style={{ fontSize: "12px", opacity: 0.7, marginBottom: "6px", textAlign: "left" }}>Filter</div>
      <Focusable flow-children="horizontal" style={{ display: "flex", alignItems: "center", gap: "8px", width: "100%" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Dropdown
            key={value ?? "no-game-filter"}
            menuLabel="DeckClip games"
            rgOptions={options}
            selectedOption={value}
            strDefaultLabel="Select a game"
            onChange={(option) => {
              const selected = typeof option?.data === "string"
                ? option.data
                : typeof option === "string" ? option : null;
              if (selected) onChange(selected);
            }}
          />
        </div>
        {value && (
          <Button aria-label="Clear game filter" style={{ width: "40px", minWidth: "40px", padding: 0 }} onClick={onClear}>×</Button>
        )}
      </Focusable>
    </div>
  );
}
