import { ArrowUpRight, Film, FolderOpen, Play, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { errorMessage } from "../api/client";
import { useOpenProjectFolder, useRenders, type RenderListRow } from "../api/projects";
import { ProjectThumb } from "../components/editing/ProjectThumb";
import { CountLine } from "../components/master/CountLine";
import { bytesText, durationText, matchesText } from "../components/master/format";
import { SearchBox } from "../components/master/SearchBox";
import { RenderPlayerDialog } from "../components/renders/RenderPlayerDialog";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Select } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState } from "../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../components/ui/Table";
import { useToast } from "../components/ui/Toast";
import { editorPath } from "../layout/nav";
import { formatDateTime, formatRelative } from "../lib/format";
import { FORMAT_SHORT_LABELS } from "../types/project";
import { RENDER_PRESETS, renderPresetLabel } from "../types/timeline";

function presetOrder(preset: string): number {
  const index = (RENDER_PRESETS as readonly string[]).indexOf(preset.split(" ")[0] ?? preset);
  return index === -1 ? RENDER_PRESETS.length : index;
}

/** `/renders`: every finished render of every edit, with its size, length and folder. */
export function RendersPage() {
  const { toast } = useToast();
  const renders = useRenders();
  const openFolder = useOpenProjectFolder();
  const [search, setSearch] = useState("");
  const [preset, setPreset] = useState("");
  const [playing, setPlaying] = useState<RenderListRow | null>(null);

  const rows = renders.data ?? [];
  const presets = useMemo(() => [...new Set(rows.map((row) => row.preset))].sort((a, b) => presetOrder(a) - presetOrder(b)), [rows]);
  const filtered = useMemo(() => rows.filter((row) => (!preset || row.preset === preset) && matchesText(search, row.name, row.preset, row.quality)), [rows, preset, search]);

  const onOpenFolder = (row: RenderListRow) =>
    openFolder.mutate(
      { id: row.project_id },
      { onError: (error) => toast({ tone: "error", title: "Could not open the folder", description: errorMessage(error) }) },
    );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Renders" description="Every finished video file across your edits: its size, length and where it is. The low-resolution previews are not listed." />

      {rows.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <SearchBox value={search} onChange={setSearch} placeholder="Search renders" className="w-64" />
          <Select wrapperClassName="w-44" value={preset} onChange={(event) => setPreset(event.target.value)} aria-label="Filter by size">
            <option value="">All sizes</option>
            {presets.map((id) => (
              <option key={id} value={id}>
                {renderPresetLabel(id)}
              </option>
            ))}
          </Select>
        </div>
      ) : null}

      <Card flush>
        {renders.isPending ? (
          <LoadingBlock label="Reading the renders..." />
        ) : renders.isError ? (
          <ErrorState error={renders.error} title="Could not read the renders" onRetry={() => void renders.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Film}
            title="No finished renders yet"
            description="Renders appear here once a size (720p, 1080p or 4K) is rendered in the editor."
            action={
              <LinkButton to="/" variant="primary" size="sm">
                Go to My edits
              </LinkButton>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState icon={Search} title="No renders match" description="Clear the search or pick another size." />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="min-w-[260px]">Edit</TH>
                  <TH className="w-[150px]">Size</TH>
                  <TH className="w-[90px]">Quality</TH>
                  <TH className="w-[90px] whitespace-nowrap text-right">Length</TH>
                  <TH className="w-[100px] whitespace-nowrap text-right">File size</TH>
                  <TH className="w-[130px]">Rendered</TH>
                  <TH className="w-[100px]">Exported</TH>
                  <TH className="w-[280px] text-right">Actions</TH>
                </TR>
              </THead>
              <TBody>
                {filtered.map((row) => (
                  <TR key={`${row.project_id}:${row.preset}`}>
                    <TD>
                      <div className="flex items-center gap-3">
                        <ProjectThumb key={row.project_id} projectId={row.project_id} sources={["06_images/scene_01.png", "06_images/scene_01.jpg", "06_images/scene_01.webp"]} className="w-20" />
                        <div className="min-w-0">
                          <Link to={editorPath(row.project_id)} className="line-clamp-2 font-medium text-ink hover:text-accent-text hover:underline">
                            {row.name}
                          </Link>
                          <div className="text-xs text-ink-faint">{FORMAT_SHORT_LABELS[row.format]}</div>
                        </div>
                      </div>
                    </TD>
                    <TD>
                      <div className="flex flex-col items-start gap-0.5">
                        <Badge tone={row.preset.startsWith("2160p") ? "accent" : "neutral"}>{renderPresetLabel(row.preset)}</Badge>
                        {row.width && row.height ? (
                          <span className="text-[11px] tabular-nums text-ink-faint">
                            {row.width} x {row.height}
                          </span>
                        ) : null}
                      </div>
                    </TD>
                    <TD className="text-ink-muted">{row.quality}</TD>
                    <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{durationText(row.duration_s)}</TD>
                    <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{bytesText(row.size_bytes)}</TD>
                    <TD className="text-ink-muted" title={formatDateTime(row.rendered_at)}>
                      {row.rendered_at ? formatRelative(row.rendered_at) : "-"}
                    </TD>
                    <TD>{row.exported ? <Badge tone="ok">Exported</Badge> : <span className="text-xs text-ink-faint">Not yet</span>}</TD>
                    <TD className="text-right">
                      <div className="inline-flex flex-wrap items-center justify-end gap-1">
                        <Button variant="ghost" size="sm" icon={<Play />} onClick={() => setPlaying(row)} disabled={!row.url}>
                          Watch
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={<FolderOpen />}
                          loading={openFolder.isPending && openFolder.variables?.id === row.project_id}
                          onClick={() => onOpenFolder(row)}
                          title={`Opens the edit's folder; the file is at ${row.path}`}
                        >
                          Open folder
                        </Button>
                        <LinkButton to={editorPath(row.project_id)} variant="ghost" size="sm" icon={<ArrowUpRight />}>
                          Editor
                        </LinkButton>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <CountLine shown={filtered.length} total={rows.length} noun="render" />
          </>
        )}
      </Card>

      <RenderPlayerDialog render={playing} onClose={() => setPlaying(null)} />
    </div>
  );
}
