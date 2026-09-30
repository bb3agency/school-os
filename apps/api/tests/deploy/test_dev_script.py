"""`make seed-synthetic` runs with the same environment as `make dev-host` (invariant 11).

Settings read only ``SOS_*`` environment variables (never ``.env`` itself), so the seeder run bare
from the Makefile had no database URL or local-dev master key. It now goes through
``scripts/dev.py --seed-only``, which builds the host environment from ``.env``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _dev() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sos_dev_script", ROOT / "scripts" / "dev.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_INV_011_seed_synthetic_target_uses_the_dev_host_environment() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^seed-synthetic:.*\n\t(.+)$", makefile, re.M)
    assert recipe is not None
    assert "scripts/dev.py --seed-only" in recipe.group(1)
    assert "--profile $(PROFILE)" in recipe.group(1)


def test_INV_011_seed_only_passes_the_seeder_arguments_through() -> None:
    dev = _dev()
    argv = ["dev.py", "--seed-only", "--", "--profile", "small", "--tenants", "3"]
    assert dev.seed_args(argv) == ["--profile", "small", "--tenants", "3"]
    assert dev.seed_args(["dev.py", "--seed-only"]) == []


def test_INV_011_seed_only_does_not_need_free_app_ports() -> None:
    # `make seed-synthetic` is also run while `make dev-host` is up (ports busy).
    dev = _dev()
    assert "check_ports" in dev.preflight.__code__.co_varnames


def test_ADR_0029_dev_host_reapplies_bootstrap_sql_before_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A Postgres volume created before a new role (sos_purger, ADR-0029) was added to
    # infra/db/bootstrap.sql never re-runs the init script, so 0032 failed on `make dev-host`.
    # bootstrap.sql is idempotent and additive; dev-host re-applies it before alembic.
    dev = _dev()
    labels: list[str] = []
    commands: list[list[str]] = []

    def fake_step(label: str, command: list[str], env: dict[str, str], cwd: Path = ROOT) -> bool:
        labels.append(label)
        commands.append(command)
        return True

    monkeypatch.setattr(dev, "step", fake_step)
    assert dev.prepare({}, seed=False)
    roles = next(i for i, label in enumerate(labels) if "bootstrap.sql" in label)
    migrations = next(i for i, label in enumerate(labels) if "alembic" in label)
    assert roles < migrations
    assert commands[roles][-2:] == ["bash", "/docker-entrypoint-initdb.d/10-bootstrap.sh"]
    assert "exec" in commands[roles]
    assert "-T" in commands[roles]


def test_ADR_0029_make_dev_reapplies_bootstrap_sql_before_the_stack() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^dev: .*\n((?:\t.+\n)+)", makefile, re.M)
    assert recipe is not None
    lines = recipe.group(1).splitlines()
    bootstrap = next(i for i, line in enumerate(lines) if "db-bootstrap" in line)
    stack = next(i for i, line in enumerate(lines) if "--build" in line)
    assert bootstrap < stack
