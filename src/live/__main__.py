"""Entry point for ``python -m src.live``."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if __name__ == "__main__":
    main = import_module("live.cli").main
    raise SystemExit(main())
