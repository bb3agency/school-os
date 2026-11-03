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


def _drill_urls() -> str:
    """The restore drill's URL exports through its verify_all container run."""
    start = RESTORE.index("export SOS_DATABASE_URL=")
    return RESTORE[start : RESTORE.index("python -m app.audit.verify_all")]


def test_SEC_024_restore_drill_verifies_both_chains_on_the_copy() -> None:
    block = _drill_urls()
    app_url = "postgresql+psycopg://sos_app:${SOS_APP_DB_PASSWORD}@db:5432/$db"
    platform_url = "postgresql+psycopg://sos_platform:${SOS_PLATFORM_DB_PASSWORD}@db:5432/$db"
    assert f'export SOS_DATABASE_URL="{app_url}"' in block
    assert f'export SOS_PLATFORM_DATABASE_URL="{platform_url}"' in block
    run = block[block.index("sos_compose run --rm --no-deps") :]
    assert re.search(r"-e SOS_DATABASE_URL\s", run)
    assert re.search(r"-e SOS_PLATFORM_DATABASE_URL\s", run)


def test_SEC_024_restore_drill_keeps_db_passwords_off_the_command_line() -> None:
    """The drill's container run takes the database URLs by name from its environment
    (``-e NAME``): ``-e NAME=value`` puts the role passwords on the docker command line, which any
    host process can read in /proc/*/cmdline while it runs (db-bootstrap uses getenv for the same
    reason; test_dedicated_hardening.py)."""
    block = _drill_urls()
    run = block[block.index("sos_compose run --rm --no-deps") :]
    assert "PASSWORD" not in run
    assert not re.search(r"-e \"?SOS_[A-Z_]*URL=", run)


BACKUP = (REPO_ROOT / "deploy" / "dedicated" / "scripts" / "backup.sh").read_text("utf-8")


def test_SEC_024_backup_failures_after_the_trap_record_the_failure() -> None:
    """Once ``trap on_error ERR`` is set, a failed check must go through the trap (backup.json
    status failed, BackupSuccess 0): ``die`` exits without it, so a full disk left no trace."""
    after_trap = BACKUP[BACKUP.index("trap on_error ERR") :]
    assert "die " not in after_trap
    assert re.search(r"avail_kb > db_kb / 2\)\) \|\| \{[^}]*\bfalse\b", after_trap)
