import { cn } from "../../lib/cn";
import { formatTenths } from "./previewTime";
import { useStoreTime, type TimeStore } from "./timeStore";

export interface TimeDisplayProps {
  store: TimeStore;
  /** the length of the video in seconds */
  duration: number;
  className?: string;
}

/**
 * The monitor clock in tenths of a second: the current time in amber, the length in muted ink
 * ("1:05.6 / 2:04.0"). It re-renders ten times a second at most, on its own.
 */
export function TimeDisplay({ store, duration, className }: TimeDisplayProps) {
  const time = useStoreTime(store, 0.1);
  return (
    <span role="timer" aria-live="off" className={cn("whitespace-nowrap font-mono text-[13px] tabular-nums leading-none", className)}>
      <span className="text-[#FFC000]" data-testid="preview-current-time">
        {formatTenths(time)}
      </span>
      <span className="text-[#7C8BA3]">
        {" / "}
        {formatTenths(duration)}
      </span>
    </span>
  );
}
