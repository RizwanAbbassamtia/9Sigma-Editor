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
3. No sign-in. The app opens on the Edit room. Settings are per laptop. No shared folder.
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
screens, the channel form, the storyboard board (the Storyboard tab comes in v0.3), demo videos,
CashCow docs. `yt-dlp`, `anthropic`, `google-genai`, `youtube-transcript-api` leave
`pyproject.toml`.

**Add** (section 3 to 6): New edit from files, the timing tool, the caption import, the quality
presets, the export with checksum, the new shell.

## 2. Shell and routes

`/` = the Edit room list ("My edits": name, length, last change, status; New edit; Open folder;
Delete to the Recycle Bin), `/edit/:id` = the editor (today's EditReview layout as the main screen:
live preview monitor, scene strip, transition per cut, popups and captions switches, music, render
sizes, checks, Render and Export), `/renders` = every rendered file, `/settings` = Paths, Render,
Captions, Health checks, About (version, update check). A 48 px rail with My edits, Renders,
Settings; no top-bar user chip. Dark theme as the CashCow editor tokens.

## 3. New edit from files (`api/projects.py`: `POST /api/edits`, `models/edit_input.py`)

Form: name; format (16:9 long, 9:16 Short); pictures and clips (many files, order by name or by
drag), per item an optional caption line; narration audio (mp3/wav/m4a; optional); music file
(optional; licence note field); caption text (typed paragraphs) or an SRT/VTT file (optional).

The app builds the project folder (`01_media/`, `05_voice/`, `06_images/`, `07_edit/`, `08_export/`,
`project.json` with `kind: "edit"`), and the first timeline: scene length = narration sentence
timing when the captions are typed or imported (sentence starts estimated by word count, nudged in
the timing tool), else an even split of the narration length, else 6 s per picture; camera moves
alternate presets; transitions from the Cinematic pack with the no-repeat rule; captions from the
text with word times spread evenly inside each sentence until the timing tool adjusts them;
popups from the per-item caption lines. Clips keep their own length (trimmed to the scene when
longer) and are muted unless "keep the clip's sound" is ticked.

## 4. Editor screen (v0.1 scope) and checks

Everything the CashCow Edit review offers today, plus: the timing tool (`VoiceTimingPanel`:
waveform with draggable sentence starts, Play from here, Shift all after this by ±0.1 s,
`POST /api/edits/{id}/timing`), the caption editor (edit cue text and times in a table; the ASS
writer re-runs), "Replace picture" per scene, "Reorder scenes" (drag in the strip, which moves the
sentences with the scene), "Add scene" from a file, music volume and dip (uses the ducking stem),
and the quality presets of section 5. Checks kept: music licence note present, loudness, true
peaks, caption speed (characters per second), picture missing. Every edit is autosaved to
`07_edit/timeline.json` with a revision and a 20-step undo in the browser.

## 5. Render and export

Render sizes 720p, 1080p, 4K (behind "Allow 4K"). Quality: **Standard** (x264 crf 20 medium),
**High** (crf 17 slow), **Best** (crf 15 veryslow, two-pass optional). Upload presets **YouTube
1080p** and **YouTube 4K** (H.264 High, AAC-LC 48 kHz 384 kb/s, `-movflags +faststart`,
`-maxrate 12M -bufsize 24M` at 1080p and `-maxrate 45M -bufsize 90M` at 4K). Optional H.265 copy.
Range render of the in/out range. Export copies the chosen renders, a poster frame (first scene)
and `export.txt` (name, length, sizes, quality, checksums) to the export folder. The cached scene
clips mean a quality change re-encodes only the final pass.

## 6. Settings, installer, update

Settings: projects and exports folders (mapped drives allowed), render defaults (sizes, quality,
encoder speed, captions burned in, caption style), Allow 4K, health checks, About with the version
and "Check for updates". The installer and runtime are the copied tools with the new names; the
update check reads `RizwanAbbassamtia/9Sigma-Editor` releases. `docs/INSTALL.md` for the team.

## 7. Tests and proof

All kept tests pass under the new names. New tests: project creation from files in each
combination (pictures only; with narration; with SRT; with clips; Short), timing nudge, caption
edit, reorder moves sentences, quality presets produce the expected FFmpeg arguments, upload
presets' bitrate flags, export checksums, the renders list, settings. A real 3-scene 480p render
with narration and music. Front-end typecheck, build and vitest. A full install of the Setup EXE
into a test folder starts the app and renders the sample edit. A short screenshot set of every
screen under `docs/screens/`.
