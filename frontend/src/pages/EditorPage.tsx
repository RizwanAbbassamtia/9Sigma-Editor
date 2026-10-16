import { ArrowLeft, Clapperboard, FolderOpen, MessageSquare, RotateCcw, Subtitles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";

import { errorMessage } from "../api/client";
import { PROXY_FILE, projectMediaUrl, renderProgressOf, sceneImageFile, useApplyEdits, useEditPayload, useRefreshEdit, useRenderStatus, useSaveTimeline } from "../api/edit";
import { useRebuildMusicStem, useTimelineBundle } from "../api/preview";
import { useOpenProjectFolder, useProject } from "../api/projects";
import { useRenderSettings } from "../api/settings";
import { cutsFromTimeline, maxCutSeconds, sameCuts, timelineWithCuts, type CutTransition } from "../components/edit/cutTransitions";
import { formatClock, sceneIndexAt, transitionLabel } from "../components/edit/editUtils";
import { MusicSelector } from "../components/edit/MusicSelector";
import { SceneStrip, type SceneStripItem } from "../components/edit/SceneStrip";
import { CaptionsPanel } from "../components/editor/CaptionsPanel";
import { RenderPanel } from "../components/editor/RenderPanel";
import { ScenesPanel } from "../components/editor/ScenesPanel";
import { ChecksPanel, DetailsPanel, ExportPanel } from "../components/editor/SidePanels";
import { TimingPanel } from "../components/editor/TimingPanel";
import { bundleFromTimeline, withCuts, withOverlays } from "../components/preview/bundle";
import { LivePreview, type LivePreviewHandle } from "../components/preview/LivePreview";
import { Panel } from "../components/script/Panel";
import { useTransitionCatalogue } from "../components/storyboard/transitionCatalogue";
import { TransitionPicker } from "../components/storyboard/TransitionPicker";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { PageHeader } from "../components/ui/PageHeader";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../components/ui/States";
import { useToast } from "../components/ui/Toast";
import { FORMAT_LABELS } from "../types/project";
import type { EditChoices } from "../types/timeline";

const SAVE_DELAY_MS = 600;

function DbField({ label, value, min, max, disabled, onCommit }: { label: string; value: number; min: number; max: number; disabled: boolean; onCommit: (value: number) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <label className="flex flex-col gap-1">
      <span className="flex items-center justify-between text-[13px] font-medium text-ink-muted">
        {label}
        <span className="tabular-nums text-ink">{draft > 0 ? `+${draft}` : draft} dB</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={draft}
        disabled={disabled}
        className="accent-accent"
        onChange={(event) => setDraft(Number(event.target.value))}
        onPointerUp={() => {
          if (draft !== value) onCommit(draft);
        }}
        onKeyUp={() => {
          if (draft !== value) onCommit(draft);
        }}
      />
    </label>
  );
}

/** `/edit/:id`: the editor (docs/CONTRACT-v0.1.md section 4). */
export function EditorPage() {
  const { id } = useParams();
  const projectId = id ?? "";
  const { toast } = useToast();
  const project = useProject(projectId);
  const { query, payload } = useEditPayload(projectId);
  const status = useRenderStatus(projectId, Boolean(projectId));
  const refresh = useRefreshEdit(projectId);
  const apply = useApplyEdits(projectId);
  const saveTimeline = useSaveTimeline(projectId);
  const openFolder = useOpenProjectFolder();
  const { resolved: renderSettings } = useRenderSettings();
  const catalogue = useTransitionCatalogue().data ?? null;

  const running = Boolean(status.data?.running);
  const progress = renderProgressOf(status.data);

  // A render that just ended: everything is read again (renders, checks, the preview).
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) refresh();
    wasRunning.current = running;
  }, [running, refresh]);

  // The monitor (live preview, or the last render) and its playhead, shared with the strip.
  const previewRef = useRef<LivePreviewHandle | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const bundleQuery = useTimelineBundle(projectId, payload !== null);
  const savedTimeline = payload?.timeline ?? null;
  const baseBundle = useMemo(() => {
    const fromServer = bundleQuery.data ?? null;
    if (fromServer) return fromServer;
    if (savedTimeline && (bundleQuery.isError || bundleQuery.isSuccess)) return bundleFromTimeline(projectId, savedTimeline);
    return null;
  }, [bundleQuery.data, bundleQuery.isError, bundleQuery.isSuccess, savedTimeline, projectId]);

  // The music stem and the loudness for the preview are made once per timeline.
  const { mutate: prepareMusic, isPending: preparingMusic } = useRebuildMusicStem(projectId);
  const musicAsked = useRef<string | null>(null);
  useEffect(() => {
    const bundle = bundleQuery.data;
    if (!bundle || running) return;
    const needsStem = Boolean(bundle.timeline.music.path) && !bundle.media.stem_url;
    const hasSound = Boolean(bundle.media.voice_url || bundle.timeline.music.path);
    const needsLoudness = bundle.preview_gain_db === null || (hasSound && bundle.preview_gain_curve === null);
    if (!needsStem && !needsLoudness) return;
    const key = `${bundle.revision}|${bundle.timeline.music.path ?? ""}`;
    if (musicAsked.current === key) return;
    musicAsked.current = key;
    prepareMusic();
  }, [bundleQuery.data, running, prepareMusic]);

  // The cuts: edited here, saved as the whole timeline shortly after the last change.
  const savedCuts = useMemo(() => cutsFromTimeline(savedTimeline), [savedTimeline]);
  const [cuts, setCuts] = useState<CutTransition[]>([]);
  const savedKey = useMemo(() => JSON.stringify(savedCuts), [savedCuts]);
  const lastSavedKey = useRef("");
  useEffect(() => {
    if (savedKey !== lastSavedKey.current) {
      lastSavedKey.current = savedKey;
      setCuts(savedCuts);
    }
  }, [savedCuts, savedKey]);
  const cutsDirty = !sameCuts(cuts, savedCuts);
  const saveTimer = useRef<number | null>(null);
  useEffect(() => {
    if (!cutsDirty || !savedTimeline || saveTimeline.isPending) return;
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = null;
      saveTimeline.mutate(
        { timeline: timelineWithCuts(savedTimeline, cuts), revision: savedTimeline.revision },
        { onError: (error) => toast({ tone: "error", title: "The transition was not saved", description: errorMessage(error) }) },
      );
    }, SAVE_DELAY_MS);
    return () => {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    };
  }, [cuts, cutsDirty, savedTimeline, saveTimeline, toast]);

  const liveBundle = useMemo(() => {
    if (!baseBundle || !payload) return baseBundle;
    const cutsFit = cuts.length === baseBundle.timeline.scenes.length;
    const withTransitions = cutsFit ? withCuts(baseBundle, cuts) : baseBundle;
    return withOverlays(withTransitions, { popups: payload.popups_enabled, captions: payload.captions_enabled });
  }, [baseBundle, cuts, payload]);

  // After a transition change the preview plays once around that cut.
  const [aroundCut, setAroundCut] = useState<number | null>(null);
  useEffect(() => {
    if (aroundCut === null || !liveBundle) return;
    const scenes = liveBundle.timeline.scenes;
    const next = scenes[aroundCut + 1];
    const at = next ? next.start_s : liveBundle.timeline.duration_s;
    setAroundCut(null);
    previewRef.current?.playAround(at, 2, 2);
  }, [aroundCut, liveBundle]);

  const stripThumbs = useMemo(() => new Map((bundleQuery.data?.pictures ?? []).filter((picture) => picture.thumb_url).map((picture) => [picture.scene, picture.thumb_url!] as const)), [bundleQuery.data]);
  const stripScenes = useMemo<SceneStripItem[]>(
    () =>
      (payload?.scenes ?? []).map((scene) => ({
        index: scene.index,
        start_s: scene.start_s,
        end_s: scene.end_s,
        thumbUrl: stripThumbs.get(scene.index) ?? projectMediaUrl(projectId, scene.image_path ?? sceneImageFile(scene.index), scene.image_url),
        transition: scene.transition,
        transitionName: scene.transition ? transitionLabel(scene.transition, catalogue) : undefined,
        popupText: scene.popup_text,
        locked: scene.locked,
      })),
    [payload, projectId, catalogue, stripThumbs],
  );
  const onPreviewTime = useCallback(
    (seconds: number) => setCurrentTime((previous) => (sceneIndexAt(stripScenes, previous) === sceneIndexAt(stripScenes, seconds) ? previous : seconds)),
    [stripScenes],
  );
  const seekTo = (seconds: number) => {
    const target = Math.max(0, seconds + 0.05);
    previewRef.current?.seek(target, { play: true });
    setCurrentTime(target);
  };

  const choose = (edits: EditChoices, what: string) =>
    apply.mutate(edits, { onError: (error) => toast({ tone: "error", title: `Could not change ${what}`, description: errorMessage(error) }) });

  if (project.isPending || query.isPending) {
    return <LoadingBlock label="Opening the edit..." />;
  }

  if (project.isError) {
    return <ErrorState error={project.error} title="Could not open this edit" onRetry={() => void project.refetch()} />;
  }

  if (query.isError && !payload) {
    const missing = query.error instanceof Error && /no timeline/i.test(query.error.message);
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={project.data.name} description={FORMAT_LABELS[project.data.format]} actions={<LinkButton to="/" variant="secondary" size="sm" icon={<ArrowLeft />}>My edits</LinkButton>} />
        {missing ? (
          <EmptyState
            icon={Clapperboard}
            title="This edit has no timeline yet"
            description={project.data.status_note || "The timeline is built from the pictures, clips and narration. Try building it again, or make a new edit from your files."}
            action={
              <Button variant="primary" size="sm" icon={<RotateCcw />} loading={apply.isPending} onClick={() => choose({ rebuild: true }, "the timeline")}>
                Build the timeline
              </Button>
            }
          />
        ) : (
          <ErrorState error={query.error} title="Could not load the timeline" onRetry={() => void query.refetch()} />
        )}
      </div>
    );
  }

  if (!payload) {
    return <LoadingBlock label="Loading the timeline..." />;
  }

  const summary = payload.summary;
  const edit = project.data;
  const busy = apply.isPending || saveTimeline.isPending;
  const locked = running;
  const proxySrc = projectMediaUrl(projectId, payload.proxy_path ?? PROXY_FILE, payload.proxy_url);
  const proxySize = payload.timeline ? `${payload.timeline.proxy.width} x ${payload.timeline.proxy.height}` : "preview";
  const enable4k = payload.enable_4k ?? renderSettings.render.enable_4k;
  const musicTrack = payload.music_path ? payload.music_tracks.find((track) => track.path === payload.music_path) : undefined;
  const musicLicenseOk = payload.music_license_ok || (musicTrack?.license_ok ?? false);

  const patchCut = (index: number, cut: CutTransition) => {
    setCuts((current) => current.map((item, i) => (i === index ? cut : item)));
    setAroundCut(index);
  };

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={edit.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{FORMAT_LABELS[edit.format]}</Badge>
            <Badge tone="neutral">
              {summary.scene_count} scene{summary.scene_count === 1 ? "" : "s"}
            </Badge>
            <Badge tone="neutral">{formatClock(summary.duration_s)}</Badge>
            {summary.width && summary.height ? (
              <Badge tone="neutral">
                {summary.width} x {summary.height}
              </Badge>
            ) : null}
            {running ? <Badge tone="accent" dot>Rendering</Badge> : null}
            {cutsDirty || saveTimeline.isPending ? <Badge tone="warn">Saving...</Badge> : null}
          </span>
        }
        actions={
          <>
            <LinkButton to="/" variant="ghost" size="sm" icon={<ArrowLeft />}>
              My edits
            </LinkButton>
            <Button
              variant="secondary"
              size="sm"
              icon={<FolderOpen />}
              loading={openFolder.isPending}
              onClick={() => openFolder.mutate({ id: projectId }, { onError: (error) => toast({ tone: "error", title: "Could not open the folder", description: errorMessage(error) }) })}
            >
              Open folder
            </Button>
          </>
        }
      />

      {running ? (
        <Notice tone="info" title={progress?.message || "Rendering..."}>
          The edit is locked while the render runs. Stop it in the Render panel to change something.
        </Notice>
      ) : null}

      <Panel title="Preview">
        <div className="flex flex-col gap-3">
          <LivePreview
            projectId={projectId}
            controllerRef={previewRef}
            bundle={liveBundle}
            loading={!liveBundle && bundleQuery.isPending}
            unavailable="The live preview appears once the timeline is built."
            aspect={summary.aspect ?? payload.timeline?.aspect ?? "16:9"}
            proxySrc={proxySrc}
            proxyNote={`Low-resolution render (${proxySize}). The final sizes are sharper.`}
            activity={preparingMusic ? "Preparing the music..." : null}
            onTimeChange={onPreviewTime}
          />
          <SceneStrip
            scenes={stripScenes}
            currentTime={currentTime}
            aspect={summary.aspect}
            onSeek={(scene) => seekTo(scene.start_s)}
            renderCut={
              payload.timeline && cuts.length === stripScenes.length
                ? (_scene, position) => {
                    const cut = cuts[position]!;
                    const last = position === stripScenes.length - 1;
                    return (
                      <TransitionPicker
                        variant="compact"
                        value={cut}
                        onChange={(next) => patchCut(position, next)}
                        format={payload.timeline?.format ?? "long"}
                        channelPack={renderSettings.render.transition_pack}
                        label={last ? "Ending transition" : `Transition from scene ${position + 1} to ${position + 2}`}
                        maxDurationS={maxCutSeconds(payload.timeline, position)}
                        changed={!sameCuts([cut], [savedCuts[position] ?? cut])}
                        disabled={locked || busy}
                      />
                    );
                  }
                : undefined
            }
          />
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-4">
          <ScenesPanel projectId={projectId} scenes={payload.scenes} thumbs={stripThumbs} disabled={locked} onSeek={(scene) => seekTo(scene.start_s)} />

          <Panel title="Timeline" description="Popups, captions and music for the whole video.">
            <div className="flex flex-col gap-5">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex items-start gap-3 rounded-md border border-line px-3 py-2.5">
                  <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={payload.popups_enabled} disabled={locked || busy} onChange={(event) => choose({ popups_enabled: event.target.checked }, "the popups")} />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
                      <MessageSquare className="size-3.5 text-ink-faint" aria-hidden />
                      Show popups
                    </span>
                    <span className="block text-xs text-ink-muted">{summary.popup_count > 0 ? `${summary.popup_count} short text pops.` : "No popups: add popup text to a scene when you make an edit."}</span>
                  </span>
                </label>
                <label className="flex items-start gap-3 rounded-md border border-line px-3 py-2.5">
                  <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={payload.captions_enabled} disabled={locked || busy} onChange={(event) => choose({ captions_enabled: event.target.checked }, "the captions")} />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
                      <Subtitles className="size-3.5 text-ink-faint" aria-hidden />
                      Burn in captions
                    </span>
                    <span className="block text-xs text-ink-muted">{summary.caption_cues > 0 ? `${summary.caption_cues} captions timed to the narration.` : "Captions at the bottom of the picture."}</span>
                  </span>
                </label>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-ink-muted">Background music</span>
                <MusicSelector
                  tracks={payload.music_tracks}
                  value={payload.music_path}
                  licenseOk={musicLicenseOk}
                  onChange={(path) => choose({ music_path: path ?? "" }, "the music")}
                  folder={payload.music_folder}
                  disabled={locked || busy}
                />
              </div>

              {payload.timeline?.music.path ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <DbField label="Music volume" value={Math.round(payload.timeline.music.gain_db)} min={-30} max={12} disabled={locked || busy} onCommit={(value) => choose({ music_gain_db: value }, "the music volume")} />
                  <DbField label="Dip under the voice" value={Math.round(payload.timeline.music.duck_db)} min={-30} max={0} disabled={locked || busy} onCommit={(value) => choose({ music_duck_db: value }, "the music dip")} />
                </div>
              ) : null}

              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
                <p className="text-xs text-ink-faint">Scenes, transitions and the timing you set are kept when the timeline is built again.</p>
                <Button variant="ghost" size="sm" icon={<RotateCcw />} disabled={locked || busy} onClick={() => choose({ rebuild: true }, "the timeline")}>
                  Build the timeline again
                </Button>
              </div>
            </div>
          </Panel>

          <RenderPanel projectId={projectId} payload={payload} running={running} progress={progress} enable4k={enable4k} busy={busy} />
          <CaptionsPanel projectId={projectId} disabled={locked} duration={summary.duration_s} />
          <TimingPanel projectId={projectId} disabled={locked} onSeek={seekTo} />
        </div>

        <div className="flex flex-col gap-4 xl:sticky xl:top-16 xl:self-start">
          <ChecksPanel payload={payload} />
          <ExportPanel project={edit} payload={payload} disabled={locked || busy} />
          <DetailsPanel project={edit} disabled={locked} />
        </div>
      </div>
    </div>
  );
}
