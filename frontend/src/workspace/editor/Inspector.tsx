import { ChevronDown, ChevronRight, Lock, MoreHorizontal, Trash2, Unlock } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { projectMediaUrl, sceneImageFile } from "../../api/edit";
import { MEDIA_ACCEPT } from "../../components/edits/NewEditDialog";
import { FilePickButton } from "../../components/form/FilePickButton";
import { TransitionPreview } from "../../components/storyboard/TransitionPicker";
import { cn } from "../../lib/cn";
import { MOTION_PRESETS } from "../../preview/motion";
import { MOTION_EASE_LABELS, MOTION_EASES, POPUP_ANIMS, SPEED_STEPS, defaultAdjust, defaultFrame, isDefaultAdjust, isIdentityFrame, renderPresetLabel, type ColourAdjust, type FrameAdjust, type MotionEase, type TimelinePopup, type TimelineScene } from "../../types/timeline";
import { MOTION_LABELS, POPUP_POSITIONS, POPUP_POSITION_LABELS } from "../../types/storyboard";
import { TRANSITION_PACK_LABELS } from "../../types/transitions";
import { useEditor } from "./context";
import {
  LONG_POPUP_S,
  MIN_CLIP_S,
  MIN_POPUP_S,
  type Selection,
  applyPreset,
  addPopup,
  clock,
  cutLimit,
  findAudioClip,
  findLayerClip,
  mergeCueWithNext,
  mergeScene,
  motionView,
  moveCut,
  pausesOf,
  rectFromView,
  removeAudioClip,
  removeAudioTrack,
  removeCue,
  removeLayer,
  removeLayerClip,
  removeMarker,
  removePopup,
  sceneIndexAt,
  sceneTitle,
  seconds,
  setMotion,
  setMusic,
  setSceneAdjust,
  setSceneClip,
  setSceneFrame,
  setSceneLength,
  setTransition,
  sourceSpan,
  splitCue,
  splitScene,
  swapMotion,
  transitionEntry,
  trimAudioClip,
  trimLayerClip,
  updateAudioClip,
  updateAudioTrack,
  updateCue,
  updateLayer,
  updateLayerClip,
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
const linkButton = "ws-focus h-6 rounded px-2 text-[11px] text-ink-muted hover:bg-ed-raised hover:text-ink disabled:opacity-50";

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

/** A slider with its value next to it (the value is committed on every move). */
function SliderRow({ label, value, min, max, step, suffix, disabled, onChange, format }: { label: string; value: number; min: number; max: number; step: number; suffix?: string; disabled?: boolean; onChange: (value: number) => void; format?: (value: number) => string }) {
  return (
    <Row label={label}>
      <span className="flex items-center gap-2">
        <input type="range" min={min} max={max} step={step} value={value} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))} className="flex-1 accent-accent" aria-label={label} />
        <span className="w-14 text-right tnum text-[11px] text-ink">{format ? format(value) : `${Math.round(value * 100) / 100}${suffix ?? ""}`}</span>
      </span>
    </Row>
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

function SpeedRow({ value, disabled, onChange, max = 4 }: { value: number; disabled?: boolean; onChange: (value: number) => void; max?: number }) {
  return (
    <Row label="Speed">
      <span className="flex items-center gap-1">
        <select value={SPEED_STEPS.includes(value as (typeof SPEED_STEPS)[number]) ? String(value) : "custom"} disabled={disabled} onChange={(event) => { if (event.target.value !== "custom") onChange(Number(event.target.value)); }} className={cn(control, "w-24")}>
          {SPEED_STEPS.filter((step) => step <= max).map((step) => (
            <option key={step} value={String(step)}>
              {step}x{step === 1 ? " (normal)" : ""}
            </option>
          ))}
          <option value="custom">Other</option>
        </select>
        <NumberBox value={value} min={0.25} max={max} step={0.05} suffix="x" disabled={disabled} onCommit={onChange} />
      </span>
    </Row>
  );
}

// ---- the v0.2 sections: frame, adjust ---------------------------------------------------------

function FrameSection({ frame, onChange, disabled, open, onToggle, showFit = true }: { frame: FrameAdjust; onChange: (patch: Partial<FrameAdjust> | null) => void; disabled: boolean; open: boolean; onToggle: () => void; showFit?: boolean }) {
  const pct = (value: number) => `${Math.round(value * 100)}%`;
  const crop = frame.crop;
  const setCrop = (index: number, value: number) => {
    const next = [...crop] as [number, number, number, number];
    next[index] = Math.max(0, Math.min(0.95, value / 100));
    onChange({ crop: next });
  };
  return (
    <Section title="Frame" summary={isIdentityFrame(frame) ? `Fills the frame (${frame.fit})` : `${pct(frame.scale)}, ${frame.rotation ? `${frame.rotation}°, ` : ""}${pct(frame.opacity)} opacity`} open={open} onToggle={onToggle}>
      <SliderRow label="Scale" value={frame.scale} min={0.05} max={3} step={0.01} disabled={disabled} onChange={(scale) => onChange({ scale })} format={pct} />
      <SliderRow label="Position X" value={frame.x} min={-0.75} max={0.75} step={0.005} disabled={disabled} onChange={(x) => onChange({ x })} format={(v) => `${Math.round(v * 100)}%`} />
      <SliderRow label="Position Y" value={frame.y} min={-0.75} max={0.75} step={0.005} disabled={disabled} onChange={(y) => onChange({ y })} format={(v) => `${Math.round(v * 100)}%`} />
      <SliderRow label="Rotation" value={frame.rotation} min={-180} max={180} step={1} disabled={disabled} onChange={(rotation) => onChange({ rotation })} format={(v) => `${Math.round(v)}°`} />
      <SliderRow label="Opacity" value={frame.opacity} min={0} max={1} step={0.01} disabled={disabled} onChange={(opacity) => onChange({ opacity })} format={pct} />
      {showFit ? (
        <Row label="Fit">
          <select value={frame.fit} disabled={disabled} onChange={(event) => onChange({ fit: event.target.value === "cover" ? "cover" : "contain" })} className={control}>
            <option value="contain">Whole picture inside the frame</option>
            <option value="cover">Fill the frame (edges cut off)</option>
          </select>
        </Row>
      ) : null}
      <Row label="Crop">
        <span className="grid grid-cols-4 gap-1">
          {(["Left", "Top", "Right", "Bottom"] as const).map((side, index) => (
            <span key={side} className="flex flex-col gap-0.5">
              <input type="number" min={0} max={95} step={1} value={Math.round((crop[index] ?? 0) * 100)} disabled={disabled} onChange={(event) => setCrop(index, Number(event.target.value))} className={control} aria-label={`Crop ${side.toLowerCase()}`} title={`Crop from the ${side.toLowerCase()}, percent`} />
              <span className="text-center text-[9px] text-ink-faint">{side}</span>
            </span>
          ))}
        </span>
      </Row>
      <div className="flex items-center gap-3 text-[11px] text-ink">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" className="accent-accent" checked={frame.flip_h} disabled={disabled} onChange={(event) => onChange({ flip_h: event.target.checked })} />
          Flip left-right
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" className="accent-accent" checked={frame.flip_v} disabled={disabled} onChange={(event) => onChange({ flip_v: event.target.checked })} />
          Flip up-down
        </label>
        <button type="button" disabled={disabled || isIdentityFrame(frame)} onClick={() => onChange(null)} className={cn(linkButton, "ml-auto")}>
          Reset
        </button>
      </div>
    </Section>
  );
}

function AdjustSection({ adjust, onChange, disabled, open, onToggle }: { adjust: ColourAdjust | null; onChange: (patch: Partial<ColourAdjust> | null) => void; disabled: boolean; open: boolean; onToggle: () => void }) {
  const value = adjust ?? defaultAdjust();
  const show = (v: number) => `${v > 0 ? "+" : ""}${Math.round(v * 100)}`;
  return (
    <Section title="Adjust" summary={isDefaultAdjust(adjust) ? "As shot" : `Brightness ${show(value.brightness)}, contrast ${show(value.contrast)}, colour ${show(value.saturation)}`} open={open} onToggle={onToggle}>
      <SliderRow label="Brightness" value={value.brightness} min={-1} max={1} step={0.01} disabled={disabled} onChange={(brightness) => onChange({ brightness })} format={show} />
      <SliderRow label="Contrast" value={value.contrast} min={-1} max={1} step={0.01} disabled={disabled} onChange={(contrast) => onChange({ contrast })} format={show} />
      <SliderRow label="Colour" value={value.saturation} min={-1} max={1} step={0.01} disabled={disabled} onChange={(saturation) => onChange({ saturation })} format={show} />
      <div className="flex items-center justify-between text-[11px] text-ink-faint">
        <span>0 = as shot. Colour -100 makes it black and white.</span>
        <button type="button" disabled={disabled || isDefaultAdjust(adjust)} onClick={() => onChange(null)} className={linkButton}>
          Reset
        </button>
      </div>
    </Section>
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
        <button type="button" disabled={state.locked} onClick={() => actions.edit(`swap the start and end of scene ${scene.index + 1}`, (timeline) => swapMotion(timeline, scene.index))} className={cn(linkButton, "ml-auto")}>
          Swap
        </button>
        <button type="button" disabled={state.locked || !spec} onClick={() => actions.edit(`reset the move of scene ${scene.index + 1}`, (timeline) => applyPreset(timeline, scene.index, scene.motion.preset, picture))} className={linkButton}>
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

/** A clip scene's clip: speed, where in the file it starts, its sound. */
function ClipSection({ scene, open, onToggle }: { scene: TimelineScene; open: boolean; onToggle: () => void }) {
  const { state, actions } = useEditor();
  const clip = scene.clip;
  if (!clip) return null;
  const span = sourceSpan({ in_s: clip.in_s, speed: clip.speed, start_s: scene.start_s, end_s: scene.end_s });
  const over = clip.duration_s !== null && span[1] > clip.duration_s + 0.05;
  return (
    <Section title="Clip" summary={`${clip.speed}x · from ${clock(clip.in_s)}${clip.keep_sound ? " · with sound" : ""}`} open={open} onToggle={onToggle}>
      <SpeedRow value={clip.speed} disabled={state.locked} onChange={(speed) => actions.edit(`set the speed of scene ${scene.index + 1}`, (doc) => setSceneClip(doc, scene.index, { speed }))} />
      <Row label="From">
        <NumberBox value={Math.round(clip.in_s * 100) / 100} min={0} max={clip.duration_s !== null ? Math.max(0, clip.duration_s - 0.1) : undefined} step={0.1} suffix="s into the file" disabled={state.locked} onCommit={(in_s) => actions.edit(`change where scene ${scene.index + 1} starts in its clip`, (doc) => setSceneClip(doc, scene.index, { in_s }))} />
      </Row>
      <p className={cn("text-[11px]", over ? "text-warn" : "text-ink-faint")}>
        Uses {clock(span[0])} to {clock(span[1])} of the file{clip.duration_s !== null ? ` (${clock(clip.duration_s)} long)` : ""}. {over ? "The clip runs out before the scene ends: its last frame holds." : "The scene's length is set by the narration or the Length field."}
      </p>
      <label className="flex items-center gap-2 text-[11px] text-ink">
        <input type="checkbox" className="accent-accent" checked={clip.keep_sound} disabled={state.locked} onChange={(event) => actions.edit(event.target.checked ? `keep the sound of scene ${scene.index + 1}` : `mute scene ${scene.index + 1}`, (doc) => setSceneClip(doc, scene.index, { keep_sound: event.target.checked }))} />
        Keep the clip's own sound
      </label>
      <SliderRow label="Volume" value={clip.gain_db} min={-30} max={12} step={0.5} disabled={state.locked || !clip.keep_sound} onChange={(gain_db) => actions.edit(`change the volume of scene ${scene.index + 1}`, (doc) => setSceneClip(doc, scene.index, { gain_db }))} format={(v) => `${v > 0 ? "+" : ""}${v} dB`} />
    </Section>
  );
}

// ---- the inspector by selection ----------------------------------------------------------------

function SceneInspector({ index }: { index: number }) {
  const { state, actions } = useEditor();
  const scene = state.timeline.scenes[index];
  const { open, toggle } = useOpenSections(["picture", "clip", "motion", "transition", "popup"]);
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
  const file = scene.clip ? (scene.clip.path.split("/").at(-1) ?? scene.clip.path) : (scene.image.split("/").at(-1) ?? "");
  const playhead = state.timeStore.get();
  const ripple = state.timingSource === "scenes";
  const run = (label: string, result: { timeline: typeof state.timeline; note: string | null }) => {
    if (result.note) actions.notify("info", result.note);
    else actions.edit(label, () => result.timeline);
  };
  return (
    <>
      <Header title={sceneTitle(scene, state.sentences)} subtitle={`${clock(scene.start_s)} to ${clock(scene.end_s)} · ${seconds(length)} · ${scene.clip ? "a clip" : "a picture"}${scene.sentence_ids.length > 0 && !ripple ? ` · ${scene.sentence_ids.length} sentence${scene.sentence_ids.length === 1 ? "" : "s"}` : ""}`}>
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
      <Section title={scene.clip ? "Clip file" : "Picture"} summary={file} open={open.has("picture")} onToggle={() => toggle("picture")}>
        <div className="flex items-center gap-2">
          <div className="h-[54px] w-24 shrink-0 overflow-hidden rounded bg-black">{src ? <img src={src} alt="" className="size-full object-cover" /> : null}</div>
          <div className="min-w-0 text-[11px]">
            <p className="truncate text-ink">{file}</p>
            <p className="text-ink-faint">{picture ? `${picture.width} x ${picture.height}` : ""}</p>
            <p className="text-ink-faint">{scene.clip ? "Fills the frame unless the Frame section says otherwise" : "Fit: cover (the camera move picks the view)"}</p>
          </div>
        </div>
        <Row label="Length">
          <NumberBox value={Math.round(length * 10) / 10} min={ripple ? 0.5 : 2} step={0.1} suffix={ripple ? "s (moves everything after it)" : "s (moves the next cut)"} disabled={state.locked} onCommit={(value) => run(`set the length of scene ${index + 1}`, setSceneLength(state.timeline, index, value, state.sentences, ripple ? "ripple" : "roll"))} />
        </Row>
        <FilePickButton accept={MEDIA_ACCEPT} size="sm" variant="secondary" disabled={state.locked} onPick={(file) => void actions.replacePicture(index, file)} title="Replace this picture or clip">
          Replace...
        </FilePickButton>
      </Section>
      {scene.clip ? <ClipSection scene={scene} open={open.has("clip")} onToggle={() => toggle("clip")} /> : null}
      {scene.clip ? (
        <FrameSection frame={scene.frame ?? defaultFrame("cover")} disabled={state.locked} open={open.has("frame")} onToggle={() => toggle("frame")} onChange={(patch) => actions.edit(`change the frame of scene ${index + 1}`, (doc) => setSceneFrame(doc, index, patch))} />
      ) : (
        <MotionSection scene={scene} open={open.has("motion")} onToggle={() => toggle("motion")} />
      )}
      <AdjustSection adjust={scene.adjust} disabled={state.locked} open={open.has("adjust")} onToggle={() => toggle("adjust")} onChange={(patch) => actions.edit(`adjust the colour of scene ${index + 1}`, (doc) => setSceneAdjust(doc, index, patch))} />
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
      {!ripple ? <NarrationSection scene={scene} open={open.has("narration")} onToggle={() => toggle("narration")} /> : null}
    </>
  );
}

function LayerClipInspector({ id }: { id: string }) {
  const { state, actions } = useEditor();
  const found = findLayerClip(state.timeline, id);
  const { open, toggle } = useOpenSections(["time", "frame"]);
  if (!found) return <Header title="Layer clip" subtitle="This clip is gone." />;
  const { layer, clip } = found;
  const disabled = state.locked || layer.locked;
  const source = state.sources.get(clip.source);
  const thumb = source?.thumb_url ?? state.media?.items.find((item) => item.path === clip.source)?.thumb_url ?? null;
  const span = sourceSpan(clip);
  const length = clip.end_s - clip.start_s;
  const edit = (label: string, patch: Partial<Omit<typeof clip, "id">>) => actions.edit(label, (doc) => updateLayerClip(doc, id, patch));
  return (
    <>
      <Header title={clip.name || clip.source.split("/").at(-1) || "Layer clip"} subtitle={`On ${layer.name} · ${clock(clip.start_s)} to ${clock(clip.end_s)} · ${seconds(length)}${layer.locked ? " · the layer is locked" : ""}`}>
        <button type="button" disabled={disabled} onClick={() => { actions.edit("remove a layer clip", (doc) => removeLayerClip(doc, id)); actions.select({ kind: "none" }); }} className="ws-focus grid size-7 place-items-center rounded text-ink-faint hover:bg-fail/10 hover:text-fail disabled:opacity-50" title="Remove this clip from the layer" aria-label="Remove">
          <Trash2 className="size-4" aria-hidden />
        </button>
      </Header>
      <div className="flex items-center gap-2 border-b border-ed-line px-3 py-2">
        <div className={cn("shrink-0 overflow-hidden rounded bg-black", state.timeline.aspect === "9:16" ? "h-16 w-9" : "h-[54px] w-24")}>{thumb ? <img src={thumb} alt="" className="size-full object-cover" /> : null}</div>
        <div className="min-w-0 text-[11px]">
          <p className="truncate text-ink">{clip.source.split("/").at(-1)}</p>
          <p className="text-ink-faint">{clip.kind === "clip" ? "A clip" : "A picture"}{source && source.width > 0 ? ` · ${source.width} x ${source.height}` : ""}{clip.duration_s !== null ? ` · ${clock(clip.duration_s)} long` : ""}</p>
        </div>
      </div>
      <Section title="Time" summary={`${clock(clip.start_s)} · ${seconds(length)}${clip.kind === "clip" ? ` · ${clip.speed}x` : ""}`} open={open.has("time")} onToggle={() => toggle("time")}>
        <Row label="Start">
          <NumberBox value={Math.round(clip.start_s * 100) / 100} min={0} step={0.1} suffix="s" disabled={disabled} onCommit={(value) => actions.edit("move a layer clip", (doc) => updateLayerClip(doc, id, { start_s: value, end_s: value + length }))} />
        </Row>
        <Row label="Length">
          <NumberBox value={Math.round(length * 100) / 100} min={MIN_CLIP_S} step={0.1} suffix="s" disabled={disabled} onCommit={(value) => actions.edit("trim a layer clip", (doc) => trimLayerClip(doc, id, "end", clip.start_s + value))} />
        </Row>
        {clip.kind === "clip" ? (
          <>
            <SpeedRow value={clip.speed} disabled={disabled} onChange={(speed) => edit("set the speed of a layer clip", { speed })} />
            <Row label="From">
              <NumberBox value={Math.round(clip.in_s * 100) / 100} min={0} step={0.1} suffix="s into the file" disabled={disabled} onCommit={(in_s) => edit("change where a layer clip starts in its file", { in_s })} />
            </Row>
            <p className="text-[11px] text-ink-faint">Uses {clock(span[0])} to {clock(span[1])} of the file.{clip.duration_s !== null && span[1] > clip.duration_s + 0.05 ? " The clip runs out before the end: its last frame holds." : ""}</p>
          </>
        ) : null}
      </Section>
      <FrameSection frame={clip.frame} disabled={disabled} open={open.has("frame")} onToggle={() => toggle("frame")} onChange={(patch) => actions.edit("change the frame of a layer clip", (doc) => updateLayerClip(doc, id, { frame: patch === null ? { ...defaultFrame(clip.frame.fit) } : { ...clip.frame, ...patch } }))} />
      <AdjustSection adjust={clip.adjust} disabled={disabled} open={open.has("adjust")} onToggle={() => toggle("adjust")} onChange={(patch) => actions.edit("adjust the colour of a layer clip", (doc) => updateLayerClip(doc, id, { adjust: patch === null ? defaultAdjust() : { ...clip.adjust, ...patch } }))} />
      {clip.kind === "clip" ? (
        <Section title="Sound" summary={clip.keep_sound ? `On, ${clip.gain_db} dB` : "Muted"} open={open.has("sound")} onToggle={() => toggle("sound")}>
          <label className="flex items-center gap-2 text-[11px] text-ink">
            <input type="checkbox" className="accent-accent" checked={clip.keep_sound} disabled={disabled} onChange={(event) => edit(event.target.checked ? "keep the sound of a layer clip" : "mute a layer clip", { keep_sound: event.target.checked })} />
            Keep the clip's own sound
          </label>
          <SliderRow label="Volume" value={clip.gain_db} min={-30} max={12} step={0.5} disabled={disabled || !clip.keep_sound} onChange={(gain_db) => edit("change the volume of a layer clip", { gain_db })} format={(v) => `${v > 0 ? "+" : ""}${v} dB`} />
        </Section>
      ) : null}
      <p className="px-3 py-2 text-[11px] text-ink-faint">Drag the clip along its lane to move it; drag its edges to trim. S splits it at the playhead.</p>
    </>
  );
}

function AudioClipInspector({ id }: { id: string }) {
  const { state, actions } = useEditor();
  const found = findAudioClip(state.timeline, id);
  const { open, toggle } = useOpenSections(["time", "sound"]);
  if (!found) return <Header title="Sound clip" subtitle="This clip is gone." />;
  const { track, clip } = found;
  const disabled = state.locked;
  const length = clip.end_s - clip.start_s;
  const span = sourceSpan(clip);
  const edit = (label: string, patch: Partial<Omit<typeof clip, "id">>) => actions.edit(label, (doc) => updateAudioClip(doc, id, patch));
  return (
    <>
      <Header title={clip.name || clip.source.split("/").at(-1) || "Sound clip"} subtitle={`On ${track.name}${track.muted ? " (muted)" : ""} · ${clock(clip.start_s)} to ${clock(clip.end_s)} · ${seconds(length)}`}>
        <button type="button" disabled={disabled} onClick={() => { actions.edit("remove a sound clip", (doc) => removeAudioClip(doc, id)); actions.select({ kind: "none" }); }} className="ws-focus grid size-7 place-items-center rounded text-ink-faint hover:bg-fail/10 hover:text-fail disabled:opacity-50" title="Remove this clip from the track" aria-label="Remove">
          <Trash2 className="size-4" aria-hidden />
        </button>
      </Header>
      {clip.text ? (
        <div className="border-b border-ed-line px-3 py-2 text-[11px]">
          <p className="text-ink-faint">An AI voice{clip.voice ? ` (${clip.voice})` : ""}. It says:</p>
          <p className="mt-1 max-h-24 overflow-y-auto text-ink">{clip.text}</p>
        </div>
      ) : null}
      <Section title="Time" summary={`${clock(clip.start_s)} · ${seconds(length)} · ${clip.speed}x`} open={open.has("time")} onToggle={() => toggle("time")}>
        <Row label="Start">
          <NumberBox value={Math.round(clip.start_s * 100) / 100} min={0} step={0.1} suffix="s" disabled={disabled} onCommit={(value) => actions.edit("move a sound clip", (doc) => updateAudioClip(doc, id, { start_s: value, end_s: value + length }))} />
        </Row>
        <Row label="Length">
          <NumberBox value={Math.round(length * 100) / 100} min={MIN_CLIP_S} step={0.1} suffix="s" disabled={disabled} onCommit={(value) => actions.edit("trim a sound clip", (doc) => trimAudioClip(doc, id, "end", clip.start_s + value))} />
        </Row>
        <SpeedRow value={clip.speed} disabled={disabled} onChange={(speed) => edit("set the speed of a sound clip", { speed })} max={clip.voice ? 2 : 4} />
        <Row label="From">
          <NumberBox value={Math.round(clip.in_s * 100) / 100} min={0} step={0.1} suffix="s into the file" disabled={disabled} onCommit={(in_s) => edit("change where a sound clip starts in its file", { in_s })} />
        </Row>
        <p className="text-[11px] text-ink-faint">Plays {clock(span[0])} to {clock(span[1])} of the file{clip.duration_s !== null ? ` (${clock(clip.duration_s)} long)` : ""}. {clip.voice ? "A voice sounds natural up to about 1.5x." : ""}</p>
      </Section>
      <Section title="Sound" summary={`${clip.gain_db > 0 ? "+" : ""}${clip.gain_db} dB · fades ${clip.fade_in_s} s / ${clip.fade_out_s} s`} open={open.has("sound")} onToggle={() => toggle("sound")}>
        <SliderRow label="Volume" value={clip.gain_db} min={-30} max={12} step={0.5} disabled={disabled} onChange={(gain_db) => edit("change the volume of a sound clip", { gain_db })} format={(v) => `${v > 0 ? "+" : ""}${v} dB`} />
        <Row label="Fade in">
          <NumberBox value={clip.fade_in_s} min={0} max={10} step={0.1} suffix="s" disabled={disabled} onCommit={(fade_in_s) => edit("change the fade of a sound clip", { fade_in_s })} />
        </Row>
        <Row label="Fade out">
          <NumberBox value={clip.fade_out_s} min={0} max={10} step={0.1} suffix="s" disabled={disabled} onCommit={(fade_out_s) => edit("change the fade of a sound clip", { fade_out_s })} />
        </Row>
        <p className="text-[11px] text-ink-faint">The music still dips under the narration only; a sound clip plays at its own volume.</p>
      </Section>
    </>
  );
}

function LayerInspector({ id }: { id: string }) {
  const { state, actions } = useEditor();
  const layer = state.timeline.layers.find((item) => item.id === id);
  const [name, setName] = useState(layer?.name ?? "");
  useEffect(() => setName(layer?.name ?? ""), [id, layer?.name]);
  if (!layer) return <Header title="Layer" subtitle="This layer is gone." />;
  const position = state.timeline.layers.indexOf(layer);
  return (
    <>
      <Header title={layer.name} subtitle={`${layer.clips.length} clip${layer.clips.length === 1 ? "" : "s"} · ${position === state.timeline.layers.length - 1 ? "the top layer" : `layer ${position + 1} of ${state.timeline.layers.length}`}`}>
        <button type="button" disabled={state.locked} onClick={() => { actions.edit(`remove ${layer.name}`, (doc) => removeLayer(doc, id)); actions.select({ kind: "none" }); }} className="ws-focus grid size-7 place-items-center rounded text-ink-faint hover:bg-fail/10 hover:text-fail disabled:opacity-50" title="Remove this layer and its clips" aria-label="Remove">
          <Trash2 className="size-4" aria-hidden />
        </button>
      </Header>
      <div className="flex flex-col gap-2 px-3 py-3">
        <Row label="Name">
          <input value={name} disabled={state.locked} onChange={(event) => setName(event.target.value)} onBlur={() => name.trim() && name !== layer.name && actions.edit("rename a layer", (doc) => updateLayer(doc, id, { name: name.trim() }))} onKeyDown={(event) => { if (event.key === "Enter") (event.target as HTMLInputElement).blur(); }} className={control} />
        </Row>
        <label className="flex items-center gap-2 text-[11px] text-ink">
          <input type="checkbox" className="accent-accent" checked={layer.visible} disabled={state.locked} onChange={(event) => actions.edit(event.target.checked ? `show ${layer.name}` : `hide ${layer.name}`, (doc) => updateLayer(doc, id, { visible: event.target.checked }))} />
          Shown in the video
        </label>
        <label className="flex items-center gap-2 text-[11px] text-ink">
          <input type="checkbox" className="accent-accent" checked={layer.locked} disabled={state.locked} onChange={(event) => actions.edit(event.target.checked ? `lock ${layer.name}` : `unlock ${layer.name}`, (doc) => updateLayer(doc, id, { locked: event.target.checked }))} />
          Locked (its clips cannot be moved)
        </label>
        <p className="text-[11px] text-ink-faint">Layers sit over the scenes and under the popups and captions. Drop pictures and clips from Media onto this lane, or press "Add as a layer".</p>
      </div>
    </>
  );
}

function TrackInspector({ id }: { id: string }) {
  const { state, actions } = useEditor();
  const track = state.timeline.audio_tracks.find((item) => item.id === id);
  const [name, setName] = useState(track?.name ?? "");
  useEffect(() => setName(track?.name ?? ""), [id, track?.name]);
  if (!track) return <Header title="Sound track" subtitle="This track is gone." />;
  return (
    <>
      <Header title={track.name} subtitle={`${track.clips.length} clip${track.clips.length === 1 ? "" : "s"}${track.muted ? " · muted" : ""}`}>
        <button type="button" disabled={state.locked} onClick={() => { actions.edit(`remove ${track.name}`, (doc) => removeAudioTrack(doc, id)); actions.select({ kind: "none" }); }} className="ws-focus grid size-7 place-items-center rounded text-ink-faint hover:bg-fail/10 hover:text-fail disabled:opacity-50" title="Remove this track and its clips" aria-label="Remove">
          <Trash2 className="size-4" aria-hidden />
        </button>
      </Header>
      <div className="flex flex-col gap-2 px-3 py-3">
        <Row label="Name">
          <input value={name} disabled={state.locked} onChange={(event) => setName(event.target.value)} onBlur={() => name.trim() && name !== track.name && actions.edit("rename a sound track", (doc) => updateAudioTrack(doc, id, { name: name.trim() }))} onKeyDown={(event) => { if (event.key === "Enter") (event.target as HTMLInputElement).blur(); }} className={control} />
        </Row>
        <label className="flex items-center gap-2 text-[11px] text-ink">
          <input type="checkbox" className="accent-accent" checked={track.muted} disabled={state.locked} onChange={(event) => actions.edit(event.target.checked ? `mute ${track.name}` : `unmute ${track.name}`, (doc) => updateAudioTrack(doc, id, { muted: event.target.checked }))} />
          Muted (left out of the video)
        </label>
        <SliderRow label="Volume" value={track.gain_db} min={-30} max={12} step={0.5} disabled={state.locked} onChange={(gain_db) => actions.edit(`change the volume of ${track.name}`, (doc) => updateAudioTrack(doc, id, { gain_db }))} format={(v) => `${v > 0 ? "+" : ""}${v} dB`} />
        <p className="text-[11px] text-ink-faint">Every clip on the track plays at its own volume plus this one. Drop sound files from Media onto this lane, or press "Add as a sound".</p>
      </div>
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
      <Header title="Music" subtitle={music.path ? name : "Pick a sound file under Media and press Use as music"} />
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
  const layers = state.timeline.layers.length;
  const tracks = state.timeline.audio_tracks.length;
  return (
    <>
      <Header title="Video" subtitle={scene ? `Under the playhead: ${sceneTitle(scene, state.sentences)}` : state.timeline.scenes.length === 0 ? "No scenes yet: import files under Media" : "Nothing selected"}>
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
      <Section title="Layers and sound" summary={`${layers} layer${layers === 1 ? "" : "s"}, ${tracks} sound track${tracks === 1 ? "" : "s"}`} open={open.has("layers")} onToggle={() => toggle("layers")}>
        <p className="text-[11px] text-ink-muted">{layers} layer{layers === 1 ? "" : "s"} over the scenes and {tracks} sound track{tracks === 1 ? "" : "s"} under the narration. Add more with the toolbar buttons of the timeline; click a lane's name to open it here.</p>
        <p className="text-[11px] text-ink-faint">{state.timingSource === "scenes" ? "No narration: each scene has its own length (the Length field moves everything after it)." : "With a narration, the scenes follow the sentences; a scene's Length moves the next cut."}</p>
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
    case "layerclip":
      body = <LayerClipInspector key={selection.id} id={selection.id} />;
      break;
    case "audioclip":
      body = <AudioClipInspector key={selection.id} id={selection.id} />;
      break;
    case "layer":
      body = <LayerInspector key={selection.id} id={selection.id} />;
      break;
    case "track":
      body = <TrackInspector key={selection.id} id={selection.id} />;
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
