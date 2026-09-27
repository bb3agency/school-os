"""``make seed-synthetic``: deterministic synthetic schools, staff, students and documents
(docs/12 §3). NEVER real data.

Usage::

    python -m app.devtools.seed_synthetic [--tenants N] [--seed S] [--dataset-version v1]
                                          [--code-prefix synth] [--profile none|small|full]
                                          [--students N] [--no-documents] [--out-dir DIR]

``--profile``: ``none`` seeds schools and staff only (the default of the bare CLI, so
``scripts/dev.py`` stays fast; ``make seed-synthetic`` passes ``full``); ``small`` adds 400
students per school, ``full`` 2,000 (``--students`` overrides the number), each with guardians,
enrolments and per-source values with mismatches injected at documented rates
(:mod:`app.devtools.students`), plus rendered register pages and circulars stored through the
documents service (``--no-documents`` skips them). For each school a manifest of the injected
mismatches and the expected findings (admission numbers and rule IDs only) is written to
``<out-dir>/manifest-<code>.json`` (default ``.data/synthetic``, git-ignored).

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
    from app.devtools.student_seeder import Uploader

ALLOWED_ENVS: Final = frozenset({Environment.LOCAL, Environment.CI})
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2
REFUSED_MESSAGE: Final = "seed-synthetic is disabled outside local/ci (SOS_ENV)\n"
DEFAULT_OUT_DIR: Final = ".data/synthetic"
CLI_DEFAULT_PROFILE: Final = "none"  # make seed-synthetic passes --profile full


def _parser() -> argparse.ArgumentParser:
    from app.devtools import plan, students  # noqa: PLC0415 - only after the environment check

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
        "--profile",
        choices=sorted(students.PROFILES),
        default=CLI_DEFAULT_PROFILE,
        help="students per school: none (staff only), small (400) or full (2,000)",
    )
    p.add_argument(
        "--students",
        type=int,
        default=None,
        help=f"students per school (overrides the profile size; 0..{students.MAX_STUDENTS})",
    )
    p.add_argument(
        "--no-documents",
        action="store_true",
        help="do not store register-page images and circulars",
    )
    p.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help="where the per-school mismatch manifests are written",
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
    uploader: Uploader | None = None,
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
    from app.devtools import manifest, seeder, student_seeder  # noqa: PLC0415
    from app.devtools.plan import PlanError, build_plan  # noqa: PLC0415
    from app.devtools.students import MAX_STUDENTS, build_students  # noqa: PLC0415

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
    if args.students is not None and not 0 <= args.students <= MAX_STUDENTS:
        err.write(f"seed-synthetic: --students must be between 0 and {MAX_STUDENTS}\n")
        return EXIT_REFUSED
    options: seeder.StudentOptions | None = None
    if args.profile != "none" or args.students:
        profile, count = args.profile, args.students
        version, seed = plan.dataset_version, plan.seed
        options = seeder.StudentOptions(
            build=lambda t: build_students(
                t, dataset_version=version, seed=seed, profile=profile, count=count
            ),
            pages=lambda t, school: student_seeder.register_pages_for(
                t, school, dataset_version=version, seed=seed
            ),
            documents=not args.no_documents,
            uploader=uploader or student_seeder.http_uploader,
        )
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
            plan,
            wrapper=wrapper or get_key_wrapper(settings),
            owner_bootstrap=owner_bootstrap,
            students=options,
        )
    except seeder.SeedError as exc:
        log.error("devtools.seed_synthetic.failed", error_type=type(exc).__name__)
        err.write(f"seed-synthetic: {exc}\n")
        return EXIT_FAILED
    finally:
        if admin_engine is not None:
            admin_engine.dispose()
    result = summary.as_json()
    if options is not None:
        written = manifest.write_manifests(summary, plan, out_dir=args.out_dir, options=options)
        result["manifests"] = [str(p) for p in written]
    out.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
