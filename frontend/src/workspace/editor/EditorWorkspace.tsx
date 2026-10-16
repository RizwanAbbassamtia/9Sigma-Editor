import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router";

import { useMe, useSessionEnded } from "../../api/auth";
import { ApiError, errorMessage } from "../../api/client";
import { useAddScene, useApplyEdits, useEditPayload, useNudgeTiming, useRefreshEdit, useRemoveScene, useReorderScenes, useReplaceScenePicture, useRenderStatus, useSaveTimeline } from "../../api/edit";
import { queryKeys } from "../../api/keys";
import { getMusicEnvelope, usePeaks, useRebuildMusicStem, useTimelineBundle } from "../../api/preview";
import { useProject } from "../../api/projects";
import { useRenderSettings } from "../../api/settings";
import type { LivePreviewHandle } from "../../components/preview/LivePreview";
import { createTimeStore } from "../../components/preview/timeStore";
import { useTransitionCatalogue } from "../../components/storyboard/transitionCatalogue";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/Dialog";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState } from "../../components/ui/States";
import { useToast } from "../../components/ui/Toast";
import type { BundlePicture } from "../../types/preview";
import type { EditChoices, Timeline } from "../../types/timeline";
import { WorkspaceShell } from "../WorkspaceShell";
import { EditorContext, type EditorActions, type EditorState, type SaveState } from "./context";
import { History, NO_SELECTION, moveSceneEdge, sentenceStarts, snapTo, wordBoundaries, type Selection } from "./document";
import { Inspector } from "./Inspector";
import { Library } from "./Library";
import { Monitor } from "./Monitor";
import { Timeline as TimelineLanes } from "./Timeline";
import { TopBar } from "./TopBar";

const SAVE_DELAY_MS = 600;

function nowClock(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.matches("input, textarea, select, [contenteditable=true]");
}

/** `/edit/:id`: the Edit room (docs/design/EDITOR-DESIGN.md section 4). */
export function EditorWorkspace() {
  const { id } = useParams();
  const projectId = id ?? "";
  const me = useMe();
  const ended = useSessionEnded();
  const navigate = useNavigate();
  useEffect(() => {
    if (ended) navigate("/login?ended=1", { replace: true });
  }, [ended, navigate]);
  if (me.isPending) return <LoadingBlock label="Opening the editor..." />;
  if (me.isError || me.data === null) return <Navigate to="/login" replace state={{ from: `/edit/${projectId}` }} />;
  return <EditorLoaded projectId={projectId} />;
}

function EditorLoaded({ projectId }: { projectId: string }) {
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const project = useProject(projectId);
  const { query, payload } = useEditPayload(projectId);
  const bundleQuery = useTimelineBundle(projectId, payload !== null);
  const voicePeaks = usePeaks(projectId, "voice", payload !== null);
  const musicPeaks = usePeaks(projectId, "music", Boolean(payload?.timeline?.music.path));
  const catalogue = useTransitionCatalogue().data ?? null;
  const { resolved: renderSettings } = useRenderSettings();
  const status = useRenderStatus(projectId, true);
  const refresh = useRefreshEdit(projectId);
  const save = useSaveTimeline(projectId);
  const apply = useApplyEdits(projectId);
  const replacePictureMutation = useReplaceScenePicture(projectId);
  const addSceneMutation = useAddScene(projectId);
  const reorderMutation = useReorderScenes(projectId);
  const removeSceneMutation = useRemoveScene(projectId);
  const nudge = useNudgeTiming(projectId);
  const { mutate: prepareMusic, isPending: preparingMusic } = useRebuildMusicStem(projectId);

  const envelopeUrl = bundleQuery.data?.media.envelope_url ?? null;
  const envelope = useQuery({
    queryKey: [...queryKeys.project(projectId), "preview", "envelope", envelopeUrl],
    queryFn: () => getMusicEnvelope(envelopeUrl as string),
    enabled: Boolean(envelopeUrl),
    staleTime: 60_000,
  });

  // ---- the document ------------------------------------------------------------------------------
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [serverRevision, setServerRevision] = useState(0);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "clean" });
  const [conflict, setConflict] = useState(false);
  const history = useRef(new History());
  const dirty = useRef(false);
  const timer = useRef<number | null>(null);
  const [historyTick, setHistoryTick] = useState(0);

  // The server's timeline replaces the local one when it is newer and nothing is unsaved.
  useEffect(() => {
    const fresh = payload?.timeline;
    if (!fresh) return;
    if (timeline === null || (fresh.revision > serverRevision && !dirty.current)) {
      setTimeline(fresh);
      setServerRevision(fresh.revision);
      if (timeline !== null) history.current.clear();
    } else if (fresh.revision > serverRevision && dirty.current) {
      setConflict(true);
    }
  }, [payload?.timeline, serverRevision, timeline]);

  const flushSave = useCallback(async (doc: Timeline, revision: number) => {
    setSaveState({ kind: "saving" });
    try {
      const saved = await save.mutateAsync({ timeline: doc, revision });
      dirty.current = false;
      setServerRevision(saved.revision);
      setTimeline((current) => (current ? { ...current, revision: saved.revision } : current));
      setSaveState({ kind: "saved", at: nowClock() });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setSaveState({ kind: "conflict" });
        setConflict(true);
      } else {
        setSaveState({ kind: "retrying", error: errorMessage(error) });
        timer.current = window.setTimeout(() => void flushSave(doc, revision), 4000);
      }
    }
  }, [save]);

  const scheduleSave = useCallback((doc: Timeline, revision: number) => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      void flushSave(doc, revision);
    }, SAVE_DELAY_MS);
  }, [flushSave]);

  const locked = Boolean(status.data?.running);

  const edit = useCallback((label: string, next: (doc: Timeline) => Timeline): boolean => {
    if (!timeline) return false;
    if (locked) {
      toast({ tone: "info", title: "A render is running", description: "Stop it in the Render menu to change the edit." });
      return false;
    }
    const after = next(timeline);
    if (after === timeline) return false;
    history.current.push(timeline, label);
    setHistoryTick((v) => v + 1);
    dirty.current = true;
    setTimeline(after);
    scheduleSave(after, serverRevision);
    return true;
  }, [timeline, locked, toast, scheduleSave, serverRevision]);

  const undo = useCallback(() => {
    if (!timeline) return;
    const entry = history.current.undo(timeline);
    if (!entry) return;
    setHistoryTick((v) => v + 1);
    dirty.current = true;
    setTimeline(entry.timeline);
    scheduleSave(entry.timeline, serverRevision);
    toast({ tone: "info", title: `Undid: ${entry.label}` });
  }, [timeline, scheduleSave, serverRevision, toast]);

  const redo = useCallback(() => {
    if (!timeline) return;
    const entry = history.current.redo(timeline);
    if (!entry) return;
    setHistoryTick((v) => v + 1);
    dirty.current = true;
    setTimeline(entry.timeline);
    scheduleSave(entry.timeline, serverRevision);
  }, [timeline, scheduleSave, serverRevision]);

  /** Server-side changes wait for the unsaved edit to land first, then reload everything. */
  const serverChange = useCallback(async (work: () => Promise<unknown>, done: string) => {
    if (locked) {
      toast({ tone: "info", title: "A render is running", description: "Stop it in the Render menu to change the edit." });
      return;
    }
    try {
      if (timer.current !== null && timeline) {
        window.clearTimeout(timer.current);
        timer.current = null;
        await flushSave(timeline, serverRevision);
      }
      await work();
      dirty.current = false;
      history.current.clear();
      setHistoryTick((v) => v + 1);
      refresh();
      if (done) toast({ tone: "success", title: done });
    } catch (error) {
      toast({ tone: "error", title: "That change did not work", description: errorMessage(error) });
    }
  }, [locked, toast, timeline, flushSave, serverRevision, refresh]);

  // ---- the playhead and the preview ------------------------------------------------------------
  const timeStore = useMemo(() => createTimeStore(0), []);
  const preview = useRef<LivePreviewHandle | null>(null);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    const tick = window.setInterval(() => setPlaying(Boolean(preview.current?.playing())), 200);
    return () => window.clearInterval(tick);
  }, []);
  const [selection, setSelection] = useState<Selection>(NO_SELECTION);
  const [range, setRange] = useState<{ in_s: number | null; out_s: number | null }>({ in_s: null, out_s: null });
  const [loop, setLoop] = useState(false);
  const [snapping, setSnapping] = useState(true);
  const [linkPopups, setLinkPopups] = useState(true);
  const [followPlayhead, setFollowPlayhead] = useState(true);

  // the music stem and the loudness for the preview, made once per timeline
  const musicAsked = useRef<string | null>(null);
  useEffect(() => {
    const bundle = bundleQuery.data;
    if (!bundle || locked) return;
    const needsStem = Boolean(bundle.timeline.music.path) && !bundle.media.stem_url;
    const hasSound = Boolean(bundle.media.voice_url || bundle.timeline.music.path);
    const needsLoudness = bundle.preview_gain_db === null || (hasSound && bundle.preview_gain_curve === null);
    if (!needsStem && !needsLoudness) return;
    const key = `${bundle.revision}|${bundle.timeline.music.path ?? ""}`;
    if (musicAsked.current === key) return;
    musicAsked.current = key;
    prepareMusic();
  }, [bundleQuery.data, locked, prepareMusic]);

  // a render that just ended: everything is read again
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !locked) refresh();
    wasRunning.current = locked;
  }, [locked, refresh]);

  const sentences = bundleQuery.data?.sentences ?? [];
  const pictures = useMemo(() => new Map<number, BundlePicture>((bundleQuery.data?.pictures ?? []).map((picture) => [picture.scene, picture])), [bundleQuery.data]);

  const seek = useCallback((seconds: number, play?: boolean) => {
    preview.current?.seek(Math.max(0, seconds), play ? { play: true } : undefined);
    timeStore.set(Math.max(0, seconds));
  }, [timeStore]);

  const actions = useMemo<EditorActions>(() => ({
    select: setSelection,
    edit,
    undo,
    redo,
    seek,
    playPause: () => preview.current?.toggle(),
    stepFrames: (frames) => seek(timeStore.get() + frames / (timeline?.fps ?? 30)),
    playAround: (seconds) => preview.current?.playAround(seconds, 2, 2),
    markIn: () => setRange((current) => ({ in_s: timeStore.get(), out_s: current.out_s !== null && current.out_s > timeStore.get() ? current.out_s : null })),
    markOut: () => setRange((current) => ({ in_s: current.in_s !== null && current.in_s < timeStore.get() ? current.in_s : 0, out_s: timeStore.get() })),
    clearRange: () => setRange({ in_s: null, out_s: null }),
    toggleLoop: () => setLoop((v) => !v),
    toggleSnapping: () => setSnapping((v) => !v),
    toggleLinkPopups: () => setLinkPopups((v) => !v),
    toggleFollowPlayhead: () => setFollowPlayhead((v) => !v),
    preview,
    replacePicture: (index, file) => serverChange(() => replacePictureMutation.mutateAsync({ index, file }), `Scene ${index + 1} has its new picture`),
    addScene: (file) => serverChange(() => addSceneMutation.mutateAsync({ file }), "Scene added at the end"),
    reorderScenes: (order) => serverChange(() => reorderMutation.mutateAsync(order), "Scenes reordered; the narration moved with them"),
    removeScene: (index) => serverChange(() => removeSceneMutation.mutateAsync(index), `Scene ${index + 1} removed`),
    shortenPause: (sentenceIndex, shift_s) => serverChange(() => nudge.mutateAsync({ index: sentenceIndex, shift_s }), `Pause shortened by ${Math.abs(shift_s).toFixed(1)} s; everything after it moved earlier`),
    applyChoices: (choices: EditChoices) => serverChange(() => apply.mutateAsync(choices), ""),
    rebuild: () => serverChange(() => apply.mutateAsync({ rebuild: true }), "The timeline was built again"),
    notify: (tone, title, description) => toast({ tone, title, description }),
  }), [edit, undo, redo, seek, timeStore, timeline?.fps, serverChange, replacePictureMutation, addSceneMutation, reorderMutation, removeSceneMutation, nudge, apply, toast]);

  // ---- keyboard ----------------------------------------------------------------------------------
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event.target)) {
        if (event.key === "Escape") (event.target as HTMLElement).blur();
        return;
      }
      if (!timeline) return;
      const key = event.key;
      const ctrl = event.ctrlKey || event.metaKey;
      const t = timeStore.get();
      const fps = timeline.fps;
      const command = (name: string) => window.dispatchEvent(new CustomEvent("sge:timeline-command", { detail: { command: name } }));
      const zoom = (detail: { factor?: number; fit?: boolean }) => window.dispatchEvent(new CustomEvent("sge:timeline-zoom", { detail }));
      if (ctrl && key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (ctrl && key.toLowerCase() === "y") {
        event.preventDefault();
        redo();
        return;
      }
      if (ctrl && key.toLowerCase() === "e") {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent("sge:open-render-menu"));
        return;
      }
      if (ctrl && key.toLowerCase() === "l") {
        event.preventDefault();
        setLoop((v) => !v);
        return;
      }
      if (ctrl && (key.toLowerCase() === "b" || key.toLowerCase() === "k")) {
        event.preventDefault();
        command("split");
        return;
      }
      if (ctrl && key === "ArrowUp") {
        event.preventDefault();
        const starts = sentenceStarts(sentences).filter((s) => s < t - 0.05);
        seek(starts.length > 0 ? Math.max(...starts) : 0);
        return;
      }
      if (ctrl && key === "ArrowDown") {
        event.preventDefault();
        const starts = sentenceStarts(sentences).filter((s) => s > t + 0.05);
        seek(starts.length > 0 ? Math.min(...starts) : timeline.duration_s);
        return;
      }
      if (ctrl) return;
      switch (key) {
        case " ":
        case "k":
        case "K":
          event.preventDefault();
          preview.current?.toggle();
          return;
        case "ArrowLeft":
          event.preventDefault();
          seek(t - (event.shiftKey ? 1 : 1 / fps));
          return;
        case "ArrowRight":
          event.preventDefault();
          seek(t + (event.shiftKey ? 1 : 1 / fps));
          return;
        case "ArrowUp": {
          event.preventDefault();
          const cuts = [0, ...timeline.scenes.map((scene) => scene.start_s)].filter((c) => c < t - 0.05);
          seek(cuts.length > 0 ? Math.max(...cuts) : 0);
          return;
        }
        case "ArrowDown": {
          event.preventDefault();
          const cuts = timeline.scenes.map((scene) => scene.start_s).filter((c) => c > t + 0.05);
          seek(cuts.length > 0 ? Math.min(...cuts) : timeline.duration_s);
          return;
        }
        case "Home":
          event.preventDefault();
          seek(0);
          return;
        case "End":
          event.preventDefault();
          seek(timeline.duration_s);
          return;
        case "i":
        case "I":
          actions.markIn();
          return;
        case "o":
        case "O":
          actions.markOut();
          return;
        case "x":
        case "X":
          if (event.altKey) actions.clearRange();
          return;
        case "s":
        case "S":
          command("split");
          return;
        case "Delete":
        case "Backspace":
          event.preventDefault();
          command(event.shiftKey ? "delete-forward" : "delete");
          return;
        case "q":
        case "Q":
        case "w":
        case "W": {
          if (selection.kind !== "scene") return;
          const targets = event.shiftKey ? wordBoundaries(sentences) : sentenceStarts(sentences);
          const hit = snapTo(t, targets.map((time) => ({ t: time, label: "" })), 0.3);
          const result = moveSceneEdge(timeline, selection.index, key.toLowerCase() === "q" ? "start" : "end", hit ? hit.t : t, sentences);
          if (result.note) toast({ tone: "info", title: result.note });
          else edit(`move the ${key.toLowerCase() === "q" ? "start" : "end"} of scene ${selection.index + 1}`, () => result.timeline);
          return;
        }
        case "m":
        case "M":
          window.dispatchEvent(new CustomEvent("sge:timeline-command", { detail: { command: "marker" } }));
          return;
        case "n":
        case "N":
          setSnapping((v) => !v);
          return;
        case "=":
        case "+":
          zoom({ factor: 1.25 });
          return;
        case "-":
          zoom({ factor: 1 / 1.25 });
          return;
        case "\\":
          zoom({ fit: true });
          return;
        case "z":
        case "Z":
          if (event.shiftKey) zoom({ fit: true });
          return;
        case "?":
          window.dispatchEvent(new CustomEvent("sge:toggle-shortcuts"));
          return;
        case "Escape":
          setSelection(NO_SELECTION);
          return;
        default:
          return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [timeline, timeStore, sentences, selection, seek, undo, redo, edit, actions, toast]);

  // ---- rendering ---------------------------------------------------------------------------------
  if (project.isPending || query.isPending) return <LoadingBlock label="Opening the edit..." />;
  if (project.isError) return <ErrorState error={project.error} title="Could not open this edit" onRetry={() => void project.refetch()} />;
  if (!payload || !timeline) {
    const missing = query.isError && query.error instanceof Error && /no timeline/i.test(query.error.message);
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas p-8">
        <EmptyState
          title={missing ? "This edit has no timeline yet" : "Could not load the timeline"}
          description={missing ? "The timeline is built from the pictures, clips and narration. Try building it again." : query.error ? errorMessage(query.error) : ""}
          action={
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={() => navigate("/")}>
                My edits
              </Button>
              <Button variant="primary" size="sm" loading={apply.isPending} onClick={() => apply.mutate({ rebuild: true }, { onError: (error) => toast({ tone: "error", title: "Could not build the timeline", description: errorMessage(error) }) })}>
                Build the timeline
              </Button>
            </div>
          }
        />
      </div>
    );
  }

  const state: EditorState = {
    projectId,
    project: project.data,
    payload,
    bundle: bundleQuery.data ?? null,
    timeline,
    sentences,
    pictures,
    voicePeaks: voicePeaks.data ?? null,
    musicPeaks: musicPeaks.data ?? null,
    envelope: envelope.data ?? null,
    catalogue,
    pack: renderSettings.render.transition_pack,
    format: project.data.format,
    fps: timeline.fps,
    duration: timeline.duration_s,
    selection,
    locked,
    renderStatus: status.data,
    saveState,
    canUndo: history.current.past.length > 0 && historyTick >= 0,
    canRedo: history.current.future.length > 0,
    undoLabel: history.current.undoLabel,
    redoLabel: history.current.redoLabel,
    playing,
    range,
    loop,
    snapping,
    linkPopups,
    followPlayhead,
    timeStore,
  };

  return (
    <EditorContext.Provider value={{ state, actions }}>
      <WorkspaceShell topBar={<TopBar />}>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-[3] border-b border-ed-line">
            <Library />
            <Monitor preparingMusic={preparingMusic} />
            <Inspector />
          </div>
          <div className="flex min-h-[220px] flex-[2]">
            <TimelineLanes />
          </div>
        </div>
      </WorkspaceShell>
      <ConfirmDialog
        open={conflict}
        title="This video changed in another window"
        description="Reload it to see the latest version. Your unsaved changes here will be lost."
        confirmLabel="Reload this video"
        cancelLabel="Keep editing"
        onConfirm={() => {
          setConflict(false);
          dirty.current = false;
          history.current.clear();
          setTimeline(null);
          setServerRevision(0);
          setSaveState({ kind: "clean" });
          void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
        }}
        onCancel={() => setConflict(false)}
      />
    </EditorContext.Provider>
  );
}
