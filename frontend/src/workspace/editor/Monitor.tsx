import { Crosshair, FolderInput, Maximize2, Upload } from "lucide-react";
import { useMemo, useState } from "react";

import { FilesPickButton } from "../../components/form/FilesPickButton";
import { LivePreview } from "../../components/preview/LivePreview";
import { useStoreTime } from "../../components/preview/timeStore";
import { Transport } from "../../components/preview/Transport";
import { cn } from "../../lib/cn";
import { bundleFromTimeline, withOverlays } from "../../components/preview/bundle";
import { useEditor } from "./context";
import { timecode } from "./document";
import { LIBRARY_ACCEPT } from "./Library";

function SafeAreas({ portrait }: { portrait: boolean }) {
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      <div className="absolute inset-[5%] border border-dashed border-white/35" title="Title-safe (90%)" />
      <div className="absolute inset-[3.5%] border border-white/20" title="Action-safe (93%)" />
      {!portrait ? <div className="absolute inset-y-0 left-1/2 w-[31.6%] -translate-x-1/2 border-x border-dotted border-accent/50" title="9:16 crop guide" /> : null}
    </div>
  );
}

function HeaderToggle({ pressed, onClick, title, children }: { pressed: boolean; onClick: () => void; title: string; children: React.ReactNode }) {
  return (
    <button type="button" aria-pressed={pressed} title={title} onClick={onClick} className={cn("ws-focus flex h-6 items-center gap-1 rounded px-1.5 text-[11px]", pressed ? "bg-ed-raised-hi text-ink" : "text-ink-faint hover:bg-ed-raised hover:text-ink")}>
      {children}
    </button>
  );
}

/** The monitor: header, the live preview, the transport (docs/design/EDITOR-DESIGN.md section 4.4). */
export function Monitor({ preparingMusic }: { preparingMusic: boolean }) {
  const { state, actions } = useEditor();
  const [safe, setSafe] = useState(false);
  const portrait = state.timeline.aspect === "9:16";
  const time = useStoreTime(state.timeStore, 1 / state.fps);

  // The live document, drawn as the preview bundle: the server's bundle (word times, media)
  // with the timeline the person is editing.
  const liveBundle = useMemo(() => {
    const base = state.bundle ?? bundleFromTimeline(state.projectId, state.timeline);
    return withOverlays({ ...base, timeline: state.timeline }, { popups: state.payload.popups_enabled, captions: state.payload.captions_enabled });
  }, [state.bundle, state.timeline, state.projectId, state.payload.popups_enabled, state.payload.captions_enabled]);

  const loopRange = state.loop && state.range.in_s !== null && state.range.out_s !== null ? { in_s: state.range.in_s, out_s: state.range.out_s } : state.loop ? { in_s: 0, out_s: state.duration } : null;

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-ed-panel" aria-label="Monitor">
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-ed-line bg-ed-panel-2 px-3">
        <span className="text-[12px] font-semibold text-ink">Preview</span>
        <span className="flex items-center gap-1 rounded-full border border-ok/40 bg-ok/10 px-2 py-0.5 text-[10px] font-medium text-ok" title="Drawn from the timeline as you edit. Motion and transitions match the final render; text can differ by a pixel or two.">
          <span className="size-1.5 rounded-full bg-ok" aria-hidden />
          Live
        </span>
        {preparingMusic ? <span className="text-[11px] text-ink-faint">Preparing the music...</span> : null}
        <div className="ml-auto flex items-center gap-1">
          <HeaderToggle pressed={safe} onClick={() => setSafe((v) => !v)} title="Safe areas: title-safe 90%, action-safe 93%, and the 9:16 crop guide">
            <Crosshair className="size-3.5" aria-hidden />
            Safe areas
          </HeaderToggle>
          <HeaderToggle pressed={false} onClick={() => document.documentElement.requestFullscreen?.()} title="Full screen (Esc to leave)">
            <Maximize2 className="size-3.5" aria-hidden />
          </HeaderToggle>
        </div>
      </div>
      {state.timeline.scenes.length === 0 ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-black px-6 text-center">
          <FolderInput className="size-8 text-ink-faint" aria-hidden />
          <p className="text-[13px] font-medium text-ink">Add pictures, clips or sound from the Media tab to start</p>
          <p className="max-w-sm text-[11px] text-ink-faint">Import your files on the left, then press "Add to video" on a picture or clip. The video is built from those scenes; layers, sounds, the narration and the music go on top.</p>
          <FilesPickButton accept={LIBRARY_ACCEPT} size="sm" variant="primary" icon={<Upload />} loading={state.busy !== null} disabled={state.locked} onPick={(files) => void actions.importMedia(files)}>
            Import files
          </FilesPickButton>
        </div>
      ) : (
        <LivePreview
          projectId={state.projectId}
          controllerRef={actions.preview}
          bundle={liveBundle}
          aspect={state.timeline.aspect}
          chrome="monitor"
          timeStore={state.timeStore}
          loopRange={loopRange}
          onPlayingChange={undefined}
          frameOverlay={safe ? <SafeAreas portrait={portrait} /> : null}
          className="min-h-0 flex-1"
        />
      )}
      <div className="shrink-0 border-t border-ed-line bg-ed-panel-2">
        <div className="flex h-5 items-center px-3 font-timecode text-[13px]">
          <span className="text-accent-text">{timecode(time, state.fps)}</span>
          <span className="ml-2 text-ink-faint">/ {timecode(state.duration, state.fps)}</span>
        </div>
        <Transport
          store={state.timeStore}
          duration={state.duration}
          playing={state.playing}
          loop={state.loop}
          inPoint={state.range.in_s}
          outPoint={state.range.out_s}
          onPlayPause={actions.playPause}
          onStep={actions.stepFrames}
          onPreviousCut={() => {
            const cuts = [0, ...state.timeline.scenes.map((scene) => scene.start_s)].filter((t) => t < time - 0.05);
            actions.seek(cuts.length > 0 ? Math.max(...cuts) : 0);
          }}
          onNextCut={() => {
            const cuts = state.timeline.scenes.map((scene) => scene.start_s).filter((t) => t > time + 0.05);
            actions.seek(cuts.length > 0 ? Math.min(...cuts) : state.duration);
          }}
          onStart={() => actions.seek(0)}
          onEnd={() => actions.seek(state.duration)}
          onMarkIn={actions.markIn}
          onMarkOut={actions.markOut}
          onClearRange={actions.clearRange}
          onToggleLoop={actions.toggleLoop}
        />
      </div>
    </section>
  );
}
