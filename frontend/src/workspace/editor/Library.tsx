import { ArrowLeftRight, Film, FolderInput, Image as ImageIcon, Layers, Mic, Move, Music, Search, Shuffle, Trash2, Type, Upload, Video, Volume2 } from "lucide-react";
import { useMemo, useState, type DragEvent, type ReactNode } from "react";

import { projectMediaUrl, sceneImageFile } from "../../api/edit";
import { useVoices } from "../../api/voice";
import { AUDIO_ACCEPT, CAPTIONS_ACCEPT, MEDIA_ACCEPT } from "../../components/edits/NewEditDialog";
import { FilePickButton } from "../../components/form/FilePickButton";
import { FilesPickButton } from "../../components/form/FilesPickButton";
import { TransitionPreview } from "../../components/storyboard/TransitionPicker";
import { cn } from "../../lib/cn";
import { MOTION_PRESETS } from "../../preview/motion";
import { MEDIA_DRAG_TYPE, MEDIA_KIND_LABELS, type MediaItem } from "../../types/media";
import { MOTION_ENERGY, MOTION_LABELS, transitionDuration, transitionsInPack, type MotionPreset } from "../../types/storyboard";
import { TRANSITION_PACK_LABELS, TRANSITION_PACKS, type TransitionPack } from "../../types/transitions";
import { CAPTION_PRESET_LABELS, CAPTION_PRESETS, POPUP_ANIMS } from "../../types/timeline";
import { MAX_PROVIDER_SPEED, MAX_VOICE_TEXT_CHARS, MIN_PROVIDER_SPEED, VOICE_PROVIDER_LABEL, type VoiceUse } from "../../types/voice";
import { useEditor } from "./context";
import { addAudioClip, addLayerClip, addPopup, applyPreset, applyTransitionToAll, clock, cutLimit, sceneTitle, setTransition, transitionEntry, updateCue, varyTransitions } from "./document";

type Tab = "media" | "motion" | "transitions" | "text" | "music";

const TABS: ReadonlyArray<{ id: Tab; label: string; icon: typeof ImageIcon }> = [
  { id: "media", label: "Media", icon: FolderInput },
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

export const LIBRARY_ACCEPT = `${MEDIA_ACCEPT},${AUDIO_ACCEPT},${CAPTIONS_ACCEPT}`;

const smallButton = "ws-focus h-6 rounded-md border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50";

function SectionTitle({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-3 pb-1.5 pt-3">
      <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-ink-muted">{children}</span>
      {actions}
    </div>
  );
}

/** "0:05.0" for a length, or "" */
function lengthOf(item: MediaItem): string {
  return item.duration_s !== null ? clock(item.duration_s) : item.width > 0 ? `${item.width} x ${item.height}` : "";
}

function KindIcon({ kind }: { kind: MediaItem["kind"] }) {
  const Icon = kind === "clip" ? Video : kind === "audio" ? Volume2 : kind === "captions" ? Type : ImageIcon;
  return <Icon className="size-5 text-ink-faint" aria-hidden />;
}

/** One library file: a card that can be dragged onto the timeline, with its actions. */
function MediaCard({ item }: { item: MediaItem }) {
  const { state, actions } = useEditor();
  const busy = state.locked || state.busy !== null;
  const playhead = state.timeStore.get();
  const selectedScene = state.selection.kind === "scene" ? state.selection.index : undefined;
  const noScenes = state.timeline.scenes.length === 0;
  const dragStart = (event: DragEvent) => {
    event.dataTransfer.setData(MEDIA_DRAG_TYPE, JSON.stringify({ path: item.path, kind: item.kind, name: item.name, duration_s: item.duration_s }));
    event.dataTransfer.effectAllowed = "copy";
  };
  const addAsLayer = () => {
    let note: string | null = null;
    let id = "";
    const done = actions.edit(`add ${item.name} as a layer`, (doc) => {
      const result = addLayerClip(doc, null, { path: item.path, kind: item.kind, name: item.name, duration_s: item.duration_s }, playhead);
      note = result.note;
      id = result.id;
      return result.timeline;
    });
    if (note) actions.notify("info", note);
    else if (done && id) actions.select({ kind: "layerclip", id });
  };
  const addAsSound = () => {
    let note: string | null = null;
    let id = "";
    const done = actions.edit(`add ${item.name} as a sound`, (doc) => {
      const result = addAudioClip(doc, null, { path: item.path, kind: item.kind, name: item.name, duration_s: item.duration_s, text: item.text, voice: item.voice }, playhead);
      note = result.note;
      id = result.id;
      return result.timeline;
    });
    if (note) actions.notify("info", note);
    else if (done && id) actions.select({ kind: "audioclip", id });
  };
  return (
    <li className="flex flex-col gap-1 rounded-md border border-ed-line bg-ed-raised p-1.5" draggable={!busy} onDragStart={dragStart} title={`${item.name}: drag it onto a lane of the timeline`}>
      <div className={cn("relative flex w-full items-center justify-center overflow-hidden rounded bg-black", state.timeline.aspect === "9:16" ? "aspect-[9/16] max-h-28" : "aspect-video")}>
        {item.thumb_url ? <img src={item.thumb_url} alt="" className="size-full object-cover" loading="lazy" draggable={false} /> : <KindIcon kind={item.kind} />}
        <span className="absolute left-1 top-1 rounded bg-black/70 px-1 text-[9px] font-semibold uppercase text-white">{item.ai_voice ? "AI voice" : MEDIA_KIND_LABELS[item.kind]}</span>
        {lengthOf(item) ? <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[9px] text-white tnum">{lengthOf(item)}</span> : null}
      </div>
      <span className="truncate text-[11px] text-ink" title={item.name}>
        {item.name}
      </span>
      {item.in_use.length > 0 ? <span className="truncate text-[10px] text-accent-text">In use: {item.in_use.join(", ")}</span> : null}
      <div className="flex flex-wrap gap-1">
        {item.kind === "picture" || item.kind === "clip" ? (
          <>
            <button type="button" disabled={busy} onClick={() => void actions.addScene(item.path, selectedScene, item.kind === "clip")} className={smallButton} title={selectedScene !== undefined ? `A new scene after scene ${selectedScene + 1}` : "A new scene at the end of the video"}>
              Add to video
            </button>
            <button type="button" disabled={busy || noScenes} onClick={addAsLayer} className={smallButton} title={noScenes ? "Add a scene first" : "On a layer over the scenes, at the playhead"}>
              Add as a layer
            </button>
          </>
        ) : null}
        {item.kind === "audio" ? (
          <>
            <button type="button" disabled={busy} onClick={() => void actions.setNarration({ media: item.path })} className={smallButton} title="The voice the scenes and captions follow">
              Use as narration
            </button>
            <button type="button" disabled={busy} onClick={() => void actions.applyChoices({ music_path: item.path })} className={smallButton} title="Background music, dipped under the voice">
              Use as music
            </button>
            <button type="button" disabled={busy || noScenes} onClick={addAsSound} className={smallButton} title={noScenes ? "Add a scene first" : "On a sound track, at the playhead"}>
              Add as a sound
            </button>
          </>
        ) : null}
        {item.kind === "captions" ? <span className="text-[10px] text-ink-faint">Caption files are used when you start from files.</span> : null}
        <button type="button" disabled={busy || item.in_use.length > 0} onClick={() => void actions.removeMedia(item.path)} className="ws-focus ml-auto grid size-6 place-items-center rounded text-ink-faint hover:bg-fail/10 hover:text-fail disabled:opacity-40" title={item.in_use.length > 0 ? "Take it off the timeline first" : "Remove from the library"} aria-label={`Remove ${item.name}`}>
          <Trash2 className="size-3.5" aria-hidden />
        </button>
      </div>
    </li>
  );
}

function MediaTab() {
  const { state, actions } = useEditor();
  const [over, setOver] = useState(false);
  const [filter, setFilter] = useState<"all" | MediaItem["kind"]>("all");
  const items = state.media?.items ?? [];
  const shown = filter === "all" ? items : items.filter((item) => item.kind === filter);
  const busy = state.busy !== null;
  const dropFiles = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    const files = Array.from(event.dataTransfer.files ?? []);
    if (files.length > 0) void actions.importMedia(files);
  };
  return (
    <div className="flex flex-col">
      <SectionTitle
        actions={
          <FilesPickButton accept={LIBRARY_ACCEPT} size="sm" variant="primary" icon={<Upload />} loading={busy} disabled={state.locked} onPick={(files) => void actions.importMedia(files)} title="Pictures, clips, sound and caption files">
            Import
          </FilesPickButton>
        }
      >
        Media
      </SectionTitle>
      <div
        onDragOver={(event) => {
          if (Array.from(event.dataTransfer.types).includes("Files")) {
            event.preventDefault();
            setOver(true);
          }
        }}
        onDragLeave={() => setOver(false)}
        onDrop={dropFiles}
        className={cn("mx-3 flex flex-col items-center justify-center gap-1 rounded-md border border-dashed px-3 py-4 text-center text-[11px]", over ? "border-accent bg-accent/10 text-ink" : "border-ed-line-strong text-ink-faint")}
      >
        <Upload className="size-4" aria-hidden />
        {state.busy ?? "Drag pictures, clips and sound files here, or press Import."}
      </div>
      {items.length > 0 ? (
        <div className="flex items-center gap-1 px-3 pt-2">
          {(["all", "picture", "clip", "audio"] as const).map((kind) => (
            <button key={kind} type="button" onClick={() => setFilter(kind)} className={cn("ws-focus rounded-full px-2 py-0.5 text-[11px]", filter === kind ? "bg-ed-raised-hi text-ink" : "text-ink-faint hover:text-ink")}>
              {kind === "all" ? "All" : kind === "picture" ? "Pictures" : kind === "clip" ? "Clips" : "Sound"}
            </button>
          ))}
        </div>
      ) : null}
      {items.length === 0 ? (
        <p className="px-3 pb-3 pt-3 text-[11px] text-ink-faint">Nothing imported yet. Pictures and clips become scenes with "Add to video" or layers over them; sound files become the narration, the music or a sound clip. You can also drag a file straight onto a lane of the timeline.</p>
      ) : (
        <ul className="grid grid-cols-2 gap-2 px-3 pb-3 pt-2">
          {shown.map((item) => (
            <MediaCard key={item.path} item={item} />
          ))}
        </ul>
      )}
      {state.timeline.scenes.length > 0 ? (
        <>
          <SectionTitle>Scenes of this video</SectionTitle>
          <ul className="grid grid-cols-3 gap-1.5 px-3 pb-3">
            {state.timeline.scenes.map((scene) => {
              const picture = state.pictures.get(scene.index);
              const src = picture?.thumb_url ?? projectMediaUrl(state.projectId, scene.image || sceneImageFile(scene.index));
              const selected = state.selection.kind === "scene" && state.selection.index === scene.index;
              return (
                <li key={scene.index}>
                  <button type="button" onClick={() => { actions.select({ kind: "scene", index: scene.index }); actions.seek(scene.start_s + 0.05); }} className={cn("ws-focus relative block w-full overflow-hidden rounded border bg-ed-track", selected ? "border-accent" : "border-ed-line hover:border-ed-hover-border")} title={`${sceneTitle(scene, state.sentences)}: select the scene`}>
                    <div className={cn("w-full bg-black", state.timeline.aspect === "9:16" ? "aspect-[9/16]" : "aspect-video")}>{src ? <img src={src} alt="" className="size-full object-cover" loading="lazy" draggable={false} /> : null}</div>
                    <span className="absolute left-1 top-1 rounded bg-black/70 px-1 text-[9px] font-semibold text-white">{scene.index + 1}</span>
                    {scene.clip ? <Video className="absolute bottom-1 right-1 size-3 text-white/80" aria-hidden /> : null}
                  </button>
                  {selected ? (
                    <FilePickButton accept={MEDIA_ACCEPT} size="sm" variant="ghost" className="mt-1 w-full" disabled={state.locked} onPick={(file) => void actions.replacePicture(scene.index, file)} title="Replace this scene's picture or clip">
                      Replace...
                    </FilePickButton>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
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
                disabled={!selected || state.locked || Boolean(selected?.clip)}
                onClick={() => selected && actions.edit(`set the move of scene ${selected.index + 1} to ${spec.label}`, (timeline) => applyPreset(timeline, selected.index, spec.id, state.pictures.get(selected.index)))}
                className={cn("ws-focus flex w-full flex-col gap-1 rounded-md border bg-ed-raised p-2 text-left disabled:opacity-50", inUse ? "border-accent" : "border-ed-line hover:border-ed-hover-border")}
                title={selected ? (selected.clip ? "A clip scene holds its frame; moves are for pictures" : `Apply ${spec.label} to scene ${selected.index + 1}`) : "Select a scene first"}
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
      <p className="px-3 pb-3 pt-2 text-[11px] text-ink-faint">{selected ? (selected.clip ? "This scene plays a clip. Its frame (position, scale, crop) is in the inspector." : `Click a move to apply it to scene ${selected.index + 1}. Fine-tune the start and end in the inspector.`) : "Select a scene on the timeline to apply a move."}</p>
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

/** Make a voice from text (docs/CONTRACT-v0.2.md section 5). */
function VoiceSection() {
  const { state, actions } = useEditor();
  const keySet = Boolean(state.voiceStatus?.key_set);
  const voices = useVoices(keySet);
  const [text, setText] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1);
  const [use, setUse] = useState<VoiceUse>("sound");
  const [name, setName] = useState("");
  const chosen = voiceId || voices.data?.[0]?.id || "";
  const noScenes = state.timeline.scenes.length === 0;
  const ready = keySet && text.trim().length > 0 && chosen.length > 0 && state.busy === null && !state.locked && !(use === "sound" && noScenes);
  const make = async () => {
    if (!ready) return;
    const playhead = state.timeStore.get();
    const voiceName = voices.data?.find((voice) => voice.id === chosen)?.name ?? "";
    const result = await actions.makeVoice({ text: text.trim(), voice_id: chosen, voice_name: voiceName, speed, name: name.trim(), use, at_s: playhead });
    if (!result) return;
    if (use === "sound") {
      let note: string | null = null;
      let id = "";
      const done = actions.edit("add the AI voice", (doc) => {
        const added = addAudioClip(doc, null, { path: result.item.path, kind: "audio", name: result.item.name, duration_s: result.duration_s, text: text.trim(), voice: voiceName }, playhead);
        note = added.note;
        id = added.id;
        return added.timeline;
      });
      if (note) actions.notify("info", `The voice is in the library. ${note}`);
      else if (done && id) {
        actions.select({ kind: "audioclip", id });
        actions.notify("success", "The voice is on a sound track at the playhead", `${clock(result.duration_s)} long.`);
      }
    } else {
      actions.notify("success", "The voice is the narration now", "The scenes and captions follow its words.");
    }
    setText("");
  };
  return (
    <>
      <SectionTitle>AI voice (optional)</SectionTitle>
      <div className="flex flex-col gap-2 px-3 pb-3">
        {!keySet ? (
          <p className="text-[11px] text-ink-faint">Optional: if you want voices made from text, add your {VOICE_PROVIDER_LABEL} API key under Settings, AI voice, and the voices appear here. Everything else works without it.</p>
        ) : (
          <>
            <textarea value={text} onChange={(event) => setText(event.target.value.slice(0, MAX_VOICE_TEXT_CHARS))} rows={4} placeholder="What the voice should say. One or more sentences; blank lines make paragraphs." aria-label="Text for the voice" className="ws-focus w-full rounded-md border border-ed-line bg-ed-raised p-1.5 text-[12px] text-ink placeholder:text-ink-faint" />
            <div className="flex items-center justify-between text-[10px] text-ink-faint">
              <span>{text.length} / {MAX_VOICE_TEXT_CHARS} characters</span>
              {voices.isError ? <span className="text-fail">The voices could not be read.</span> : null}
            </div>
            <label className="grid grid-cols-[64px_1fr] items-center gap-2 text-[11px] text-ink-muted">
              <span>Voice</span>
              <select value={chosen} onChange={(event) => setVoiceId(event.target.value)} className="ws-focus h-6 w-full rounded border border-ed-line bg-ed-raised px-1.5 text-[12px] text-ink">
                {(voices.data ?? []).map((voice) => (
                  <option key={voice.id} value={voice.id}>
                    {voice.name}{voice.description ? ` (${voice.description})` : ""}
                  </option>
                ))}
                {!voices.data?.length ? <option value="">{voices.isPending ? "Reading the voices..." : "No voices"}</option> : null}
              </select>
            </label>
            <label className="grid grid-cols-[64px_1fr] items-center gap-2 text-[11px] text-ink-muted">
              <span>Speed</span>
              <span className="flex items-center gap-2">
                <input type="range" min={MIN_PROVIDER_SPEED} max={MAX_PROVIDER_SPEED} step={0.05} value={speed} onChange={(event) => setSpeed(Number(event.target.value))} className="flex-1 accent-accent" aria-label="Speaking speed" />
                <span className="w-10 text-right tnum text-ink">{speed.toFixed(2)}x</span>
              </span>
            </label>
            <label className="grid grid-cols-[64px_1fr] items-center gap-2 text-[11px] text-ink-muted">
              <span>Name</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="From the text when empty" className="ws-focus h-6 w-full rounded border border-ed-line bg-ed-raised px-1.5 text-[12px] text-ink placeholder:text-ink-faint" />
            </label>
            <div className="flex items-center gap-1 text-[11px]">
              <button type="button" onClick={() => setUse("sound")} aria-pressed={use === "sound"} className={cn("ws-focus flex h-6 items-center gap-1 rounded px-2", use === "sound" ? "bg-ed-raised-hi text-ink" : "text-ink-muted hover:text-ink")}>
                <Volume2 className="size-3.5" aria-hidden /> A sound at the playhead
              </button>
              <button type="button" onClick={() => setUse("narration")} aria-pressed={use === "narration"} className={cn("ws-focus flex h-6 items-center gap-1 rounded px-2", use === "narration" ? "bg-ed-raised-hi text-ink" : "text-ink-muted hover:text-ink")}>
                <Mic className="size-3.5" aria-hidden /> The narration
              </button>
            </div>
            <p className="text-[10px] text-ink-faint">{use === "narration" ? "Replaces the narration; the scenes and captions follow the spoken words exactly." : noScenes ? "Add a scene first; sounds play under the scenes." : "Goes on a sound track at the playhead. Speed up or slow down the clip afterwards in the inspector."}</p>
            <button type="button" disabled={!ready} onClick={() => void make()} className="ws-focus flex h-7 w-fit items-center gap-1 rounded-md bg-accent px-3 text-[11px] font-semibold text-accent-ink disabled:opacity-50">
              <Mic className="size-3.5" aria-hidden />
              {state.busy === "Making the voice..." ? "Making the voice..." : "Make the voice"}
            </button>
          </>
        )}
      </div>
    </>
  );
}

function TextTab() {
  const { state, actions } = useEditor();
  const style = state.timeline.captions.style;
  return (
    <div className="flex flex-col">
      <VoiceSection />
      <SectionTitle
        actions={
          <button
            type="button"
            disabled={state.locked || state.timeline.scenes.length === 0}
            onClick={() => {
              const t = state.timeStore.get();
              let id = "";
              if (actions.edit("add a popup", (timeline) => {
                const result = addPopup(timeline, t, "New popup", state.sentences);
                id = result.id;
                return result.timeline;
              }) && id) actions.select({ kind: "popup", id });
            }}
            className={smallButton}
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
        <p className="px-3 pb-3 text-[11px] text-ink-faint">No sound files in the library yet. Import an MP3 or WAV under Media and press "Use as music" there.</p>
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
      <SectionTitle>Sound tracks</SectionTitle>
      <p className="px-3 pb-3 text-[11px] text-ink-faint">
        <Layers className="mr-1 inline size-3.5" aria-hidden />
        Extra voices and sounds go on sound tracks under the narration: press "Add as a sound" on a file under Media, or drag it onto a sound lane. {state.timeline.audio_tracks.length} track{state.timeline.audio_tracks.length === 1 ? "" : "s"} so far.
      </p>
    </div>
  );
}

/** The library (docs/design/EDITOR-DESIGN.md section 4.3): tabs with icons over labels. */
export function Library() {
  const { state } = useEditor();
  const [tab, setTab] = useState<Tab>(state.timeline.scenes.length === 0 ? "media" : "media");
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
        {tab === "media" ? <MediaTab /> : tab === "motion" ? <MotionTab /> : tab === "transitions" ? <TransitionsTab /> : tab === "text" ? <TextTab /> : <MusicTab />}
      </div>
    </aside>
  );
}

export type { MotionPreset };
