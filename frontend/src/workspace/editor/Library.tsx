import { ArrowLeftRight, Film, Image as ImageIcon, Move, Music, Search, Shuffle, Type, Upload } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { projectMediaUrl, sceneImageFile } from "../../api/edit";
import { MEDIA_ACCEPT } from "../../components/edits/NewEditDialog";
import { FilePickButton } from "../../components/form/FilePickButton";
import { TransitionPreview } from "../../components/storyboard/TransitionPicker";
import { cn } from "../../lib/cn";
import { MOTION_PRESETS } from "../../preview/motion";
import { MOTION_ENERGY, MOTION_LABELS, transitionDuration, transitionsInPack, type MotionPreset } from "../../types/storyboard";
import { TRANSITION_PACK_LABELS, TRANSITION_PACKS, type TransitionPack } from "../../types/transitions";
import { CAPTION_PRESET_LABELS, CAPTION_PRESETS, POPUP_ANIMS } from "../../types/timeline";
import { useEditor } from "./context";
import { addPopup, applyPreset, applyTransitionToAll, sceneTitle, setTransition, transitionEntry, updateCue, varyTransitions, cutLimit } from "./document";

type Tab = "pictures" | "motion" | "transitions" | "text" | "music";

const TABS: ReadonlyArray<{ id: Tab; label: string; icon: typeof ImageIcon }> = [
  { id: "pictures", label: "Pictures", icon: ImageIcon },
  { id: "motion", label: "Motion", icon: Move },
  { id: "transitions", label: "Transitions", icon: Film },
  { id: "text", label: "Text", icon: Type },
  { id: "music", label: "Music", icon: Music },
];

const ENERGY_TONE: Record<string, string> = {
  calm: "border-ok/30 bg-ok/12 text-ok",
  medium: "border-info/30 bg-info/12 text-info",
  high: "border-accent/40 bg-accent/15 text-accent-text",
};

function SectionTitle({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-3 pb-1.5 pt-3">
      <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-ink-muted">{children}</span>
      {actions}
    </div>
  );
}

function PicturesTab() {
  const { state, actions } = useEditor();
  const selected = state.selection.kind === "scene" ? state.selection.index : null;
  return (
    <div className="flex flex-col">
      <SectionTitle
        actions={
          <FilePickButton accept={MEDIA_ACCEPT} size="sm" variant="ghost" icon={<Upload />} disabled={state.locked} onPick={(file) => void actions.addScene(file)} title="Add a picture or clip as a new scene at the end">
            Add
          </FilePickButton>
        }
      >
        This video
      </SectionTitle>
      <ul className="grid grid-cols-2 gap-2 px-3">
        {state.timeline.scenes.map((scene) => {
          const picture = state.pictures.get(scene.index);
          const src = picture?.thumb_url ?? projectMediaUrl(state.projectId, scene.image || sceneImageFile(scene.index));
          return (
            <li key={scene.index}>
              <button
                type="button"
                onClick={() => {
                  actions.select({ kind: "scene", index: scene.index });
                  actions.seek(scene.start_s + 0.05);
                }}
                className={cn("ws-focus relative block w-full overflow-hidden rounded-md border bg-ed-track", selected === scene.index ? "border-accent" : "border-ed-line hover:border-ed-hover-border")}
                title={`${sceneTitle(scene, state.sentences)}: select the scene`}
              >
                <div className={cn("w-full bg-black", state.timeline.aspect === "9:16" ? "aspect-[9/16]" : "aspect-video")}>{src ? <img src={src} alt="" className="size-full object-cover" loading="lazy" /> : null}</div>
                <span className="absolute left-1 top-1 rounded bg-black/70 px-1 text-[10px] font-semibold text-white">{scene.index + 1}</span>
              </button>
              {selected === scene.index ? (
                <FilePickButton accept={MEDIA_ACCEPT} size="sm" variant="ghost" className="mt-1 w-full" disabled={state.locked} onPick={(file) => void actions.replacePicture(scene.index, file)} title="Replace this scene's picture or clip">
                  Replace...
                </FilePickButton>
              ) : null}
            </li>
          );
        })}
      </ul>
      <p className="px-3 pb-3 pt-2 text-[11px] text-ink-faint">Select a scene, then Replace... to swap its picture. Add puts a new scene at the end.</p>
    </div>
  );
}

function MotionTab() {
  const { state, actions } = useEditor();
  const selected = state.selection.kind === "scene" ? state.timeline.scenes[state.selection.index] : null;
  const presets = Object.values(MOTION_PRESETS);
  return (
    <div className="flex flex-col">
      <SectionTitle>Camera moves</SectionTitle>
      <ul className="grid grid-cols-2 gap-2 px-3">
        {presets.map((spec) => {
          const inUse = selected?.motion.preset === spec.id;
          const energy = (MOTION_ENERGY as Record<string, string>)[spec.id] ?? spec.energy;
          return (
            <li key={spec.id}>
              <button
                type="button"
                disabled={!selected || state.locked}
                onClick={() => selected && actions.edit(`set the move of scene ${selected.index + 1} to ${spec.label}`, (timeline) => applyPreset(timeline, selected.index, spec.id, state.pictures.get(selected.index)))}
                className={cn("ws-focus flex w-full flex-col gap-1 rounded-md border bg-ed-raised p-2 text-left disabled:opacity-50", inUse ? "border-accent" : "border-ed-line hover:border-ed-hover-border")}
                title={selected ? `Apply ${spec.label} to scene ${selected.index + 1}` : "Select a scene first"}
              >
                <MotionGlyph spec={spec} />
                <span className="flex items-center justify-between">
                  <span className="text-[11px] font-medium text-ink">{(MOTION_LABELS as Record<string, string>)[spec.id] ?? spec.label}</span>
                  <span className={cn("rounded-full border px-1.5 text-[9px] font-semibold uppercase", ENERGY_TONE[energy] ?? ENERGY_TONE.medium)}>{energy}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="px-3 pb-3 pt-2 text-[11px] text-ink-faint">{selected ? `Click a move to apply it to scene ${selected.index + 1}. Fine-tune the start and end in the inspector.` : "Select a scene on the timeline to apply a move."}</p>
    </div>
  );
}

/** A small drawing of the move: the start box (green) and the end box (red) over a frame. */
function MotionGlyph({ spec }: { spec: { start: readonly number[]; end: readonly number[] } }) {
  const box = (rect: readonly number[], colour: string) => (
    <span className="absolute border" style={{ left: `${rect[0]! * 100}%`, top: `${rect[1]! * 100}%`, width: `${rect[2]! * 100}%`, height: `${rect[3]! * 100}%`, borderColor: colour }} />
  );
  return (
    <span className="relative block aspect-video w-full overflow-hidden rounded bg-ed-track" aria-hidden>
      {box(spec.start, "#22C55E")}
      {box(spec.end, "#F87171")}
    </span>
  );
}

function TransitionsTab() {
  const { state, actions } = useEditor();
  const [pack, setPack] = useState<TransitionPack | "all">(state.pack);
  const [search, setSearch] = useState("");
  const [playing, setPlaying] = useState<string | null>(null);
  const catalogue = state.catalogue;
  const cutIndex = state.selection.kind === "cut" ? state.selection.index : state.selection.kind === "scene" ? state.selection.index : null;
  const cutScene = cutIndex !== null ? state.timeline.scenes[cutIndex] : null;
  const current = cutScene ? transitionEntry(catalogue, cutScene.transition_out.type) : null;
  const entries = useMemo(() => {
    if (!catalogue) return [];
    const list = pack === "all" ? catalogue.transitions : transitionsInPack(catalogue, pack);
    const needle = search.trim().toLowerCase();
    return needle ? list.filter((entry) => entry.label.toLowerCase().includes(needle) || entry.feel.toLowerCase().includes(needle)) : list;
  }, [catalogue, pack, search]);

  const apply = (entryId: string) => {
    if (!catalogue || cutIndex === null || !cutScene) return;
    const entry = catalogue.transitions.find((item) => item.id === entryId);
    if (!entry) return;
    const length = Math.min(transitionDuration(catalogue, entry, state.pack, state.format), cutLimit(state.timeline, cutIndex));
    if (actions.edit(`set the cut after scene ${cutIndex + 1} to ${entry.label}`, (timeline) => setTransition(timeline, cutIndex, entry.id, length))) {
      actions.playAround(cutScene.end_s);
    }
  };

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center gap-1 px-3 pt-3">
        {([...TRANSITION_PACKS, "all"] as const).map((option) => (
          <button key={option} type="button" onClick={() => setPack(option)} className={cn("ws-focus rounded-full px-2.5 py-1 text-[11px] font-medium", pack === option ? "bg-ed-raised-hi text-ink" : "text-ink-faint hover:text-ink")}>
            {option === "all" ? "All" : TRANSITION_PACK_LABELS[option]}
            {option === state.pack ? <span className="ml-1 text-accent-text" title="This edit's pack">★</span> : null}
          </button>
        ))}
      </div>
      <div className="relative px-3 pt-2">
        <Search className="pointer-events-none absolute left-5 top-1/2 size-3.5 translate-y-[1px] text-ink-faint" aria-hidden />
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search transitions" aria-label="Search transitions" className="ws-focus h-7 w-full rounded-md border border-ed-line bg-ed-raised pl-7 pr-2 text-[12px] text-ink placeholder:text-ink-faint" />
      </div>
      <ul className="grid grid-cols-3 gap-2 px-3 pt-2">
        {entries.map((entry) => {
          const inUse = current?.id === entry.id;
          return (
            <li key={entry.id} onMouseEnter={() => setPlaying(entry.id)} onMouseLeave={() => setPlaying(null)}>
              <button
                type="button"
                disabled={cutIndex === null || state.locked}
                onClick={() => apply(entry.id)}
                className={cn("ws-focus flex w-full flex-col gap-1 rounded-md border bg-ed-raised p-1 text-left disabled:opacity-60", inUse ? "border-accent" : "border-ed-line hover:border-ed-hover-border")}
                title={cutIndex === null ? "Select a scene or a cut first" : `${entry.label}: ${entry.feel}`}
              >
                <TransitionPreview entry={entry} playing={playing === entry.id} className="aspect-video w-full rounded-sm" />
                <span className="truncate text-[11px] text-ink">{entry.label}</span>
                <span className="flex items-center justify-between text-[10px] text-ink-faint">
                  <span>{entry.duration_s.toFixed(1)} s</span>
                  <span className={cn("rounded-full border px-1 text-[9px] font-semibold uppercase", ENERGY_TONE[entry.energy])}>{entry.energy}</span>
                  {inUse ? <span className="text-accent-text">In use</span> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <div className="flex items-center gap-2 px-3 py-3">
        <button
          type="button"
          disabled={!current || state.locked}
          onClick={() => current && actions.edit(`apply ${current.label} to every cut`, (timeline) => applyTransitionToAll(timeline, current.id, cutScene?.transition_out.duration_s ?? current.duration_s))}
          className="ws-focus flex h-7 items-center gap-1 rounded-md border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50"
          title="Use the selected cut's transition on every cut"
        >
          <ArrowLeftRight className="size-3.5" aria-hidden />
          Apply to all
        </button>
        <button
          type="button"
          disabled={!catalogue || state.locked}
          onClick={() => catalogue && actions.edit("vary every transition", (timeline) => varyTransitions(timeline, catalogue, state.pack, state.format))}
          className="ws-focus flex h-7 items-center gap-1 rounded-md border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50"
          title="A mix from the pack with no repeats"
        >
          <Shuffle className="size-3.5" aria-hidden />
          Vary all
        </button>
      </div>
    </div>
  );
}

function TextTab() {
  const { state, actions } = useEditor();
  const style = state.timeline.captions.style;
  return (
    <div className="flex flex-col">
      <SectionTitle
        actions={
          <button
            type="button"
            disabled={state.locked}
            onClick={() => {
              const t = state.timeStore.get();
              let id = "";
              if (actions.edit("add a popup", (timeline) => {
                const result = addPopup(timeline, t, "New popup", state.sentences);
                id = result.id;
                return result.timeline;
              }) && id) actions.select({ kind: "popup", id });
            }}
            className="ws-focus h-6 rounded-md border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50"
            title="Add a popup at the playhead (T)"
          >
            Add popup at playhead
          </button>
        }
      >
        Popups
      </SectionTitle>
      <ul className="flex flex-col gap-1 px-3">
        {POPUP_ANIMS.map((anim) => (
          <li key={anim} className="rounded-md border border-ed-line bg-ed-raised px-2 py-1.5 text-[11px] text-ink-muted">
            {anim === "slide_left" ? "Slide in from the left" : anim === "fade" ? "Fade in and out" : "Pop"}
            <span className="block text-[10px] text-ink-faint">Pick it on a popup in the inspector.</span>
          </li>
        ))}
      </ul>
      <SectionTitle>Caption look</SectionTitle>
      <ul className="flex flex-col gap-1 px-3 pb-3">
        {CAPTION_PRESETS.map((preset) => {
          const inUse = style.preset === preset;
          return (
            <li key={preset}>
              <button
                type="button"
                disabled={state.locked}
                onClick={() => actions.edit(`caption look: ${CAPTION_PRESET_LABELS[preset]}`, (timeline) => ({ ...timeline, captions: { ...timeline.captions, style: { ...timeline.captions.style, preset } } }))}
                className={cn("ws-focus w-full rounded-md border px-2 py-1.5 text-left text-[11px] text-ink disabled:opacity-50", inUse ? "border-accent bg-ed-raised" : "border-ed-line bg-ed-raised hover:border-ed-hover-border")}
                title="Applies to the whole video"
              >
                {CAPTION_PRESET_LABELS[preset]}
                {inUse ? <span className="ml-2 text-[10px] text-accent-text">In use</span> : null}
              </button>
            </li>
          );
        })}
        <li className="pt-1 text-[11px] text-ink-faint">
          Captions {state.timeline.captions.enabled ? "are burnt in" : "are off"}.{" "}
          <button type="button" className="text-accent-text hover:underline" onClick={() => actions.edit(state.timeline.captions.enabled ? "captions off" : "captions on", (timeline) => ({ ...timeline, captions: { ...timeline.captions, enabled: !timeline.captions.enabled } }))}>
            {state.timeline.captions.enabled ? "Switch off" : "Switch on"}
          </button>
        </li>
      </ul>
      {state.selection.kind === "caption" ? (
        <p className="px-3 pb-3 text-[11px] text-ink-faint">
          Fix the selected caption's text in the inspector.{" "}
          <button type="button" className="text-accent-text hover:underline" onClick={() => state.selection.kind === "caption" && actions.edit("clear caption", (timeline) => updateCue(timeline, (state.selection as { id: string }).id, {}))}>
            Keep it
          </button>
        </p>
      ) : null}
    </div>
  );
}

function MusicTab() {
  const { state, actions } = useEditor();
  const tracks = state.payload.music_tracks;
  const current = state.timeline.music.path;
  return (
    <div className="flex flex-col">
      <SectionTitle>This edit's music</SectionTitle>
      {tracks.length === 0 ? (
        <p className="px-3 pb-3 text-[11px] text-ink-faint">No music files in the edit's 01_media folder. Add one when you make an edit, or copy an MP3 or WAV file into that folder and press "Build the timeline again".</p>
      ) : (
        <ul className="flex flex-col gap-1.5 px-3 pb-3">
          {tracks.map((track) => {
            const inUse = current === track.path;
            return (
              <li key={track.path} className={cn("flex items-center gap-2 rounded-md border px-2 py-1.5", inUse ? "border-accent bg-ed-raised" : "border-ed-line bg-ed-raised")}>
                <Music className="size-4 shrink-0 text-ink-faint" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-ink">{track.name}</span>
                  <span className={cn("text-[10px]", track.license_ok || state.payload.music_licence_note ? "text-ok" : "text-warn")}>{track.license_ok || state.payload.music_licence_note ? "Licence noted" : "No licence yet"}</span>
                </span>
                <button type="button" disabled={inUse || state.locked} onClick={() => void actions.applyChoices({ music_path: track.path })} className="ws-focus h-6 rounded-md border border-ed-line bg-ed-raised-hi px-2 text-[11px] text-ink disabled:opacity-50">
                  {inUse ? "In use" : "Use"}
                </button>
              </li>
            );
          })}
          {current ? (
            <li>
              <button type="button" disabled={state.locked} onClick={() => void actions.applyChoices({ music_path: "" })} className="ws-focus text-[11px] text-ink-faint hover:text-ink">
                Remove the music
              </button>
            </li>
          ) : null}
        </ul>
      )}
    </div>
  );
}

/** The library (docs/design/EDITOR-DESIGN.md section 4.3): tabs with icons over labels. */
export function Library() {
  const [tab, setTab] = useState<Tab>("transitions");
  return (
    <aside className="flex w-[304px] min-w-[260px] shrink-0 flex-col border-r border-ed-line bg-ed-panel" aria-label="Library">
      <div className="flex h-14 shrink-0 items-stretch border-b border-ed-line bg-ed-panel-2" role="tablist">
        {TABS.map((item) => {
          const Icon = item.icon;
          const active = tab === item.id;
          return (
            <button key={item.id} type="button" role="tab" aria-selected={active} onClick={() => setTab(item.id)} className={cn("ws-focus flex flex-1 flex-col items-center justify-center gap-1 text-[10px] font-medium", active ? "text-ink" : "text-ink-faint hover:text-ink")}>
              <Icon className={cn("size-4", active && "text-accent-text")} aria-hidden />
              {item.label}
            </button>
          );
        })}
      </div>
      <div className="ws-scroll min-h-0 flex-1 overflow-y-auto">
        {tab === "pictures" ? <PicturesTab /> : tab === "motion" ? <MotionTab /> : tab === "transitions" ? <TransitionsTab /> : tab === "text" ? <TextTab /> : <MusicTab />}
      </div>
    </aside>
  );
}

export type { MotionPreset };
