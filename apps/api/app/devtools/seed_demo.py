"""``make seed-demo`` / ``make demo-reset``: the demo school for sales demonstrations (FA-12;
FR-DEMO-001..015; docs/guides/demo-school.md). NEVER real data.

Usage::

    python -m app.devtools.seed_demo [--seed S] [--students N] [--no-documents] [--reset]
                                     [--out-dir DIR]

Seeds one synthetic school, "Sunrise Synthetic Demo School" (code ``demo-g<n>-a``), exactly as
``make seed-synthetic`` seeds a school (staff for every role, students with planted mismatches,
register pages and circulars, Class X fees and messaging), then applies the demo scenario of
:mod:`app.devtools.demo_seeder` (earlier-year dues, add-on fees, compliance certificates, the
school calendar, data-quality findings and UDISE+ follow-ups, a CBSE disclosure draft and the
weekly brief). Re-running is idempotent: it completes the live generation.

``--reset`` (FR-DEMO-004) deletes the live demo school through the offboarding purge (every row
and file of that school; the audit chain is kept as for any offboarded school) and seeds the
next generation ``demo-g<n+1>-a``, so a presenter starts every demo from the same state. Only
schools whose code is ``demo-g<n>-a`` AND whose ID is the generator's UUIDv5 for that code are
ever touched: a school created by hand is never deleted.

Refuses to run unless ``SOS_ENV`` is ``local`` or ``ci`` (exit code 2), with the same check as
``seed-synthetic`` (:func:`app.devtools.seed_synthetic.allowed`), before anything else is
imported; settings that fail validation are a refusal too. There is no demo environment: a demo
on a hosted server needs an ADR and an owner decision (FR-DEMO-015). stdout gets one JSON
summary with IDs, codes and counts only. Exit codes: 0 ok, 1 seeding failed, 2 refused or bad
arguments.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TextIO

from app.core.config import Settings, get_settings
from app.devtools.seed_synthetic import EXIT_FAILED, EXIT_OK, EXIT_REFUSED, allowed

if TYPE_CHECKING:
    from app.core.crypto import KeyWrapper
    from app.devtools.seeder import OwnerBootstrap
    from app.devtools.student_seeder import Uploader

REFUSED_MESSAGE: Final = "seed-demo is disabled outside local/ci (SOS_ENV)\n"
DEFAULT_OUT_DIR: Final = ".data/demo"
LIVE: Final = frozenset({"provisioning", "active"})
RESETTABLE: Final = frozenset({"active", "suspended", "offboarding"})


def _parser() -> argparse.ArgumentParser:
    from app.devtools import demo  # noqa: PLC0415 - only after the environment check

    p = argparse.ArgumentParser(
        prog="python -m app.devtools.seed_demo",
        description="Create (or reset) the SYNTHETIC demo school (local/ci only).",
    )
    p.add_argument("--seed", type=int, default=demo.DEMO_SEED, help="random seed")
    p.add_argument(
        "--students", type=int, default=demo.DEMO_STUDENTS, help="students in the demo school"
    )
    p.add_argument(
        "--no-documents", action="store_true", help="do not store register pages and circulars"
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="delete the live demo school (offboarding purge) and seed the next generation",
    )
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="where the manifest is written")
    return p


@dataclass(frozen=True, slots=True)
class DemoTenant:
    tenant_id: uuid.UUID
    code: str
    generation: int
    status: str


def demo_tenants() -> list[DemoTenant]:
    """Every demo school of this database, by generation (IDs, codes and statuses only)."""
    from app.core.db import context_free_session, tenant_session  # noqa: PLC0415
    from app.devtools import demo, plan  # noqa: PLC0415
    from app.tenancy import service as tenancy  # noqa: PLC0415

    with context_free_session() as session:
        ids = tenancy.list_tenant_ids(session, None)
    found: list[DemoTenant] = []
    for tenant_id in ids:
        with tenant_session(tenant_id) as session:
            tenant = tenancy.get_tenant(session)
        generation = demo.generation_of(tenant.code)
        if generation is None or tenant_id != plan.tenant_id_for(tenant.code):
            continue
        found.append(DemoTenant(tenant_id, tenant.code, generation, tenant.status))
    return sorted(found, key=lambda t: t.generation)


def reset(tenants: Sequence[DemoTenant]) -> list[dict[str, Any]]:
    """Offboard and purge every demo school that still holds data (FR-DEMO-004). Fails closed
    when anything is left after the purge."""
    # Every module must have registered its purge owner, as in the worker (the purge refuses
    # otherwise: 409 purge_owner_missing). The app entry point imports them all; it is loaded by
    # name so that devtools takes no static dependency on the leaf modules behind it.
    importlib.import_module("app.main")
    from app.core.db import platform_session  # noqa: PLC0415
    from app.tenancy import service as tenancy  # noqa: PLC0415

    done: list[dict[str, Any]] = []
    for t in tenants:
        if t.status not in RESETTABLE:
            continue
        if t.status != "offboarding":
            with platform_session() as pdb:
                tenancy.begin_offboarding(pdb, t.tenant_id)
        purged = tenancy.purge_tenant(t.tenant_id)
        left = tenancy.verify_tenant_purged(t.tenant_id)
        if left.rows or left.objects:
            raise RuntimeError(f"demo school {t.code}: data remains after the purge")
        done.append(
            {
                "tenant_id": str(t.tenant_id),
                "code": t.code,
                "rows_deleted": sum(purged.rows.values()),
                "objects_deleted": purged.objects_deleted,
            }
        )
    return done


def next_generation(tenants: Sequence[DemoTenant], *, after_reset: bool) -> int:
    """The live generation to complete, or the next one after a reset (or when none is live)."""
    if not tenants:
        return 1
    live = [t.generation for t in tenants if t.status in LIVE]
    if live and not after_reset:
        return max(live)
    return max(t.generation for t in tenants) + 1


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
    if not allowed(settings):
        err.write(REFUSED_MESSAGE)
        return EXIT_REFUSED
    settings = settings or get_settings()

    # Imported only now: these modules read settings and configure logging at import time.
    from app.core.crypto import get_key_wrapper  # noqa: PLC0415
    from app.core.errors import DomainError  # noqa: PLC0415
    from app.core.logging import get_logger  # noqa: PLC0415
    from app.devtools import (  # noqa: PLC0415
        demo,
        demo_seeder,
        finance_seeder,
        manifest,
        seeder,
        student_seeder,
    )
    from app.devtools.students import MAX_STUDENTS, build_students  # noqa: PLC0415

    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed the usage error
        return EXIT_REFUSED if exc.code else EXIT_OK
    if not 1 <= args.students <= MAX_STUDENTS:
        err.write(f"seed-demo: --students must be between 1 and {MAX_STUDENTS}\n")
        return EXIT_REFUSED
    log = get_logger("app.devtools.seed_demo")
    try:
        existing = demo_tenants()
        purged = reset(existing) if args.reset else []
        generation = next_generation(existing, after_reset=args.reset)
        plan = demo.build_demo_plan(seed=args.seed, generation=generation)
        school = plan.tenants[0]
        version, seed, count = plan.dataset_version, plan.seed, args.students
        options = seeder.StudentOptions(
            build=lambda t: build_students(
                t, dataset_version=version, seed=seed, profile="small", count=count
            ),
            pages=lambda t, s: student_seeder.register_pages_for(
                t, s, dataset_version=version, seed=seed
            ),
            documents=not args.no_documents,
            uploader=uploader or student_seeder.http_uploader,
            fees_messaging=True,
        )
        summary = seeder.seed(
            plan,
            wrapper=wrapper or get_key_wrapper(settings),
            owner_bootstrap=owner_bootstrap or seeder.PlatformOwnerBootstrap(),
            students=options,
        )
        scenario = demo_seeder.seed_scenario(school)
    except (
        seeder.SeedError,
        demo_seeder.DemoSeedError,
        demo.DemoPlanError,
        RuntimeError,
        DomainError,
    ) as exc:
        log.error("devtools.seed_demo.failed", error_type=type(exc).__name__)
        err.write(f"seed-demo: {exc}\n")
        return EXIT_FAILED
    tenant = summary.tenants[0]
    tenant.created.update(scenario)
    tenant.counts.update(finance_seeder.counts(school.tenant_id))
    tenant.counts.update(demo_seeder.counts(school.tenant_id))
    if scenario and tenant.outcome == "unchanged":
        tenant.outcome = "updated"
    result = summary.as_json()
    result["event"] = "seed_demo.summary"
    result["generation"] = generation
    result["reset"] = purged
    written = manifest.write_manifests(summary, plan, out_dir=args.out_dir, options=options)
    result["manifests"] = [str(p) for p in written]
    log.info("devtools.seed_demo.done", tenant_id=str(school.tenant_id), outcome=tenant.outcome)
    out.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return EXIT_OK


__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "DemoTenant",
    "demo_tenants",
    "main",
    "next_generation",
    "reset",
]

if __name__ == "__main__":
    raise SystemExit(main())
