import { ArrowDown, ArrowUp, ImageIcon, ImagePlus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { errorMessage } from "../../api/client";
import { projectMediaUrl, sceneImageFile, useAddScene, useRemoveScene, useReorderScenes, useReplaceScenePicture } from "../../api/edit";
import { MOTION_LABELS, type MotionPreset } from "../../types/storyboard";
import type { EditSceneView } from "../../types/timeline";
import { formatClock } from "../edit/editUtils";
import { MEDIA_ACCEPT } from "../edits/NewEditDialog";
import { FilePickButton } from "../form/FilePickButton";
import { Panel } from "../script/Panel";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/Dialog";
import { useToast } from "../ui/Toast";

export interface ScenesPanelProps {
  projectId: string;
  scenes: EditSceneView[];
  /** small pictures from the preview bundle, by scene index */
  thumbs: ReadonlyMap<number, string>;
  disabled: boolean;
  onSeek: (scene: EditSceneView) => void;
}

function motionLabel(preset: string | null): string {
  if (!preset) return "";
  return (MOTION_LABELS as Record<string, string>)[preset as MotionPreset] ?? preset.replace(/_/g, " ");
}

/**
 * The scenes of the edit in order: add a picture or clip, replace one, move one up or down,
 * remove one. Every change goes to the server at once and the timeline is built again.
 */
export function ScenesPanel({ projectId, scenes, thumbs, disabled, onSeek }: ScenesPanelProps) {
  const { toast } = useToast();
  const add = useAddScene(projectId);
  const replace = useReplaceScenePicture(projectId);
  const reorder = useReorderScenes(projectId);
  const remove = useRemoveScene(projectId);
  const [removing, setRemoving] = useState<EditSceneView | null>(null);
  const busy = add.isPending || replace.isPending || reorder.isPending || remove.isPending;

  const report = (title: string) => ({
    onSuccess: (result: { warnings: string[] }) => {
      if (result.warnings.length > 0) toast({ tone: "info", title, description: result.warnings.join(" ") });
    },
    onError: (error: unknown) => toast({ tone: "error", title: `Could not ${title.toLowerCase()}`, description: errorMessage(error) }),
  });

  const move = (position: number, step: -1 | 1) => {
    const order = scenes.map((_scene, index) => index);
    const target = position + step;
    if (target < 0 || target >= order.length) return;
    [order[position], order[target]] = [order[target]!, order[position]!];
    reorder.mutate(order, report("Move the scene"));
  };

  return (
    <Panel
      title="Scenes"
      description="One picture or clip per scene, in the order of the video. The narration timing decides how long each one shows."
      actions={
        <FilePickButton accept={MEDIA_ACCEPT} size="sm" variant="primary" icon={<ImagePlus />} disabled={disabled || busy} loading={add.isPending} onPick={(file) => add.mutate({ file }, report("Add the scene"))}>
          Add picture or clip
        </FilePickButton>
      }
    >
      {scenes.length === 0 ? (
        <p className="text-[13px] text-ink-muted">No scenes yet. Add a picture or a clip.</p>
      ) : (
        <ol className="flex flex-col gap-2">
          {scenes.map((scene, position) => {
            const thumb = thumbs.get(scene.index) ?? projectMediaUrl(projectId, scene.image_path ?? sceneImageFile(scene.index), scene.image_url);
            return (
              <li key={`${scene.index}-${scene.start_s}`} className="flex items-center gap-3 rounded-md border border-line bg-surface px-3 py-2">
                <button type="button" onClick={() => onSeek(scene)} className="relative h-12 w-20 shrink-0 overflow-hidden rounded bg-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60" title="Play from this scene">
                  {thumb ? (
                    <img src={thumb} alt="" className="size-full object-cover" loading="lazy" />
                  ) : (
                    <span className="flex size-full items-center justify-center text-ink-faint">
                      <ImageIcon className="size-4" aria-hidden />
                    </span>
                  )}
                  <span className="absolute left-1 top-1 rounded bg-navy/70 px-1 text-[10px] font-semibold text-white">{position + 1}</span>
                </button>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] text-ink" title={scene.image_path ?? ""}>
                    {scene.image_path ? scene.image_path.split("/").at(-1) : `Scene ${position + 1}`}
                  </p>
                  <p className="text-[11px] tabular-nums text-ink-muted">
                    {formatClock(scene.start_s)} to {formatClock(scene.end_s)} ({scene.duration_s.toFixed(1)} s)
                    {scene.motion ? <span className="ml-2 text-ink-faint">{motionLabel(scene.motion)}</span> : null}
                    {scene.popup_text ? <span className="ml-2 text-ink-faint">Popup: {scene.popup_text}</span> : null}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <FilePickButton accept={MEDIA_ACCEPT} size="sm" variant="ghost" icon={<RefreshCw />} disabled={disabled || busy} title="Replace this picture or clip" onPick={(file) => replace.mutate({ index: position, file }, report("Replace the picture"))}>
                    Replace
                  </FilePickButton>
                  <Button variant="ghost" size="sm" icon={<ArrowUp />} aria-label="Move up" disabled={disabled || busy || position === 0} onClick={() => move(position, -1)} />
                  <Button variant="ghost" size="sm" icon={<ArrowDown />} aria-label="Move down" disabled={disabled || busy || position === scenes.length - 1} onClick={() => move(position, 1)} />
                  <Button variant="ghost" size="sm" icon={<Trash2 />} aria-label={`Remove scene ${position + 1}`} disabled={disabled || busy || scenes.length === 1} onClick={() => setRemoving(scene)} />
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <ConfirmDialog
        open={removing !== null}
        title={removing ? `Remove scene ${scenes.indexOf(removing) + 1}?` : ""}
        description="The scene leaves the video; its picture stays in the edit's folder. The narration keeps its timing."
        confirmLabel="Remove the scene"
        cancelLabel="Keep it"
        tone="danger"
        loading={remove.isPending}
        onConfirm={() => {
          if (!removing) return;
          remove.mutate(scenes.indexOf(removing), { ...report("Remove the scene"), onSettled: () => setRemoving(null) });
        }}
        onCancel={() => setRemoving(null)}
      />
    </Panel>
  );
}
