"""Synthetic data generator entrypoint (`make seed-synthetic`). NEVER real data.

Task 13 fills this in; until then it refuses to run anywhere but local/ci.
"""

from __future__ import annotations

import sys

from app.core.config import get_settings


def main() -> int:
    settings = get_settings()
    if settings.is_production_like:
        sys.stderr.write("seed-synthetic is disabled outside local/ci\n")
        return 2
    sys.stdout.write("seed-synthetic: generator not implemented yet (roadmap Task 13)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
