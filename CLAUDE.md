# 9Sigma Editor - working notes for Claude

## What this is
A standalone Windows desktop video editor for narrated picture-and-clip videos, copied once from
CashCow Studio's editing engine and the approved Edit room design, then developed on its own.
No sign-in, no automation pipeline. Plan: `docs/PLAN.md`. Current contract: `docs/CONTRACT-v0.1.md`.

## Layout (after the copy)
- `backend/sigma_editor/` Python 3.12 package (FastAPI API, render engine, CLI `sge`).
- `frontend/` Vite + React + TypeScript + Tailwind v4 app, built into `frontend/dist`, served by the backend.
- `tests/` pytest. `installer/` and `tools/` for the Setup EXE, runtime and releases.
- Per-user data never lives in the repo: app data in `%LOCALAPPDATA%\9SigmaEditor`, projects in
  `Videos\9SigmaEditor` (git-ignored).

## Commands (Windows, Git Bash)
- Python env: `F:/9SigmaEditor/.venv/Scripts/python.exe` (create with `python -m venv .venv`; install with
  `.venv/Scripts/python.exe -m pip install -e ".[dev]"`).
- Tests: `.venv/Scripts/python.exe -m pytest -q`
- Lint: `.venv/Scripts/python.exe -m ruff check backend tests tools`
- API server: `.venv/Scripts/python.exe -m sigma_editor.cli serve` (port 8766, 127.0.0.1 only)
- Front end: `cd frontend && npm install && npm run build` (also `npm run dev`, `npm run typecheck`).
  Node is at `C:\Program Files\nodejs` if not on PATH.
- FFmpeg 9 is installed on this machine and on PATH.

## Rules
- Bind servers to 127.0.0.1 only. No secrets in the repo.
- Follow the current contract exactly; change the contract first if something must differ, and say so.
- Pydantic v2 models are the source of truth; mirror them in `frontend/src/types/`.
- Windows paths may contain spaces; always quote. Keep temp files in short paths.
- Keep user-facing text plain English, no jargon; the users are video editors, not developers.
- This repo does not sync with CashCow Studio: never copy from it again without the owner's say-so.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
