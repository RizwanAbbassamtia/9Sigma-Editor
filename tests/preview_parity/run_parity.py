"""Runs the preview parity or performance proof outside pytest (dev only).

    .venv/Scripts/python.exe tests/preview_parity/run_parity.py projects [KEY ...]
    .venv/Scripts/python.exe tests/preview_parity/run_parity.py rerender [KEY ...]
    .venv/Scripts/python.exe tests/preview_parity/run_parity.py parity [KEY ...]
    .venv/Scripts/python.exe tests/preview_parity/run_parity.py perf [full|auto]

Needs Playwright for Python on ``PYTHONPATH`` and a Chromium (``PLAYWRIGHT_BROWSERS_PATH`` or
``SGE_PARITY_CHROMIUM``), FFmpeg and Node.js. Writes under ``SGE_PARITY_ROOT`` (default
``F:/cfs_tmp/m5/parity``): ``report.json``, ``perf.json`` and the worst frame pairs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parity_harness as ph  # noqa: E402
import parity_perf  # noqa: E402


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "parity"
    keys = argv[1:] or None
    if command in ("projects", "rerender"):
        manifest = ph.prepare_projects(ph.ParityEnv(), keys, rerender=command == "rerender")
        print(json.dumps(manifest, indent=1))
        return 0
    if command == "parity":
        report = ph.parity(keys)
        print(json.dumps({k: v for k, v in report.items() if k != "frames"}, indent=1)[:4000])
        return 0 if report["passed"] else 1
    if command == "perf":
        result = parity_perf.performance(quality=keys[0] if keys else "full")
        print(json.dumps(result, indent=1))
        return 0 if result["passed"] else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
