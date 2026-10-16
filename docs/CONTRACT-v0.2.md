# 9Sigma Editor v0.2 contract: the CapCut-style project

Binding for the second wave (the owner's request of 2026-10-16, evening: "when we start new it
should be like CapCut: import video, images or any content, like voice; frame adjustment of
every kind of video and image content; multiple editing layers of images, video clips and
voices; an AI option to make a voice from text through an API key; voice speed, video speed,
image time", with Adobe Premiere Pro as the reference for the feature set). Everything in
`docs/CONTRACT-v0.1.md` stays in force unless a section below changes it. Plan: `docs/PLAN.md`.

## 0. Rules (added to v0.1 section 0)

1. Every new field of `timeline.json` is optional with a default, so a v0.1 timeline reads,
   plays and renders exactly as before (its render key does not change).
2. The scene track stays the backbone of the video: the length of the video is the end of the
   last scene, the narration keeps the two-clock timing of v0.1, and everything new (layers,
   sound tracks) sits on top of or under it and is cut off at the end of the last scene.
3. The AI voice is optional (the owner, 2026-10-16: "that voice should be optional; if we need
   to use it, otherwise keep"): the editor does everything without it, and it only appears as
   usable once a key is added. It is the one feature that talks to a service on the internet. It needs the
   person's own API key, saved on this laptop only (never in the repo, never in `settings.json`
   where Settings shows it, never in an edit's folder). Without a key, everything else works as
   before. The provider is ElevenLabs (the owner wrote "ai33"; no service of that name exists,
   and ElevenLabs is the usual text-to-speech service sold by API key; the provider is one
   module, so another can be added without touching the editor).
4. Plain English everywhere, as in CLAUDE.md: "layer", "sound track", "clip", "picture",
   "frame", "speed"; never "overlay", "PiP", "atempo", "setpts".

## 1. Start like CapCut

- "My edits" opens with a big **Create project** card (name, shape 16:9 or 9:16, language) that
  makes an empty edit and opens the Edit room at once (`POST /api/edits/blank`, JSON
  `{name, format, language}`, answers the `Project` with status `new`). "Start from files"
  (the v0.1 dialog, one request with every file) stays as the second button.
- An empty edit has `project.json`, the five folders, `07_edit/scenes.json` with no scenes,
  `05_voice/timing.json` with no sentences (`source: "scenes"`), a 1 s silent
  `05_voice/voice.wav` and `07_edit/timeline.json` with no scenes (`duration_s: 0`, the frame
  size of the default render size, the Settings' render sizes).
- The Edit room with no scenes shows the Media tab open, an "Import" drop zone ("Drag pictures,
  clips and sound files here, or press Import"), the monitor says "Add pictures, clips or sound
  from the Media tab to start", and the timeline says "Drag material here and start to create".
  Render and Export are off until there is a scene.

## 2. The media library (`01_media/`)

- **Import** copies files into the edit's `01_media/` folder as they are (unique names, the
  v0.1 name rules): pictures (png, jpg, jpeg, webp, bmp, tif), clips (mp4, mov, m4v, mkv, webm,
  avi), sound (mp3, wav, m4a, aac, flac, ogg, opus, wma, aif, aiff) and caption files (srt,
  vtt). AI voices land in `01_media/voice/`. `POST /api/edits/{id}/media` (multipart
  `files[]`) answers the whole library; `GET /api/edits/{id}/media` lists it;
  `DELETE /api/edits/{id}/media?path=` removes a file that nothing uses (409 otherwise).
- A library item (`models/media.py` `MediaItem`): `path` (relative to the edit's folder),
  `name`, `kind` (`picture`, `clip`, `audio`, `captions`), `size_bytes`, `duration_s` (clips
  and sound, read with ffprobe and cached in `01_media/.library.json` by name, size and date),
  `width` and `height` (pictures and clips), `thumb_url` (pictures through the thumbnail
  route; clips through a poster frame cached in `07_edit/preview/posters/`), `url` (the files
  route) and `in_use` (plain words: "scene 3", "layer 1", "sound 1", "the narration", "the
  music").
- From the library a person can: **Add to video** (a picture or clip becomes a scene at the
  end or after the selected scene: `POST /api/edits/{id}/scenes` with the form field `media`
  naming the library path instead of an upload; a clip is not copied again), **Add as a
  layer** (a layer clip at the playhead, see section 3), **Use as narration** (a sound file:
  `POST /api/edits/{id}/narration` with `media` or an upload, and optionally `caption_text`;
  the narration is converted as in v0.1 and the timing is estimated again over its length,
  keeping the caption text the edit already had), **Use as music** (the v0.1 choice), **Add as
  a sound** (a sound clip on a sound track at the playhead). Items can also be dragged onto
  the lanes of the timeline: the Scenes lane adds a scene, a layer lane adds a layer clip at
  the drop time, a sound lane adds a sound clip, the Music lane sets the music.

## 3. Layers and sound tracks (`models/timeline.py`)

- `Timeline.layers: list[Layer]` (bottom to top; drawn over the scenes, under the popups and
  captions). `Layer`: `id` (`layer-01` ...), `name`, `visible`, `locked`, `clips`. `LayerClip`:
  `id` (`lc-001` ...), `source` (a picture or clip in the edit's folder), `kind` (`picture`,
  `clip`), `name`, `start_s`, `end_s` (on the timeline), `in_s` (clips: where in the file the
  clip starts), `speed` (clips: 0.25 to 4), `keep_sound`, `gain_db`, `duration_s` (the clip
  file's own length when known), `frame` (section 4), `adjust` (section 4). Clips on one
  layer never overlap (the model refuses it; the editor clamps a drag to its neighbours).
- `Timeline.audio_tracks: list[AudioTrack]` (under the voice). `AudioTrack`: `id`
  (`track-01` ...), `name`, `kind` (`voice` or `sound`, a label only), `muted`, `gain_db`,
  `clips`. `AudioClip`: `id` (`ac-001` ...), `source`, `name`, `start_s`, `end_s`, `in_s`,
  `speed` (0.5 to 2 for a voice, 0.25 to 4 otherwise), `gain_db`, `fade_in_s`, `fade_out_s`,
  `text` and `voice` (filled for an AI voice: what it says and which voice). Clips on one
  track never overlap.
- The narration track of v0.1 (`timeline.voice`) stays the main voice: it is what the
  sentences, captions and the music dip follow. Extra voices and sounds are sound tracks.
- Layer and sound clips are a browser document edit (undo, autosave) like everything else in
  the Edit room. The scene plan ignores them; a rebuild keeps them (`build_edit_timeline`
  carries `layers` and `audio_tracks` over from the saved timeline).

## 4. Frame, adjust, speed and picture time

- `FrameAdjust` (where a picture or clip sits in the frame, CapCut's "Basic" and "Crop"):
  `x`, `y` (the centre, as fractions of the frame width and height, 0 = the middle, -0.5 =
  the left or top edge), `scale` (1 = fills the frame as `fit` says), `rotation` (degrees),
  `opacity` (0 to 1), `crop` (`[left, top, right, bottom]` as fractions of the source, each
  under 0.95), `fit` (`cover` or `contain`), `flip_h`, `flip_v`.
- `ColourAdjust` (CapCut's "Adjust"): `brightness`, `contrast`, `saturation`, each -1 to 1
  (0 = as shot).
- A layer clip has both. A scene has `frame` (clips only: a picture scene's frame is its
  camera move, Scale and Centre in the Motion section as in v0.1), `adjust` (any scene), and
  `clip: ClipSource | None` for a scene made from a video
  clip: `path`, `in_s`, `speed` (0.25 to 4), `keep_sound`, `gain_db`, `duration_s`. v0.1 wrote
  the clip only in the scene plan (`scenes.json`); v0.2 writes it in the timeline too, so the
  renderer and the preview play the video. A scene whose `clip` is set is rendered from the
  clip (section 6); its `image` stays its poster frame for the lanes and the library.
- **Speed.** `speed` on a clip scene, a layer clip or a sound clip: the source plays faster or
  slower (a clip at 2x shows twice as many seconds of the file in the same scene). The scene's
  own length does not change; the inspector shows how much of the clip the scene uses.
- **Picture time.** The length of a scene is `end_s - start_s`. The inspector's Length field:
  in an edit with a narration it moves the next cut (the rolling edit of v0.1, same limits);
  in an edit without one (`timing.source == "scenes"`) it moves everything after the scene
  (a ripple), and the scene plan keeps each scene's chosen length (`est_duration_s`) so Add,
  Remove and Rebuild keep them.
- The camera move (Motion) stays as in v0.1 for pictures. A clip scene holds its frame (no
  camera move) in v0.2.

## 5. The AI voice (ElevenLabs)

- Settings > **AI voice**: the API key (shown masked, Save, Remove, Test), saved in
  `<app_data_dir>/secrets.json` (`PUT /api/voice/key {api_key}`; `GET /api/voice/status`
  answers `{provider, key_set, key_hint}`). `GET /api/voice/voices` lists the account's
  voices (`{id, name, description}`); without a key it answers 422 "Add your ElevenLabs API
  key in Settings first." A provider error is answered as 502 with the provider's message in
  plain English.
- Library > Text > **Make a voice from text**: the text (up to 5,000 characters), the voice, a
  speaking speed (0.7 to 1.2, the provider's own range; the clip's `speed` adds more), a
  name, and what to do with it: **Add as a sound** (a sound clip on a sound track at the
  playhead) or **Use as narration** (replaces the narration; the sentence and word timing comes
  from the provider's character times, `timing.source: "aligned"`, so captions and scenes follow
  the voice exactly). `POST /api/edits/{id}/voice {text, voice_id, voice_name, speed, name, use}`
  writes `01_media/voice/<name>.mp3` and `<name>.mp3.alignment.json`, answers the library item
  (and the fresh editor payload when it became the narration). "Add as a sound" places the
  clip in the browser document at the playhead (the route only makes the file).
- The request runs while the person waits (a few seconds for a paragraph; up to a minute for
  a long script); the app never retries a paid request on its own.

## 6. Render

- **Clip scenes** are drawn by a new branch of the scene-clip renderer: FFmpeg decodes the
  clip from `in_s` at `speed`, fits it to the frame (`frame`: crop, scale, position, rotation,
  flips; `fit: cover` fills the frame when `frame` is null), holds the last frame when the
  clip is shorter than the scene, and writes the scene clip with the same encoding, lead-in
  and tail-out frames as a picture scene, so transitions, caching and the cut phase stay the
  same. The clip's own sound (`keep_sound`) is mixed in the final pass at the scene's place,
  at `speed`, at `gain_db`.
- **Layers** go into the final pass between the scenes and the popups: each visible layer
  clip is an input (a picture looped for its length, a clip from `in_s` at `speed`), fitted
  with `crop`, `scale`, `rotate`, the flips, `eq` for the adjust, its opacity, and overlaid
  at its place for its time (`enable=between`). Captions stay on top of everything.
- **Sound tracks** are mixed with the voice and the music before the loudness pass: every
  clip of every track that is not muted, cut from `in_s`, at `speed`, with its gain and fades,
  placed at `start_s`. The loudness target (-16 LUFS) applies to the whole mix.
- **Adjust** on a scene is applied in the final pass (`eq` enabled for the scene's time).
- The render key covers every new field and every new source file (a replaced clip or sound
  file makes a render "Out of date"). A range render keeps the layer and sound clips that
  fall inside the range.

## 7. The live preview

- Clip scenes and layer clips play as video in the monitor: the engine keeps one hidden video
  element per clip file, seeks it to `in_s + (t - start_s) x speed` while paused and lets it
  run at `speed` while playing (re-aligned when it drifts more than a sixth of a second).
  Layers are drawn as sprites with their frame and adjust over the scene and under the popups
  and captions. Sound clips play through audio elements synced to the preview clock. The
  picture is a preview: a frame may differ slightly from the render while a clip seeks.
- A scene's adjust applies to the whole monitor frame while that scene shows.

## 8. The Edit room (additions to v0.1 section 4)

- Library tabs: **Media** (section 2) replaces Pictures; Text gains the AI voice; Music stays.
- Timeline lanes, top to bottom: Markers, Popups, Captions, one lane per layer (top layer
  first; header with the name, show/hide, lock, remove; "Add a layer"), Scenes, Voice, one
  lane per sound track (header with the name, mute, remove; "Add a sound track"), Music.
  Layer and sound clips move along their lane (clamped to neighbours), trim at the edges, are
  split at the playhead (S) and removed (Delete). Snapping follows the v0.1 rules.
- Inspector: a **layer clip** (Source, Time: start, length, speed, from; Frame; Adjust;
  Sound; Remove), a **sound clip** (Time, speed, volume, fades, the AI text when it has
  one, Remove), a **layer** or **sound track** header, and the **scene** gains Length, Speed
  (clips), Frame (clips: position, scale, rotation, opacity, crop, flips; pictures: crop and
  flips next to the Motion section), Adjust and Sound (clips).
- Keyboard: no new keys; S and Delete work on the selected layer or sound clip too.

## 9. What stays for later

The camera move on a clip scene; dragging the frame on the monitor; speed ramps; a clip's
sound in the preview at its exact gain; chroma key; text layers beyond popups and captions;
the other text-to-speech providers; speech-to-text for a recorded narration (v0.4 in the plan).

## 10. Tests and proof

Backend: the models (defaults, old timelines unchanged, overlap refused), the blank project
and the import routes, Add to video from the library, the narration route, the render graph
with a layer, a sound track and a clip scene (real FFmpeg on tiny media), the render key with
the new fields, the AI voice routes with a fake provider (no network in tests). Front end:
the document operations for layers and sound tracks, the parser. Proof on the owner's laptop
(2026-10-16, night): the project "Demo CapCut project" created empty in the browser, three
pictures, a clip and two sound files imported, a picture scene and a clip scene added from the
library, the logo added as a layer and a tone as a sound clip, a narration set afterwards (the
clip, the layer and the sound survived the rebuild), the clip scene playing as video in the
monitor with the layer over it, then rendered at 720p through the app: 9.8 s, picture and sound,
the layer at its place in the frame and the clip's own frames in the clip scene.
