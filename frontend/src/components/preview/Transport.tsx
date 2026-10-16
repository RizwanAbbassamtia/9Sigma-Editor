import { ChevronFirst, ChevronLast, Pause, Play, Repeat, SkipBack, SkipForward, StepBack, StepForward, X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import { formatTenths } from "./previewTime";
import { TimeDisplay } from "./TimeDisplay";
import type { TimeStore } from "./timeStore";

export interface TransportProps {
  store: TimeStore;
  duration: number;
  playing: boolean;
  /** nothing to play yet */
  disabled?: boolean;
  loop: boolean;
  inPoint: number | null;
  outPoint: number | null;
  onPlayPause: () => void;
  /** frames back (negative) or forward */
  onStep: (frames: number) => void;
  onPreviousCut: () => void;
  onNextCut: () => void;
  onStart: () => void;
  onEnd: () => void;
  onMarkIn: () => void;
  onMarkOut: () => void;
  onClearRange: () => void;
  onToggleLoop: () => void;
}

function TransportButton({
  label,
  keys,
  onClick,
  disabled,
  pressed,
  wide,
  children,
}: {
  label: string;
  keys?: string;
  onClick: () => void;
  disabled?: boolean;
  pressed?: boolean;
  /** a text button ("In", "Out") instead of a square icon button */
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={pressed}
      title={keys ? `${label} (${keys})` : label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "grid h-7 shrink-0 place-items-center rounded-md text-[#96A3BC] transition-colors",
        wide ? "px-1.5 font-mono text-[11px]" : "w-7",
        "hover:bg-[#1E2533] hover:text-[#E8EDF7] disabled:pointer-events-none disabled:opacity-40",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#E8EDF7]",
        pressed && "bg-[#283044] text-[#E8EDF7]",
        "[&_svg]:size-3.5",
      )}
    >
      {children}
    </button>
  );
}

/**
 * The monitor's transport (design 4.4): the clock on the left; to start, previous cut, frame
 * back, play, frame forward, next cut, to end in the middle; the in and out marks and loop on
 * the right. Every tooltip names its shortcut.
 */
export function Transport({
  store,
  duration,
  playing,
  disabled,
  loop,
  inPoint,
  outPoint,
  onPlayPause,
  onStep,
  onPreviousCut,
  onNextCut,
  onStart,
  onEnd,
  onMarkIn,
  onMarkOut,
  onClearRange,
  onToggleLoop,
}: TransportProps) {
  const hasRange = inPoint !== null || outPoint !== null;
  const rangeText = hasRange ? `${formatTenths(inPoint ?? 0)}-${formatTenths(outPoint ?? duration)}` : null;
  return (
    <div className="grid min-h-11 grid-cols-[1fr_auto_1fr] items-center gap-2 border-t border-[#232B3B] bg-[#181E2A] px-2.5 py-1.5">
      <div className="min-w-0">
        <TimeDisplay store={store} duration={duration} />
      </div>
      <div className="flex items-center gap-0.5" role="group" aria-label="Playback">
        <TransportButton label="Go to start" keys="Home" onClick={onStart} disabled={disabled}>
          <ChevronFirst aria-hidden />
        </TransportButton>
        <TransportButton label="Previous cut" keys="Up" onClick={onPreviousCut} disabled={disabled}>
          <SkipBack aria-hidden />
        </TransportButton>
        <TransportButton label="One frame back" keys="Left" onClick={() => onStep(-1)} disabled={disabled}>
          <StepBack aria-hidden />
        </TransportButton>
        <button
          type="button"
          aria-label={playing ? "Pause" : "Play"}
          title={playing ? "Pause (Space)" : "Play (Space)"}
          disabled={disabled}
          onClick={onPlayPause}
          className={cn(
            "mx-1.5 grid size-[34px] shrink-0 place-items-center rounded-full bg-[#E8EDF7] text-[#0A0D14] transition-colors hover:bg-white",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#E8EDF7] disabled:opacity-40",
          )}
        >
          {playing ? <Pause className="size-4" aria-hidden /> : <Play className="ml-0.5 size-4" aria-hidden />}
        </button>
        <TransportButton label="One frame forward" keys="Right" onClick={() => onStep(1)} disabled={disabled}>
          <StepForward aria-hidden />
        </TransportButton>
        <TransportButton label="Next cut" keys="Down" onClick={onNextCut} disabled={disabled}>
          <SkipForward aria-hidden />
        </TransportButton>
        <TransportButton label="Go to end" keys="End" onClick={onEnd} disabled={disabled}>
          <ChevronLast aria-hidden />
        </TransportButton>
      </div>
      <div className="flex min-w-0 items-center justify-end gap-0.5">
        {rangeText ? (
          <span className="mr-1 hidden truncate font-mono text-[11px] text-[#96A3BC] sm:inline" title="The in-out range">
            {rangeText}
          </span>
        ) : null}
        <TransportButton label="Mark in" keys="I" onClick={onMarkIn} disabled={disabled} wide>
          In
        </TransportButton>
        <TransportButton label="Mark out" keys="O" onClick={onMarkOut} disabled={disabled} wide>
          Out
        </TransportButton>
        {hasRange ? (
          <TransportButton label="Clear in and out" keys="Alt+X" onClick={onClearRange} disabled={disabled}>
            <X aria-hidden />
          </TransportButton>
        ) : null}
        <TransportButton
          label={hasRange ? "Loop the in-out range" : "Loop the whole video"}
          keys="Ctrl+L"
          onClick={onToggleLoop}
          disabled={disabled}
          pressed={loop}
        >
          <Repeat aria-hidden />
        </TransportButton>
      </div>
    </div>
  );
}
