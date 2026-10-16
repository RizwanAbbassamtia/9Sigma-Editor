import { AlertTriangle, ArrowLeft, Check, ChevronDown, Clapperboard, FolderOpen, Keyboard, MoreHorizontal, Redo2, Undo2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { errorMessage } from "../../api/client";
import { useOpenProjectFolder } from "../../api/projects";
import { cn } from "../../lib/cn";
import { FORMAT_SHORT_LABELS } from "../../types/project";
import { useEditor } from "./context";
import { clock } from "./document";
import { RenderMenu } from "./RenderMenu";
import { ShortcutSheet } from "./ShortcutSheet";

function BarButton({ children, title, onClick, disabled, primary, pressed, className }: { children: ReactNode; title: string; onClick?: () => void; disabled?: boolean; primary?: boolean; pressed?: boolean; className?: string }) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "ws-focus flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[12px] font-medium transition-colors disabled:opacity-40",
        primary ? "bg-accent text-accent-ink hover:bg-accent-hover" : "text-ink-muted hover:bg-ed-raised hover:text-ink",
        pressed && "bg-ed-raised-hi text-ink",
        className,
      )}
    >
      {children}
    </button>
  );
}

function SaveStateChip() {
  const { state } = useEditor();
  const save = state.saveState;
  const text =
    save.kind === "saved" ? `Saved ${save.at}` : save.kind === "saving" ? "Saving..." : save.kind === "retrying" ? "Not saved: retrying" : save.kind === "conflict" ? "Changed elsewhere" : "Saved";
  const tone = save.kind === "retrying" || save.kind === "conflict" ? "text-warn" : "text-ink-faint";
  return (
    <span className={cn("shrink-0 px-2 text-[11px]", tone)} title={save.kind === "retrying" ? save.error : undefined} role="status">
      {text}
    </span>
  );
}

function ChecksPill() {
  const { state } = useEditor();
  const failing = [...state.payload.checks, ...state.payload.gate_results].filter((check) => !check.passed);
  const blocking = failing.filter((check) => check.severity === "block");
  const [open, setOpen] = useState(false);
  const label = blocking.length > 0 ? `${blocking.length} to fix` : failing.length > 0 ? `${failing.length} tip${failing.length === 1 ? "" : "s"}` : "All good";
  const tone = blocking.length > 0 ? "border-fail/40 bg-fail/10 text-fail" : failing.length > 0 ? "border-warn/40 bg-warn/10 text-warn" : "border-ok/40 bg-ok/10 text-ok";
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className={cn("ws-focus flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-medium", tone)} title="Checks: music licence, length, loudness, pictures, caption speed">
        {blocking.length > 0 || failing.length > 0 ? <AlertTriangle className="size-3.5" aria-hidden /> : <Check className="size-3.5" aria-hidden />}
        {label}
      </button>
      {open ? (
        <div className="absolute right-0 top-9 z-40 w-80 rounded-md border border-ed-line-strong bg-ed-panel p-3 shadow-[0_12px_32px_rgb(0_0_0/0.5)]" role="dialog" aria-label="Checks">
          {failing.length === 0 && state.payload.warnings.length === 0 ? (
            <p className="text-[12px] text-ok">Nothing to flag.</p>
          ) : (
            <ul className="flex flex-col gap-2 text-[12px]">
              {failing.map((check) => (
                <li key={check.id} className="flex items-start gap-2">
                  <AlertTriangle className={cn("mt-0.5 size-3.5 shrink-0", check.severity === "block" ? "text-fail" : "text-warn")} aria-hidden />
                  <span>
                    <span className="font-medium text-ink">{check.title}</span>
                    {check.detail ? <span className="text-ink-muted">: {check.detail}</span> : null}
                  </span>
                </li>
              ))}
              {state.payload.warnings.map((warning) => (
                <li key={warning} className="flex items-start gap-2 text-ink-muted">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
                  <span>{warning}</span>
                </li>
              ))}
            </ul>
          )}
          {blocking.length > 0 ? <p className="mt-2 text-[11px] text-fail">A check in red stops the export until it is fixed.</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/** The 44 px bar above the panels (docs/design/EDITOR-DESIGN.md section 4.2). */
export function TopBar() {
  const { state, actions } = useEditor();
  const navigate = useNavigate();
  const openFolder = useOpenProjectFolder();
  const [renderOpen, setRenderOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [shortcuts, setShortcuts] = useState(false);
  const moreRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!moreOpen) return;
    const close = (event: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(event.target as Node)) setMoreOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [moreOpen]);

  useEffect(() => {
    const onOpen = () => setRenderOpen(true);
    const onSheet = () => setShortcuts((v) => !v);
    window.addEventListener("sge:open-render-menu", onOpen);
    window.addEventListener("sge:toggle-shortcuts", onSheet);
    return () => {
      window.removeEventListener("sge:open-render-menu", onOpen);
      window.removeEventListener("sge:toggle-shortcuts", onSheet);
    };
  }, []);

  const status = state.renderStatus;
  const rendering = Boolean(status?.running);
  const renderLabel = rendering ? `${status?.preset ?? "Rendering"} ${status?.pct !== null && status?.pct !== undefined ? `${Math.round(status.pct)}%` : "..."}` : "Render";

  return (
    <div className="flex h-full min-w-0 flex-1 items-center">
      {/* above the library */}
      <div className="flex w-[304px] min-w-[240px] shrink-0 items-center gap-1 px-2">
        <BarButton title="Back to My edits" onClick={() => navigate("/")}>
          <ArrowLeft className="size-4" aria-hidden />
        </BarButton>
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold text-ink" title={state.project.name}>
            {state.project.name}
          </p>
          <p className="truncate text-[11px] text-ink-faint">
            {FORMAT_SHORT_LABELS[state.project.format]} · {clock(state.duration)} · {state.fps} fps
          </p>
        </div>
      </div>

      {/* above the monitor */}
      <div className="flex min-w-0 flex-1 items-center justify-between gap-2 px-2">
        <div className="flex items-center gap-1">
          <BarButton title={state.undoLabel ? `Undo: ${state.undoLabel} (Ctrl+Z)` : "Nothing to undo"} onClick={actions.undo} disabled={!state.canUndo}>
            <Undo2 className="size-4" aria-hidden />
          </BarButton>
          <BarButton title={state.redoLabel ? `Redo: ${state.redoLabel} (Ctrl+Y)` : "Nothing to redo"} onClick={actions.redo} disabled={!state.canRedo}>
            <Redo2 className="size-4" aria-hidden />
          </BarButton>
          <SaveStateChip />
        </div>
        <div className="flex items-center rounded-md border border-ed-line bg-ed-raised p-0.5 text-[11px] font-medium" role="group" aria-label="View">
          <span className="rounded bg-ed-raised-hi px-2.5 py-1 text-ink">Timeline</span>
        </div>
        <div className="relative flex items-center gap-1">
          <BarButton title="Open the edit's folder" onClick={() => openFolder.mutate({ id: state.projectId }, { onError: (error) => actions.notify("error", "Could not open the folder", errorMessage(error)) })}>
            <FolderOpen className="size-4" aria-hidden />
          </BarButton>
          <BarButton title="Render (Ctrl+E)" primary onClick={() => setRenderOpen((v) => !v)} pressed={renderOpen}>
            <Clapperboard className="size-4" aria-hidden />
            {renderLabel}
            <ChevronDown className="size-3.5" aria-hidden />
          </BarButton>
          {renderOpen ? <RenderMenu onClose={() => setRenderOpen(false)} /> : null}
        </div>
      </div>

      {/* above the inspector */}
      <div className="flex w-[336px] min-w-[300px] shrink-0 items-center justify-end gap-2 px-2">
        <ChecksPill />
        <div className="relative" ref={moreRef}>
          <BarButton title="More" onClick={() => setMoreOpen((v) => !v)} pressed={moreOpen}>
            <MoreHorizontal className="size-4" aria-hidden />
          </BarButton>
          {moreOpen ? (
            <div className="absolute right-0 top-9 z-40 w-56 rounded-md border border-ed-line-strong bg-ed-panel py-1 shadow-[0_12px_32px_rgb(0_0_0/0.5)]" role="menu">
              <button type="button" role="menuitem" className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-ink hover:bg-ed-raised" onClick={() => { setMoreOpen(false); setShortcuts(true); }}>
                <Keyboard className="size-4 text-ink-faint" aria-hidden />
                Keyboard shortcuts
              </button>
              <button type="button" role="menuitem" className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-ink hover:bg-ed-raised" onClick={() => { setMoreOpen(false); void actions.rebuild(); }}>
                <Undo2 className="size-4 text-ink-faint" aria-hidden />
                Build the timeline again
              </button>
            </div>
          ) : null}
        </div>
      </div>
      {shortcuts ? <ShortcutSheet onClose={() => setShortcuts(false)} /> : null}
    </div>
  );
}
