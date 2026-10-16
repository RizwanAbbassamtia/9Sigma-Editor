# 9Sigma Editor

A standalone Windows desktop editor for narrated picture-and-clip videos: live preview,
cinematic transitions, captions, music, and high-quality renders. No sign-in to any online
service; everything stays on the computer. Plan: docs/PLAN.md. Current contract:
docs/CONTRACT-v0.2.md (on top of docs/CONTRACT-v0.1.md). Installing and using it: docs/INSTALL.md.

## Working on the code

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check backend tests tools installer
cd frontend && npm install && npm run build && cd ..
.venv\Scripts\python.exe -m sigma_editor.cli serve      (http://127.0.0.1:8766)
.venv\Scripts\python.exe -m sigma_editor.cli open       (the app in its own window)
```

Layout: `backend/sigma_editor/` (FastAPI API, the render engine, the CLI `sge`), `frontend/`
(Vite + React + TypeScript, built into `frontend/dist` and served by the backend), `tests/`,
`installer/` and `tools/` (the Setup EXE, the runtime, releases), `config/` (render, caption and
transition settings, the shipped accounts), `assets/` (fonts, icons).

The editing engine was copied once from CashCow Studio (commit `2a10946`) with
`tools/copy_from_cashcow.py`; since then this repository develops on its own.
