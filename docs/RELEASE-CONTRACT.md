# 9Sigma Editor release contract

How a version of 9Sigma Editor is built, installed and updated. The tools under `tools/` and
`installer/` and the update check in `backend/sigma_editor/updates.py` follow this document;
`docs/INSTALL.md` says the same in the words of the team.

## 1. Versions

The version is the `version` in `pyproject.toml` (plain `MAJOR.MINOR.PATCH`). The running app
reads it from the package; the built runtime writes it to `VERSION` and `install-info.json`; a
release is the git tag `v<version>` and the GitHub release of that tag. Versions compare as three
numbers; anything that is not a version (a tag such as `test`) is never offered as an update.

## 2. The runtime folder (`tools/build_runtime.py`)

`dist/9SigmaEditor/` is the whole app with its own Python:

```
VERSION, install-info.json     the version, when and how it was built, the parallax add-on flag
9SigmaEditor.cmd               opens the app window (sge open)
9SigmaEditor-Server.cmd        runs only the server in a console window (support, testing)
runtime\                       python.org's embeddable Python 3.12, every dependency, the
                               Visual C++ runtime DLLs, sitecustomize.py (puts runtime\ffmpeg\bin
                               on PATH), and after the install runtime\ffmpeg\
app\                           backend\ (the package as source), frontend\dist, config\, assets\
```

Dependencies are pinned to the versions of the virtual environment that ran the tests. FFmpeg is
not bundled: the installer downloads it (section 3). The 2.5D parallax add-on (onnxruntime) is
included unless the build runs with `--no-parallax`.

## 3. The Setup EXE (`tools/make_setup_exe.py`, `installer/setup_app.py`)

`dist/Install-9SigmaEditor-<version>.exe` is `installer/setup_app.py` frozen with PyInstaller
(one file, a small tkinter window), with `build/9SigmaEditor-<version>-payload.zip` (the runtime
folder, zipped) appended after it and a 16-byte trailer: the payload's offset (8 bytes, little
endian) and the magic `SIGMA9ED`. The window opens at once; the payload streams from the EXE's
own file with a progress bar.

Steps, in order: **check** this computer (Windows 10 or later, disk space, no other copy
running), **extract** the payload into `<target>.new`, **install** (move it into place; an
upgrade keeps `runtime\ffmpeg` and anything else already in the folder, and rolls back when
placing fails), **ffmpeg** (download `ffmpeg-release-essentials.zip` from gyan.dev into
`runtime\ffmpeg`, checked against the published SHA-256; skipped when FFmpeg is already there or
on PATH; `--skip-ffmpeg` for offline tests), **verify** (a self-test imports the app and runs its
health checks), **shortcuts** (desktop and Start menu `9Sigma Editor.lnk`, an Add/Remove programs
entry, `uninstall.exe`). The target is `%LOCALAPPDATA%\Programs\9SigmaEditor` unless
`SGE_INSTALL_DIR` says otherwise; no administrator rights are needed. The log is `install.log`
in the target folder. Unattended runs: `--auto` (no questions), `--open` (start the app at the
end), `--wait-pid <pid>` (wait for the running app to close first), `--target <folder>`. The
installer never touches the person's edits or settings.

## 4. Publishing (`tools/publish_release.py`, `tools/withdraw_release.py`)

`publish_release.py` builds nothing: it takes `dist/Install-9SigmaEditor-<version>.exe`, makes
sure the git tag `v<version>` exists on the pushed commit, creates the GitHub release of
`RizwanAbbassamtia/9Sigma-Editor` with the release notes, attaches the installer (GitHub records
its size and `sha256:` digest, which the update check reads) and prints the manual steps when the
token is missing or the upload fails. The token comes from the environment, never from the
repository. `withdraw_release.py` turns a release back into a draft, so "latest release" is the
previous one again and no app downloads the withdrawn version.

## 5. The in-app update (`backend/sigma_editor/updates.py`, `GET /api/system/update`)

The server checks GitHub in a background thread at most every 6 hours (30 minutes after a
failure, sooner on **Check for updates**), the API first and the release page as a fallback, and
never makes a page wait. A newer version shows as a notice at the top of the app with
**Update now** and **Download**. **Update now** downloads the installer into the app data folder,
keeps it only when it is exactly the file the release describes (size, digest and the version
embedded in the installer), refuses while a render is running, then starts the installer with
`--auto --open --wait-pid <this process> --target <this install folder>` and closes the app;
the installer reopens it when done. A copy that was not installed by the Setup EXE (running from
the source code) sees the notice but cannot install (`install_blocker` says why).
