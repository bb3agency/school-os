"""Migrations upgrade, downgrade and re-upgrade cleanly (CLAUDE.md §6.12, docs/12 §2).

Runs on a separate, freshly bootstrapped database so the shared test database is untouched.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    with engine.connect() as conn:
        rows: list[str] = list(
            conn.execute(
                text(
                    "SELECT schemaname || '.' || tablename FROM pg_tables "
                    "WHERE schemaname IN ('core','sis','kb','audit','ops','platform') "
                    "AND tablename <> 'alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        result = set(rows)
    engine.dispose()
    return result


def test_upgrade_downgrade_upgrade_roundtrip(
    test_database: object, make_alembic_config: Callable[[str], Config]
) -> None:
    url = test_database.create_fresh("schoolos_migrations")  # type: ignore[attr-defined]
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    at_head = _tables(url)
    command.downgrade(cfg, "base")
    assert _tables(url) == set(), "downgrade to base must remove every migrated table"
    command.upgrade(cfg, "head")
    assert _tables(url) == at_head


def test_every_step_is_reversible(
    test_database: object, make_alembic_config: Callable[[str], Config]
) -> None:
    url = test_database.create_fresh("schoolos_migrations_steps")  # type: ignore[attr-defined]
    cfg = make_alembic_config(url)
    revisions = [
        r.revision for r in reversed(list(ScriptDirectory.from_config(cfg).walk_revisions()))
    ]
    previous = "base"
    for rev in revisions:
        command.upgrade(cfg, rev)
        command.downgrade(cfg, previous)
        command.upgrade(cfg, rev)
        previous = rev


def test_revision_chain_is_linear(make_alembic_config: Callable[[str], Config]) -> None:
    script = ScriptDirectory.from_config(make_alembic_config("postgresql+psycopg://unused"))
    assert len(script.get_heads()) == 1, "migrations must form one linear chain"
