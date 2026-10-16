# Installing and running 9Sigma Editor

## For the team: the Setup EXE

1. Download `Install-9SigmaEditor-<version>.exe` from the releases page of
   `RizwanAbbassamtia/9Sigma-Editor` on GitHub (or get it from the owner).
2. Double-click it. No administrator rights are needed. It copies the app into
   `%LOCALAPPDATA%\Programs\9SigmaEditor`, fetches FFmpeg (about 100 MB, once), makes a desktop
   shortcut and a Start menu entry, and runs a short self-test.
3. Open **9Sigma Editor** from the desktop. The app opens in its own window.
4. Sign in with the user name and password you were given (`user1` to `user10`). Change the
   password under **Settings > Your account** the first time.

The app keeps every edit in `Videos\9SigmaEditor\projects\<date>_<name>\` and every export in
`Videos\9SigmaEditor\exports\` (both can be changed under **Settings > Folders**). Nothing leaves
the computer: there is no sign-in to any online service and no upload.

When a newer version is published, a notice appears at the top of the app with **Update now**.

### Making a video

1. **My edits > New edit**: pick the pictures and clips (one per scene), the narration recording,
   the music, and either type the caption text or add an SRT/VTT file. Name it and press
   **Make the edit**.
2. The editor opens. Play the live preview, change a transition on the strip, add or replace
   scenes, switch popups and captions, choose the music and its volume, move sentence starts in
   **Timing**, fix the text in **Captions**.
3. **Render**: tick the sizes, pick the quality (Standard, High, Best) and, for YouTube, the
   upload preset, then **Render selected**. Progress shows on each size; **Stop** cancels.
4. **Export** puts the rendered files, a poster frame and `export.txt` (with checksums) into the
   exports folder; **Open export folder** shows them.

### If something is wrong

* **Settings > Health checks** says what is missing (FFmpeg, fonts, folders, disk space) and how
  to fix it.
* The app's log is `%LOCALAPPDATA%\9SigmaEditor\logs\app.log`. Send it with the description of
  the problem.
* A forgotten password: the owner runs `sge users set-password <username>` on the computer (see
  below) and hands over the new one.

## For the owner: running from the source code

Needs Python 3.12, Node (for the screens) and FFmpeg 9 on PATH.

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd frontend && npm install && npm run build && cd ..
.venv\Scripts\python.exe -m sigma_editor.cli open
```

`sge open` starts the server on `127.0.0.1:8766` and opens the app window; `sge serve` runs only
the server (then open `http://127.0.0.1:8766` in a browser); `sge doctor` runs the health checks
in the console.

Accounts: `config/users.json` ships the ten accounts (hashed passwords; the initial passwords are
in `config/local.user-passwords.txt`, which never goes into git). On the first start the file is
copied to `%LOCALAPPDATA%\9SigmaEditor\users.json`, where password changes are kept.
`sge users list`, `sge users set-password <username>`, `sge users add <username>` and
`sge users enable <username> --off` manage them; `tools/make_users.py` makes a fresh set.

Checks before a commit:

```
.venv\Scripts\python.exe -m ruff check backend tests tools installer
.venv\Scripts\python.exe -m pytest -q
cd frontend && npm run typecheck && npm test && npm run build
```

## Building the Setup EXE

```
.venv\Scripts\python.exe tools\build_runtime.py        # dist\9SigmaEditor with its own Python
.venv\Scripts\python.exe tools\make_setup_exe.py       # dist\Install-9SigmaEditor-<version>.exe
.venv\Scripts\python.exe tools\publish_release.py      # a GitHub release with the installer
```

`build_runtime.py` downloads the embeddable Python once into a cache folder and installs the
pinned dependencies into it; `--no-parallax` leaves the 2.5D parallax add-on out (smaller).
`make_setup_exe.py` freezes `installer/setup_app.py` with PyInstaller in its own environment and
appends the zipped app. The release tools need a GitHub token in the environment (never in the
repository).
