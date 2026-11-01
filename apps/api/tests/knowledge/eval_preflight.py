"""Entrypoint of ``make eval-live-preflight`` (docs/06 §13.7; ADR-0033).

Lives next to ``eval_bridge.py`` (the ``make eval-live`` entrypoint) because it reads the process
environment, which inside the app package only ``app.core.config`` may do. The checks themselves
are :mod:`app.knowledge.live_preflight` (offline: no network, no model call).
"""

from __future__ import annotations

import os
import sys

from app.knowledge import live_preflight

if __name__ == "__main__":
    sys.exit(live_preflight.main(sys.argv[1:], env=os.environ))
