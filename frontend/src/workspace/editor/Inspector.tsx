import { ChevronDown, ChevronRight, Lock, MoreHorizontal, Trash2, Unlock } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { projectMediaUrl, sceneImageFile } from "../../api/edit";
import { MEDIA_ACCEPT } from "../../components/edits/NewEditDialog";
import { FilePickButton } from "../../components/form/FilePickButton";
import { TransitionPreview } from "../../components/storyboard/TransitionPicker";
import { cn } from "../../lib/cn";
import { MOTION_PRESETS } from "../../preview/motion";
import { MOTION_EASE_LABELS, MOTION_EASES, POPUP_ANIMS, renderPresetLabel, type MotionEase, type TimelinePopup, type TimelineScene } from "../../types/timeline";
import { MOTION_LABELS, POPUP_POSITIONS, POPUP_POSITION_LABELS } from "../../types/storyboard";
import { TRANSITION_PACK_LABELS } from "../../types/transitions";
import { useEditor } from "./context";
import {
  LONG_POPUP_S,
  MIN_POPUP_S,
  type Selection,
  applyPreset,
  addPopup,
  clock,
  cutLimit,
  mergeCueWithNext,
  mergeScene,
  motionView,
  moveCut,
  pausesOf,
  rectFromView,
  removeCue,
  removeMarker,
  removePopup,
  sceneIndexAt,
  sceneTitle,
  seconds,
  setMotion,
  setMusic,
  setTransition,
  splitCue,
  splitScene,
  swapMotion,
  transitionEntry,
  updateCue,
  updateMarker,
  updatePopup,
  setLocked,
} from "./document";

// ---- small parts ----------------------------------------------------------------------------

function Section({ title, summary, open, onToggle, children, actions }: { title: string; summary?: ReactNode; open: boolean; onToggle: () => void; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="border-b border-ed-line">
      <header className="flex h-8 items-center gap-1 px-2">
        <button type="button" onClick={onToggle} aria-expanded={open} className="ws-focus flex min-w-0 flex-1 items-center gap-1.5 rounded px-1 text-left">
          {open ? <ChevronDown className="size-3.5 shrink-0 text-ink-faint" aria-hidden /> : <ChevronRight className="size-3.5 shrink-0 text-ink-faint" aria-hidden />}
          <span className="text-[12px] font-semibold text-ink">{title}</span>
          {!open && summary ? <span className="ml-auto truncate text-[11px] text-ink-faint">{summary}</span> : null}
        </button>
        {actions}
      </header>
      {open ? <div className="flex flex-col gap-2 px-3 pb-3">{children}</div> : null}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid grid-cols-[72px_1fr] items-center gap-2 text-[11px] text-ink-muted">
      <span>{label}</span>
      {children}
    </label>
  );
}

const control = "ws-focus h-6 w-full rounded border border-ed-line bg-ed-raised px-1.5 text-[12px] text-ink tnum disabled:opacity-50";

function NumberBox({ value, onCommit, min, max, step = 0.1, suffix, disabled }: { value: number; onCommit: (value: number) => void; min?: number; max?: number; step?: number; suffix?: string; disabled?: boolean }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const parsed = Number(text);
    if (!Number.isFinite(parsed)) return setText(String(value));
    const clamped = Math.max(min ?? -Infinity, Math.min(max ?? Infinity, parsed));
    if (clamped !== value) onCommit(clamped);
    else setText(String(value));
  };
  return (
    <span className="flex items-center gap-1">
      <input type="number" step={step} value={text} disabled={disabled} onChange={(event) => setText(event.target.value)} onBlur={commit} onKeyDown={(event) => { if (event.key === "Enter") (event.target as HTMLInputElement).blur(); if (event.key === "Escape") setText(String(value)); }} className={control} />
      {suffix ? <span className="text-[11px] text-ink-faint">{suffix}</span> : null}
    </span>
  );
}

function useOpenSections(initial: string[]) {
  const [open, setOpen] = useState<Set<string>>(new Set(initial));
  const toggle = (key: string) => setOpen((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });
  return { open, toggle };
}

function Header({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <header className="flex items-start gap-2 border-b border-ed-line px-3 py-2">
      <div className="min-w-0 flex-1">
        <h2 className="truncate text-[13px] font-semibold text-ink">{title}</h2>
        {subtitle ? <p className="text-[11px] text-ink-faint">{subtitle}</p> : null}
      </div>
      {children}
    </header>
  );
}

// ---- the sections ----------------------------------------------------------------------------

function TransitionSection({ index, open, onToggle }: { index: number; open: boolean; onToggle: () => void }) {
  const { state, actions } = useEditor();
  const scene = state.timeline.scenes[index];
  if (!scene) return null;
  const entry = transitionEntry(state.catalogue, scene.transition_out.type);
  const limit = cutLimit(state.timeline, index);
  const last = index === state.timeline.scenes.length - 1;
  const packs = entry ? entry.pack.map((pack) => TRANSITION_PACK_LABELS[pack]).join(", ") : "";
  return (
    <Section title={last ? "Ending transition" : "Transition out"} summary={`${entry?.label ?? scene.transition_out.type}, ${scene.transition_out.duration_s.toFixed(1)} s`} open={open} onToggle={onToggle}>
      <div className="flex items-center gap-2">
        {entry ? <TransitionPreview entry={entry} playing className="aspect-video w-24 shrink-0 rounded-sm" /> : null}
        <div className="min-w-0">
          <p className="truncate text-[12px] font-medium text-ink">{entry?.label ?? scene.transition_out.type}</p>
          <p className="text-[11px] text-ink-faint">{packs}{entry ? ` · ${entry.energy}` : ""}</p>
        </div>
      </div>
      <Row label="Length">
        <span className="flex items-center gap-2">
          <input type="range" min={0} max={Math.max(0.1, limit)} step={0.05} value={Math.min(scene.transition_out.duration_s, limit)} disabled={state.locked} onChange={(event) => actions.edit(`set the length of the cut after scene ${index + 1}`, (timeline) => setTransition(timeline, index, scene.transition_out.type, Number(event.target.value)))} className="flex-1 accent-accent" aria-label="Transition length" />
          <span className="w-12 text-right text-[11px] tnum text-ink">{scene.transition_out.duration_s.toFixed(2)} s</span>
        </span>
      </Row>
      <p className="text-[11px] text-ink-faint">Longest here: {limit.toFixed(1)} s (a quarter of the shorter scene). Pick another look under Library, Transitions.</p>
    </Section>
  );
}

function MotionSection({ scene, open, onToggle }: { scene: TimelineScene; open: boolean; onToggle: () => void }) {
  const { state, actions } = useEditor();
  const picture = state.pictures.get(scene.index);
  const view = motionView(scene, picture, state.timeline);
  const [which, setWhich] = useState<"start" | "end">("end");
  const spec = MOTION_PRESETS[scene.motion.preset];
  const label = (MOTION_LABELS as Record<string, string>)[scene.motion.preset] ?? scene.motion.preset;
  const custom = spec ? JSON.stringify(scene.edited.includes("motion")) : "";
  const commit = (field: "scale" | "cx" | "cy", value: number) => {
    const scale = which === "start" ? view.startScale : view.endScale;
    const centre: [number, number] = which === "start" ? [...view.startCentre] : [...view.endCentre];
    const nextScale = field === "scale" ? value : scale;
    if (field === "cx") centre[0] = value;
    if (field === "cy") centre[1] = value;
    const rect = rectFromView(nextScale, centre, picture, state.timeline);
    actions.edit(`change the ${which} of the move of scene ${scene.index + 1}`, (timeline) => setMotion(timeline, scene.index, which === "start" ? { start_rect: rect } : { end_rect: rect }));
  };
  const scale = which === "start" ? view.startScale : view.endScale;
  const centre = which === "start" ? view.startCentre : view.endCentre;
  return (
    <Section title="Motion" summary={label} open={open} onToggle={onToggle}>
      <div className="flex flex-wrap gap-1">
        {Object.values(MOTION_PRESETS).map((item) => (
          <button key={item.id} type="button" disabled={state.locked} onClick={() => actions.edit(`set the move of scene ${scene.index + 1} to ${item.label}`, (timeline) => applyPreset(timeline, scene.index, item.id, picture))} className={cn("ws-focus h-6 rounded border px-1.5 text-[10px] disabled:opacity-50", scene.motion.preset === item.id ? "border-accent bg-ed-raised text-ink" : "border-ed-line bg-ed-raised text-ink-muted hover:border-ed-hover-border")} title={item.label}>
            {(MOTION_LABELS as Record<string, string>)[item.id] ?? item.label}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1 text-[11px]">
        <button type="button" onClick={() => setWhich("start")} aria-pressed={which === "start"} className={cn("ws-focus flex h-6 items-center gap-1 rounded px-2", which === "start" ? "bg-ed-raised-hi text-ink" : "text-ink-muted hover:text-ink")}>
          <span className="size-2 rounded-full bg-[#22C55E]" aria-hidden /> Start
        </button>
        <button type="button" onClick={() => setWhich("end")} aria-pressed={which === "end"} className={cn("ws-focus flex h-6 items-center gap-1 rounded px-2", which === "end" ? "bg-ed-raised-hi text-ink" : "text-ink-muted hover:text-ink")}>
          <span className="size-2 rounded-full bg-[#F87171]" aria-hidden /> End
        </button>
        <button type="button" disabled={state.locked} onClick={() => actions.edit(`swap the start and end of scene ${scene.index + 1}`, (timeline) => swapMotion(timeline, scene.index))} className="ws-focus ml-auto h-6 rounded px-2 text-ink-muted hover:bg-ed-raised hover:text-ink disabled:opacity-50">
          Swap
        </button>
        <button type="button" disabled={state.locked || !spec} onClick={() => actions.edit(`reset the move of scene ${scene.index + 1}`, (timeline) => applyPreset(timeline, scene.index, scene.motion.preset, picture))} className="ws-focus h-6 rounded px-2 text-ink-muted hover:bg-ed-raised hover:text-ink disabled:opacity-50">
          Reset
        </button>
      </div>
      <Row label="Scale">
        <NumberBox value={scale} min={100} max={400} step={1} suffix="%" disabled={state.locked} onCommit={(value) => commit("scale", value)} />
      </Row>
      <Row label="Centre X">
        <NumberBox value={centre[0]} min={0} max={100} step={0.5} suffix="%" disabled={state.locked} onCommit={(value) => commit("cx", value)} />
      </Row>
      <Row label="Centre Y">
        <NumberBox value={centre[1]} min={0} max={100} step={0.5} suffix="%" disabled={state.locked} onCommit={(value) => commit("cy", value)} />
      </Row>
      <Row label="Ease">
        <select value={scene.motion.ease ?? ""} disabled={state.locked} onChange={(event) => actions.edit(`change the ease of scene ${scene.index + 1}`, (timeline) => setMotion(timeline, scene.index, { ease: (event.target.value || null) as MotionEase | null }))} className={control}>
          <option value="">The move's own</option>
          {MOTION_EASES.map((ease) => (
            <option key={ease} value={ease}>
              {MOTION_EASE_LABELS[ease]}
            </option>
          ))}
        </select>
      </Row>
      {scale > 160 ? <p className="text-[11px] text-warn">Very close: the picture may look soft at 1080p.</p> : null}
      {custom ? null : null}
    </Section>
  );
}

function PopupFields({ popup }: { popup: TimelinePopup }) {
  const { state, actions } = useEditor();
  const scene = state.timeline.scenes[popup.scene];
  const words = popup.text.trim().split(/\s+/).filter(Boolean).length;
  const [text, setText] = useState(popup.text);
  useEffect(() => setText(popup.text), [popup.id, popup.text]);
  const length = popup.end_s - popup.start_s;
  return (
    <>
      <label className="flex flex-col gap-1 text-[11px] text-ink-muted">
        <span className="flex justify-between">
          Text <span className={cn(words > 6 ? "text-fail" : "text-ink-faint")}>{words} / 6 words</span>
        </span>
        <input value={text} disabled={state.locked} onChange={(event) => setText(event.target.value)} onBlur={() => text !== popup.text && actions.edit("change the popup text", (timeline) => updatePopup(timeline, popup.id, { text }))} onKeyDown={(event) => { if (event.key === "Enter") (event.target as HTMLInputElement).blur(); }} className={control} />
      </label>
      <Row label="Position">
        <div className="grid grid-cols-5 gap-1">
          {POPUP_POSITIONS.map((position) => (
            <button key={position} type="button" disabled={state.locked} aria-pressed={popup.position === position} title={POPUP_POSITION_LABELS[position]} onClick={() => actions.edit("move the popup", (timeline) => updatePopup(timeline, popup.id, { position }))} className={cn("ws-focus h-6 rounded border text-[9px] disabled:opacity-50", popup.position === position ? "border-accent bg-ed-raised text-ink" : "border-ed-line bg-ed-raised text-ink-faint")}>
              {position === "center" ? "C" : position.split("-").map((part) => part[0]!.toUpperCase()).join("")}
            </button>
          ))}
        </div>
      </Row>
      <Row label="Animation">
        <select value={popup.anim} disabled={state.locked} onChange={(event) => actions.edit("change the popup animation", (timeline) => updatePopup(timeline, popup.id, { anim: event.target.value as TimelinePopup["anim"] }))} className={control}>
          {POPUP_ANIMS.map((anim) => (
            <option key={anim} value={anim}>
              {anim === "slide_left" ? "Slide" : anim === "fade" ? "Fade" : "Pop"}
            </option>
          ))}
        </select>
      </Row>
      <Row label="In">
        <NumberBox value={Math.round((popup.start_s - (scene?.start_s ?? 0)) * 10) / 10} min={0} step={0.1} suffix={`s after scene ${popup.scene + 1} starts`} disabled={state.locked} onCommit={(value) => actions.edit("re-time the popup", (timeline) => updatePopup(timeline, popup.id, { start_s: (scene?.start_s ?? 0) + value, end_s: Math.max((scene?.start_s ?? 0) + value + MIN_POPUP_S, popup.end_s) }))} />
      </Row>
      <Row label="Length">
        <NumberBox value={Math.round(length * 10) / 10} min={MIN_POPUP_S} step={0.1} suffix="s" disabled={state.locked} onCommit={(value) => actions.edit("re-time the popup", (timeline) => updatePopup(timeline, popup.id, { end_s: popup.start_s + value }))} />
      </Row>
      {length > LONG_POPUP_S ? <p className="text-[11px] text-warn">Longer than 4 s: popups work best short.</p> : null}
      <button type="button" disabled={state.locked} onClick={() => { actions.edit("remove the popup", (timeline) => removePopup(timeline, popup.id)); actions.select({ kind: "scene", index: popup.scene }); }} className="ws-focus flex h-6 w-fit items-center gap-1 rounded px-1.5 text-[11px] text-fail hover:bg-fail/10 disabled:opacity-50">
        <Trash2 className="size-3.5" aria-hidden /> Remove the popup
      </button>
    </>
  );
}

function NarrationSection({ scene, open, onToggle }: { scene: TimelineScene; open: boolean; onToggle: () => void }) {
  const { state, actions } = useEditor();
  const sentences = state.sentences.filter((sentence) => scene.sentence_ids.includes(sentence.id));
  const words = sentences.reduce((sum, sentence) => sum + sentence.words.length, 0);
  return (
    <Section title="Narration" summary={`${sentences.length} sentence${sentences.length === 1 ? "" : "s"} · ${words} words`} open={open} onToggle={onToggle}>
      {sentences.length === 0 ? <p className="text-[11px] text-ink-faint">No sentence starts in this scene.</p> : null}
      <ol className="flex flex-col gap-1">
        {sentences.map((sentence) => {
          const position = state.sentences.indexOf(sentence);
          const canCutHere = position > 0 && Math.abs(sentence.start_s - scene.start_s) > 0.05;
          return (
            <li key={sentence.id} className="rounded border border-ed-line bg-ed-raised px-2 py-1">
              <button type="button" onClick={() => actions.seek(sentence.start_s)} className="ws-focus text-left text-[11px] text-ink">
                <span className="mr-1 text-ink-faint">{position + 1}</span>
                {sentence.text}
              </button>
              <div className="flex items-center justify-between text-[10px] text-ink-faint">
                <span>{clock(sentence.start_s)} to {clock(sentence.end_s)}</span>
                {canCutHere && scene.index > 0 ? (
                  <button type="button" disabled={state.locked} onClick={() => { const result = moveCut(state.timeline, scene.index - 1, sentence.start_s, state.sentences); if (result.note) actions.notify("info", result.note); else actions.edit(`move cut ${scene.index} to ${scene.index + 1}`, () => result.timeline); }} className="ws-focus text-accent-text hover:underline disabled:opacity-50" title="Start this scene at this sentence">
                    Move the cut here
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </Section>
  );
}

// ---- the inspector by selection ----------------------------------------------------------------

function SceneInspector({ index }: { index: number }) {
  const { state, actions } = useEditor();
  const scene = state.timeline.scenes[index];
  const { open, toggle } = useOpenSections(["picture", "motion", "transition", "popup"]);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (event: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenu(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menu]);
  if (!scene) return null;
  const picture = state.pictures.get(index);
  const popup = state.timeline.popups.find((item) => item.scene === index);
  const length = scene.end_s - scene.start_s;
  const src = picture?.thumb_url ?? projectMediaUrl(state.projectId, scene.image || sceneImageFile(index));
  const file = scene.image.split("/").at(-1) ?? "";
  const playhead = state.timeStore.get();
  const run = (label: string, result: { timeline: typeof state.timeline; note: string | null }) => {
    if (result.note) actions.notify("info", result.note);
    else actions.edit(label, () => result.timeline);
  };
  return (
    <>
      <Header title={sceneTitle(scene, state.sentences)} subtitle={`${clock(scene.start_s)} to ${clock(scene.end_s)} · ${seconds(length)} · ${scene.sentence_ids.length} sentence${scene.sentence_ids.length === 1 ? "" : "s"}`}>
        <button type="button" disabled={state.locked} onClick={() => actions.edit(scene.locked ? `unlock scene ${index + 1}` : `lock scene ${index + 1}`, (timeline) => setLocked(timeline, index, !scene.locked))} className="ws-focus grid size-7 place-items-center rounded text-ink-faint hover:bg-ed-raised hover:text-ink" title={scene.locked ? "Unlock: a rebuild may change this scene again" : "Lock: a rebuild keeps this scene as it is"}>
          {scene.locked ? <Lock className="size-4" aria-hidden /> : <Unlock className="size-4" aria-hidden />}
        </button>
        <div className="relative" ref={menuRef}>
          <button type="button" onClick={() => setMenu((v) => !v)} className="ws-focus grid size-7 place-items-center rounded text-ink-faint hover:bg-ed-raised hover:text-ink" title="More" aria-label="More">
            <MoreHorizontal className="size-4" aria-hidden />
          </button>
          {menu ? (
            <div className="absolute right-0 top-8 z-40 w-56 rounded-md border border-ed-line-strong bg-ed-panel py-1 shadow-[0_12px_32px_rgb(0_0_0/0.5)]" role="menu">
              {[
                { label: "Split at the playhead (S)", run: () => run(`split scene ${index + 1}`, splitScene(state.timeline, index, playhead, state.sentences)) },
                { label: "Merge with the previous scene", run: () => run(`merge scene ${index + 1} into ${index}`, mergeScene(state.timeline, index, "previous", state.sentences)) },
                { label: "Merge with the next scene", run: () => run(`merge scene ${index + 1} into ${index + 2}`, mergeScene(state.timeline, index, "next", state.sentences)) },
                { label: "Move one scene earlier", run: () => { if (index > 0) { const order = state.timeline.scenes.map((_s, i) => i); [order[index - 1], order[index]] = [order[index]!, order[index - 1]!]; void actions.reorderScenes(order); } } },
                { label: "Move one scene later", run: () => { if (index < state.timeline.scenes.length - 1) { const order = state.timeline.scenes.map((_s, i) => i); [order[index + 1], order[index]] = [order[index]!, order[index + 1]!]; void actions.reorderScenes(order); } } },
                { label: "Remove the scene and its picture", run: () => void actions.removeScene(index) },
              ].map((item) => (
                <button key={item.label} type="button" role="menuitem" disabled={state.locked} onClick={() => { setMenu(false); item.run(); }} className="flex w-full px-3 py-1.5 text-left text-[12px] text-ink hover:bg-ed-raised disabled:opacity-50">
                  {item.label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </Header>
      <Section title="Picture" summary={file} open={open.has("picture")} onToggle={() => toggle("picture")}>
        <div className="flex items-center gap-2">
          <div className="h-[54px] w-24 shrink-0 overflow-hidden rounded bg-black">{src ? <img src={src} alt="" className="size-full object-cover" /> : null}</div>
          <div className="min-w-0 text-[11px]">
            <p className="truncate text-ink">{file}</p>
            <p className="text-ink-faint">{picture ? `${picture.width} x ${picture.height}` : ""}</p>
            <p className="text-ink-faint">Fit: cover</p>
          </div>
        </div>
        <FilePickButton accept={MEDIA_ACCEPT} size="sm" variant="secondary" disabled={state.locked} onPick={(file) => void actions.replacePicture(index, file)} title="Replace this picture or clip">
          Replace...
        </FilePickButton>
      </Section>
      <MotionSection scene={scene} open={open.has("motion")} onToggle={() => toggle("motion")} />
      <TransitionSection index={index} open={open.has("transition")} onToggle={() => toggle("transition")} />
      <Section title="Popup" summary={popup ? `"${popup.text}"` : "None"} open={open.has("popup")} onToggle={() => toggle("popup")}>
        {popup ? (
          <PopupFields popup={popup} />
        ) : (
          <button type="button" disabled={state.locked} onClick={() => { let id = ""; if (actions.edit(`add a popup to scene ${index + 1}`, (timeline) => { const result = addPopup(timeline, Math.max(scene.start_s, Math.min(playhead, scene.end_s - 1)), "New popup", state.sentences); id = result.id; return result.timeline; }) && id) actions.select({ kind: "popup", id }); }} className="ws-focus h-7 w-fit rounded-md border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50">
            Add a popup
          </button>
        )}
      </Section>
      <NarrationSection scene={scene} open={open.has("narration")} onToggle={() => toggle("narration")} />
    </>
  );
}

function CutInspector({ index }: { index: number }) {
  const { state } = useEditor();
  const [open, setOpen] = useState(true);
  const last = index >= state.timeline.scenes.length - 1;
  return (
    <>
      <Header title={last ? `The ending of scene ${index + 1}` : `Cut between scene ${index + 1} and scene ${index + 2}`} subtitle={`at ${clock(state.timeline.scenes[index]?.end_s ?? 0)}`} />
      <TransitionSection index={index} open={open} onToggle={() => setOpen((v) => !v)} />
      <p className="px-3 py-2 text-[11px] text-ink-faint">Drag the cut on the Scenes lane to move it to another sentence start (Shift: a word, Alt: any frame).</p>
    </>
  );
}

function PopupInspector({ id }: { id: string }) {
  const { state } = useEditor();
  const popup = state.timeline.popups.find((item) => item.id === id);
  if (!popup) return <Header title="Popup" subtitle="This popup is gone." />;
  return (
    <>
      <Header title={`Popup: ${popup.text}`} subtitle={`Belongs to scene ${popup.scene + 1} · ${clock(popup.start_s)} to ${clock(popup.end_s)}`} />
      <div className="flex flex-col gap-2 px-3 py-3">
        <PopupFields popup={popup} />
      </div>
    </>
  );
}

function CaptionInspector({ id }: { id: string }) {
  const { state, actions } = useEditor();
  const cue = state.timeline.captions.cues.find((item) => item.id === id);
  const [text, setText] = useState(cue?.text.replace(/\\N/g, "\n") ?? "");
  useEffect(() => setText(cue?.text.replace(/\\N/g, "\n") ?? ""), [id, cue?.text]);
  if (!cue) return <Header title="Caption" subtitle="This caption is gone." />;
  const position = state.timeline.captions.cues.indexOf(cue);
  const save = () => {
    const next = text.trim().replace(/\n/g, "\\N");
    if (next && next !== cue.text) actions.edit("fix a caption", (timeline) => updateCue(timeline, cue.id, { text: next }));
  };
  return (
    <>
      <Header title={`Caption ${position + 1}`} subtitle={`${clock(cue.start_s)} to ${clock(cue.end_s)} · the words keep their times`} />
      <div className="flex flex-col gap-2 px-3 py-3">
        <textarea value={text} rows={3} disabled={state.locked} onChange={(event) => setText(event.target.value)} onBlur={save} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); (event.target as HTMLTextAreaElement).blur(); } }} className="ws-focus w-full rounded border border-ed-line bg-ed-raised p-1.5 text-[12px] text-ink disabled:opacity-50" aria-label="Caption text" />
        <Row label="Start">
          <NumberBox value={Math.round(cue.start_s * 100) / 100} min={0} step={0.05} suffix="s" disabled={state.locked} onCommit={(value) => actions.edit("re-time a caption", (timeline) => updateCue(timeline, cue.id, { start_s: value }))} />
        </Row>
        <Row label="End">
          <NumberBox value={Math.round(cue.end_s * 100) / 100} min={0} step={0.05} suffix="s" disabled={state.locked} onCommit={(value) => actions.edit("re-time a caption", (timeline) => updateCue(timeline, cue.id, { end_s: value }))} />
        </Row>
        <div className="flex flex-wrap gap-1">
          <button type="button" disabled={state.locked} onClick={() => actions.edit("split a caption", (timeline) => splitCue(timeline, cue.id, Math.ceil(cue.text.split(/\s+/).length / 2)))} className="ws-focus h-6 rounded border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50">
            Split in two
          </button>
          <button type="button" disabled={state.locked} onClick={() => actions.edit("merge two captions", (timeline) => mergeCueWithNext(timeline, cue.id))} className="ws-focus h-6 rounded border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50">
            Merge with next
          </button>
          <button type="button" disabled={state.locked} onClick={() => { actions.edit("remove a caption", (timeline) => removeCue(timeline, cue.id)); actions.select({ kind: "none" }); }} className="ws-focus h-6 rounded px-2 text-[11px] text-fail hover:bg-fail/10 disabled:opacity-50">
            Remove
          </button>
        </div>
        <p className="text-[11px] text-ink-faint">Captions follow the spoken words; the look is set for the whole video under Library, Text.</p>
      </div>
    </>
  );
}

function SentenceInspector({ id }: { id: string }) {
  const { state, actions } = useEditor();
  const position = state.sentences.findIndex((sentence) => sentence.id === id);
  const sentence = state.sentences[position];
  if (!sentence) return <Header title="Sentence" />;
  const pauses = pausesOf(state.sentences);
  const before = pauses.find((pause) => pause.index === position - 1);
  const after = pauses.find((pause) => pause.index === position);
  return (
    <>
      <Header title={`Sentence ${position + 1}`} subtitle={`${clock(sentence.start_s)} to ${clock(sentence.end_s)} · ${sentence.words.length} words`} />
      <div className="flex flex-col gap-2 px-3 py-3 text-[12px]">
        <p className="text-ink">{sentence.text}</p>
        <p className="text-[11px] text-ink-faint">
          Pause before: {before ? seconds(before.length_s) : "none"} · Pause after: {after ? seconds(after.length_s) : "none"}
        </p>
        {after ? (
          <button type="button" onClick={() => actions.select({ kind: "pause", after: sentence.id })} className="ws-focus w-fit text-[11px] text-accent-text hover:underline">
            Shorten the pause after this sentence
          </button>
        ) : null}
        <p className="text-[11px] text-ink-faint">The recording is never changed here. Move its start on the Timing tool of the sentence (drag the cut on the Scenes lane).</p>
      </div>
    </>
  );
}

function PauseInspector({ after }: { after: string }) {
  const { state, actions } = useEditor();
  const pause = pausesOf(state.sentences).find((item) => item.after === after);
  const [custom, setCustom] = useState("0.6");
  if (!pause) return <Header title="Pause" subtitle="This pause is gone." />;
  const shortenTo = (target: number) => {
    const remove = Math.max(0, pause.length_s - target);
    if (remove < 0.05) return;
    void actions.shortenPause(pause.index + 1, -remove);
  };
  const longOnes = pausesOf(state.sentences).filter((item) => item.length_s > 1.0);
  const saved = longOnes.reduce((sum, item) => sum + (item.length_s - 0.6), 0);
  return (
    <>
      <Header title={`Pause after sentence ${pause.index + 1}`} subtitle={`${seconds(pause.length_s)} · ${clock(pause.start_s)} to ${clock(pause.end_s)}`} />
      <div className="flex flex-col gap-2 px-3 py-3">
        <p className="text-[11px] text-ink-muted">Shorten to:</p>
        <div className="flex flex-wrap gap-1">
          <button type="button" disabled={state.locked || pause.length_s <= 0.3} onClick={() => shortenTo(0.3)} className="ws-focus h-6 rounded border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50">0.3 s</button>
          <button type="button" disabled={state.locked || pause.length_s <= 0.6} onClick={() => shortenTo(0.6)} className="ws-focus h-6 rounded border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50">0.6 s (natural)</button>
          <span className="flex items-center gap-1">
            <input type="number" min={0.1} step={0.1} value={custom} onChange={(event) => setCustom(event.target.value)} className={cn(control, "w-16")} aria-label="Shorten to this many seconds" />
            <button type="button" disabled={state.locked} onClick={() => shortenTo(Math.max(0.1, Number(custom) || 0.6))} className="ws-focus h-6 rounded border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi disabled:opacity-50">Apply</button>
          </span>
        </div>
        <p className="text-[11px] text-ink-faint">This is the one edit that moves everything after it earlier. The cut stays at the start of sentence {pause.index + 2}.</p>
        {longOnes.length > 1 ? (
          <p className="text-[11px] text-ink-faint">
            {longOnes.length} pauses are longer than 1.0 s (shortening them all to 0.6 s would save {seconds(saved)}). Shorten them one at a time.
          </p>
        ) : null}
      </div>
    </>
  );
}

function MusicInspector() {
  const { state, actions } = useEditor();
  const music = state.timeline.music;
  const name = music.path ? (music.path.split(/[\\/]/).at(-1) ?? music.path) : "No music";
  return (
    <>
      <Header title="Music" subtitle={music.path ? name : "Pick a track under Library, Music"} />
      <div className="flex flex-col gap-2 px-3 py-3">
        <Row label="Volume">
          <span className="flex items-center gap-2">
            <input type="range" min={-30} max={12} step={0.5} value={music.gain_db} disabled={state.locked || !music.path} onChange={(event) => actions.edit("change the music volume", (timeline) => setMusic(timeline, { gain_db: Number(event.target.value) }))} className="flex-1 accent-accent" aria-label="Music volume" />
            <span className="w-14 text-right tnum text-[11px] text-ink">{music.gain_db} dB</span>
          </span>
        </Row>
        <Row label="Dip">
          <span className="flex items-center gap-2">
            <input type="range" min={-30} max={0} step={0.5} value={music.duck_db} disabled={state.locked || !music.path} onChange={(event) => actions.edit("change the dip under the voice", (timeline) => setMusic(timeline, { duck_db: Number(event.target.value) }))} className="flex-1 accent-accent" aria-label="Dip under the voice" />
            <span className="w-14 text-right tnum text-[11px] text-ink">{music.duck_db} dB</span>
          </span>
        </Row>
        <Row label="Start at">
          <NumberBox value={music.start_s} min={0} step={0.5} suffix="s into the track" disabled={state.locked || !music.path} onCommit={(value) => actions.edit("change where the music starts", (timeline) => setMusic(timeline, { start_s: value }))} />
        </Row>
        <Row label="Fade in">
          <NumberBox value={music.fade_in_s} min={0} max={10} step={0.5} suffix="s" disabled={state.locked || !music.path} onCommit={(value) => actions.edit("change the music fade", (timeline) => setMusic(timeline, { fade_in_s: value }))} />
        </Row>
        <Row label="Fade out">
          <NumberBox value={music.fade_out_s} min={0} max={10} step={0.5} suffix="s" disabled={state.locked || !music.path} onCommit={(value) => actions.edit("change the music fade", (timeline) => setMusic(timeline, { fade_out_s: value }))} />
        </Row>
        <p className="text-[11px] text-ink-faint">{music.path ? (music.license_ok ? "Licence noted." : "No licence noted yet: write it under Details on My edits, or add a licence file next to the track.") : ""}</p>
      </div>
    </>
  );
}

function MarkerInspector({ id }: { id: string }) {
  const { state, actions } = useEditor();
  const marker = state.timeline.markers.find((item) => item.id === id);
  const [label, setLabel] = useState(marker?.label ?? "");
  useEffect(() => setLabel(marker?.label ?? ""), [id, marker?.label]);
  if (!marker) return <Header title="Marker" subtitle="This marker is gone." />;
  return (
    <>
      <Header title={marker.label || "Marker"} subtitle={`at ${clock(marker.t)}`} />
      <div className="flex flex-col gap-2 px-3 py-3">
        <Row label="Label">
          <input value={label} disabled={state.locked} onChange={(event) => setLabel(event.target.value)} onBlur={() => label !== marker.label && actions.edit("rename a marker", (timeline) => updateMarker(timeline, marker.id, { label }))} onKeyDown={(event) => { if (event.key === "Enter") (event.target as HTMLInputElement).blur(); }} className={control} />
        </Row>
        <Row label="Colour">
          <input type="color" value={marker.colour} disabled={state.locked} onChange={(event) => actions.edit("recolour a marker", (timeline) => updateMarker(timeline, marker.id, { colour: event.target.value }))} className="h-6 w-10 rounded border border-ed-line bg-ed-raised" aria-label="Marker colour" />
        </Row>
        <Row label="Time">
          <NumberBox value={Math.round(marker.t * 10) / 10} min={0} max={state.duration} step={0.1} suffix="s" disabled={state.locked} onCommit={(value) => actions.edit("move a marker", (timeline) => updateMarker(timeline, marker.id, { t: value }))} />
        </Row>
        <label className="flex items-center gap-2 text-[11px] text-ink">
          <input type="checkbox" className="accent-accent" checked={marker.chapter} disabled={state.locked} onChange={(event) => actions.edit("mark a chapter", (timeline) => updateMarker(timeline, marker.id, { chapter: event.target.checked }))} />
          Use as a YouTube chapter
        </label>
        <button type="button" disabled={state.locked} onClick={() => { actions.edit("remove a marker", (timeline) => removeMarker(timeline, marker.id)); actions.select({ kind: "none" }); }} className="ws-focus flex h-6 w-fit items-center gap-1 rounded px-1.5 text-[11px] text-fail hover:bg-fail/10 disabled:opacity-50">
          <Trash2 className="size-3.5" aria-hidden /> Remove
        </button>
      </div>
    </>
  );
}

function VideoInspector() {
  const { state, actions } = useEditor();
  const time = state.timeStore.get();
  const index = sceneIndexAt(state.timeline, time);
  const scene = state.timeline.scenes[index];
  const { open, toggle } = useOpenSections(["captions", "output"]);
  return (
    <>
      <Header title="Video" subtitle={scene ? `Under the playhead: ${sceneTitle(scene, state.sentences)}` : "Nothing selected"}>
        {scene ? (
          <button type="button" onClick={() => actions.select({ kind: "scene", index })} className="ws-focus h-7 rounded border border-ed-line bg-ed-raised px-2 text-[11px] text-ink hover:bg-ed-raised-hi">
            Select
          </button>
        ) : null}
      </Header>
      <Section title="Captions" summary={state.timeline.captions.enabled ? "On" : "Off"} open={open.has("captions")} onToggle={() => toggle("captions")}>
        <label className="flex items-center gap-2 text-[11px] text-ink">
          <input type="checkbox" className="accent-accent" checked={state.payload.captions_enabled} disabled={state.locked} onChange={(event) => void actions.applyChoices({ captions_enabled: event.target.checked })} />
          Burn the captions into the video
        </label>
        <label className="flex items-center gap-2 text-[11px] text-ink">
          <input type="checkbox" className="accent-accent" checked={state.payload.popups_enabled} disabled={state.locked} onChange={(event) => void actions.applyChoices({ popups_enabled: event.target.checked })} />
          Show the popups
        </label>
        <p className="text-[11px] text-ink-faint">{state.timeline.captions.cues.length} captions · look: {state.timeline.captions.style.preset}</p>
      </Section>
      <Section title="Output" summary={state.payload.presets.map(renderPresetLabel).join(", ") || "No size ticked"} open={open.has("output")} onToggle={() => toggle("output")}>
        <p className="text-[11px] text-ink-muted">Sizes ticked for the render: {state.payload.presets.map(renderPresetLabel).join(", ") || "none"}. Change them in the Render menu.</p>
        <p className="text-[11px] text-ink-faint">{state.timeline.width} x {state.timeline.height}, {state.fps} fps, {clock(state.duration)}.</p>
      </Section>
      <Section title="Music" summary={state.timeline.music.path ? "On" : "None"} open={open.has("music")} onToggle={() => toggle("music")}>
        <button type="button" onClick={() => actions.select({ kind: "music" })} className="ws-focus w-fit text-[11px] text-accent-text hover:underline">
          Open the music settings
        </button>
      </Section>
    </>
  );
}

/** The inspector follows the selection (docs/design/EDITOR-DESIGN.md section 4.5). */
export function Inspector() {
  const { state } = useEditor();
  const selection: Selection = state.selection;
  let body: ReactNode;
  switch (selection.kind) {
    case "scene":
      body = <SceneInspector key={`scene-${selection.index}`} index={selection.index} />;
      break;
    case "cut":
      body = <CutInspector key={`cut-${selection.index}`} index={selection.index} />;
      break;
    case "popup":
      body = <PopupInspector key={selection.id} id={selection.id} />;
      break;
    case "caption":
      body = <CaptionInspector key={selection.id} id={selection.id} />;
      break;
    case "sentence":
      body = <SentenceInspector key={selection.id} id={selection.id} />;
      break;
    case "pause":
      body = <PauseInspector key={selection.after} after={selection.after} />;
      break;
    case "music":
      body = <MusicInspector />;
      break;
    case "marker":
      body = <MarkerInspector key={selection.id} id={selection.id} />;
      break;
    default:
      body = <VideoInspector />;
  }
  return (
    <aside className="ws-scroll flex w-[336px] min-w-[300px] shrink-0 flex-col overflow-y-auto border-l border-ed-line bg-ed-panel" aria-label="Inspector">
      {body}
    </aside>
  );
}
