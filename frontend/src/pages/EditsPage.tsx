import { Clapperboard, FolderOpen, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { errorMessage } from "../api/client";
import { useDeleteProject, useOpenProjectFolder, usePatchProject, useProjects } from "../api/projects";
import { formatClock } from "../components/edit/editUtils";
import { ProjectThumb } from "../components/editing/ProjectThumb";
import { NewEditDialog } from "../components/edits/NewEditDialog";
import { CountLine } from "../components/master/CountLine";
import { matchesText } from "../components/master/format";
import { SearchBox } from "../components/master/SearchBox";
import { Badge, type BadgeTone } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog, Dialog } from "../components/ui/Dialog";
import { Field } from "../components/ui/Field";
import { Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState } from "../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../components/ui/Table";
import { useToast } from "../components/ui/Toast";
import { editorPath } from "../layout/nav";
import { formatDateTime, formatRelative } from "../lib/format";
import { FORMAT_SHORT_LABELS, STATUS_LABELS, type ProjectStatus, type ProjectSummary } from "../types/project";

const STATUS_TONE: Record<ProjectStatus, BadgeTone> = {
  new: "neutral",
  ready: "info",
  rendering: "accent",
  rendered: "ok",
  exported: "ok",
  failed: "fail",
};

/** `/`: every edit on this computer, newest first, with New edit. */
export function EditsPage() {
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<ProjectSummary | null>(null);
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);
  const projects = useProjects({ refetchInterval: 15_000 });
  const openFolder = useOpenProjectFolder();
  const remove = useDeleteProject();
  const patch = usePatchProject();
  const [newName, setNewName] = useState("");

  const rows = useMemo(() => [...(projects.data ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at)), [projects.data]);
  const filtered = useMemo(() => rows.filter((row) => matchesText(search, row.name, row.language, STATUS_LABELS[row.status])), [rows, search]);

  const onOpenFolder = (row: ProjectSummary) =>
    openFolder.mutate(
      { id: row.id },
      { onError: (error) => toast({ tone: "error", title: "Could not open the folder", description: errorMessage(error) }) },
    );

  const startRename = (row: ProjectSummary) => {
    setRenaming(row);
    setNewName(row.name);
  };

  const confirmRename = () => {
    if (!renaming || !newName.trim()) return;
    patch.mutate(
      { id: renaming.id, patch: { name: newName.trim() } },
      {
        onSuccess: () => {
          setRenaming(null);
          toast({ tone: "success", title: "Edit renamed" });
        },
        onError: (error) => toast({ tone: "error", title: "Could not rename the edit", description: errorMessage(error) }),
      },
    );
  };

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: () => {
        setDeleting(null);
        toast({ tone: "success", title: "Edit moved to the Recycle Bin" });
      },
      onError: (error) => toast({ tone: "error", title: "Could not delete the edit", description: errorMessage(error) }),
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="My edits"
        description="Every video you are making on this computer. Open one to edit it, or start a new one from your pictures, clips and narration."
        actions={
          <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>
            New edit
          </Button>
        }
      />

      {rows.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <SearchBox value={search} onChange={setSearch} placeholder="Search edits" className="w-64" />
        </div>
      ) : null}

      <Card flush>
        {projects.isPending ? (
          <LoadingBlock label="Reading your edits..." />
        ) : projects.isError ? (
          <ErrorState error={projects.error} title="Could not read your edits" onRetry={() => void projects.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Clapperboard}
            title="No edits yet"
            description="Start with New edit: pick your pictures or clips, add the narration and the music, and the first timeline is built for you."
            action={
              <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>
                New edit
              </Button>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState icon={Search} title="No edits match" description="Clear the search to see every edit." />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="min-w-[280px]">Edit</TH>
                  <TH className="w-[80px]">Shape</TH>
                  <TH className="w-[90px] text-right">Length</TH>
                  <TH className="w-[200px]">Status</TH>
                  <TH className="w-[130px]">Changed</TH>
                  <TH className="w-[300px] text-right">Actions</TH>
                </TR>
              </THead>
              <TBody>
                {filtered.map((row) => (
                  <TR key={row.id}>
                    <TD>
                      <div className="flex items-center gap-3">
                        <ProjectThumb key={row.id} projectId={row.id} sources={["06_images/scene_01.png", "06_images/scene_01.jpg", "06_images/scene_01.webp"]} className="w-20" />
                        <div className="min-w-0">
                          <Link to={editorPath(row.id)} className="line-clamp-2 font-medium text-ink hover:text-accent-text hover:underline">
                            {row.name}
                          </Link>
                          <div className="text-xs text-ink-faint">{row.language}</div>
                        </div>
                      </div>
                    </TD>
                    <TD>
                      <Badge tone="neutral">{FORMAT_SHORT_LABELS[row.format]}</Badge>
                    </TD>
                    <TD className="text-right tabular-nums text-ink-muted">{row.duration_s > 0 ? formatClock(row.duration_s) : "-"}</TD>
                    <TD>
                      <div className="flex flex-col items-start gap-1">
                        <Badge tone={STATUS_TONE[row.status]} dot>
                          {STATUS_LABELS[row.status]}
                        </Badge>
                        {row.status_note ? (
                          <span className="line-clamp-2 text-xs text-ink-faint" title={row.status_note}>
                            {row.status_note}
                          </span>
                        ) : null}
                      </div>
                    </TD>
                    <TD className="text-ink-muted" title={formatDateTime(row.updated_at)}>
                      {formatRelative(row.updated_at)}
                    </TD>
                    <TD className="text-right">
                      <div className="inline-flex flex-wrap items-center justify-end gap-1">
                        <LinkButton to={editorPath(row.id)} variant="primary" size="sm" icon={<Clapperboard />}>
                          Open
                        </LinkButton>
                        <Button variant="ghost" size="sm" icon={<Pencil />} onClick={() => startRename(row)}>
                          Rename
                        </Button>
                        <Button variant="ghost" size="sm" icon={<FolderOpen />} loading={openFolder.isPending && openFolder.variables?.id === row.id} onClick={() => onOpenFolder(row)}>
                          Open folder
                        </Button>
                        <Button variant="ghost" size="sm" icon={<Trash2 />} onClick={() => setDeleting(row)} aria-label={`Delete ${row.name}`}>
                          Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <CountLine shown={filtered.length} total={rows.length} noun="edit" />
          </>
        )}
      </Card>

      <NewEditDialog open={creating} onClose={() => setCreating(false)} />

      <Dialog
        open={renaming !== null}
        title="Rename the edit"
        description="The folder on disk is renamed too."
        onClose={() => setRenaming(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setRenaming(null)} disabled={patch.isPending}>
              Cancel
            </Button>
            <Button variant="primary" onClick={confirmRename} disabled={!newName.trim()} loading={patch.isPending}>
              Rename
            </Button>
          </>
        }
      >
        <Field label="Name" htmlFor="rename-edit">
          <Input
            id="rename-edit"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") confirmRename();
            }}
            data-autofocus
          />
        </Field>
      </Dialog>

      <ConfirmDialog
        open={deleting !== null}
        title={deleting ? `Delete "${deleting.name}"?` : ""}
        description="The edit's folder, with its files and renders, goes to the Recycle Bin. You can get it back from there."
        confirmLabel="Move to the Recycle Bin"
        cancelLabel="Keep it"
        tone="danger"
        loading={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
