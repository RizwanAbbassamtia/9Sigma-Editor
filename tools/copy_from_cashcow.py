"""One-time copy of the kept files from CashCow Studio into this repository.

    python tools\\copy_from_cashcow.py            copy
    python tools\\copy_from_cashcow.py --list     only print what would be copied

Source: F:/CashCowStudio at commit 2a10946 (Storyboard v2, the latest commit on main whose
message mentions the M5.1 preview engine; docs/CONTRACT-v0.1.md section 0). Read-only on the
source: every file is read with ``git show <commit>:<path>`` from the object store; nothing in
F:/CashCowStudio is checked out, written or touched.

What is copied is the "Keep" list of the contract (section 1) plus the files the kept code
needs; text files get the renames of section 0.2 (package ``sigma_editor``, product "9Sigma
Editor", command ``sge``, settings prefix ``SGE_``, app data folder ``9SigmaEditor``, port 8766,
the ``9Sigma-Editor`` GitHub repository); binary files (fonts, transition previews) are copied
as they are. A file that already exists here is never overwritten (the new code written for
this app lives next to the copy), it is listed as "kept".

After the copy this repository is independent: never run this again without the owner's
say-so (CLAUDE.md).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SRC = "F:/CashCowStudio"
COMMIT = "2a10946"
DST = Path(__file__).resolve().parent.parent

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".md", ".json", ".yaml", ".yml", ".css", ".html", ".txt", ".toml",
    ".mjs", ".js", ".svg", ".cfg", ".ini", ".typed",
}

# Order matters: the longer, more specific names first.
REPLACEMENTS: list[tuple[str, str]] = [
    ("RizwanAbbassamtia/CashCow-studio-", "RizwanAbbassamtia/9Sigma-Editor"),
    ("CashCow-studio-", "9Sigma-Editor"),
    ("cashcow_studio", "sigma_editor"),
    ("cashcow-studio", "9sigma-editor"),
    ("CashCowStudio", "9SigmaEditor"),
    ("CashCow Studio", "9Sigma Editor"),
    ("cashcow.ico", "sigma.ico"),
    ("CCS_", "SGE_"),
    ("CashCow", "9Sigma"),
    ("cashcow", "sigma"),
    ("8765", "8766"),
]
WORD_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bccs\b"), "sge"),
]

BACKEND = "backend/cashcow_studio/"
BACKEND_KEEP_PREFIXES = ("render/", "audio/", "policy/")
BACKEND_KEEP_FILES = {
    "app.py", "cli.py", "config.py", "doctor.py", "launch.py", "origin_guard.py", "py.typed",
    "updates.py",
    "api/__init__.py", "api/deps.py", "api/doctor.py", "api/files.py", "api/preview.py",
    "api/projects.py", "api/settings.py", "api/system.py", "api/updates.py", "api/ws.py",
    "models/timeline.py", "models/timing.py", "models/transitions.py",
    "storage/settings_store.py",
    "export/exporter.py",
}
# Files that move to a new home inside the package (None = dropped).
BACKEND_MOVES: dict[str, str | None] = {
    "pipeline/stages/edit.py": "edit/renderer.py",
    "pipeline/stages/base.py": "edit/stage_base.py",
    "pipeline/jobs.py": "edit/jobs.py",
    "pipeline/events.py": "edit/events.py",
    "llm/config.py": "config_files.py",
    "audio/aligner.py": None,
}

FRONTEND_KEEP_PREFIXES = (
    "frontend/public/",
    "frontend/src/preview/",
    "frontend/src/components/edit/",
    "frontend/src/components/ui/",
    "frontend/src/components/voice/",
)
FRONTEND_KEEP_FILES = {
    "frontend/.gitignore", "frontend/index.html", "frontend/package.json",
    "frontend/package-lock.json", "frontend/tsconfig.json", "frontend/vite.config.ts",
    "frontend/src/index.css", "frontend/src/main.tsx", "frontend/src/App.tsx",
    "frontend/src/api/client.ts", "frontend/src/api/doctor.ts", "frontend/src/api/edit.ts",
    "frontend/src/api/files.ts", "frontend/src/api/index.ts", "frontend/src/api/keys.ts",
    "frontend/src/api/preview.ts", "frontend/src/api/preview.test.ts",
    "frontend/src/api/projects.ts", "frontend/src/api/settings.ts", "frontend/src/api/system.ts",
    "frontend/src/api/update.ts", "frontend/src/api/voice.ts", "frontend/src/api/ws.ts",
    "frontend/src/components/editing/ProjectThumb.tsx",
    "frontend/src/components/editing/RenderPlayerDialog.tsx",
    "frontend/src/components/editing/useRenders.ts",
    "frontend/src/components/editing/editingProjects.ts",
    "frontend/src/components/form/SegmentedControl.tsx",
    "frontend/src/components/form/NumberField.tsx",
    "frontend/src/components/master/format.ts", "frontend/src/components/master/routes.ts",
    "frontend/src/components/master/SearchBox.tsx", "frontend/src/components/master/CountLine.tsx",
    "frontend/src/components/preview/LivePreview.tsx",
    "frontend/src/components/preview/LivePreview.test.tsx",
    "frontend/src/components/preview/PreviewChips.tsx",
    "frontend/src/components/preview/QualityMenu.tsx",
    "frontend/src/components/preview/TimeDisplay.tsx",
    "frontend/src/components/preview/Transport.tsx",
    "frontend/src/components/preview/builderRules.ts",
    "frontend/src/components/preview/builderRules.test.ts",
    "frontend/src/components/preview/bundle.ts", "frontend/src/components/preview/bundle.test.ts",
    "frontend/src/components/preview/engineContract.test.ts",
    "frontend/src/components/preview/engineLoader.ts",
    "frontend/src/components/preview/mockMedia.ts",
    "frontend/src/components/preview/previewTime.ts",
    "frontend/src/components/preview/previewTime.test.ts",
    "frontend/src/components/preview/timeStore.ts",
    "frontend/src/components/preview/usePreviewEngine.ts",
    "frontend/src/components/preview/usePreviewEngine.test.tsx",
    "frontend/src/components/projects/ProgressBar.tsx",
    "frontend/src/components/review/EditReview.tsx",
    "frontend/src/components/review/EditReview.test.tsx",
    "frontend/src/components/script/Panel.tsx",
    "frontend/src/components/settings/DepthModelRow.tsx",
    "frontend/src/components/settings/DoctorPanel.tsx",
    "frontend/src/components/settings/PathsCard.tsx",
    "frontend/src/components/settings/RenderCard.tsx",
    "frontend/src/components/storyboard/TransitionPackPreview.tsx",
    "frontend/src/components/storyboard/TransitionPicker.tsx",
    "frontend/src/components/storyboard/transitionCatalogue.ts",
    "frontend/src/layout/DoctorPill.tsx", "frontend/src/layout/UpdateNotice.tsx",
    "frontend/src/lib/cn.ts", "frontend/src/lib/format.ts", "frontend/src/lib/formErrors.ts",
    "frontend/src/lib/labels.ts", "frontend/src/lib/slugify.ts", "frontend/src/lib/theme.ts",
    "frontend/src/pages/NotFoundPage.tsx", "frontend/src/pages/SettingsPage.tsx",
    "frontend/src/pages/editing/RendersPage.tsx",
    "frontend/src/types/doctor.ts", "frontend/src/types/index.ts", "frontend/src/types/preview.ts",
    "frontend/src/types/preview.test.ts", "frontend/src/types/project.ts",
    "frontend/src/types/settings.ts", "frontend/src/types/storyboard.ts",
    "frontend/src/types/system.ts", "frontend/src/types/timeline.ts",
    "frontend/src/types/timing.ts", "frontend/src/types/update.ts",
}

TESTS_KEEP = {
    "tests/conftest.py", "tests/test_caption_presets.py", "tests/test_doctor.py",
    "tests/test_engine_limits.py", "tests/test_files_api.py", "tests/test_hardening_api.py",
    "tests/test_motion.py", "tests/test_motion_ease.py", "tests/test_music_stem.py",
    "tests/test_parallax.py", "tests/test_policy.py", "tests/test_preview_api.py",
    "tests/test_release_tools.py", "tests/test_render_assembly.py",
    "tests/test_render_captions.py", "tests/test_render_ffmpeg.py",
    "tests/test_render_fixtures.py", "tests/test_render_popups.py", "tests/test_render_stage.py",
    "tests/test_render_timeline.py", "tests/test_settings_api.py",
    "tests/test_settings_depth_model.py", "tests/test_setup_app.py",
    "tests/test_transition_catalogue.py", "tests/test_transitions_easing.py",
    "tests/test_transitions_library.py", "tests/test_transitions_perf.py",
    "tests/test_updates.py", "tests/test_voice_audio.py", "tests/test_voice_segments_render.py",
}
TESTS_KEEP_PREFIXES = ("tests/preview_parity/",)

OTHER_KEEP = {
    ".gitattributes",
    "installer/setup_app.py",
    "tools/build_runtime.py", "tools/make_icon.py", "tools/make_setup_exe.py",
    "tools/publish_release.py", "tools/withdraw_release.py",
    "tools/make_transition_previews.py", "tools/render_smoke.py",
    "tools/make_preview_fixtures.py", "tools/preview_engine_fixtures.py",
    "config/captions.yaml", "config/render.yaml", "config/transitions.yaml", "config/voice.yaml",
}
OTHER_KEEP_PREFIXES = ("assets/fonts/",)


def git_show(path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", SRC, "show", f"{COMMIT}:{path}"], capture_output=True, check=True
    )
    return result.stdout


def tree() -> list[str]:
    out = subprocess.run(
        ["git", "-C", SRC, "ls-tree", "-r", "--name-only", COMMIT],
        capture_output=True, check=True, text=True, encoding="utf-8",
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def destination(path: str) -> str | None:
    """The path inside this repository for a source path, or None when it is dropped."""
    if path.startswith(BACKEND):
        rest = path[len(BACKEND):]
        if rest in BACKEND_MOVES:
            moved = BACKEND_MOVES[rest]
            return None if moved is None else f"backend/sigma_editor/{moved}"
        if rest in BACKEND_KEEP_FILES or rest.startswith(BACKEND_KEEP_PREFIXES):
            return f"backend/sigma_editor/{rest}"
        return None
    if path in FRONTEND_KEEP_FILES or path.startswith(FRONTEND_KEEP_PREFIXES):
        return path
    if path in TESTS_KEEP or path.startswith(TESTS_KEEP_PREFIXES):
        return path
    if path in OTHER_KEEP or path.startswith(OTHER_KEEP_PREFIXES):
        return path
    return None


def rename_text(text: str) -> str:
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    for pattern, new in WORD_REPLACEMENTS:
        text = pattern.sub(new, text)
    return text


def is_text(path: str) -> bool:
    name = Path(path).name
    return Path(path).suffix.lower() in TEXT_SUFFIXES or name in (".gitignore", ".gitattributes")


def main(argv: list[str]) -> int:
    list_only = "--list" in argv
    plan = [(path, target) for path in tree() if (target := destination(path)) is not None]
    copied: list[str] = []
    kept: list[str] = []
    for source, target in plan:
        if list_only:
            print(f"{source} -> {target}")
            continue
        out = DST / target
        if out.exists():
            kept.append(target)
            continue
        data = git_show(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        if is_text(source):
            out.write_text(rename_text(data.decode("utf-8")), encoding="utf-8", newline="\n")
        else:
            out.write_bytes(data)
        copied.append(target)
    if list_only:
        print(f"{len(plan)} files would be copied")
        return 0
    for target in kept:
        print(f"kept (already here): {target}")
    print(f"copied {len(copied)} files from CashCow Studio {COMMIT}; {len(kept)} kept as written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
