# M5 contract: the editing workspace. Part 1 = M5.1 preview engine

Design source: `docs/design/EDITOR-DESIGN.md` (approved by the owner on 2026-10-11 with the
recommended answers to section 14: scenes stay movable after the voice exists; the app computes
the music dip; cast and places references go to the picture tool; a free Windows scratch voice for
the early animatic; time in tenths of a second; design for 100% and 150% scaling; approving also
starts the export copy). The owner also asked that the editor be a **tab inside the storyboard
screen**: one workspace route with Storyboard | Timeline tabs, same video, selection and playhead
kept when switching (design 2.1 already says one workspace, two views).

This file is the binding contract for the M5 waves. Part 1 covers **M5.1, the preview engine**
(design section 5 and 12/M5.1). Parts 2 to 4 (timeline editor, Storyboard v2, polish and
hand-offs) are added here before each wave starts. Rules from CLAUDE.md apply: contract first, code
second; Pydantic models are the source of truth and are mirrored in `frontend/src/types/`.

## 0. Rules for M5.1

1. **One set of maths.** The preview mirrors the renderer, never the other way round. Every formula
   comes from `docs/design/MOTION-PARITY.md`, `docs/design/TRANSITION-PARITY.md`, `render/assembly.py`
   (frame layout of cuts, centred on the boundary), `render/ffmpeg.py` (popup motion and fades),
   `render/captions.py` (line breaks, style) and `render/timeline_builder.py` (scene bounds). Where
   the renderer must change so that both sides can agree (voice segments, music stem, motion.ease,
   caption presets), the renderer changes in this wave and its tests prove the output.
2. **FFmpeg makes every file.** The engine draws frames and plays audio; it never encodes.
3. **Laptop budget** (design 5.6): 30 fps at 960 x 540 with 2% or fewer dropped frames, seek at
   most 50 ms cached and 250 ms cold, first frame of a 60-scene project within 1.5 s, renderer
   process memory at most 700 MB. Measured on this laptop by the integrate step.
4. **Offline and mock-friendly.** Works with `CCS_*_PROVIDER=mock` (placeholder pictures, silent
   voice): a `Mock media` chip, flat waveform, everything else live.
5. **No regressions.** EditReview keeps "Watch the last render" (the proxy video); the live preview
   is added next to it. Existing timelines without the new fields still play and still render.

## 1. Data additions (all additive; `models/timeline.py`, mirrored in `types/timeline.ts`)

- `timeline.revision: int` (starts at 1; the renderer ignores it).
- `scenes[].sentence_ids: [str]`, `scenes[].edited: [str]` (field names the person changed).
- `scenes[].motion.ease: str | null` overriding the preset curve: `ease_in_out_cubic`,
  `ease_out_quart`, `ease_in_out_sine`, `snap`, `linear`. `render/motion.py` honours it.
- `voice.segments: [{sentence_id, src_start_s, src_end_s, at_s}] | null`. Null means the whole
  `voice.wav` from `start_s` as today. When present the renderer builds the voice track from the
  segments (`atrim` + `concat`), and the preview schedules one buffer per sentence.
- `popups[].id: str`, `popups[].anchor: {sentence_id, offset_s} | null`, `popups[].edited: bool`,
  `popups[].locked: bool`.
- `captions.cues[].id: str`, `captions.cues[].edited: bool`;
  `captions.style.preset: "bold-outline" | "active-word" | "single-word-pop" | "boxed"`,
  `captions.style.active_colour: str`, `captions.style.layout: "phrase" | "single_word"`
  (presets defined in `config/captions.yaml`; the ASS writer gets them in this wave).
- `markers: [{id, t, label, colour, chapter: bool}]` (the renderer ignores them; chapters feed export later).
- `music.stem: str | null` = path of `07_edit/music_ducked.wav`; `music.envelope: str | null` = path
  of `07_edit/music_envelope.json` (`{step_s: 0.01, gain_db: [..]}`).
- `render.preview_gain_db: float | null` measured by the backend (EBU R128 of voice + stem) so the
  preview plays at the loudness of the final file.
- The timeline builder fills `sentence_ids`, `voice.segments` (from `05_voice/timing.json` and the
  per-sentence files when they exist), ids for popups and cues, and leaves the rest null/empty.
  `pipeline/stages/edit.py` builds the music stem and the envelope before rendering (section 3).

## 2. Backend routes (`api/preview.py`, mounted under the existing project router prefix)

| Method | Path | Returns |
|---|---|---|
| GET | `/api/projects/{id}/timeline` | `{timeline, revision, sentences: [{id, text, start_s, end_s, words: [{w, start_s, end_s}]}], sections: [{id, name, start_scene, end_scene}], pictures: [{scene, path, width, height, thumb_url}], media: {voice_url, stem_url, envelope_url, fonts: [{family, url}]}, preview_gain_db}` |
| GET | `/api/projects/{id}/media/thumb?path=...&h=128` | cached JPEG (Pillow), path restricted to the project folder |
| GET | `/api/projects/{id}/media/peaks/{voice\|music}.json` | `{step_s: 0.01, min: [..], max: [..]}` cached by file hash |
| GET | `/api/projects/{id}/media/voice/segments/{sid}.wav` | the sentence audio; an own recording is split once with timing.json |
| POST | `/api/projects/{id}/popups/preview` | body `{text, style, position, anim, aspect, height}` → `{png_url, x, y, w, h}` using `render/popups.py` |
| POST | `/api/projects/{id}/music/stem` | rebuilds `music_ducked.wav` + envelope from the timeline's music block → `{stem_url, envelope_url, preview_gain_db}` |
| GET | `/api/projects/{id}/media/music` | the timeline's own music track, only from the channel's music folder or the project folder (ETag, ranges); added by 8.7.5, named by the bundle's `media.music_url` |
| GET | `/api/fonts/{file}` | the Noto fonts from `assets/fonts` (allow-list, cache headers) |

All need a signed-in user; viewers may read everything and call the two POSTs (they only compute
previews). Paths are validated against the project folder (no traversal). Static
`/transitions/catalogue.json` and previews already exist.

## 3. Renderer changes (`render/*`, `pipeline/stages/edit.py`)

1. **Music stem.** `render/music.py` gains `build_ducked_stem(timeline, voice_wav, out_wav, envelope_json)`:
   voice RMS at 10 ms, gain = `duck_db` while voice is present with the timeline's attack/release
   (default 300/800 ms) smoothed, applied after track offset, looping, volume and fades; written as
   48 kHz stereo WAV. The renderer mixes the stem and no longer runs `sidechaincompress`; `loudnorm`
   stays. `tests/test_music_stem.py` proves: silence where the voice is loud drops by `duck_db`
   within 0.5 dB, the stem length equals the timeline, and a render using the stem has the same
   loudness (within 1 LU) as before.
2. **Voice segments.** When `voice.segments` is set, the voice track is `atrim`+`concat` of the
   segments at their `at_s` (gaps are silence). Test: a timeline with two sentences swapped renders
   the words in the swapped order (compare against the sentence files by cross-correlation).
3. **motion.ease** overrides the preset curve in `render/motion.py` and the zoompan expression.
4. **Caption presets** in `render/captions.py` and `config/captions.yaml`: `active-word` (colour
   switch per word using the word times), `single-word-pop` (one word per cue, scale pop over
   0.12 s), `boxed` (semi-transparent box). The default stays `bold-outline`.
5. `edit.py` builds the stem and envelope and measures `preview_gain_db` before rendering; the
   review payload exposes them.

## 4. The engine (`frontend/src/preview/`, framework-free TypeScript + PixiJS 8, Canvas 2D fallback)

- `description.ts`: `RenderDescription` built from the timeline bundle (frames, clip windows, cut
  frames centred on the boundary, popups, cues, voice segments, stem) using `motion.ts`
  (ease, rect interpolation, cover-region clamp, handheld wobble) and `bounds.ts` (scene bounds =
  `timeline_builder` rules). Both are tested against JSON fixtures emitted by
  `tools/make_preview_fixtures.py` (Python reference) to 1e-6.
- `transitions/`: one GLSL filter per Transitions v2 id from TRANSITION-PARITY.md (same easing,
  same noise seeds); legacy xfade names mapped as the renderer maps them; `dip_white` end fade.
- `parallax.ts`: 64 x 36 mesh warp with back and front layers when a depth map exists, else Ken Burns.
- `text.ts`: captions drawn on Canvas 2D → texture: Noto via `FontFace` from `/api/fonts`, split on
  `\N`, size `48/1080 x H`, outline first, bottom-centre with `margin_v = 60/1080 x H`, active-word
  and single-word-pop presets; popups use the backend PNG (debounced 150 ms) with the motion
  formulas from `render/ffmpeg.py` (`travel = max(1, 120 x scale)`, ease, slide/pop, fades).
- `audio.ts`: Web Audio graph; one buffer per sentence scheduled at `at_s`, LRU about 40 MB and a
  window of 10 s behind to 60 s ahead; the stem on a second source; master gain from
  `preview_gain_db`; scrub grains (60 ms, 10 ms fades) when "Scrub audio" is on.
- `clock.ts`: audio clock while playing; frame number `n = floor(t x fps + 1e-6)`; late frames
  dropped, audio never; on-demand frames while paused.
- `engine.ts`: `createPreview(canvas, bundle, options)` → `{play, pause, seek, setQuality, setLoop,
  skim(t), destroy, on(event)}`; quality Full/Half/Auto (Auto drops after 3 frames over 33 ms,
  returns after 5 s); textures decoded at most 2x preview size and at most 1920 wide, 8 to 12 cached;
  WebGL2 loss → "Restarting the preview..." then Canvas 2D fallback with the `Simplified preview` chip;
  missing picture slate; `Mock media` chip.
- React wrapper `components/preview/LivePreview.tsx`: monitor, transport (play/pause, step, loop,
  in/out), quality menu, time display in tenths of a second, "Watch the last render" toggle to the
  proxy video, "Compare" placeholder (M5.2).

## 5. Integration in this wave

- `EditReview.tsx` shows **Live preview** as the default monitor, with "Watch the last render" as a
  toggle; the scene strip click seeks the live preview; changing a transition or popup in the review
  updates the preview at once (bundle refetch or local patch).
- The storyboard review gets a small **Play animatic** button that opens the same preview over the
  virtual timeline (pictures stage onwards; before the voice exists it uses estimated timing from
  the storyboard and says so). Full Storyboard v2 comes in M5.3.

## 6. Parity and performance proof (`tests/preview_parity/`, dev only)

- Playwright + headless Chromium (installed under `F:/cfs_tmp/playwright` or the default cache;
  never committed) renders 40 frames per project for three mock projects (3 scenes; 12 scenes using
  every transition; a Short) at 960 x 540; FFmpeg renders the same projects at 1080p and frames are
  extracted and scaled. Required: SSIM >= 0.95 on motion-only frames, >= 0.92 on transition frames,
  caption box IoU >= 0.9; voice and picture within +/-1 frame over 10 minutes; music RMS per 400 ms
  window within +/-1 dB. Results written to `F:/cfs_tmp/m5/parity/report.json` and summarised in the
  integrate report with the worst frames saved as PNG pairs.
- Performance run on this laptop: the 12-scene and a 60-scene/10-minute mock project; dropped-frame
  rate, seek times, first frame, renderer memory (`performance.measureUserAgentSpecificMemory` or
  the WebView2 process working set).

## 7. Out of scope for M5.1 (later parts)

Timeline lanes and editing commands, inspector, undo/history, structure operations, hand-offs,
Ask Claude, Storyboard v2 board, cast and places. Nothing in this wave may remove today's review
screens.

## 8. M5.1 as built: deviations, integration changes and the proof (recorded by the integrate step)

The code and its tests follow this section where it differs from sections 1 to 6.

### 8.1 Deviations kept from the build

Backend (`api/preview.py`, `render/*`, `models/timeline.py`):

1. `GET /timeline` adds `virtual` (true before the edit step: the timeline is worked out in memory
   from the storyboard, the timing and the pictures that exist; nothing is saved),
   `timing_estimated` (no voice yet), `cue_words` (`{cue_id: [{w, start_s, end_s}]}`, the word times
   the ASS writer uses) and `preview_gain_curve` (8.2.9). It answers 404 only when there is no
   storyboard; a picture that does not exist yet has `path: ""` and `thumb_url: null`.
2. Peaks add `duration_s` and `source` (`voice`, `stem` or `track`): music peaks come from the stem
   while it is current for the timeline, else from the raw track in its own time; a video with no
   music gets 404. The cache key is the file's path, size and date, not a hash of its content.
3. `music_envelope.json` holds `{step_s: 0.01, gain_db}` plus `key`, `version` (`stem.1`),
   `sample_rate`, `length_s`, `duck_db`, `attack_s`, `release_s`, `threshold` and `lookahead`. The
   voice counts as present when its 10 ms RMS is above `ducking.threshold` (0.03); the dip is a
   straight ramp in dB that starts `attack_s` before the voice and comes back over `release_s`.
4. The stem and envelope URLs carry `?v=<stem key>` (bundle, `POST /music/stem`, review payload); in
   the bundle they are null while the stem is not current. The review payload names them `stem_url`,
   `envelope_url` and `preview_gain_db` (also inside `timeline.music` and `timeline.render`).
5. `POST /music/stem` does not rewrite `timeline.json` (the edit step records the paths and the
   gain), answers 404 before `timeline.json` exists and returns `preview_gain_db: null` when the
   loudness cannot be measured. `preview_gain_db` is null for a silent mix (-70 LUFS or quieter),
   clamped to +/-30 dB and cached with the loudness curve in `07_edit/preview/audio.json`.
6. `POST /popups/preview` accepts `style` and `anim` and ignores them (the PNG does not depend on
   them).
7. Own recordings (`own_recording` in timing.json): `voice.segments` cover the whole file with no
   gaps (first clip from 0, each to the next sentence's start, the last to the end of the file), so
   the render sounds as before; the segments route serves the sentence span joined with its clips.
   M5.2's pause shortening must trim a clip's `src_end_s` for these timelines.
8. Caption `layout: single_word` shows one word at a time for any preset; `single-word-pop` always
   does; the builder then makes one cue per word.
9. Viewers may call the two POSTs: `auth/middleware.py` `VIEWER_WRITES` lists them.
10. `scenes[].motion.ease` is left out of the scene-clip hash when null, and the render key leaves
    out what the renderer ignores and the M5 fields while they hold their defaults, so the new
    fields alone never cause a render. This wave's renderer changes do: the music mix (the stem
    instead of `sidechaincompress`) and the centred zoompan crop (8.2.2) render every video once
    more.

Engine and screens (`frontend/src/preview/`, `components/preview/`):

11. Captions follow what libass draws rather than the literal `48/1080 x H` and `60/1080 x H`: the
    ASS PlayRes (1920 x 1080, or 1080 x 1920 for 9:16) scaled by `H / PlayResY`, the font sized as
    libass sizes it (`REAL_DIM`: em = size x unitsPerEm / (winAscent + winDescent), 35.2 px for Noto
    Sans 48 at 1080p), so 9:16 captions are `48/1920 x H`; the bold and the sub-pixel placement as
    8.2.10 describes.
12. The engine API is a superset of section 4: `update(bundle)`, `ready`, `renderFrame(seconds)`,
    `snapshot()`, `currentTime()`, `duration()`, `isPlaying()`, `setScrubAudio()`, `stats()`,
    `description()`; events add `stats` and `frame`; options add `renderer`, `audio`, `maxSize`,
    `fetch`, `settings`, `catalogue`, `pictureUrl`, `voiceSegmentUrl`, `popupPreview`,
    `popupsEnabled`, `animatic`, `mockMedia`. It reads two optional bundle fields the backend does
    not send yet, `pictures[].depth_url` and `media.mock`; without `depth_url` a parallax scene asks
    for `scene_NN.depth.png` beside the picture (one 404 per bundle when there is none).
13. The first popup PNG request goes out at once, later changes are debounced 150 ms; until it
    arrives a stand-in tagged "Drawing..." is drawn.
14. Pictures are decoded at most 2x the preview and at most 1920 wide, from the sized JPEG route (16
    to 2160 pixels tall, 8.2.8) or, for a picture needed taller still, the project file.
15. Auto quality retries Full after 5 s, then after 10, 20 ... 60 s when it had to drop again.
16. Two-layer parallax is simplified (near objects on a 192-cell grid, a push-pull fill instead of
    TELEA, no guided-filter edge snap); blurs sample directly (motion blur at most 64 taps).
17. Intro and outro clips are not part of the preview (it shows the scenes part).
18. The storyboard's animatic is laid out in the browser from the storyboard on screen (unsaved
    edits included); the server's virtual bundle gives the voice timing and picture sizes. It plays
    no music ("Music is added at the edit step") and no scratch voice (M5.3); before the voice
    exists it plays no sound.
19. EditReview calls `POST /music/stem` once per timeline revision when the bundle has music but no
    current stem, or no known gain, or no loudness curve, with a "Preparing the music..." chip
    meanwhile. "Compare" is a disabled placeholder until M5.2; scrub audio and skim have no controls
    yet (M5.2 timeline).

### 8.2 Changes made by the integrate step

1. **Voice level (engine).** FFmpeg turns the mono voice into stereo with the centre at -3 dB on
   each speaker (swresample's `center_mix_level`); Web Audio copied it at full level, so the
   preview's voice was 3 dB louder against the music and overall. The voice bus is now one channel
   at `VOICE_UPMIX_GAIN` = 1/sqrt(2) (`audio.ts`).
2. **Zoompan crop centred (renderer).** zoompan cuts whole pixels at `floor(x)` with
   `floor(iw/zoom)` columns, so every Ken Burns render sat on average 0.75 canvas pixels up and left
   of the sub-pixel window (0.63 to 0.68 output px at 1080p against the Python reference, against
   the 0.5 px MOTION-PARITY promises). `motion.ZoompanExprs.centred()` adds half the truncated width
   plus half a pixel; `CLIP_ENGINE_VERSION` is `clips.2`. `tests/test_motion_ease.py` measures
   rendered frames by phase correlation.
3. **Picture sampling (engine).** The Ken Burns quad has its own shader (`KB_FRAGMENT`): four
   bilinear taps across each output pixel on the mip level where the pixel is at most two texels,
   instead of the GPU's trilinear blend.
4. **Smooth first playback (engine).** Transition shaders (and their blur and morph passes) are
   compiled one at a time while the preview is paused (an ANGLE link took up to 500 ms on the first
   frame of each new cut); pictures go to the GPU when they load, one at a time; the audio context
   opens after the first frame instead of on the first Play (opening the sound device held the page
   about 400 ms).
5. **Animatic and texture fixes.** An animatic before the pictures stage waited 6 s on "Starting the
   preview..." (a scene with no picture now counts as drawn: its slate) and asked for voice clips
   that do not exist; picture textures are unbound from the quads before they are released.
6. **Fixtures.** One folder, `frontend/src/preview/fixtures/`, one generator,
   `tools/make_preview_fixtures.py`: the seven fixtures of section 4 plus the engine's wider ones
   (`engine_*.json`, from `tools/preview_engine_fixtures.py`, formerly a script outside the
   repository) and `animatic.json`; `--check` (run by `tests/test_motion_ease.py`) covers all 17.
7. **The proof** lives in `tests/preview_parity/` (8.3).
8. **Cold seeks and picture sizes (engine and thumbnail route).** After a jump the frame's own
   pictures load first and alone; the neighbours follow once they are in, and downloads for scenes
   far from the new place stop (`PictureStore.cancel`, an `AbortController` per load). The thumbnail
   route makes JPEGs up to 2160 tall instead of 1080 (`THUMB_MAX_HEIGHT`; front end
   `THUMB_MAX_HEIGHT` and `MAX_THUMB_HEIGHT`): a portrait or square picture under a 16:9 camera
   window, or any picture of a 9:16 preview, needs 1536 to 1920 pixels and was read as the original
   PNG (a 2048 x 2048 PNG took about 170 ms to decode). The preview's pictures (taller than 360) are
   JPEG quality 95 instead of 82 (`THUMB_VERSION` `thumb.2`); the strip's stay at 82.
9. **Loudness curve (backend and engine, additive fields).** The renderer's one-pass `loudnorm` does
   not apply one gain: over the first 3 s it follows the loudness of the first 3 s, later the
   integrated loudness so far, so the constant `preview_gain_db` was up to 1.03 dB off in the first
   seconds. `render/ffmpeg.py` `measure_mix(..., gain_curve=True)` also puts the mix through the
   assembly pass's `loudnorm` and compares the levels before and after it every 100 ms;
   `ensure_preview_gain` keeps the curve in `07_edit/preview/audio.json` (`curve_step_s`,
   `curve_gain_db`; `GAIN_VERSION` `gain.2`). `GET /timeline` adds `preview_gain_curve: {step_s,
   gain_db} | null` (`PreviewGainCurve`; front end `types/preview.ts`), only for the sound it was
   measured with; the engine's master gain follows it with linear ramps between the middles of the
   steps (`curveGainDb`, `curvePoints`) and falls back to `preview_gain_db` without it. The
   measurement costs about a twentieth of the video's length (40 s for ten minutes on this laptop),
   in the edit step and in `POST /music/stem`.
10. **Captions placed like libass (engine).** libass emboldens the regular face with FreeType's
    `FT_Outline_Embolden` (em / 64), which keeps the glyph's left and bottom edges and grows it to
    the right and upwards, and strokes the outline around the emboldened glyph; the engine had
    widened the fill on both sides and stroked the outline around the plain glyph, so its captions
    were about 0.8 px lower, 1.3 px narrower and lighter at 1080p. The text is now moved by em / 128
    right and up, the fill stroked em / 64 wide and the outline `2 x border + em / 64` wide
    (`text.ts` `strokeOutline`, `fillRun`). The browser also puts canvas text on whole pixels (a
    baseline asked for at 504.7 is drawn at 505; measured in Chromium, where libass places it to an
    eighth of a pixel), so the caption canvas is drawn with its first baseline on a whole pixel and
    composited the rest of the way (`paintCaption` returns `offsetY`, the engine's caption overlay
    uses it). The lowest caption box IoU at 960 x 540 went from 0.846 (short) and 0.875 (three) to
    0.918 and 1.0.
11. **Even frame pacing (engine).** The playhead of a drawn frame was the audio clock read when the
    animation-frame callback ran, a few milliseconds after the refresh and by a different amount
    each time; when a video frame's start fell next to a refresh, the frame came one refresh early
    or late (one picture held three refreshes, the next one), in about one play in three (28 and 34
    such stalls in 300 frames in two of the seven plays measured). `PlaybackClock.frameTime` reads
    the audio clock (`AudioEngine.nowAt`) at the animation frame's own timestamp and follows it
    smoothly (a tenth of the difference per frame, a jump of more than 40 ms taken at once); since
    then every play holds an even two refreshes per frame (95th percentile 33.4 ms in every play)
    apart from its start.
12. **Animation frames held back (engine).** The desktop app's browser pane, when it is not on
    screen, gives a page no animation frames while the page still says it is visible, and the
    preview stopped drawing (seeks were not answered, playback showed a still picture). A frame
    request that has not come after 100 ms is now run by a timer (`clock.ts` `requestFrame`,
    `FRAME_FALLBACK_MS`), as it already was for a hidden page.
13. **Render textures released cleanly (engine).** PixiJS defines a shader's resources as
    non-enumerable properties, so `unbindTargets` (`Object.keys`) found none to unbind, and every
    change of the drawing size (the quality menu, Auto dropping to Half) destroyed render textures
    still bound to the transition shaders: PixiJS warned "a 'textureSource' was destroyed while
    still bound to a shader" (9 times in 3 s of play in the live check). It lists them with
    `Object.getOwnPropertyNames` now; no warning since.
14. **Frames asked for come first (engine).** While paused, a picture loaded ahead of the playhead
    goes to the GPU only after 300 ms without a seek or a skim (`UPLOAD_QUIET_MS`), so a texture
    upload does not hold up the frame someone has just asked for.

### 8.3 How section 6 is measured

- `tests/preview_parity/`: `parity_projects.py` writes the projects and the real edit step renders
  them; `harness/` is a page built with the front end's Vite and served by the app under `/parity/`
  (it opens a project's bundle with the same engine options as the app's LivePreview, sized JPEGs up
  to 2160 included); `parity_harness.py` and `parity_perf.py` drive Playwright for Python and
  headless Chromium; `run_parity.py projects|rerender|parity|perf [full|auto]` runs them outside
  pytest and `test_preview_parity.py` inside it (skipped unless `CCS_PREVIEW_PARITY=1` /
  `CCS_PREVIEW_PERF=1`). `test_parity_metrics.py` checks the measures and the project specs in the
  normal run. Playwright for Python and its Chromium (Chrome for Testing 153) live under
  `F:/cfs_tmp/m5/playwright`, not in the repository or the venv.
- Projects: `three` (3 scenes of mock pictures, bold-outline captions, two popups), `twelve_a`,
  `twelve_b`, `twelve_c` (12 scenes each over photo-like pictures of four sizes; together their 33
  cuts use all 27 transitions and the six old xfade names; active-word, boxed and bold-outline
  captions; slide, pop and fade popups), `short` (9:16, single-word-pop), `ten_minutes` (60 scenes,
  120 sentences, 10 min 21 s, proxy only) and `pauses` (6 sentences with 1.6 s of silence after
  each, proxy only, so the music is heard alone). Twelve scenes have 11 cuts, so "12 scenes using
  every transition" is three 12-scene projects. The mock voice is silent, so the projects get a
  synthetic speech-like voice (a different waveform per sentence, word times in timing.json).
  `ten_minutes_photo` (8.7.1, performance only) is `ten_minutes` over the twelve-scene projects'
  photo-like pictures (2048 pixel PNGs), for the first frame and cold seeks with pictures like
  real ones.
- Frames: 40 per project chosen from the engine's own frame layout (two inside every cut, the middle
  of the end fade, the rest across the scenes), drawn by the engine at 960 x 540 (540 x 960) and
  taken from `final_1080p.mp4` scaled by area. SSIM is on BT.709 luma with the caption boxes left
  out (they are compared as boxes). Caption boxes: the engine draws the captions alone on flat grey
  and libass draws the render's ASS file on grey with the render's filter; the boxes are compared at
  the preview size (libass's frame scaled down) and at the render's own size (the engine drawing at
  1920 x 1080), and both must reach 0.9.
- Sound: every sentence of the engine's plan is found in the render by cross-correlation; the
  engine's real Web Audio output is recorded in the browser (an AudioWorklet behind the destination)
  and compared with the render on the video clock (offset every 2 s, the level of every 400 ms
  window; whole short videos, four 12 s stretches of the long one). Every window that is not silence
  counts, and the windows with no voice in them compare the music alone (`pauses` has 16; in the
  other projects the sentences are 0.25 s apart, so none); the proof fails when no project has a
  music-only window.
- Picture over ten minutes: the engine's frame in the middle of 8 cuts spread over the whole video
  against the render's frames n - 1, n and n + 1; the best match must be n.
- Performance: headless Chromium on this laptop (Intel UHD 620, ANGLE D3D11) with
  `--disable-direct-composition` (headless Chromium presents a WebGL canvas through a
  DirectComposition swap chain whose `Present` blocked about 400 ms every second; a window on a
  screen does not), at Full quality (960 x 540, the budget's size; `perf auto` measures Auto, the
  app's default, for information). Seeks are timed after the engine's background shader warm-up and
  once the GPU has finished it; a seek counts as cached when its pictures are decoded and on the GPU
  (the engine uploads them one at a time in the background after they load). A frame's pacing is
  measured by the screen refresh it was drawn for (requestAnimationFrame's timestamp), not by when
  its callback ran. Memory is the renderer process's working set. Cold seeks are also measured
  from an empty `07_edit/preview/thumbs` on `ten_minutes_photo`: `after_render` first makes the
  JPEGs the way the edit step does while it renders (the first view after a render), and
  `never_made` leaves them to the background maker `GET /timeline` starts (a project rendered
  before 8.7.1), reporting how many JPEGs the server had at each seek and when it had them all.

### 8.4 Results on this laptop (2026-10-13 and 14)

Frame parity, 40 frames per project at the preview size (SSIM minimum / mean; caption box IoU
minimum at 960 x 540 (540 x 960) and at the render's own size). Required: 0.95, 0.92, 0.9.

| Project | Motion SSIM | Transition SSIM | Caption IoU, preview size | Caption IoU, render size |
|---|---|---|---|---|
| three | 0.996 / 0.998 | 0.998 / 0.998 | 1.000 | 0.935 |
| twelve_a | 0.977 / 0.982 | 0.971 / 0.985 | 0.994 | 0.944 |
| twelve_b | 0.972 / 0.982 | 0.970 / 0.982 | 0.981 | 0.991 |
| twelve_c | 0.974 / 0.981 | 0.974 / 0.979 | 0.986 | 0.937 |
| short | 0.998 / 0.998 | 0.997 / 0.998 | 0.918 | 0.903 |

The lowest SSIMs are the photo-like pictures' fine noise (1/f texture) under the camera's sub-pixel
moves; the worst pairs (`F:/cfs_tmp/m5/parity/worst/`) show the same picture, motion, cut, popup and
caption on both sides. The diagonal wipe's thin white streaks seen in an earlier harness were not
found: every frame of that cut scores 0.972 to 0.983.

Sound (worst sentence offset from the engine's plan in the render; offset of the recorded engine
output against the render; worst level difference of any 400 ms window, plan and recorded; windows
with music alone). Required: +/-1 frame (33 ms), +/-1 dB.

| Project | Length | Sentences | Plan offset | Recorded offset | Level (plan / recorded) | Music alone |
|---|---|---|---|---|---|---|
| three | 16 s | 6 | 0.0 ms | 0.0 ms | 0.07 / 0.07 dB | none |
| twelve_a | 31 s | 12 | 0.0 ms | 0.0 ms | 0.07 / 0.07 dB | none |
| twelve_b | 33 s | 12 | 0.0 ms | 0.0 ms | 0.07 / 0.07 dB | none |
| twelve_c | 33 s | 12 | 0.0 ms | 0.0 ms | 0.06 / 0.06 dB | none |
| short | 12 s | 5 | 0.0 ms | 0.0 ms | 0.08 / 0.08 dB | none |
| ten_minutes | 10 min 21 s | 120 | 0.0 ms | 0.0 ms | 0.36 / 0.06 dB | none |
| pauses | 21 s | 6 | 0.0 ms | 0.0 ms | 0.44 / 0.44 dB | 16 windows, -0.08 dB on average, worst 0.44 dB |

The 0.44 dB window is the quietest one (-46.5 dBFS, the music coming back up after a sentence's
dip); the 0.36 dB one of the plan is a quiet window at 83 s. Picture over ten minutes: at all 8 cuts
(10 s to 611 s) the engine's frame matches the render's same frame best (offset 0).

Performance (headless Chromium 153, Intel UHD 620, 960 x 540, sound on and muted, Full quality):

| | 12 scenes, 31 s | 60 scenes, 10 min 21 s | Budget |
|---|---|---|---|
| Frames dropped while playing | 0 of 958 (0 %) | 11 of 1552 (0.71 %) | 2 % |
| Refresh gap between frames, 95th percentile | 33.4 ms (two refreshes) | 33.4 ms | |
| Frames held a refresh longer | 6 of 958 | 19 of 1552 | |
| Drawing per frame, p95 / max | 1.3 / 3.3 ms | 0.7 / 4.7 ms | |
| Open to first frame (bundle + engine) | 333 ms | 514 ms | 1.5 s |
| Seek to a cold frame, worst | 66 ms | 47 ms | 250 ms |
| Seek to a cached frame, worst / mean | 17.5 / 13.4 ms | 106.8 / 35.6 ms | 50 ms |
| Renderer process | 256 MB | 289 MB | 700 MB |
| GPU process | 309 MB | 181 MB | |
| Voice and music pieces started off their place | 0 of 19 | 0 of 23 | |

Auto quality (the app's default) stayed at Full in every play of every run; its last run gave the
same picture (0 and 1 dropped frames, first frames 256 and 857 ms, cold seeks within 82 ms, renderer
255 and 288 MB, cached seeks worst 90 and 106 ms). The frames held a refresh longer are mostly the
first ones of each play: the picture holds about 0.1 s while the sound starts (8.6.2).

**Cached seeks are within the budget in some runs and not in others.** In the five Full-quality runs
and one of the two Auto runs before 23:43 every cached seek was within 50 ms (worst 32 to 40 ms; the
last of them on the final engine but for 8.2.14; the other Auto run had one of 52 ms, a picture
still being sent to the GPU, which the harness now waits for); in the nine runs after, 2 to 12 of
the ten-minute project's 50 cached seeks took 100 to 108 ms (and 2 of the 12-scene project's 14 took
90 ms in one Auto run), always at the same places, the second and third scene after the jump. A
Chromium trace of such a run shows headless Chromium's GPU process presenting each frame through
`NativeViewGLSurfaceEGL::PostSubBuffer` in about 31 ms (a frame every second refresh) and once in
380 ms, while the engine's own drawing takes 1 to 7 ms; the browser holds the next animation frame
back meanwhile, and since 8.2.12 the engine draws the frame from its timer after 100 ms (before that
the same seeks waited for the browser: 427 ms in an earlier run). Turning `preserveDrawingBuffer`
off, deferring uploads (8.2.14) and letting the GPU settle before each group did not change it. The
headless present is not the app's (a window on screen presents through DirectComposition); see
8.6.3.

### 8.5 Live check (the real app, mock providers, built-in browser)

A private app on a free port with mock providers took a fresh project through the pipeline to the
edit review (6 scenes, 63.7 s, 3 popups, 19 caption cues, 720p and proxy rendered) and stopped
another at the storyboard review (no voice, no pictures); a third project, edited by an earlier
build, kept its older timeline. The desktop app's browser pane was given the test admin's session by
a local proxy (no password typed into the pane). Checked there: the edit review opens on the live
preview (chips "Live" and "Mock media"), which plays with captions and the popups; a scene in the
strip seeks there and plays (scenes 4 and 6); changing the cut after scene 4 to a diagonal wipe
shows at once (the strip says so, the preview plays from 2 s before the cut to 2 s after it and
stops, the wipe on screen) and the notice says the last render does not have it yet; switching
popups off takes them out of the preview at once; "Watch the last render" shows the proxy video at
the same time (44.2 s) with the "Last render" chip, and back; the storyboard's "Play animatic" opens
the animatic with the "Animatic", "Estimated timing" and "Mock media" chips, the picture-missing
slates and the captions, plays, jumps to the next cut, fits a 1280 x 720 window (150 % scaling) and
leaves no canvas behind when it closes; the older project showed "Preparing the music..." and then
played with its new stem and loudness curve. No console warnings or errors after 8.2.13. The pane
was not on screen during the check, so it got no animation frames (8.2.12 found that); its frames
were drawn by the timer and Auto dropped to Half, so frame pacing is measured headless (8.4), not
there. Pictures: `F:/cfs_tmp/m5/integ/shots/` (`bb_*`: the preview canvas in the built-in browser at
each step; `pw_*`: the same pages in headless Chromium through the same proxy).

### 8.6 Open items for the next parts

1. **Caption text** is drawn by the browser's rasteriser: its anti-aliased edges are a little
   lighter than libass's (more so at small sizes), and the sub-pixel placement softens it by a
   fraction of a pixel vertically. Boxes pass at both sizes; pixel-level caption parity would need
   glyph outlines drawn by the engine itself.
2. **Play start.** After each Play the picture holds about 0.1 s while the sound starts (the audio
   clock takes over behind the performance clock). Starting the picture with the sound (a short,
   still start instead of a step and a hold) would remove the first stall of every play.
   Done by 8.7.4: the picture now waits on its frame and starts with the sound.
3. **Cached seeks in a real window.** The 50 ms budget for a cached seek is met in some runs and
   missed by headless Chromium's present in others (8.4); measure seeks in the app's own window
   (WebView2 on screen) before M5.2's scrubbing builds on them. Neither this run nor the earlier one
   could: the desktop app's browser pane was not on screen.
4. **PixiJS's own animation-frame loop** (`Ticker.system`, its texture garbage collection) runs
   while a preview is open, even paused; stopping it when idle would let a laptop rest.
5. **Back-to-back cold seeks**: give the picture decodes of the frame on screen priority over the
   neighbours' (matters for scrubbing in M5.2).
6. The raw-track fallback (while a new music stem is made) plays a mono track 3 dB louder than the
   stem would (it plays at all since 8.7.5).
7. Own recordings have no gaps between their voice clips (8.1.7): M5.2's pause shortening must trim
   a clip's `src_end_s` for them rather than close a gap.
8. The main JS chunk is 863 KB (802 KB before M5; the Vite warning predates M5); the engine and
   PixiJS are their own 445 KB chunk, loaded when a monitor opens.

### 8.7 Fixes after the review (2026-10-14)

The code and its tests follow this section where it differs from the sections above.

1. **The preview's pictures are made ahead (thumbnail route, edit step, engine).** A cold seek
   waited for the server to make the sized JPEG (150 to 450 ms from a 2048 pixel PNG on this
   laptop) on every first view after a render and for every preview height not asked for before
   (Half quality, other window sizes). Now the engine asks for pictures at a few fixed heights:
   twice the drawing height rounded up to 540, 720, 1080, 1440, 1920 or 2160 (2 % over a step
   still uses it), never taller than the picture or wider than 1920 (`pictures.ts`
   `PICTURE_STEPS`, `decodeHeight`), so Half quality and nearby window sizes share files.
   `render/thumbs.py` works out the same heights for Full and Half at the 960 x 540 and
   1280 x 720 drawing sizes (540 x 960 and 720 x 1280 for 9:16) and makes them: the edit step
   while it renders (it waits at most 5 s after the render, then hands what is left to the
   background maker), and `GET /timeline` queues whatever is missing for one background maker
   (below-normal priority, one picture at a time, each picture decoded once for all its heights).
   The thumbnail route moved to `render/thumbs.py`: two requests for one JPEG make it once; a
   height over the picture's own shares the picture-sized JPEG; the JPEGs of a replaced picture
   are removed at every height; at most 8 heights are kept per picture; JPEGs are made with an
   integer reduce then bicubic and without Huffman optimisation (`THUMB_VERSION` `thumb.3`,
   about a third faster); a picture Pillow refuses (over its decompression-bomb limit) loses only
   its own scene (size 0 in the bundle, 404 for its thumbnail) instead of failing `GET /timeline`.
   The performance run measures cold seeks from an empty thumbnail folder with photo-like
   pictures in the 60-scene project (8.3, `ten_minutes_photo`).
2. **Parallax memory is bounded (engine).** Plans, their plates (two canvases up to 1280 pixels
   wide) and the plates' textures are kept only for the scenes whose pictures the engine keeps
   (the scene on screen, both sides of a cut, the next three and the one before). A plan that
   leaves them frees its plates (the compositor destroys their textures, `releasePlates`; the
   canvases are shrunk to nothing) and its depth grids; depth maps are no longer kept (a scene
   that comes back asks again, from the browser's cache). `parallaxStore.test.ts` seeks through
   60 parallax scenes twice: at most 5 plans and 5 plates are held at any time and every plate
   made is released.
3. **Parallax is worked out off the main thread (engine).** The depth analysis and the plates are
   made by a Web Worker (`parallax.worker.ts`, `parallaxJobs.ts` `ParallaxStore`), one job at a
   time, the frame's own scenes first; the plates go to the GPU through the engine's
   one-at-a-time upload queue. Nothing about parallax runs inside a frame: a parallax scene is
   drawn as its Ken Burns move until its plan (and, with near objects, its plates) is ready.
   Without workers (tests) the same jobs run on the page only while the preview is paused.
   `renderFrame` (the parity harness) waits for them.
4. **The sound at Play (engine and voice route).** A seek or a skim while paused (once the
   playhead has rested 80 ms) and the first frame decode the sound there: the voice clips under the
   playhead and the music piece there first, then the rest of the window, at most 3 loads at a
   time (`audio.ts` `loadOrder`, `MAX_LOADS`; before, up to 24 at once and only on Play). Play holds
   the picture on its frame until the sound device runs and the clips and music piece under the
   playhead are decoded, at most 300 ms (`PLAY_HOLD_MS`), then the picture and the sound start
   together, so the sentence under the playhead is no longer cut; a seek while playing and a loop
   restart do the same, and a pause during the hold keeps the playhead. Music pieces are 5 s
   (`STEM_CHUNK_S`, 20 s before) and are decoded by the browser (`decodeAudioData` on the range
   with a WAV header in front, off the main thread; the engine's own PCM reader only when that
   fails). A clip or piece that could not be loaded is asked for again after 3 s, at most three
   times (before: never again). The voice route cuts a sentence into a part file of its own per
   request (two first requests for the same sentence both failed before). This settles 8.6.2.
5. **The raw music track plays (route, bundle, engine).** `timeline.music.path` is an absolute path
   in the channel's music folder, which the project files route refuses, so the raw-track fallback
   of 8.1.2 never played. `GET /api/projects/{id}/media/music` serves the timeline's own track when
   it is an audio file inside the channel's music folder or the project folder (ETag, ranges); the
   bundle names it `media.music_url` (`?v=` from its path, size and date; null without music, or
   when the track is elsewhere or not on this PC; mirrored in `types/preview.ts` and the engine's
   `PreviewMedia`). The engine plays the raw track only from `music_url` (or a relative path inside
   the project); otherwise no music plays until the stem is ready, as "Preparing the music..."
   says.
6. **Slates (engine).** One slate canvas and one texture per scene slot (two for a cut), drawn
   again when the slot's scene (or the font) changes, instead of one per scene number for the life
   of the preview (an animatic of 60 slates held 59 canvases and textures, 352 MB more in the GPU
   process). `engine.test.ts` shows sixty slates through two canvases.
7. **Small fixes found on the way.** A popup PNG used again counts as new for the popup cache's
   pruning (a URL just handed out is no longer the first to go).
