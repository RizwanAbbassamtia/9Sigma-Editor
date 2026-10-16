import { Clapperboard, Film, Save } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { errorMessage } from "../../api/client";
import { useRenderSettings, useUpdateSettings } from "../../api/settings";
import {
  CAPTION_STYLE_LABELS,
  CAPTION_STYLES,
  QUALITIES,
  QUALITY_DESCRIPTIONS,
  QUALITY_LABELS,
  type CaptionsSettings,
  type CaptionStyleName,
  type Quality,
  type RenderSettings,
  type RenderSettingsResolved,
  type SettingsUpdate,
  X264_PRESET_LABELS,
  X264_PRESETS,
  type X264Preset,
} from "../../types/settings";
import { CAPTION_PRESET_LABELS, CAPTION_PRESETS, RENDER_PRESET_DESCRIPTIONS, RENDER_PRESET_LABELS, RENDER_PRESETS, type CaptionPreset, type RenderPreset } from "../../types/timeline";
import {
  MOTION_STYLE_DESCRIPTIONS,
  MOTION_STYLE_LABELS,
  MOTION_STYLES,
  TRANSITION_PACK_DESCRIPTIONS,
  TRANSITION_PACK_LABELS,
  TRANSITION_PACKS,
  type MotionStyle,
  type TransitionPack,
} from "../../types/transitions";
import { TransitionPackPreview } from "../storyboard/TransitionPackPreview";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Select } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState } from "../ui/States";
import { useToast } from "../ui/Toast";

interface Draft {
  render: RenderSettings;
  captions: CaptionsSettings;
}

function pickDraft(resolved: RenderSettingsResolved): Draft {
  return {
    render: { ...resolved.render, default_presets: [...resolved.render.default_presets] },
    captions: { ...resolved.captions },
  };
}

const AUTO = "__auto__";

/**
 * Settings > Render: what a new edit starts with (docs/CONTRACT-v0.1.md section 6): the sizes,
 * the quality, the encoder speed, the H.265 copy, 4K, captions, the transition pack and the
 * camera move style. The editor can change all of it per edit.
 */
export function RenderCard() {
  const { query, resolved } = useRenderSettings();
  const update = useUpdateSettings();
  const { toast } = useToast();

  const [draft, setDraft] = useState<Draft>(() => pickDraft(resolved));
  const resolvedKey = useMemo(() => JSON.stringify(pickDraft(resolved)), [resolved]);
  const lastResolvedKey = useRef("");
  useEffect(() => {
    if (resolvedKey !== lastResolvedKey.current) {
      lastResolvedKey.current = resolvedKey;
      setDraft(pickDraft(resolved));
    }
  }, [resolved, resolvedKey]);

  const changed = useMemo(() => {
    const base = pickDraft(resolved);
    return (Object.keys(draft) as Array<keyof Draft>).filter((key) => JSON.stringify(draft[key]) !== JSON.stringify(base[key]));
  }, [draft, resolved]);

  const presetsOk = draft.render.default_presets.length > 0;

  const setRender = (patch: Partial<RenderSettings>) => setDraft((current) => ({ ...current, render: { ...current.render, ...patch } }));
  const setCaptions = (patch: Partial<CaptionsSettings>) => setDraft((current) => ({ ...current, captions: { ...current.captions, ...patch } }));

  const togglePreset = (preset: RenderPreset, checked: boolean) => {
    const next = RENDER_PRESETS.filter((id) => (id === preset ? checked : draft.render.default_presets.includes(id)));
    setRender({ default_presets: next });
  };

  const toggle4k = (enabled: boolean) => {
    setRender({
      enable_4k: enabled,
      // 4K off also takes it out of the defaults, so no edit renders it by accident.
      default_presets: enabled ? draft.render.default_presets : draft.render.default_presets.filter((id) => id !== "2160p"),
    });
  };

  const save = async () => {
    if (changed.length === 0 || !presetsOk) return;
    const body: SettingsUpdate = {};
    if (changed.includes("render")) body.render = draft.render;
    if (changed.includes("captions")) body.captions = draft.captions;
    try {
      await update.mutateAsync(body);
      toast({ tone: "success", title: "Render settings saved" });
    } catch (error) {
      toast({ tone: "error", title: "Could not save the render settings", description: errorMessage(error) });
    }
  };

  const styleKnown = (CAPTION_STYLES as readonly string[]).includes(draft.captions.style);
  const presetKnown = (CAPTION_PRESETS as readonly string[]).includes(draft.captions.preset);

  return (
    <Card
      id="render"
      title="Render"
      description="What a new edit starts with. Every choice here can be changed per edit in the editor."
      actions={
        <Button variant="primary" size="sm" icon={<Save />} disabled={changed.length === 0 || !presetsOk} loading={update.isPending} onClick={() => void save()}>
          Save render settings
        </Button>
      }
    >
      {query.isPending ? (
        <LoadingBlock label="Loading render settings..." />
      ) : query.isError ? (
        <ErrorState error={query.error} title="Could not load the render settings" onRetry={() => void query.refetch()} />
      ) : (
        <div className="flex flex-col gap-7">
          <section className="grid gap-x-6 gap-y-5 md:grid-cols-[260px_1fr]">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-ink">
                <Clapperboard className="size-4 text-ink-faint" aria-hidden />
                Sizes ticked by default
              </div>
              <p className="mt-1 text-xs text-ink-muted">A new edit renders these sizes. The editor can tick or untick sizes per edit.</p>
            </div>
            <div className="flex flex-col gap-2">
              {RENDER_PRESETS.map((preset) => {
                const blocked = preset === "2160p" && !draft.render.enable_4k;
                return (
                  <label key={preset} className={blocked ? "flex items-start gap-3 opacity-60" : "flex items-start gap-3"}>
                    <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={draft.render.default_presets.includes(preset)} disabled={blocked} onChange={(event) => togglePreset(preset, event.target.checked)} />
                    <span>
                      <span className="text-[13px] font-medium text-ink">{RENDER_PRESET_LABELS[preset]}</span>
                      <span className="block text-xs text-ink-muted">{blocked ? "Turn on 4K below first." : RENDER_PRESET_DESCRIPTIONS[preset]}</span>
                    </span>
                  </label>
                );
              })}
              {!presetsOk ? <p className="text-xs text-fail">Tick at least one size, or no final video is made.</p> : null}
            </div>
          </section>

          <section className="grid gap-x-6 gap-y-5 md:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Quality</span>
              <Select value={draft.render.quality} onChange={(event) => setRender({ quality: event.target.value as Quality })}>
                {QUALITIES.map((quality) => (
                  <option key={quality} value={quality}>
                    {QUALITY_LABELS[quality]}
                  </option>
                ))}
              </Select>
              <span className="text-xs text-ink-faint">{QUALITY_DESCRIPTIONS[draft.render.quality]}</span>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Encoder speed</span>
              <Select value={draft.render.x264_preset ?? AUTO} onChange={(event) => setRender({ x264_preset: event.target.value === AUTO ? null : (event.target.value as X264Preset) })}>
                <option value={AUTO}>Let the quality decide</option>
                {X264_PRESETS.map((preset) => (
                  <option key={preset} value={preset}>
                    {X264_PRESET_LABELS[preset]}
                  </option>
                ))}
              </Select>
              <span className="text-xs text-ink-faint">Slower settings take longer and make smaller files at the same quality. Leave this to the quality unless you know what you want.</span>
            </label>
            <label className="flex items-start gap-3">
              <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={draft.render.h265_copy} onChange={(event) => setRender({ h265_copy: event.target.checked })} />
              <span>
                <span className="text-[13px] font-medium text-ink">Also make an H.265 copy</span>
                <span className="block text-xs text-ink-muted">A second, smaller file of every size (slower). Good for keeping; YouTube is happy with the normal file.</span>
              </span>
            </label>
            <label className="flex items-start gap-3">
              <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={draft.render.enable_4k} onChange={(event) => toggle4k(event.target.checked)} />
              <span>
                <span className="text-[13px] font-medium text-ink">Allow 4K (2160p)</span>
                <span className="block text-xs text-ink-muted">Four times the pixels of 1080p. Rendering takes a long time and a lot of memory.</span>
              </span>
            </label>
          </section>

          <section className="grid gap-x-6 gap-y-5 md:grid-cols-3">
            <label className="flex items-start gap-3">
              <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={draft.captions.enabled} onChange={(event) => setCaptions({ enabled: event.target.checked })} />
              <span>
                <span className="text-[13px] font-medium text-ink">Burn captions into the video</span>
                <span className="block text-xs text-ink-muted">Captions timed to the narration, drawn at the bottom of the picture. The editor can switch them off per edit.</span>
              </span>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Caption colours</span>
              <Select value={draft.captions.style} disabled={!draft.captions.enabled} onChange={(event) => setCaptions({ style: event.target.value })}>
                {!styleKnown && draft.captions.style ? <option value={draft.captions.style}>{draft.captions.style}</option> : null}
                {CAPTION_STYLES.map((style) => (
                  <option key={style} value={style}>
                    {CAPTION_STYLE_LABELS[style as CaptionStyleName]}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Caption look</span>
              <Select value={draft.captions.preset} disabled={!draft.captions.enabled} onChange={(event) => setCaptions({ preset: event.target.value })}>
                {!presetKnown && draft.captions.preset ? <option value={draft.captions.preset}>{draft.captions.preset}</option> : null}
                {CAPTION_PRESETS.map((preset) => (
                  <option key={preset} value={preset}>
                    {CAPTION_PRESET_LABELS[preset as CaptionPreset]}
                  </option>
                ))}
              </Select>
            </label>
          </section>

          <section className="grid gap-x-6 gap-y-5 md:grid-cols-[260px_1fr]">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-ink">
                <Film className="size-4 text-ink-faint" aria-hidden />
                Motion and transitions
              </div>
              <p className="mt-1 text-xs text-ink-muted">How the pictures move and how one scene changes into the next in a new edit.</p>
            </div>
            <div className="flex flex-col gap-4">
              <div className="grid gap-x-6 gap-y-4 md:grid-cols-2">
                <label className="flex flex-col gap-1.5">
                  <span className="text-[13px] font-medium text-ink-muted">Transition pack</span>
                  <Select value={draft.render.transition_pack} onChange={(event) => setRender({ transition_pack: event.target.value as TransitionPack })}>
                    {TRANSITION_PACKS.map((pack) => (
                      <option key={pack} value={pack}>
                        {TRANSITION_PACK_LABELS[pack]}
                      </option>
                    ))}
                  </Select>
                  <span className="text-xs text-ink-faint">{TRANSITION_PACK_DESCRIPTIONS[draft.render.transition_pack]}</span>
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-[13px] font-medium text-ink-muted">Camera move</span>
                  <Select value={draft.render.motion_style} onChange={(event) => setRender({ motion_style: event.target.value as MotionStyle })}>
                    {MOTION_STYLES.map((style) => (
                      <option key={style} value={style}>
                        {MOTION_STYLE_LABELS[style]}
                      </option>
                    ))}
                  </Select>
                  <span className="text-xs text-ink-faint">{MOTION_STYLE_DESCRIPTIONS[draft.render.motion_style]}</span>
                </label>
              </div>
              <TransitionPackPreview pack={draft.render.transition_pack} />
            </div>
          </section>
        </div>
      )}
    </Card>
  );
}
