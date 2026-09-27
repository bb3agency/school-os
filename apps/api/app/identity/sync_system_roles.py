"""Bring every school's SYSTEM roles in line with ``roles.yaml`` (ADR-0022; FR-IAM-011, SEC-003).

System roles are copied into a school at provisioning. When a release adds or changes a grant in
``app/authz/roles.yaml``, existing schools get it only through this operator command (a
migration cannot: role rows are tenant rows under FORCE RLS). Run it after the release's
migrations::

    python -m app.identity.sync_system_roles                    # dry run, every eligible school
    python -m app.identity.sync_system_roles --apply            # write (audited per school)
    python -m app.identity.sync_system_roles --tenant <id> [--apply] [--prune]

Behaviour (ADR-0022):

- Schools come from the allowlisted definer function ``core.list_tenant_ids`` (statuses
  ``provisioning``, ``active``, ``suspended``; ``offboarding`` and ``deleted`` are skipped). Each
  school is handled in its **own** ``tenant_session`` as ``sos_app``: RLS applies, no new
  definer function, one transaction per school; a failing school never stops the others.
- Dry run by default: the per-school transaction is read-only. ``--apply`` writes: missing
  system roles and grants are added and display names updated; grants roles.yaml no longer lists
  are removed only with ``--prune``. Custom roles and ``platform_support`` are never changed.
- Every change is audited in the same transaction (actor ``system``, keys and counts only).
- Output: one line per school plus one line per change, IDs and keys only (no names).
- Refuses unless connected as ``sos_app`` (never a superuser or a BYPASSRLS role), and until the
  migrations have put every roles.yaml permission into ``core.permissions``. On a dedicated host
  (``SOS_DEPLOYMENT_MODE=dedicated``) it handles only ``SOS_DEDICATED_TENANT_ID``.

Exit codes: 0 in line or applied · 1 refused (nothing done) · 2 invalid arguments ·
3 dry run found changes to make · 4 at least one school failed or has a role-key conflict.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from typing import Final, TextIO

from sqlalchemy import Engine

from app.core.config import DeploymentMode, Settings, get_settings
from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.identity import service as identity
from app.tenancy import service as tenancy

EXIT_OK: Final = 0
EXIT_REFUSED: Final = 1
EXIT_INVALID: Final = 2
EXIT_PENDING: Final = 3
EXIT_FAILED: Final = 4

APP_DB_ROLE: Final = "sos_app"
ELIGIBLE_STATUSES: Final = ("provisioning", "active", "suspended")

log = get_logger(__name__)


class Refused(Exception):
    """The command must not run here (reason code only)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.identity.sync_system_roles",
        description="Bring every school's system roles in line with roles.yaml (ADR-0022).",
    )
    p.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run, nothing written)"
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="also remove system-role grants roles.yaml no longer lists (can remove access)",
    )
    p.add_argument("--tenant", type=uuid.UUID, help="only this school (tenant ID)")
    return p


def eligible_tenant_ids(*, engine: Engine | None = None) -> list[uuid.UUID]:
    """Schools the sync may touch, via the allowlisted ``core.list_tenant_ids``."""
    with context_free_session(engine=engine) as session:
        return sorted(tenancy.list_tenant_ids(session, ELIGIBLE_STATUSES), key=str)


def preflight(*, engine: Engine | None = None) -> None:
    """Refuse unless RLS applies to this connection and the catalog is migrated."""
    role = identity.database_role(engine=engine)
    if role.superuser or role.bypass_rls:
        raise Refused("db_role_bypasses_rls")
    if role.name != APP_DB_ROLE:
        raise Refused("wrong_db_role")
    if identity.missing_catalog_permissions(engine=engine):
        raise Refused("catalog_not_migrated")


def targets(
    settings: Settings, only: uuid.UUID | None, *, engine: Engine | None = None
) -> list[uuid.UUID]:
    """The schools this run handles (refuses a school that is not eligible)."""
    if settings.deployment_mode is DeploymentMode.DEDICATED:
        try:
            host_tenant = uuid.UUID(settings.dedicated_tenant_id or "")
        except ValueError:
            raise Refused("dedicated_tenant_id_missing") from None
        if only is not None and only != host_tenant:
            raise Refused("tenant_mismatch")
        only = host_tenant
    eligible = eligible_tenant_ids(engine=engine)
    if only is None:
        return eligible
    if only not in eligible:
        raise Refused("tenant_not_eligible")
    return [only]


def _result(plan: identity.SystemRoleSyncPlan) -> str:
    if plan.applied:
        return "applied"
    return "pending" if plan.pending else "in_line"


def _write_plan(out: TextIO, plan: identity.SystemRoleSyncPlan) -> None:
    out.write(
        f"tenant={plan.tenant_id} result={_result(plan)}"
        f" roles_created={len(plan.create_roles)} grants_added={len(plan.add_grants)}"
        f" grants_removed={len(plan.remove_grants)} roles_updated={len(plan.update_names)}"
        f" extra_grants_kept={len(plan.extra_grants)} conflicts={len(plan.conflicts)}"
        f" unknown_system_roles={len(plan.unknown_system_roles)}\n"
    )
    for key, perms in plan.create_roles:
        out.write(f"  + role {key} ({len(perms)} grants)\n")
    for key, perm in plan.add_grants:
        out.write(f"  + {key} {perm}\n")
    for key, perm in plan.remove_grants:
        out.write(f"  - {key} {perm}\n")
    for key, perm in plan.extra_grants:
        out.write(f"  = {key} {perm} (not in roles.yaml; kept, use --prune to remove)\n")
    for key in plan.update_names:
        out.write(f"  ~ {key} display names\n")
    for key in plan.conflicts:
        out.write(f"  ! {key} is a custom role in this school; not changed\n")
    for key in plan.unknown_system_roles:
        out.write(f"  ? {key} system role not in roles.yaml; not changed\n")


def run(
    tenant_ids: Sequence[uuid.UUID],
    *,
    apply: bool,
    prune: bool,
    out: TextIO,
    engine: Engine | None = None,
) -> int:
    """Sync each school in its own transaction; return the exit code."""
    counts = {"in_line": 0, "pending": 0, "applied": 0, "failed": 0, "conflict": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id, engine=engine) as session:
                plan = identity.sync_system_roles(session, tenant_id, apply=apply, prune=prune)
        except Exception as exc:
            counts["failed"] += 1
            log.exception(
                "identity.system_role_sync.failed",
                tenant_id=str(tenant_id),
                error=type(exc).__name__,
            )
            out.write(f"tenant={tenant_id} result=failed error={type(exc).__name__}\n")
            continue
        counts[_result(plan)] += 1
        if plan.conflicts:
            counts["conflict"] += 1
        _write_plan(out, plan)
        log.info(
            "identity.system_role_sync.school",
            tenant_id=str(tenant_id),
            result=_result(plan),
            roles_created=len(plan.create_roles),
            grants_added=len(plan.add_grants),
            grants_removed=len(plan.remove_grants),
            roles_updated=len(plan.update_names),
            conflicts=len(plan.conflicts),
        )
    out.write(
        f"summary mode={'apply' if apply else 'dry_run'} prune={str(prune).lower()}"
        f" schools={len(tenant_ids)} in_line={counts['in_line']} pending={counts['pending']}"
        f" applied={counts['applied']} conflict={counts['conflict']} failed={counts['failed']}\n"
    )
    log.info("identity.system_role_sync.done", apply=apply, prune=prune, **counts)
    if counts["failed"] or counts["conflict"]:
        return EXIT_FAILED
    if counts["pending"]:
        return EXIT_PENDING
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed the usage error
        return EXIT_OK if exc.code == 0 else EXIT_INVALID
    settings = settings or get_settings()
    try:
        preflight(engine=engine)
        tenant_ids = targets(settings, args.tenant, engine=engine)
    except Refused as exc:
        err.write(f"refused: {exc.code}\n")
        log.warning("identity.system_role_sync.refused", reason=exc.code)
        return EXIT_REFUSED
    return run(tenant_ids, apply=args.apply, prune=args.prune, out=out, engine=engine)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
