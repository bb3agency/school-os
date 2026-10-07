"""Monthly partitions for ``audit.events`` (docs/05 §12).

Partitions are DDL, so they are created by the object owner, never by the app role (which has
no CREATE privilege) and never through a SECURITY DEFINER function. The deploy pipeline runs,
with migrator credentials, right after ``alembic upgrade head``::

    python -m app.audit.partitions --months-ahead 12

Migration 0002 already creates 24+ months, so a missed run is not an outage; the daily
verification job logs ``audit.partitions.low_runway`` when fewer than ``RUNWAY_WARN_DAYS``
remain. There is deliberately no DEFAULT partition: an insert outside every partition fails
(fail closed) instead of silently landing in a catch-all table.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from datetime import date

from sqlalchemy import Connection, create_engine, pool, text

from app.core.config import get_settings

DEFAULT_MONTHS_AHEAD = 12
RUNWAY_WARN_DAYS = 90
_NAME_RE = re.compile(r"^events_y(\d{4})m(\d{2})$")


def ensure_partitions(
    connection: Connection, months_ahead: int = DEFAULT_MONTHS_AHEAD
) -> list[str]:
    """Create missing partitions for this month + ``months_ahead`` (connection = migrator).

    Runs ``SET ROLE sos_owner`` for the duration of the call so the new tables are owned by
    ``sos_owner`` exactly like migration-created ones. The caller commits.
    """
    connection.execute(text("SET LOCAL ROLE sos_owner"))
    created: Iterable[object] = connection.execute(
        text("SELECT audit.ensure_partitions(:n)"), {"n": months_ahead}
    ).scalars()
    return [str(name) for name in created]


def partition_upper_bound(names: list[str]) -> date | None:
    """First day after the newest partition, from ``events_yYYYYmMM`` names."""
    months = [date(int(m.group(1)), int(m.group(2)), 1) for n in names if (m := _NAME_RE.match(n))]
    if not months:
        return None
    last = max(months)
    return date(last.year + (last.month // 12), last.month % 12 + 1, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create audit.events monthly partitions.")
    parser.add_argument("--months-ahead", type=int, default=DEFAULT_MONTHS_AHEAD)
    args = parser.parse_args(argv)
    url = get_settings().checked_migrator_url()
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.begin() as conn:
            created = ensure_partitions(conn, args.months_ahead)
    finally:
        engine.dispose()
    sys.stdout.write(f"audit partitions created: {', '.join(created) or 'none'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
