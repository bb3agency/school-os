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
