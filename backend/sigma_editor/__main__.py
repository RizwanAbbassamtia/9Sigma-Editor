"""``python -m sigma_editor`` opens the app (same as ``sge open``).

Extra arguments are passed to the ``sge`` command line, so
``python -m sigma_editor doctor`` works too.
"""

from __future__ import annotations

import sys

from .cli import app

if __name__ == "__main__":
    app(sys.argv[1:] or ["open"])
