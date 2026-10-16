import { ChevronsLeft, ChevronsRight, MoveHorizontal } from "lucide-react";

import { errorMessage } from "../../api/client";
import { useNudgeTiming } from "../../api/edit";
import { useTiming } from "../../api/projects";
import { TIMING_SOURCE_HELP, TIMING_SOURCE_LABELS } from "../../types/timing";
import { formatClock } from "../edit/editUtils";
import { Panel } from "../script/Panel";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { LoadingBlock } from "../ui/Spinner";
import { Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

const STEPS = [0.1, 0.5] as const;

/**
 * The timing tool (docs/CONTRACT-v0.1.md section 4): one row per sentence of the narration
 * with its start and end. Move a sentence's start earlier or later, or shift it together
 * with everything after it. The scenes and the captions follow at once.
 */
export function TimingPanel({ projectId, disabled, onSeek }: { projectId: string; disabled: boolean; onSeek: (seconds: number) => void }) {
  const { toast } = useToast();
  const timing = useTiming(projectId);
  const nudge = useNudgeTiming(projectId);
  const doc = timing.data;

  const move = (index: number, body: { start_s?: number; shift_s?: number }) =>
    nudge.mutate({ index, ...body }, { onError: (error) => toast({ tone: "error", title: "Could not move the sentence", description: errorMessage(error) }) });

  return (
    <Panel
      title="Timing"
      description="Where each sentence of the narration starts and ends. Moving a start moves the scene cut and the captions with it."
      actions={doc ? <Badge tone={doc.nudged ? "accent" : "neutral"}>{doc.nudged ? "Moved by hand" : TIMING_SOURCE_LABELS[doc.source]}</Badge> : null}
    >
      {timing.isPending ? (
        <LoadingBlock label="Loading the timing..." />
      ) : timing.isError || !doc ? (
        <Notice tone="info">This edit has no narration timing. Add caption text or a caption file when you make an edit.</Notice>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-xs text-ink-faint">{TIMING_SOURCE_HELP[doc.source]}</p>
          <ol className="flex max-h-[420px] flex-col gap-1.5 overflow-y-auto pr-1">
            {doc.sentences.map((sentence, index) => (
              <li key={sentence.id} className="flex flex-wrap items-center gap-2 rounded-md border border-line px-3 py-2">
                <button type="button" onClick={() => onSeek(sentence.start_s)} className="w-[108px] shrink-0 text-left text-[11px] tabular-nums text-ink-muted hover:text-ink" title="Play from here">
                  {formatClock(sentence.start_s)} to {formatClock(sentence.end_s)}
                </button>
                <p className="min-w-[200px] flex-1 text-[13px] text-ink">
                  <span className="mr-2 text-[11px] font-semibold tabular-nums text-ink-faint">{index + 1}</span>
                  {sentence.text || <span className="italic text-ink-faint">(no text)</span>}
                </p>
                <div className="flex items-center gap-0.5" aria-label={`Move the start of sentence ${index + 1}`}>
                  {STEPS.map((step) => (
                    <Button key={`earlier-${step}`} variant="ghost" size="sm" icon={<ChevronsLeft />} disabled={disabled || nudge.isPending || index === 0} title={`Start ${step} s earlier`} onClick={() => move(index, { start_s: Math.max(0, sentence.start_s - step) })}>
                      {step}
                    </Button>
                  ))}
                  {STEPS.map((step) => (
                    <Button key={`later-${step}`} variant="ghost" size="sm" icon={<ChevronsRight />} disabled={disabled || nudge.isPending || index === 0} title={`Start ${step} s later`} onClick={() => move(index, { start_s: sentence.start_s + step })}>
                      {step}
                    </Button>
                  ))}
                  <Button variant="ghost" size="sm" icon={<MoveHorizontal />} disabled={disabled || nudge.isPending} title="Move this sentence and everything after it 0.5 s later" onClick={() => move(index, { shift_s: 0.5 })}>
                    +0.5 rest
                  </Button>
                  <Button variant="ghost" size="sm" icon={<MoveHorizontal />} disabled={disabled || nudge.isPending || index === 0} title="Move this sentence and everything after it 0.5 s earlier" onClick={() => move(index, { shift_s: -0.5 })}>
                    -0.5 rest
                  </Button>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Panel>
  );
}
