import { Music } from "lucide-react";

import { fileName, type MusicTrack } from "../../types/timeline";
import { Badge } from "../ui/Badge";
import { Select } from "../ui/Input";
import { formatDuration } from "./editUtils";

export interface MusicSelectorProps {
  /** the audio files in the edit's music folder (01_media) */
  tracks: MusicTrack[];
  /** the chosen track path, or null for no music */
  value: string | null;
  /** the chosen track is licensed: a licence file next to it, or the licence note in Details */
  licenseOk: boolean;
  onChange: (path: string | null) => void;
  /** the edit's music folder, for the empty state */
  folder?: string | null;
  disabled?: boolean;
}

const NONE = "__none__";

/** Picks the background music from the edit's music folder; shows whether its licence is known. */
export function MusicSelector({ tracks, value, licenseOk, onChange, folder, disabled }: MusicSelectorProps) {
  const known = value ? tracks.find((track) => track.path === value) : undefined;
  const license = licenseOk || (known?.license_ok ?? false);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={value ?? NONE} onChange={(event) => onChange(event.target.value === NONE ? null : event.target.value)} disabled={disabled} aria-label="Background music" wrapperClassName="min-w-[240px] flex-1">
          <option value={NONE}>No music</option>
          {value && !known ? <option value={value}>{fileName(value)} (from the timeline)</option> : null}
          {tracks.map((track) => (
            <option key={track.path} value={track.path}>
              {track.name}
              {track.duration_s ? ` (${formatDuration(track.duration_s)})` : ""}
              {track.license_ok ? "" : " - no licence file"}
            </option>
          ))}
        </Select>
        {value ? (
          <Badge tone={license ? "ok" : "warn"} dot>
            {license ? "Licence noted" : "No licence yet"}
          </Badge>
        ) : (
          <Badge tone="neutral">Silent background</Badge>
        )}
      </div>
      {value && !license ? (
        <p className="text-xs text-warn">
          No licence is known for <span className="font-mono">{fileName(value)}</span>. Write where it comes from in the music licence note (Details), or put a <span className="font-mono">{fileName(value)}.license.txt</span> file next to it. The video still renders; the check warns until then.
        </p>
      ) : null}
      {tracks.length === 0 ? (
        <p className="flex items-start gap-2 text-xs text-ink-faint">
          <Music className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            No music files{folder ? <> in <span className="font-mono">{folder}</span></> : " in the edit's music folder"}. Add a music file when you make an edit, or copy MP3 or WAV files into that folder (Open folder) and press "Build the timeline again".
          </span>
        </p>
      ) : null}
    </div>
  );
}
