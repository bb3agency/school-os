"""Dedicated-host restore drill script (SEC-024, NFR-AVL-005; docs/10 §15.3; docs/11 R11).

Regressions found by the local restore drill of 2026-11-01 (docs/security/drills/):
- the drill's table/row summary skipped the fin and msg schemas (35 tables, incl. the fee ledger);
- ``python -m app.audit.verify_all`` verified the platform chain of the LIVE database, because
  only ``SOS_DATABASE_URL`` pointed at the restored copy.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
RESTORE = (REPO_ROOT / "deploy" / "dedicated" / "scripts" / "restore.sh").read_text("utf-8")
BOOTSTRAP = (REPO_ROOT / "infra" / "db" / "bootstrap.sql").read_text("utf-8")


def _bootstrap_schemas() -> set[str]:
    return set(re.findall(r"CREATE SCHEMA IF NOT EXISTS (\w+)", BOOTSTRAP))


def test_SEC_024_restore_summary_covers_every_application_schema() -> None:
    schemas = _bootstrap_schemas()
    assert {"core", "sis", "fin", "msg", "platform"} <= schemas
    lists = re.findall(r"(?:nspname|schemaname) IN \(([^)]*)\)", RESTORE)
    assert len(lists) == 2
    for found in lists:
        assert set(re.findall(r"'(\w+)'", found)) == schemas


def test_SEC_024_restore_drill_verifies_both_chains_on_the_copy() -> None:
    block = RESTORE[RESTORE.index("sos_compose run --rm --no-deps") :]
    block = block[: block.index("python -m app.audit.verify_all")]
    assert '-e "SOS_DATABASE_URL=postgresql+psycopg://sos_app:' in block
    assert '-e "SOS_PLATFORM_DATABASE_URL=postgresql+psycopg://sos_platform:' in block
    assert block.count('@db:5432/$db"') == 2
