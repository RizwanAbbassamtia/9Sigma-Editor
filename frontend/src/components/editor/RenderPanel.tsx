import { FileText, Info, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { errorMessage } from "../../api/client";
import { projectMediaUrl, RENDER_LOG_FILE, useApplyEdits, useCancelRender, useStartRender, type RenderProgress } from "../../api/edit";
import type { Quality, UploadPreset } from "../../api/projects";
import { isQuality, isUploadPreset, QUALITIES, QUALITY_DESCRIPTIONS, QUALITY_LABELS, UPLOAD_PRESET_LABELS, UPLOAD_PRESETS } from "../../types/settings";
import { renderPresetLabel, type EditReviewPayload } from "../../types/timeline";
import { RenderPresets } from "../edit/RenderPresets";
import { Panel } from "../script/Panel";
import { Button } from "../ui/Button";
import { Input, Select } from "../ui/Input";
import { Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

export interface RenderPanelProps {
  projectId: string;
  payload: EditReviewPayload;
  running: boolean;
  progress: RenderProgress | null;
  enable4k: boolean;
  /** another request is in flight */
  busy: boolean;
}

/**
 * What the last renders could reuse: the server's `render_notes`, else each finished render's
 * own `reuse_note`.
 */
function lastRenderNotes(payload: EditReviewPayload): string[] {
  if (payload.render_notes.length > 0) return payload.render_notes;
  return payload.renders.filter((render) => render.status === "done" && render.reuse_note).map((render) => `${render.preset === "proxy" ? "Preview" : renderPresetLabel(render.preset)}: ${render.reuse_note}`);
}

/**
 * Render: the quality and upload choices, the H.265 copy, the sizes with their status and
 * progress, Stop while a render runs, and "Check a part" for a short range render.
 */
export function RenderPanel({ projectId, payload, running, progress, enable4k, busy }: RenderPanelProps) {
  const { toast } = useToast();
  const apply = useApplyEdits(projectId);
  const start = useStartRender(projectId);
  const cancel = useCancelRender(projectId);
  const [quality, setQuality] = useState<Quality>(isQuality(payload.quality) ? payload.quality : "standard");
  const [upload, setUpload] = useState<UploadPreset>(isUploadPreset(payload.upload_preset) ? payload.upload_preset : "none");
  const [h265, setH265] = useState(payload.h265_copy);
  const [rangeStart, setRangeStart] = useState("0");
  const [rangeEnd, setRangeEnd] = useState("10");

  // The saved choices win when the server sends new ones (after a render).
  useEffect(() => {
    if (isQuality(payload.quality)) setQuality(payload.quality);
    if (isUploadPreset(payload.upload_preset)) setUpload(payload.upload_preset);
    setH265(payload.h265_copy);
  }, [payload.quality, payload.upload_preset, payload.h265_copy]);

  const lockedUi = running || busy || apply.isPending || start.isPending;
  const renderLogUrl = projectMediaUrl(projectId, payload.render_log_path ?? RENDER_LOG_FILE);
  const notes = lastRenderNotes(payload);
  const duration = payload.summary.duration_s;

  const onToggle = (preset: string, checked: boolean) => {
    const presets = payload.available_presets.filter((id) => (id === preset ? checked : payload.presets.includes(id)));
    apply.mutate({ presets }, { onError: (error) => toast({ tone: "error", title: "Could not save the sizes", description: errorMessage(error) }) });
  };

  const onRender = (presets: string[]) => {
    const names = presets.length > 0 ? presets.map(renderPresetLabel).join(", ") : "the preview";
    start.mutate(
      { presets, quality, upload_preset: upload, h265_copy: h265 },
      {
        onSuccess: () => toast({ tone: "info", title: `Rendering ${names}`, description: "Progress shows on each size. The page refreshes when the render is done." }),
        onError: (error) => toast({ tone: "error", title: `Could not start rendering ${names}`, description: errorMessage(error) }),
      },
    );
  };

  const onRenderRange = () => {
    const from = Number(rangeStart);
    const to = Number(rangeEnd);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) {
      toast({ tone: "error", title: "Check the times", description: "The end must come after the start." });
      return;
    }
    const presets = payload.presets.length > 0 ? [payload.presets[0]!] : [payload.available_presets[0] ?? "720p"];
    start.mutate(
      { presets, quality, upload_preset: upload, h265_copy: false, range: { start_s: from, end_s: Math.min(to, duration || to) } },
      {
        onSuccess: () => toast({ tone: "info", title: "Rendering the part", description: "The file lands in the edit's folder under 07_edit/range." }),
        onError: (error) => toast({ tone: "error", title: "Could not render the part", description: errorMessage(error) }),
      },
    );
  };

  return (
    <Panel
      title="Render"
      description="Tick the sizes the export should include, pick the quality, render here and watch the progress."
      actions={
        <>
          {running ? (
            <Button variant="danger" size="sm" icon={<Square />} loading={cancel.isPending} onClick={() => cancel.mutate(undefined, { onError: (error) => toast({ tone: "error", title: "Could not stop the render", description: errorMessage(error) }) })}>
              Stop
            </Button>
          ) : null}
          {renderLogUrl && (payload.renders.some((render) => render.status !== "not_rendered") || payload.proxy_path) ? (
            <a href={renderLogUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-accent-text hover:underline">
              <FileText className="size-3.5" aria-hidden />
              Render log
            </a>
          ) : null}
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-ink-muted">Quality</span>
            <Select value={quality} disabled={lockedUi} onChange={(event) => setQuality(event.target.value as Quality)}>
              {QUALITIES.map((option) => (
                <option key={option} value={option}>
                  {QUALITY_LABELS[option]}
                </option>
              ))}
            </Select>
            <span className="text-xs text-ink-faint">{QUALITY_DESCRIPTIONS[quality]}</span>
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-ink-muted">Upload preset</span>
            <Select value={upload} disabled={lockedUi} onChange={(event) => setUpload(event.target.value as UploadPreset)}>
              {UPLOAD_PRESETS.map((option) => (
                <option key={option} value={option}>
                  {UPLOAD_PRESET_LABELS[option]}
                </option>
              ))}
            </Select>
            <span className="text-xs text-ink-faint">Caps the bitrate the way YouTube likes it. Leave off for a file you keep.</span>
          </label>
          <label className="flex items-start gap-3 pt-6">
            <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={h265} disabled={lockedUi} onChange={(event) => setH265(event.target.checked)} />
            <span>
              <span className="text-[13px] font-medium text-ink">Also make an H.265 copy</span>
              <span className="block text-xs text-ink-muted">A smaller second file of each size; slower.</span>
            </span>
          </label>
        </div>

        {payload.last_render_error && !running ? (
          <Notice tone="fail" title="The last render stopped">
            {payload.last_render_error}
          </Notice>
        ) : null}

        <RenderPresets
          available={payload.available_presets}
          selected={payload.presets}
          renders={payload.renders}
          aspect={payload.summary.aspect}
          enable4k={enable4k}
          running={running}
          progress={progress}
          busy={busy || apply.isPending || start.isPending}
          canRender={!running}
          mediaUrl={(render) => projectMediaUrl(projectId, render.path ?? `07_edit/final_${render.preset}.mp4`, render.play_url)}
          onToggle={onToggle}
          onRender={onRender}
        />

        <div className="flex flex-wrap items-end gap-3 border-t border-line pt-4">
          <div>
            <p className="text-[13px] font-medium text-ink">Check a part</p>
            <p className="text-xs text-ink-muted">Renders only these seconds at the first ticked size, into the edit's folder (07_edit/range).</p>
          </div>
          <label className="flex items-center gap-1.5 text-xs text-ink-muted">
            From
            <Input type="number" min={0} step={0.5} className="h-8 w-20 text-[13px]" value={rangeStart} disabled={lockedUi} onChange={(event) => setRangeStart(event.target.value)} aria-label="Start of the part in seconds" />
            s
          </label>
          <label className="flex items-center gap-1.5 text-xs text-ink-muted">
            to
            <Input type="number" min={0} step={0.5} className="h-8 w-20 text-[13px]" value={rangeEnd} disabled={lockedUi} onChange={(event) => setRangeEnd(event.target.value)} aria-label="End of the part in seconds" />
            s
          </label>
          <Button variant="secondary" size="sm" disabled={lockedUi} onClick={onRenderRange}>
            Render the part
          </Button>
        </div>

        {notes.length > 0 ? (
          <div className="border-t border-line pt-3">
            <p className="text-xs font-medium text-ink-muted">Last render</p>
            <ul className="mt-1.5 flex flex-col gap-1 text-xs text-ink-muted">
              {notes.map((note) => (
                <li key={note} className="flex items-start gap-2">
                  <Info className="mt-0.5 size-3.5 shrink-0 text-ink-faint" aria-hidden />
                  <span>{note}</span>
                </li>
              ))}
            </ul>
            <p className="mt-1.5 text-[11px] text-ink-faint">Scenes and transitions that did not change are reused, so a small edit renders much faster.</p>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
