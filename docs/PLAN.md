# 9Sigma Editor: plan v0.1 (2026-10-14)

Owner's brief (2026-10-14): "the editing panel as a separate editing tool with all its settings,
working independently for the user; a desktop app like CapCut or Adobe; the existing CashCow
Studio stays as it is; this is an extra app." Decisions the owner made the same day: an
**independent copy** (the editor develops on its own from the copy; no sync with CashCow Studio),
**no sign-in** (a single-user desktop app), and the editor shown in the "editor, explained"
video, meaning the Edit room of `EDITOR-DESIGN.md`. AI help is optional and comes later as
Suggest buttons through the person's own Claude sign-in; the first releases are manual editing.

## 1. What it is

A Windows desktop app for narrated picture-and-clip videos: the person adds pictures, short
video clips, a narration recording (or any audio), music and caption text, edits on a
CapCut-style timeline with a live preview, and renders 720p, 1080p or 4K files with high-quality
presets. No accounts, no shared folder, no automation pipeline. It is a copy of CashCow Studio's
editing engine and the approved Edit room design, cut free of the rest.

Users: the 9Sigma team's video editors (non-developers), on Windows laptops without GPUs.

## 2. Where the code comes from

Copied once from CashCow Studio at the commit after its M5.1 preview engine landed, then renamed
and trimmed:

| Kept (renamed to the new package) | Dropped |
|---|---|
| Render engine: `render/*` (scene clips, cinematic transitions, Motion v2 and 2.5D parallax, captions, popups, music ducking stem, assembly, presets) | Research, title, script, storyboard-by-AI, voice-by-AI, pictures-by-AI stages and their providers |
| Timeline model and builder, project store for edit-only projects | Team accounts, roles, audit log, shared folder, libraries, production log |
| Preview engine (`frontend/src/preview/*`) and LivePreview | Channel master data; policy gates that only make sense for channels (the licence, loudness and picture checks stay) |
| Edit review screen, transition picker, scene strip, render settings, export of files | The Claude Code writing provider (returns later behind optional Suggest buttons) |
| Settings: paths, render sizes and quality, caption style, fonts | Login pages and the four-area shell |
| Installer, runtime, in-app update (pointing at the new repo) | Demo videos and CashCow docs |

Product name **9Sigma Editor**, package `sigma_editor`, CLI `sge`, app data
`%LOCALAPPDATA%\9SigmaEditor`, projects in `Videos\9SigmaEditor`, repo `9Sigma-Editor` on GitHub,
local folder `F:\9SigmaEditor`.

## 3. Releases

The owner's target (2026-10-14, "I need this one as a separate app: the editor explained
script"): the app the team gets is the **Edit room shown in the "editor, explained" video**
(`docs/reference/editor-explained-script.md`, designed in `docs/design/EDITOR-DESIGN.md`
section 4 and the mockup `docs/design/mockup-editor.html`): the four zones, the timeline lanes
with trim handles and snapping, the inspector, the transitions library, the render menu, the
states, undo and history. v0.1 below is the foundation (the copied engine running as its own app);
**v0.2 is the first release given to the team**, because it is the editor from the video.

- **v0.1 (foundation):** the trimmed copy runs as its own app. New edit from files: pictures and
  clips in order, narration audio, music, caption text typed or from an SRT/VTT file, a simple
  timing tool (drag sentence starts on the waveform). The Edit review screen as the main screen,
  live preview, transitions picker, popups and captions on or off, music with ducking, render
  sizes with quality Standard / High / Best and the YouTube 1080p and 4K upload presets, export
  folder with checksum. Installer and in-app update from the new repo.
- **v0.2:** the Timeline tab from `docs/design/EDITOR-DESIGN.md` section 4: four zones, lanes
  (markers, popups, captions, scenes, voice, music), trim handles, snapping, ripple moves,
  inspector with motion start and end frames, undo and history, keyboard shortcuts, render menu
  with "out of date" tracking, range render and Compare.
- **v0.3:** hand-off to CapCut, DaVinci Resolve and Kdenlive, caption styles and presets, sound
  effects lane, multi-track music. (No storyboard tab: the owner decided on 2026-10-14 that the
  storyboard stays in CashCow Studio only; 9Sigma Editor is a pure editor.)
- **v0.4:** optional AI help through the person's own Claude sign-in: Suggest camera moves and
  transitions, Suggest captions from the narration (when a speech-to-text tool is configured),
  Ask Claude.

## 4. Rules

Contract first, code second (`docs/CONTRACT-v0.1.md` before any code of v0.1). Plain-English UI.
One render at a time; the laptop budget of CashCow's design (30 fps preview at 960 x 540, 1080p
render near real time on a 4-core i7). Pydantic models are the source of truth, mirrored in
`frontend/src/types/`. Tests and the full checks (ruff, pytest, typecheck, build) before every
commit. The owner pushes; releases go on the GitHub releases page like CashCow Studio.

## 5. Decisions taken with the recommended answers (2026-10-14)

1. Captions without AI come from typed sentences with the timing tool or an SRT/VTT file in v0.1;
   speech-to-text arrives with v0.4 through a configured tool.
2. The 2.5D parallax add-on is included (about 60 MB more in the install).
3. The repository is public, like CashCow Studio, so the in-app update reads releases without a
   token.
