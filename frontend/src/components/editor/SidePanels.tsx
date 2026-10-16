import { AlertTriangle, Check, FolderOpen, PackageCheck, Save } from "lucide-react";
import { useEffect, useState } from "react";

import { errorMessage } from "../../api/client";
import { useExportEdit } from "../../api/edit";
import { useOpenProjectFolder, usePatchProject, type ExportResult } from "../../api/projects";
import { LANGUAGES, type GateResult, type Project } from "../../types/project";
import type { EditReviewPayload } from "../../types/timeline";
import { formatBytes } from "../edit/editUtils";
import { Panel } from "../script/Panel";
import { Button } from "../ui/Button";
import { Input, Select, Textarea } from "../ui/Input";
import { Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

/** The checks under the edit: the renderer's gates and the editor's own checks. */
export function ChecksPanel({ payload }: { payload: EditReviewPayload }) {
  const failing: GateResult[] = [...payload.checks, ...payload.gate_results].filter((check) => !check.passed);
  const blocking = failing.some((check) => check.severity === "block");
  return (
    <Panel title="Checks" description="Pictures, caption speed, music licence, length and loudness, as the app found them.">
      {payload.warnings.length === 0 && failing.length === 0 ? (
        <p className="flex items-center gap-2 text-[13px] text-ok">
          <Check className="size-4" aria-hidden />
          Nothing to flag.
        </p>
      ) : (
        <ul className="flex flex-col gap-2 text-[13px]">
          {failing.map((check) => (
            <li key={check.id} className="flex items-start gap-2">
              <AlertTriangle className={check.severity === "block" ? "mt-0.5 size-4 shrink-0 text-fail" : "mt-0.5 size-4 shrink-0 text-warn"} aria-hidden />
              <span>
                <span className="font-medium text-ink">{check.title}</span>
                {check.detail ? <span className="text-ink-muted">: {check.detail}</span> : null}
                {check.severity === "warn" ? <span className="text-ink-faint"> (warning)</span> : null}
              </span>
            </li>
          ))}
          {payload.warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-2 text-ink-muted">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}
      {blocking ? <p className="mt-3 text-xs text-fail">A check in red stops the export until it is fixed.</p> : null}
    </Panel>
  );
}

/** Export: copies the rendered sizes, a poster frame and the export note to the exports folder. */
export function ExportPanel({ project, payload, disabled }: { project: Project; payload: EditReviewPayload; disabled: boolean }) {
  const { toast } = useToast();
  const exportEdit = useExportEdit(project.id);
  const openFolder = useOpenProjectFolder();
  const [result, setResult] = useState<ExportResult | null>(null);
  const rendered = payload.renders.filter((render) => render.status === "done" && render.preset !== "proxy");
  const blocking = [...payload.checks, ...payload.gate_results].some((check) => !check.passed && check.severity === "block");

  const onExport = () =>
    exportEdit.mutate(
      { presets: [] },
      {
        onSuccess: (view) => {
          setResult(view);
          toast({ tone: "success", title: "Exported", description: `${view.files.length} file${view.files.length === 1 ? "" : "s"} in ${view.folder}` });
        },
        onError: (error) => toast({ tone: "error", title: "Could not export", description: errorMessage(error) }),
      },
    );

  const onOpenExport = () =>
    openFolder.mutate(
      { id: project.id, request: { target: "export" } },
      { onError: (error) => toast({ tone: "error", title: "Could not open the export folder", description: errorMessage(error) }) },
    );

  return (
    <Panel title="Export" description="Puts every rendered size, a poster frame and a note with checksums into the exports folder.">
      <div className="flex flex-col gap-3">
        <ul className="list-disc space-y-1 pl-4 text-xs text-ink-muted">
          <li>{rendered.length > 0 ? `Rendered: ${rendered.map((render) => render.preset).join(", ")}.` : "Nothing is rendered yet. Render a size first."}</li>
          {payload.music_path ? <li>{payload.music_license_ok ? "The music licence is noted." : "The music has no licence note or file yet (a warning, not a stop)."}</li> : null}
          {blocking ? <li className="text-fail">A check in red must be fixed first.</li> : null}
        </ul>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" icon={<PackageCheck />} disabled={disabled || rendered.length === 0 || blocking} loading={exportEdit.isPending} onClick={onExport}>
            Export
          </Button>
          {project.status === "exported" || result ? (
            <Button variant="secondary" size="sm" icon={<FolderOpen />} loading={openFolder.isPending} onClick={onOpenExport}>
              Open export folder
            </Button>
          ) : null}
        </div>
        {result ? (
          <div className="rounded-md border border-line bg-surface-2/40 px-3 py-2 text-xs text-ink-muted">
            <p className="font-mono text-[11px] text-ink">{result.folder}</p>
            <ul className="mt-1 space-y-0.5">
              {result.files.map((file) => (
                <li key={file.name}>
                  {file.name} <span className="text-ink-faint">({file.label}, {formatBytes(file.size_bytes)})</span>
                </li>
              ))}
            </ul>
          </div>
        ) : project.status === "exported" && project.status_note ? (
          <p className="text-xs text-ink-faint">{project.status_note}</p>
        ) : null}
      </div>
    </Panel>
  );
}

/** Details: the name, the language and the music licence note. */
export function DetailsPanel({ project, disabled }: { project: Project; disabled: boolean }) {
  const { toast } = useToast();
  const patch = usePatchProject();
  const [name, setName] = useState(project.name);
  const [language, setLanguage] = useState(project.language);
  const [note, setNote] = useState(project.music_licence_note);

  useEffect(() => {
    setName(project.name);
    setLanguage(project.language);
    setNote(project.music_licence_note);
  }, [project.name, project.language, project.music_licence_note]);

  const dirty = name.trim() !== project.name || language !== project.language || note.trim() !== project.music_licence_note;

  const save = () =>
    patch.mutate(
      { id: project.id, patch: { name: name.trim() || project.name, language, music_licence_note: note.trim() } },
      {
        onSuccess: () => toast({ tone: "success", title: "Details saved" }),
        onError: (error) => toast({ tone: "error", title: "Could not save the details", description: errorMessage(error) }),
      },
    );

  return (
    <Panel
      title="Details"
      actions={
        <Button variant="primary" size="sm" icon={<Save />} disabled={disabled || !dirty} loading={patch.isPending} onClick={save}>
          Save
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-muted">Name</span>
          <Input value={name} disabled={disabled} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-muted">Language</span>
          <Select value={language} disabled={disabled} onChange={(event) => setLanguage(event.target.value)}>
            {!LANGUAGES.includes(language) ? <option value={language}>{language}</option> : null}
            {LANGUAGES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-muted">Music licence note</span>
          <Textarea rows={3} value={note} disabled={disabled} onChange={(event) => setNote(event.target.value)} placeholder="Where the music comes from and the licence it has" />
        </label>
        {language !== project.language ? <Notice tone="info">The caption font follows the language at the next render.</Notice> : null}
      </div>
    </Panel>
  );
}
