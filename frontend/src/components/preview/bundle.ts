/**
 * Small, pure helpers around the preview bundle (docs/M5-CONTRACT.md sections 1, 2 and 5).
 *
 * - `bundleFromTimeline` builds a bundle in the browser from a parsed timeline when the server
 *   has no `GET /timeline` answer (an older backend, or the route failed): the preview still
 *   draws, without word times or story sections.
 * - The `with...` helpers are the local patches the Edit review applies at once when the
 *   reviewer changes a cut, turns popups off or turns captions off, so the live preview shows
 *   the change before anything is saved.
 */
import { projectRelativePath } from "../../api/edit";
import { projectFileUrl } from "../../api/files";
import { fontUrl, mediaThumbUrl } from "../../api/preview";
import type { BundleFont, BundlePicture, TimelineBundle } from "../../types/preview";
import type { Timeline, TimelineScene } from "../../types/timeline";
import type { CutTransition } from "../edit/cutTransitions";

/** The Noto files in `assets/fonts` (render/fonts.py `FONT_FILES`), for bundles built here. */
export const NOTO_FONT_FILES: Record<string, string> = {
  "Noto Sans": "NotoSans[wdth,wght].ttf",
  "Noto Sans Devanagari": "NotoSansDevanagari[wdth,wght].ttf",
  "Noto Naskh Arabic": "NotoNaskhArabic[wght].ttf",
  "Noto Sans Arabic": "NotoSansArabic[wdth,wght].ttf",
  "Noto Sans JP": "NotoSansJP[wght].ttf",
  "Noto Sans KR": "NotoSansKR[wght].ttf",
};

/** The fonts a timeline needs: its caption font, plus Noto Sans for popups and as the fallback. */
export function fontsFor(families: ReadonlyArray<string>): BundleFont[] {
  const fonts: BundleFont[] = [];
  for (const family of ["Noto Sans", ...families]) {
    const file = NOTO_FONT_FILES[family];
    if (file && !fonts.some((font) => font.family === family)) fonts.push({ family, url: fontUrl(file) });
  }
  return fonts;
}

/** A project file as a URL, or null for an empty path or one outside the project folder. */
export function projectFile(projectId: string, path: string | null | undefined): string | null {
  if (!path) return null;
  if (/^(https?:)?\/\//i.test(path) || path.startsWith("/api/") || path.startsWith("blob:") || path.startsWith("data:")) return path;
  const relative = projectRelativePath(path);
  return relative ? projectFileUrl(projectId, relative) : null;
}

export interface PictureSize {
  width: number;
  height: number;
}

/**
 * A bundle for `timeline` made in the browser. `sizes` holds the picture sizes the page could
 * measure (by image path); unknown sizes are sent as 0 and the engine reads them from the file.
 */
export function bundleFromTimeline(projectId: string, timeline: Timeline, sizes?: ReadonlyMap<string, PictureSize>): TimelineBundle {
  const pictures: BundlePicture[] = timeline.scenes
    .filter((scene) => scene.image)
    .map((scene) => {
      const size = sizes?.get(scene.image);
      return {
        scene: scene.index,
        path: scene.image,
        width: size?.width ?? 0,
        height: size?.height ?? 0,
        thumb_url: mediaThumbUrl(projectId, scene.image, 128),
      };
    });
  return {
    timeline,
    revision: timeline.revision,
    sentences: [],
    sections: [],
    pictures,
    media: {
      voice_url: projectFile(projectId, timeline.voice.path),
      stem_url: projectFile(projectId, timeline.music.stem),
      envelope_url: projectFile(projectId, timeline.music.envelope),
      fonts: fontsFor([timeline.captions.style.font]),
      // the track sits in the channel's music folder: only the server can name it (GET /timeline)
      music_url: null,
    },
    preview_gain_db: timeline.render.preview_gain_db,
    virtual: false,
    timing_estimated: false,
    cue_words: {},
    preview_gain_curve: null,
    sources: [],
  };
}

function sameCut(scene: TimelineScene, cut: CutTransition | undefined): boolean {
  return !cut || (cut.type === scene.transition_out.type && Math.abs(cut.duration_s - scene.transition_out.duration_s) < 1e-6);
}

/**
 * The bundle with the reviewer's cuts (the transition out of each scene). Unchanged scenes keep
 * their objects, and the whole bundle is the same object when nothing changed, so the engine
 * only redraws for a real change.
 */
export function withCuts(bundle: TimelineBundle, cuts: ReadonlyArray<CutTransition>): TimelineBundle {
  if (bundle.timeline.scenes.every((scene, index) => sameCut(scene, cuts[index]))) return bundle;
  return {
    ...bundle,
    timeline: {
      ...bundle.timeline,
      scenes: bundle.timeline.scenes.map((scene, index) => {
        const cut = cuts[index];
        return !cut || sameCut(scene, cut) ? scene : { ...scene, transition_out: { ...scene.transition_out, type: cut.type, duration_s: cut.duration_s } };
      }),
    },
  };
}

/**
 * Popups and captions as the reviewer has them switched. Off draws none; captions on draws the
 * cues the builder always writes. Popups the builder left out (popups were off when it ran)
 * only come back with a render.
 */
export function withOverlays(bundle: TimelineBundle, overlays: { popups: boolean; captions: boolean }): TimelineBundle {
  const timeline = bundle.timeline;
  const popups = overlays.popups ? timeline.popups : [];
  const captionsOn = overlays.captions;
  if (popups === timeline.popups && captionsOn === timeline.captions.enabled) return bundle;
  return {
    ...bundle,
    timeline: { ...timeline, popups, captions: { ...timeline.captions, enabled: captionsOn } },
  };
}
