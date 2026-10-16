import { Download, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { errorMessage } from "../../api/client";
import { captionsSrtUrl, useCaptions, useSaveCaptions } from "../../api/edit";
import type { CueInput } from "../../api/projects";
import { Panel } from "../script/Panel";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState } from "../ui/States";
import { useToast } from "../ui/Toast";

interface Row {
  key: number;
  id: string;
  start: string;
  end: string;
  text: string;
}

let nextKey = 1;

function rowsFrom(cues: ReadonlyArray<{ id: string; start_s: number; end_s: number; text: string }>): Row[] {
  return cues.map((cue) => ({ key: nextKey++, id: cue.id, start: cue.start_s.toFixed(2), end: cue.end_s.toFixed(2), text: cue.text.replace(/\\N/g, "\n") }));
}

function toCue(row: Row): CueInput | null {
  const start = Number(row.start);
  const end = Number(row.end);
  const text = row.text.trim();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || !text) return null;
  return { id: row.id, start_s: Math.max(0, start), end_s: end, text };
}

/**
 * The caption table (docs/CONTRACT-v0.1.md section 4): the text and the times of every caption,
 * saved with Save, and the SRT file to download. Whether captions are burnt in is a switch in
 * the Timeline panel.
 */
export function CaptionsPanel({ projectId, disabled, duration }: { projectId: string; disabled: boolean; duration: number }) {
  const { toast } = useToast();
  const captions = useCaptions(projectId);
  const save = useSaveCaptions(projectId);
  const [rows, setRows] = useState<Row[]>([]);
  const [revision, setRevision] = useState<number | null>(null);

  useEffect(() => {
    if (captions.data && captions.data.revision !== revision) {
      setRevision(captions.data.revision);
      setRows(rowsFrom(captions.data.cues));
    }
  }, [captions.data, revision]);

  const saved = useMemo(() => (captions.data ? JSON.stringify(rowsFrom(captions.data.cues).map((row) => [row.start, row.end, row.text])) : ""), [captions.data]);
  const dirty = JSON.stringify(rows.map((row) => [row.start, row.end, row.text])) !== saved;
  const invalid = rows.filter((row) => toCue(row) === null).length;

  const patch = (key: number, changes: Partial<Row>) => setRows((current) => current.map((row) => (row.key === key ? { ...row, ...changes } : row)));

  const addRow = () => {
    const last = rows.at(-1);
    const start = last ? Number(last.end) : 0;
    setRows((current) => [...current, { key: nextKey++, id: "", start: start.toFixed(2), end: Math.min(duration || start + 2, start + 2).toFixed(2), text: "" }]);
  };

  const onSave = () => {
    const cues = rows.map(toCue).filter((cue): cue is CueInput => cue !== null);
    save.mutate(
      { cues },
      {
        onSuccess: () => toast({ tone: "success", title: "Captions saved", description: "The next render burns them in." }),
        onError: (error) => toast({ tone: "error", title: "Could not save the captions", description: errorMessage(error) }),
      },
    );
  };

  return (
    <Panel
      title="Captions"
      description="What is shown at the bottom of the picture and when. Times are seconds from the start."
      actions={
        <>
          <a href={captionsSrtUrl(projectId)} download className="inline-flex items-center gap-1 text-xs font-medium text-accent-text hover:underline">
            <Download className="size-3.5" aria-hidden />
            Save as SRT
          </a>
          {dirty ? (
            <Button variant="ghost" size="sm" icon={<RotateCcw />} disabled={save.isPending} onClick={() => captions.data && setRows(rowsFrom(captions.data.cues))}>
              Discard
            </Button>
          ) : null}
          <Button variant="primary" size="sm" icon={<Save />} disabled={disabled || !dirty} loading={save.isPending} onClick={onSave}>
            Save captions
          </Button>
        </>
      }
    >
      {captions.isPending ? (
        <LoadingBlock label="Loading the captions..." />
      ) : captions.isError ? (
        <ErrorState error={captions.error} title="Could not load the captions" onRetry={() => void captions.refetch()} />
      ) : (
        <div className="flex flex-col gap-3">
          {rows.length === 0 ? <p className="text-[13px] text-ink-muted">No captions yet. Add one, or add caption text when you make an edit.</p> : null}
          <ol className="flex max-h-[420px] flex-col gap-1.5 overflow-y-auto pr-1">
            {rows.map((row, position) => {
              const bad = toCue(row) === null;
              return (
                <li key={row.key} className={bad ? "grid grid-cols-[32px_84px_84px_minmax(0,1fr)_32px] items-center gap-2 rounded-md border border-fail/50 px-2 py-1" : "grid grid-cols-[32px_84px_84px_minmax(0,1fr)_32px] items-center gap-2 rounded-md border border-line px-2 py-1"}>
                  <span className="text-right text-[11px] tabular-nums text-ink-faint">{position + 1}</span>
                  <Input type="number" min={0} step={0.1} className="h-8 text-[12px] tabular-nums" value={row.start} disabled={disabled} aria-label={`Start of caption ${position + 1}`} onChange={(event) => patch(row.key, { start: event.target.value })} />
                  <Input type="number" min={0} step={0.1} className="h-8 text-[12px] tabular-nums" value={row.end} disabled={disabled} aria-label={`End of caption ${position + 1}`} onChange={(event) => patch(row.key, { end: event.target.value })} />
                  <Input className="h-8 text-[13px]" value={row.text} disabled={disabled} aria-label={`Text of caption ${position + 1}`} onChange={(event) => patch(row.key, { text: event.target.value })} />
                  <Button variant="ghost" size="sm" icon={<Trash2 />} aria-label={`Remove caption ${position + 1}`} disabled={disabled} onClick={() => setRows((current) => current.filter((item) => item.key !== row.key))} />
                </li>
              );
            })}
          </ol>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="secondary" size="sm" icon={<Plus />} disabled={disabled} onClick={addRow}>
              Add a caption
            </Button>
            {invalid > 0 ? <p className="text-xs text-warn">{invalid === 1 ? "One caption" : `${invalid} captions`} with no text or an end before the start will be left out.</p> : null}
          </div>
        </div>
      )}
    </Panel>
  );
}
