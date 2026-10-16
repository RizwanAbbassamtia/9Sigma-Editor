import { FolderOpen, PackageCheck, Play, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { errorMessage } from "../../api/client";
import { projectMediaUrl, useCancelRender, useExportEdit, useStartRender } from "../../api/edit";
import { useOpenProjectFolder } from "../../api/projects";
import { useRenderSettings } from "../../api/settings";
import { cn } from "../../lib/cn";
import { isQuality, QUALITIES, QUALITY_LABELS, type Quality } from "../../types/settings";
import { RENDER_PRESET_SIZES, isRenderPreset, renderPresetLabel, type RenderOutput } from "../../types/timeline";
import { useEditor } from "./context";

function statusChip(render: RenderOutput | undefined, rendering: boolean): { text: string; tone: string } {
  if (rendering) return { text: "Rendering", tone: "text-accent-text" };
  if (!render || render.status === "not_rendered") return { text: "Not rendered", tone: "text-ink-faint" };
  if (render.status === "failed") return { text: "Failed", tone: "text-fail" };
  if (render.status === "rendering" || render.status === "queued") return { text: "Rendering", tone: "text-accent-text" };
  if (render.stale) return { text: render.changes ? `Out of date · ${render.changes} change${render.changes === 1 ? "" : "s"}` : "Out of date", tone: "text-warn" };
  const at = render.rendered_at ? new Date(render.rendered_at) : null;
  return { text: at && !Number.isNaN(at.getTime()) ? `Rendered ${at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Rendered", tone: "text-ok" };
}

/** The "finish" menu under the Render button (docs/design/EDITOR-DESIGN.md section 4.2). */
export function RenderMenu({ onClose }: { onClose: () => void }) {
  const { state, actions } = useEditor();
  const { resolved } = useRenderSettings();
  const start = useStartRender(state.projectId);
  const cancel = useCancelRender(state.projectId);
  const exportEdit = useExportEdit(state.projectId);
  const openFolder = useOpenProjectFolder();
  const ref = useRef<HTMLDivElement | null>(null);
  const payload = state.payload;
  const [ticked, setTicked] = useState<string[]>(payload.presets);
  const [quality, setQuality] = useState<Quality>(isQuality(payload.quality) ? payload.quality : "standard");
  const [exported, setExported] = useState<string | null>(null);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", key);
    };
  }, [onClose]);

  const status = state.renderStatus;
  const rendering = Boolean(status?.running);
  const enable4k = payload.enable_4k ?? resolved.render.enable_4k;
  const renders = new Map(payload.renders.map((render) => [render.preset, render] as const));
  const blocking = [...payload.checks, ...payload.gate_results].some((check) => !check.passed && check.severity === "block");
  const readyToExport = ticked.filter((preset) => {
    const render = renders.get(preset);
    return render && render.status === "done" && !render.stale;
  });
  const exportReason = blocking ? "A check in red must be fixed first." : readyToExport.length === 0 ? (ticked.length === 0 ? "Tick a size and render it first." : `Render ${ticked.map(renderPresetLabel).join(", ")} first: the last render is missing or out of date.`) : null;

  const toggle = (preset: string, checked: boolean) => {
    const next = payload.available_presets.filter((id) => (id === preset ? checked : ticked.includes(id)));
    setTicked(next);
    void actions.applyChoices({ presets: next });
  };

  const onRender = (range: boolean) => {
    const presets = ticked.length > 0 ? ticked : [];
    const body = { presets, quality, upload_preset: isUploadPresetSafe(payload.upload_preset), h265_copy: payload.h265_copy, ...(range && state.range.in_s !== null && state.range.out_s !== null ? { range: { start_s: state.range.in_s, end_s: state.range.out_s } } : {}) };
    start.mutate(body, {
      onSuccess: () => actions.notify("info", presets.length > 0 ? `Rendering ${presets.map(renderPresetLabel).join(", ")}` : "Rendering the preview", "Keep editing; the button shows the progress."),
      onError: (error) => actions.notify("error", "Could not start the render", errorMessage(error)),
    });
  };

  const onExport = () =>
    exportEdit.mutate(
      { presets: readyToExport },
      {
        onSuccess: (view) => {
          setExported(view.folder);
          actions.notify("success", "Exported", `${view.files.length} file${view.files.length === 1 ? "" : "s"} in ${view.folder}`);
        },
        onError: (error) => actions.notify("error", "Could not export", errorMessage(error)),
      },
    );

  const hasRange = state.range.in_s !== null && state.range.out_s !== null;

  return (
    <div ref={ref} role="dialog" aria-label="Finish this video" className="absolute right-0 top-10 z-40 w-[332px] rounded-md border border-ed-line-strong bg-ed-panel shadow-[0_12px_32px_rgb(0_0_0/0.5)]">
      <div className="flex items-center justify-between border-b border-ed-line px-3 py-2">
        <span className="text-[13px] font-semibold text-ink">Finish this video</span>
        <span className="text-[11px] text-ink-faint">Renders run in the background</span>
      </div>
      <ul className="py-1">
        {payload.available_presets.map((preset) => {
          const render = renders.get(preset);
          const is4k = preset === "2160p";
          const blocked = is4k && !enable4k;
          const chip = statusChip(render, rendering && status?.preset === preset);
          const size = isRenderPreset(preset) ? RENDER_PRESET_SIZES[preset][payload.summary.aspect === "9:16" ? "portrait" : "landscape"] : null;
          const url = render ? projectMediaUrl(state.projectId, render.path ?? `07_edit/final_${preset}.mp4`, render.play_url) : null;
          return (
            <li key={preset} className={cn("flex h-9 items-center gap-2 px-3", blocked && "opacity-50")}>
              <input type="checkbox" className="accent-accent" checked={ticked.includes(preset)} disabled={blocked || rendering} onChange={(event) => toggle(preset, event.target.checked)} aria-label={`Include ${renderPresetLabel(preset)}`} />
              <span className="w-12 text-[12px] font-medium text-ink">{renderPresetLabel(preset)}</span>
              <span className="w-20 text-[11px] text-ink-faint">{size ? `${size[0]} x ${size[1]}` : ""}</span>
              <span className={cn("min-w-0 flex-1 truncate text-[11px]", chip.tone)}>{blocked ? "Turn on in Settings" : chip.text}</span>
              {render?.status === "done" && url ? (
                <a href={url} target="_blank" rel="noreferrer" className="ws-focus grid size-6 place-items-center rounded text-ink-muted hover:bg-ed-raised hover:text-ink" title="Play this render">
                  <Play className="size-3.5" aria-hidden />
                </a>
              ) : null}
            </li>
          );
        })}
      </ul>
      <div className="flex items-center gap-3 border-t border-ed-line px-3 py-2 text-[11px] text-ink-muted">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" className="accent-accent" checked={payload.captions_enabled} disabled={rendering} onChange={(event) => void actions.applyChoices({ captions_enabled: event.target.checked })} />
          Captions
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" className="accent-accent" checked={payload.popups_enabled} disabled={rendering} onChange={(event) => void actions.applyChoices({ popups_enabled: event.target.checked })} />
          Popups
        </label>
        <label className="ml-auto flex items-center gap-1.5">
          Quality
          <select value={quality} disabled={rendering} onChange={(event) => setQuality(event.target.value as Quality)} className="ws-focus h-6 rounded border border-ed-line bg-ed-raised px-1 text-[11px] text-ink">
            {QUALITIES.map((option) => (
              <option key={option} value={option}>
                {QUALITY_LABELS[option]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {rendering ? (
        <div className="border-t border-ed-line px-3 py-2">
          <div className="flex items-center justify-between text-[11px] text-ink">
            <span className="truncate">{status?.message || "Rendering..."}</span>
            <button type="button" onClick={() => cancel.mutate(undefined, { onError: (error) => actions.notify("error", "Could not stop the render", errorMessage(error)) })} className="ws-focus flex items-center gap-1 rounded px-1.5 py-0.5 text-fail hover:bg-fail/10" title="Stop the render">
              <Square className="size-3" aria-hidden />
              Cancel
            </button>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-ed-raised">
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${status?.pct ?? 5}%` }} />
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-2 border-t border-ed-line px-3 py-2">
          <button type="button" onClick={() => onRender(false)} disabled={start.isPending} className="ws-focus flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md bg-accent px-3 text-[12px] font-semibold text-accent-ink hover:bg-accent-hover disabled:opacity-50">
            Render {ticked.length > 0 ? ticked.map(renderPresetLabel).join(", ") : "the preview"}
          </button>
          {hasRange ? (
            <button type="button" onClick={() => onRender(true)} disabled={start.isPending} className="ws-focus h-8 rounded-md border border-ed-line bg-ed-raised px-3 text-[12px] text-ink hover:bg-ed-raised-hi" title="Render only the in-out range, into 07_edit/range">
              Range only
            </button>
          ) : null}
        </div>
      )}
      {status?.error && !rendering ? (
        <p className="border-t border-ed-line px-3 py-2 text-[11px] text-fail">
          {status.error} {payload.render_log_path ? <a className="underline" href={projectMediaUrl(state.projectId, payload.render_log_path) ?? "#"} target="_blank" rel="noreferrer">Show the render log</a> : null}
        </p>
      ) : null}
      <div className="border-t border-ed-line px-3 py-2">
        <button type="button" onClick={onExport} disabled={exportReason !== null || exportEdit.isPending || rendering} className="ws-focus flex h-8 w-full items-center justify-center gap-1.5 rounded-md border border-ed-line bg-ed-raised px-3 text-[12px] font-medium text-ink hover:bg-ed-raised-hi disabled:opacity-50">
          <PackageCheck className="size-4" aria-hidden />
          Export {readyToExport.length > 0 ? readyToExport.map(renderPresetLabel).join(", ") : ""}
        </button>
        {exportReason ? <p className="mt-1.5 text-[11px] text-ink-faint">{exportReason}</p> : null}
        {exported || state.project.status === "exported" ? (
          <button type="button" onClick={() => openFolder.mutate({ id: state.projectId, request: { target: "export" } }, { onError: (error) => actions.notify("error", "Could not open the export folder", errorMessage(error)) })} className="ws-focus mt-1.5 flex items-center gap-1.5 text-[11px] text-accent-text hover:underline">
            <FolderOpen className="size-3.5" aria-hidden />
            Open the export folder
          </button>
        ) : null}
      </div>
      <button type="button" onClick={onClose} aria-label="Close" className="ws-focus absolute right-2 top-2 grid size-6 place-items-center rounded text-ink-faint hover:bg-ed-raised hover:text-ink">
        <X className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}

function isUploadPresetSafe(value: string): "none" | "youtube_1080p" | "youtube_4k" {
  return value === "youtube_1080p" || value === "youtube_4k" ? value : "none";
}
