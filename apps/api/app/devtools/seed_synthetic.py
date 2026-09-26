"""``make seed-synthetic``: deterministic synthetic schools and staff (docs/12 §3). NEVER real data.

Usage::

    python -m app.devtools.seed_synthetic [--tenants N] [--seed S] [--dataset-version v1]
                                          [--code-prefix synth]

Refuses to run unless ``SOS_ENV`` is ``local`` or ``ci`` (exit code 2). The check runs before
anything else is imported, and settings that fail validation (e.g. staging/prod guards) are a
refusal too, so the tool fails closed.

The dataset is described by :mod:`app.devtools.plan` (pure, versioned, deterministic per seed)
and applied by :mod:`app.devtools.seeder` through the tenancy and identity services. Re-running
is idempotent. stdout gets one JSON summary with IDs, codes and counts only; progress goes to
the structured log (``app.core.logging``). Exit codes: 0 ok, 1 seeding failed, 2 refused or bad
arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, TextIO

from pydantic import ValidationError

from app.core.config import Environment, Settings, get_settings

if TYPE_CHECKING:
    from app.core.crypto import KeyWrapper
    from app.devtools.seeder import OwnerBootstrap

ALLOWED_ENVS: Final = frozenset({Environment.LOCAL, Environment.CI})
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2
REFUSED_MESSAGE: Final = "seed-synthetic is disabled outside local/ci (SOS_ENV)\n"


def _parser() -> argparse.ArgumentParser:
    from app.devtools import plan  # noqa: PLC0415 - imported only after the environment check

    p = argparse.ArgumentParser(
        prog="python -m app.devtools.seed_synthetic",
        description="Create deterministic SYNTHETIC schools and staff (local/ci only).",
    )
    p.add_argument("--tenants", type=int, default=plan.DEFAULT_TENANTS, help="number of schools")
    p.add_argument("--seed", type=int, default=plan.DEFAULT_SEED, help="random seed")
    p.add_argument(
        "--dataset-version",
        default=plan.DEFAULT_DATASET_VERSION,
        help=f"one of {', '.join(plan.DATASET_VERSIONS)}",
    )
    p.add_argument(
        "--code-prefix",
        default=plan.DEFAULT_CODE_PREFIX,
        help="school codes are <prefix>-a, <prefix>-b, ...",
    )
    p.add_argument(
        "--admin-database-url",
        default=None,
        help=(
            "legacy: create each school's first owner with the database admin connection "
            "instead of the control-plane invite + acceptance path (default)"
        ),
    )
    return p


def _allowed(settings: Settings | None) -> bool:
    if settings is None:
        try:
            settings = get_settings()
        except ValidationError:
            return False  # e.g. staging/prod guards rejected the configuration: fail closed
    return settings.env in ALLOWED_ENVS


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    wrapper: KeyWrapper | None = None,
    owner_bootstrap: OwnerBootstrap | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if not _allowed(settings):
        err.write(REFUSED_MESSAGE)
        return EXIT_REFUSED
    settings = settings or get_settings()

    # Imported only now: these modules read settings and configure logging at import time.
    from sqlalchemy import Engine, create_engine  # noqa: PLC0415

    from app.core.crypto import get_key_wrapper  # noqa: PLC0415
    from app.core.logging import get_logger  # noqa: PLC0415
    from app.devtools import seeder  # noqa: PLC0415
    from app.devtools.plan import PlanError, build_plan  # noqa: PLC0415

    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed the usage error
        return EXIT_REFUSED if exc.code else EXIT_OK
    try:
        plan = build_plan(
            seed=args.seed,
            tenants=args.tenants,
            dataset_version=args.dataset_version,
            code_prefix=args.code_prefix,
        )
    except PlanError as exc:
        err.write(f"seed-synthetic: {exc}\n")
        return EXIT_REFUSED
    log = get_logger("app.devtools.seed_synthetic")
    admin_engine: Engine | None = None
    if owner_bootstrap is None:
        if args.admin_database_url:
            admin_engine = create_engine(args.admin_database_url, pool_size=1, max_overflow=0)
            owner_bootstrap = seeder.AdminOwnerBootstrap(admin_engine)
        else:
            owner_bootstrap = seeder.PlatformOwnerBootstrap()
    try:
        summary = seeder.seed(
            plan, wrapper=wrapper or get_key_wrapper(settings), owner_bootstrap=owner_bootstrap
        )
    except seeder.SeedError as exc:
        log.error("devtools.seed_synthetic.failed", error_type=type(exc).__name__)
        err.write(f"seed-synthetic: {exc}\n")
        return EXIT_FAILED
    finally:
        if admin_engine is not None:
            admin_engine.dispose()
    out.write(json.dumps(summary.as_json(), ensure_ascii=False, sort_keys=True) + "\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
