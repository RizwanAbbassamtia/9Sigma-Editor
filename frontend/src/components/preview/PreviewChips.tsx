import { LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import type { PreviewEngineStatus, PreviewQuality } from "../../types/preview";

type ChipTone = "live" | "info" | "warn" | "neutral";

const TONES: Record<ChipTone, string> = {
  live: "border-[#22C55E]/35 bg-[#22C55E]/12 text-[#86EFAC]",
  info: "border-[#60A5FA]/35 bg-[#60A5FA]/12 text-[#9CC3FA]",
  warn: "border-[#F59E0B]/40 bg-[#F59E0B]/12 text-[#FCD34D]",
  neutral: "border-[#2F3A50] bg-[#1E2533] text-[#96A3BC]",
};

/** A small status pill in the monitor header; the tooltip says what it means. */
export function PreviewChip({ tone, title, children, dot, spin }: { tone: ChipTone; title: string; children: ReactNode; dot?: boolean; spin?: boolean }) {
  return (
    <span
      title={title}
      className={cn("inline-flex h-5 shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2 text-[11px] font-medium leading-none", TONES[tone])}
    >
      {dot ? <span className="size-1.5 rounded-full bg-current" aria-hidden /> : null}
      {spin ? <LoaderCircle className="size-3 animate-spin" aria-hidden /> : null}
      {children}
    </span>
  );
}

const CHIP_TEXT = {
  live: "Drawn from the timeline as you edit. Motion and transitions match the final render; text can differ by a pixel or two.",
  animatic: "Drawn from the storyboard as you edit. The edit step lays out the final timeline the same way.",
  realVoice: "The real voice and its timing.",
  estimated: "No voice yet: the timing is estimated from the words. Scenes may move a little once the voice exists.",
  mock: "Placeholder pictures and a silent voice from the test tools. Everything else works as it will for real.",
  simplified: "Your graphics driver blocks the full preview, so some transitions are drawn as a dissolve. The final render is not affected.",

  restarting: "The graphics driver reset the preview. It is coming back now.",
  half: "The preview dropped to half quality to keep up. It goes back to full when the computer has room.",
} as const;

export interface PreviewChipsProps {
  status: PreviewEngineStatus;
  quality: PreviewQuality;
  animatic: boolean;
  estimatedTiming: boolean;
  mockMedia: boolean;
}

/** Live (or Animatic), then whatever the preview needs to say about itself (design section 7). */
export function PreviewChips({ status, quality, animatic, estimatedTiming, mockMedia }: PreviewChipsProps) {
  const simplified = status.simplified || status.renderer === "canvas2d";
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-1.5">
      {animatic ? (
        <PreviewChip tone="live" dot title={CHIP_TEXT.animatic}>
          Animatic
        </PreviewChip>
      ) : (
        <PreviewChip tone="live" dot title={CHIP_TEXT.live}>
          Live
        </PreviewChip>
      )}
      {animatic ? (
        estimatedTiming ? (
          <PreviewChip tone="warn" title={CHIP_TEXT.estimated}>
            Estimated timing
          </PreviewChip>
        ) : (
          <PreviewChip tone="neutral" title={CHIP_TEXT.realVoice}>
            Real voice
          </PreviewChip>
        )
      ) : null}
      {status.restarting ? (
        <PreviewChip tone="info" spin title={CHIP_TEXT.restarting}>
          Restarting the preview...
        </PreviewChip>
      ) : null}
      {simplified && !status.restarting ? (
        <PreviewChip tone="warn" title={CHIP_TEXT.simplified}>
          Simplified preview
        </PreviewChip>
      ) : null}
      {quality === "auto" && status.activeQuality === "half" ? (
        <PreviewChip tone="neutral" title={CHIP_TEXT.half}>
          Half quality
        </PreviewChip>
      ) : null}
      {mockMedia || status.mockMedia ? (
        <PreviewChip tone="info" title={CHIP_TEXT.mock}>
          Mock media
        </PreviewChip>
      ) : null}
    </span>
  );
}
