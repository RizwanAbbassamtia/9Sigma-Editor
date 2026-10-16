# 9Sigma Editor v0.1 contract: the standalone editor from the CashCow copy

Binding for the first wave. Plan: `docs/PLAN.md`. Source of the copy: CashCow Studio
(`F:\CashCowStudio`, GitHub `RizwanAbbassamtia/CashCow-studio-`) at the first commit that contains
its M5.1 preview engine. The copy happens once; afterwards this repo is independent.

## 0. Rules

1. Nothing in CashCow Studio changes. The copy is read-only on the source.
2. Rename everything: package `cashcow_studio` → `sigma_editor`; product "CashCow Studio" →
   "9Sigma Editor"; CLI `ccs` → `sge`; env prefix `CCS_` → `SGE_`; app data
   `%LOCALAPPDATA%\9SigmaEditor`; default projects folder `Videos\9SigmaEditor\projects`, exports
   `Videos\9SigmaEditor\exports`; server port 8766; GitHub repo `RizwanAbbassamtia/9Sigma-Editor`
   for the in-app update check; window title and shortcuts "9Sigma Editor"; icon placeholder.
3. Sign-in with the team's accounts (the owner's request of 2026-10-16, replacing "no
   sign-in"): ten preset accounts, `user1` to `user10`, kept with hashed passwords in
   `config/users.json` and copied to `<app_data_dir>/users.json` on first start. The initial
   passwords are handed to the owner in `config/local.user-passwords.txt` (never committed);
   a person changes their own password in Settings, and `sge users set-password` resets one.
   The app opens on the sign-in page, then the Edit room. The accounts only decide who may
   use the app: settings and edits stay per laptop. No shared folder.
4. Every kept feature works offline with no AI: the editor must be complete for a person who
   never signs in to anything. AI help is out of scope for v0.1.
5. Plain English, laptop budget, tests, and the full checks before every commit (as in CLAUDE.md).

## 1. What the copy keeps, drops and adds

**Keep** (renamed): `render/*` whole; `models/timeline.py`, `models/project.py` (edit-only kind),
`models/settings`, `storage/project_store.py`, `storage/settings_store.py`, `storage/db.py`
(trimmed), `api/projects.py` (edit-only subset), `api/edit.py`/`api/export.py` equivalents,
`api/preview.py`, `api/settings.py` (paths, render, captions), `api/files.py`, `api/system.py`,
`doctor.py` (FFmpeg, fonts, disk, WebView2), `cli.py` (serve, open, doctor), `config/render.yaml`,
`config/transitions.yaml`, `config/captions.yaml`, `assets/fonts`, `frontend/public/transitions`,
`frontend/src/preview/*`, `components/preview/*`, `components/review/EditReview.tsx` and
`components/edit/*` (become the main screen), `components/storyboard/TransitionPicker.tsx`,
`components/settings/{RenderCard,PathsCard}.tsx` (and the caption style part), `components/ui/*`,
`api/*.ts` for the kept routes, `types/*.ts` for the kept models, `installer/*`, `tools/build_runtime.py`,
`tools/make_setup_exe.py`, `tools/publish_release.py`, `tools/withdraw_release.py`,
`tools/make_transition_previews.py`, the tests of everything kept.

**Drop**: `auth/*`, `api/auth.py`, `api/users.py`, `api/library.py`, `library/*`, `research/*`,
`llm/*`, `providers/{voice,image,research}/*` except the mock image placeholder helper if the
render tests need it, `pipeline/*` (the stage engine; see section 3 for the replacement),
`policy/*` except the checks named in section 4, the Automation / Master Sheet / Settings-users
screens, the channel form, the storyboard board (no storyboard tab in this app, by the owner's decision of 2026-10-14), demo videos,
CashCow docs. `yt-dlp`, `anthropic`, `google-genai`, `youtube-transcript-api` leave
`pyproject.toml`.

**Add** (section 3 to 6): New edit from files, the timing tool, the caption import, the quality
presets, the export with checksum, the new shell.

## 2. Shell and routes

`/` = the Edit room list ("My edits": name, shape, length, status, last change; New edit; Open;
Rename; Open folder; Delete to the Recycle Bin), `/edit/:id` = the Edit room, a full-window
workspace laid out like CapCut and Premiere (docs/design/EDITOR-DESIGN.md section 4, the owner's
decision of 2026-10-16, see 7.14): a 48 px rail, a 44 px top bar (back, title, undo and redo,
the save state, the Timeline view, Open folder, the amber Render menu, the Checks pill, More),
the library on the left (Pictures, Motion, Transitions, Text, Music), the live preview monitor
in the middle with the transport, the inspector on the right (it follows the selection: scene,
cut, popup, caption, sentence, pause, music, marker, or the video), and the timeline below
(toolbar; header timecode; overview strip; ruler; the lanes Markers, Popups, Captions, Scenes,
Voice, Music; the playhead). `/renders` = every rendered file, `/settings` = Folders, Render
(sizes, quality, encoder speed, H.265 copy, 4K, captions, transition pack, camera move), Your
account (password), Health checks, About (version, update check). The other pages keep the
48 px rail with My edits, Renders, Settings, a light/dark switch, and at the bottom the
signed-in person's initials with Sign out; `/login` = the sign-in page (section 0.3). The Edit
room uses the dark workspace tokens of the design; the other pages the CashCow editor tokens.

## 3. New edit from files (`api/projects.py`: `POST /api/edits`, `models/edit_input.py`)

Form: name; format (16:9 long, 9:16 Short); language; pictures and clips (many files, in the
order added with Move up / Move down, or by file name), per item an optional popup line and, for
a clip, "keep its sound"; narration audio (mp3/wav/m4a; optional); music file (optional; licence
note field); caption text (typed paragraphs) or an SRT/VTT file (optional).

The app builds the project folder (`01_media/`, `05_voice/`, `06_images/`, `07_edit/`, `08_export/`,
`project.json` with `kind: "edit"`), and the first timeline: scene length = narration sentence
timing when the captions are typed or imported (sentence starts estimated by word count, nudged in
the timing tool), else an even split of the narration length, else 6 s per picture; camera moves
alternate presets; transitions from the Cinematic pack with the no-repeat rule; captions from the
text with word times spread evenly inside each sentence until the timing tool adjusts them;
popups from the per-item caption lines. Clips keep their own length (trimmed to the scene when
longer) and are muted unless "keep the clip's sound" is ticked.

## 4. Editor screen (v0.1 scope) and checks

The Edit room of docs/design/EDITOR-DESIGN.md section 4, in v0.1: select a scene, a cut, a
popup, a caption, a sentence, a pause, the music or a marker and the inspector shows its fields;
drag a cut and it snaps to a sentence start (Shift: a word, Alt: any frame; each scene keeps at
least 2 s; transitions are re-clamped to a quarter of the shorter scene); split (S) and merge
(Delete); move, trim and re-time popups (edges snap to words); fix a caption's text and times,
split or merge cues; shorten a pause (the one edit that moves everything after it, through
`POST /api/edits/{id}/timing`); pick a camera move, a transition (Apply to all, Vary all), a
caption look, the music and its volume and dip; markers (M) and popups (T) at the playhead; the
in-out range and the loop; 20 undo steps in the browser (Ctrl+Z, Ctrl+Y) over the whole
document; the Render menu (sizes with Not rendered / Out of date / Rendered, captions, popups,
quality, Render, Range only, progress, Cancel, Export, Open the export folder); the keyboard map
of the design's section 6 (the ? sheet lists it). Replace a picture, add a picture or clip,
reorder scenes and remove a scene go to the server at once (the timeline is built again there);
everything else is the document in the browser, saved whole shortly after the last change
(`PUT /api/edits/{id}/timeline` with the revision; a stale revision is refused with 409 and the
page offers to reload). Checks kept: music licence note present, loudness, true peaks, caption
speed (characters per second), picture missing. Not in v0.1 (see 7.14): dragging the motion
boxes on the monitor, dragging a scene to reorder it, the Compare-with-render wipe, lane sizes,
resizable panels.

## 5. Render and export

Render sizes 720p, 1080p, 4K (behind "Allow 4K"). Quality: **Standard** (x264 crf 20 medium),
**High** (crf 17 slow), **Best** (crf 15 veryslow, two-pass optional). Upload presets **YouTube
1080p** and **YouTube 4K** (H.264 High, AAC-LC 48 kHz 384 kb/s, `-movflags +faststart`,
`-maxrate 12M -bufsize 24M` at 1080p and `-maxrate 45M -bufsize 90M` at 4K). Optional H.265 copy.
Range render of the in/out range. Export copies the chosen renders, a poster frame (first scene)
and `export.txt` (name, length, sizes, quality, checksums) to the export folder. The cached scene
clips mean a quality change re-encodes only the final pass.

## 6. Settings, installer, update

Settings: edits and exports folders (mapped drives allowed), render defaults (sizes, quality,
encoder speed, H.265 copy, captions burned in, caption colours and look, transition pack, camera
move), Allow 4K, your account (change the password), health checks, About with the version and
"Check for updates". The installer and runtime are the copied tools with the new names and
without Deno (this app downloads nothing from YouTube); the update check reads
`RizwanAbbassamtia/9Sigma-Editor` releases. `docs/INSTALL.md` for the team.

## 7. Decisions recorded during the build (2026-10-16)

1. **Source commit.** The CashCow session named no commit, so the copy comes from the latest
   commit on CashCow main whose message mentions the preview engine: `2a10946` (Storyboard v2).
   Between the M5.1 commit (`9f41fb5`) and `2a10946` the kept areas only gained fixes (the live
   preview, the dialog, the health checks); the tip of main differs only in CashCow's installer.
   The copy is made by `tools/copy_from_cashcow.py` (read-only on CashCow Studio; never
   overwrites a file already written here) and is run by the owner.
2. **The edit's folder** holds `project.json` (not `job.json`) with `kind: "edit"`, `name`,
   `format`, `language`, `status` (new, ready, rendering, rendered, exported, failed),
   `status_note` and `duration_s`; the subfolders `01_media/`, `05_voice/`, `06_images/`,
   `07_edit/`, `08_export/` keep the engine's own paths. No SQLite index: the list reads the
   folders. Delete moves the folder to the Recycle Bin (or `_deleted/` when the bin refuses).
3. **Timing.** `05_voice/timing.json` keeps the engine's shape (`models/timing.py`, trimmed),
   with two additions: `source: "captions"` for times taken from an SRT/VTT file, and
   `nudged: true` once a person moved a sentence start in the timing tool; each sentence also
   records its `paragraph`. Without a speech-to-text tool the words inside a sentence are spread
   by length (`words_source: "estimated"`).
4. **Scene plan.** The timeline builder copied from CashCow takes its scene list as a
   `StoryboardDoc` (`models/storyboard.py`); that model is kept, trimmed to the scene fields, as
   the app's internal scene plan. It is never shown as a storyboard (there is no storyboard tab
   in this app, by the owner's decision of 2026-10-14). `models/edit_input.py` is the form of
   "New edit from files".
5. **Export** writes `export.txt` (name, length, sizes, quality, a line per file with its
   SHA-256), `checksums.sha256` (the usual `<hash>  <file>` list) and `export.json` into
   `<exports_dir>/<YYYY-MM-DD>_<name-slug>/`.
6. **Clips.** The copied engine draws scenes from pictures only. v0.1 renders a clip as a scene
   through a new clip branch in the scene-clip renderer (muted unless "keep the clip's sound");
   the live preview shows the clip's poster frame with a note until the preview engine learns
   to play video.
7. **Sign-in** (section 0.3): `auth/` holds the password hashing (PBKDF2-SHA256, 210,000
   rounds, a random salt per password), the account file, the sessions (`sessions.json` in the
   app data folder: 12 hours, or 30 days with "Keep me signed in") and the middleware that
   answers 401 for every `/api/*` call except sign-in, the sign-in status and the system info.
   Five wrong passwords in a row lock an account for 30 seconds.
8. **Two clocks in timing.json.** Each sentence has `start_frame`/`end_frame` (its span in
   `voice.wav`: the first starts at 0, each ends where the next starts, the last ends at the end
   of the recording) and `start_s`/`end_s` (its place on the timeline: the file order laid end
   to end). A nudge moves a mark; a reorder permutes the sentences; the timeline builder gets the
   voice as sentence segments (`voice_segments_of`), so the narration follows the scenes.
9. **Where the files go.** The narration's original is kept next to `05_voice/voice.wav` as
   `05_voice/original-<name>`; `01_media/` holds the pictures, clips and music as added, and is
   the edit's music folder (the music picker lists its audio files).
10. **The editor's routes** (`api/editing.py`): `GET /api/edits/{id}/edit` (the payload),
    `POST /api/edits/{id}/edit` with `EditChoices` (sizes, music, captions, popups, volume, dip,
    rebuild), `PUT /api/edits/{id}/timeline` (autosave with revision), `POST /api/edits/{id}/
    scenes` (multipart), `POST .../scenes/{n}/picture`, `POST .../scenes/reorder`, `DELETE .../
    scenes/{n}`, `GET`/`PUT /api/edits/{id}/captions`, `GET /api/edits/{id}/captions.srt`,
    `GET /api/render-presets`, `POST /api/edits/{id}/render` (`presets` missing = the ticked
    sizes, `[]` = the preview only; `quality`, `upload_preset`, `h265_copy`, `range`),
    `GET .../render/status` (polled every second while a render runs; no WebSocket in this
    app), `POST .../render/cancel`, `POST /api/edits/{id}/export`, `GET /api/renders`. The
    preview routes keep the engine's `/api/projects/{id}/...` paths; the ids are edit ids. One
    render runs at a time (`edit/render_jobs.py`); the edit is locked while it runs.
11. **Render state.** `07_edit/render_state.json` records per size the file, its length and
    frame size, the render key (content, media fingerprints, quality, upload preset), the quality
    and upload preset used and the H.265 copy; a finished file is kept while its key matches.
12. **Simplifications of v0.1** (the owner asked for a usable version on 2026-10-16): the timing
    tool moves sentences with buttons (0.1 s and 0.5 s steps) rather than a draggable waveform;
    scenes are reordered with Move up / Move down rather than by drag; there is no 20-step undo
    in the browser; the live preview shows a clip's poster frame; the 2.5D parallax add-on is
    not installed (scenes fall back to the Ken Burns move, as the health check says).
13. **Front end.** The copied screens were reduced to this app: `AppShell` (rail, title bar
    with the health pill and the update notice), `LoginPage`, `EditsPage` with `NewEditDialog`,
    `RendersPage`, `SettingsPage`. The copied components that need no CashCow concept (the live
    preview engine, the transition picker, the UI kit) are kept as they were. The preview
    fixtures (`frontend/src/preview/fixtures/`) are made again from this engine with
    `tools/make_preview_fixtures.py`.
14. **The Edit room** (the owner, 2026-10-16 evening, after seeing the first panel layout:
    "follow the CapCut / Adobe face, same buttons, same layout" as the demo video of the editor
    design). `/edit/:id` is the full-window workspace of docs/design/EDITOR-DESIGN.md sections 2
    and 4 (`frontend/src/workspace/`): `WorkspaceShell`, `EditorWorkspace` (the document, the
    history, the autosave, the keyboard map), `TopBar` with `RenderMenu`, `Library`, `Monitor`
    (the preview engine in its monitor chrome plus the transport), `Inspector`, `Timeline`
    (lanes, ruler, overview strip, drags with snapping), and `document.ts` (the pure edits:
    cuts, splits, merges, motion, transitions, popups, captions, markers, music, the history).
    The scene plan follows the saved timeline (`builder.sync_plan_from_timeline` rebuilds the
    plan's scenes from it, so splits, merges and moved cuts survive a rebuild), and each render
    row says whether it is out of date (`RenderOutput.stale`, the render key compared). What
    the design describes and v0.1 leaves for later: the motion boxes on the monitor, drag to
    reorder (Move earlier / later in the scene's More menu instead), Compare with render, the
    Hand off menu, lane sizes, resizable panels, Ask Claude.

## 8. Tests and proof

All kept tests pass under the new names (the CashCow-only tests about channels, roles, the
pipeline, providers, the virtual timeline, WebSockets, the audit log, API keys and the depth
model download were removed; the engine, preview, render, files, auth, edits, timing, caption,
export, settings, update and installer tests stay). Front-end typecheck, build and vitest pass.
Proof on 2026-10-16: a 4-scene edit made through `POST /api/edits` from pictures, a silent
narration, music and caption text; the editor opened in the app; 1080p rendered from the Render
panel with live progress and exported from the Export panel; the Renders page listed it. Still
to do: the Setup EXE built and installed into a test folder, and screenshots of every screen
under `docs/screens/`.
