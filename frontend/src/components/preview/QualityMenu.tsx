import { PREVIEW_QUALITIES, PREVIEW_QUALITY_LABELS, isPreviewQuality, type PreviewQuality } from "../../types/preview";

export interface QualityMenuProps {
  value: PreviewQuality;
  onChange: (quality: PreviewQuality) => void;
  disabled?: boolean;
}

const QUALITY_HELP: Record<PreviewQuality, string> = {
  auto: "Full quality, dropping to half while the computer is busy",
  full: "The monitor's size, up to 1280 x 720",
  half: "Half the size: smoother on a slow computer",
};

/** Full, Half or Auto (design 5.6). The final render is never affected. */
export function QualityMenu({ value, onChange, disabled }: QualityMenuProps) {
  return (
    <label className="flex items-center gap-1.5 text-[11.5px] text-[#96A3BC]" title={`Preview quality: ${QUALITY_HELP[value]}`}>
      <span>Quality</span>
      <select
        value={value}
        disabled={disabled}
        aria-label="Preview quality"
        onChange={(event) => {
          if (isPreviewQuality(event.target.value)) onChange(event.target.value);
        }}
        className="h-6 rounded-[5px] border border-[#232B3B] bg-[#1E2533] px-1.5 text-[11.5px] text-[#E8EDF7] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#E8EDF7] disabled:opacity-50"
      >
        {PREVIEW_QUALITIES.map((quality) => (
          <option key={quality} value={quality}>
            {PREVIEW_QUALITY_LABELS[quality]}
          </option>
        ))}
      </select>
    </label>
  );
}
