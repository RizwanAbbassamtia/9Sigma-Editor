# 9Sigma Editor

A standalone Windows desktop editor for narrated picture-and-clip videos: CapCut-style timeline, live preview, cinematic transitions, captions, music, and high-quality renders. Plan: docs/PLAN.md. First contract: docs/CONTRACT-v0.1.md.

## Working on the code

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check backend tests tools
.venv\Scripts\python.exe -m sigma_editor.cli serve      (http://127.0.0.1:8766)
cd frontend && npm install && npm run build
```

The editing engine was copied once from CashCow Studio (commit `2a10946`) with
`tools/copy_from_cashcow.py`; since then this repository develops on its own.
