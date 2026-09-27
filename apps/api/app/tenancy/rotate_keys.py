"""Rotate a school's data encryption key, re-encrypt its C3 values, retire old key versions
(SEC-012; docs/05 §9, 07 §8; runbook in docs/10 §9.1).

Operator command, one school per run::

    python -m app.tenancy.rotate_keys --tenant <id>                      # status + plan (dry run)
    python -m app.tenancy.rotate_keys --tenant <id> --apply              # 1. new key version
    python -m app.tenancy.rotate_keys --tenant <id> --reencrypt [--apply]  # 2. re-encrypt now
    python -m app.tenancy.rotate_keys --tenant <id> --retire [--apply]     # 3. retire old ones

1. ``--apply`` adds the next key version (fresh DEK, wrapped by the configured key wrapper: KMS
   with the tenant id as encryption context in every deployed environment). It is current for new
   writes at once in this process and within 15 minutes (key cache TTL) everywhere else. The same
   transaction queues ``keys.rotated``; a worker then runs ``maintenance.reencrypt_tenant`` on the
   maintenance queue. ``--new-hmac-key`` (incident response) also replaces the blind-index HMAC
   key; by default it is carried over so blind indexes keep matching.
2. ``--reencrypt --apply`` runs the same re-encryption in this process (resumes the job run;
   idempotent). Use it when no worker is available or to sweep stragglers written by processes
   that still had the previous version cached.
3. ``--retire --apply`` retires older versions that no stored ciphertext uses (census by
   ciphertext header), at the earliest 16 minutes after the rotation. Wrapped keys are never
   deleted and KMS material is never destroyed by this command.

Behaviour: the school is handled in its own ``tenant_session`` as ``sos_app`` (RLS applies; no
definer function); it must be ``active`` or ``suspended``. Everything written is audited in the
same transaction (actor ``system``; key versions, key id and counts only). Output: IDs, key
versions and counts only. Refuses unless connected as ``sos_app`` (never a superuser or a
BYPASSRLS role). On a dedicated host (``SOS_DEPLOYMENT_MODE=dedicated``) ``--tenant`` defaults to,
and must equal, ``SOS_DEDICATED_TENANT_ID``.

Exit codes: 0 done / nothing to do · 1 refused (nothing done) · 2 invalid arguments ·
3 dry run found work, or work remains (values still on an old version, retirement not yet
allowed) · 4 failed.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from typing import Final, TextIO

from sqlalchemy import Engine

from app.core.config import DeploymentMode, Settings, get_settings
from app.core.crypto import KeyWrapper, get_key_wrapper
from app.core.db import context_free_session, tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.identity import service as identity
from app.students import rotation
from app.tenancy import service as tenancy

EXIT_OK: Final = 0
EXIT_REFUSED: Final = 1
EXIT_INVALID: Final = 2
EXIT_PENDING: Final = 3
EXIT_FAILED: Final = 4

APP_DB_ROLE: Final = "sos_app"

log = get_logger(__name__)


class Refused(Exception):
    """The command must not run here (reason code only)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.tenancy.rotate_keys",
        description="Rotate a school's data encryption key (SEC-012). Dry run by default.",
    )
    p.add_argument("--tenant", type=uuid.UUID, help="the school (tenant ID)")
    p.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run, nothing written)"
    )
    step = p.add_mutually_exclusive_group()
    step.add_argument(
        "--reencrypt",
        action="store_true",
        help="re-encrypt values still on an older key version now, in this process",
    )
    step.add_argument(
        "--retire", action="store_true", help="retire older key versions no value uses any more"
    )
    p.add_argument(
        "--new-hmac-key",
        action="store_true",
        help="with a rotation: also replace the blind-index HMAC key (incident response)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=rotation.DEFAULT_BATCH_SIZE,
        help=f"rows per re-encryption transaction (1..{rotation.MAX_BATCH_SIZE})",
    )
    return p


def preflight(*, engine: Engine | None = None) -> None:
    """Refuse unless RLS applies to this connection."""
    role = identity.database_role(engine=engine)
    if role.superuser or role.bypass_rls:
        raise Refused("db_role_bypasses_rls")
    if role.name != APP_DB_ROLE:
        raise Refused("wrong_db_role")


def target(
    settings: Settings, only: uuid.UUID | None, *, engine: Engine | None = None
) -> uuid.UUID:
    """The school this run handles (refuses one that is not active or suspended)."""
    if settings.deployment_mode is DeploymentMode.DEDICATED:
        try:
            host_tenant = uuid.UUID(settings.dedicated_tenant_id or "")
        except ValueError:
            raise Refused("dedicated_tenant_id_missing") from None
        if only is not None and only != host_tenant:
            raise Refused("tenant_mismatch")
        only = host_tenant
    if only is None:
        raise Refused("tenant_required")
    with context_free_session(engine=engine) as session:
        eligible = tenancy.list_tenant_ids(session, tenancy.ROTATABLE_STATUSES)
    if only not in eligible:
        raise Refused("tenant_not_eligible")
    return only


def _status(out: TextIO, tenant_id: uuid.UUID, *, engine: Engine | None) -> tuple[int, int]:
    """Print key versions with their ciphertext counts; return (current, values on older)."""
    with tenant_session(tenant_id, engine=engine) as session:
        versions = tenancy.list_key_versions(session)
        counts = rotation.census(session)
    current = next((v.key_version for v in versions if v.current), 0)
    stale = sum(n for v, n in counts.items() if v != current)
    out.write(
        f"tenant={tenant_id} current_key_version={current} values_on_older_versions={stale}\n"
    )
    for v in versions:
        state = "current" if v.current else ("retired" if v.retired_at else "active")
        out.write(
            f"  key_version={v.key_version} state={state} key_id={v.key_id}"
            f" created_at={v.created_at.isoformat()} values={counts.get(v.key_version, 0)}\n"
        )
    return current, stale


def _rotate(
    out: TextIO,
    tenant_id: uuid.UUID,
    *,
    apply: bool,
    new_hmac_key: bool,
    wrapper: KeyWrapper | None,
    settings: Settings,
    engine: Engine | None,
) -> int:
    current, _ = _status(out, tenant_id, engine=engine)
    hmac = "new" if new_hmac_key else "carried"
    if not apply:
        out.write(f"plan: add key_version={current + 1} hmac_key={hmac} (use --apply)\n")
        return EXIT_PENDING
    version = rotation.rotate(
        tenant_id,
        wrapper=wrapper or get_key_wrapper(settings),
        new_hmac_key=new_hmac_key,
        engine=engine,
    )
    out.write(
        f"rotated: key_version={version} hmac_key={hmac}; re-encryption queued"
        " (maintenance.reencrypt_tenant); retire older versions with --retire after 16 minutes\n"
    )
    log.info("tenancy.rotate_keys.rotated", tenant_id=str(tenant_id), key_version=version)
    return EXIT_OK


def _reencrypt(
    out: TextIO, tenant_id: uuid.UUID, *, apply: bool, batch_size: int, engine: Engine | None
) -> int:
    current, stale = _status(out, tenant_id, engine=engine)
    if not apply:
        out.write(f"plan: re-encrypt {stale} values to key_version={current} (use --apply)\n")
        return EXIT_PENDING if stale else EXIT_OK
    run = rotation.run_reencryption(tenant_id, batch_size=batch_size, engine=engine)
    rows = " ".join(f"{k}={v}" for k, v in sorted(run.rows.items()))
    out.write(
        f"reencrypted: key_version={run.key_version} batches={run.batches} total={run.total}"
        f" remaining={run.remaining} {rows}\n".rstrip()
        + "\n"
    )
    log.info(
        "tenancy.rotate_keys.reencrypted",
        tenant_id=str(tenant_id),
        key_version=run.key_version,
        count=run.total,
        remaining=run.remaining,
    )
    return EXIT_PENDING if run.remaining else EXIT_OK


def _retire(out: TextIO, tenant_id: uuid.UUID, *, apply: bool, engine: Engine | None) -> int:
    _status(out, tenant_id, engine=engine)
    result = rotation.retire_unreferenced(tenant_id, apply=apply, engine=engine)
    verb = "retired" if apply else "plan: retire"
    for version in result.retired:
        out.write(f"{verb} key_version={version}\n")
    for version, count in sorted(result.in_use.items()):
        out.write(f"  kept key_version={version} values={count} (re-encrypt first)\n")
    for version, code in sorted(result.blocked.items()):
        out.write(f"  kept key_version={version} reason={code}\n")
    log.info(
        "tenancy.rotate_keys.retire",
        tenant_id=str(tenant_id),
        apply=apply,
        count=len(result.retired),
        in_use=len(result.in_use),
        blocked=len(result.blocked),
    )
    if result.in_use or result.blocked or (not apply and result.retired):
        return EXIT_PENDING
    return EXIT_OK


def _invalid(args: argparse.Namespace) -> str | None:
    if not 1 <= args.batch_size <= rotation.MAX_BATCH_SIZE:
        return f"--batch-size must be between 1 and {rotation.MAX_BATCH_SIZE}"
    if args.new_hmac_key and (args.reencrypt or args.retire):
        return "--new-hmac-key applies only to a rotation"
    return None


def _run(
    args: argparse.Namespace,
    tenant_id: uuid.UUID,
    *,
    out: TextIO,
    settings: Settings,
    engine: Engine | None,
    wrapper: KeyWrapper | None,
) -> int:
    if args.reencrypt:
        return _reencrypt(
            out, tenant_id, apply=args.apply, batch_size=args.batch_size, engine=engine
        )
    if args.retire:
        return _retire(out, tenant_id, apply=args.apply, engine=engine)
    return _rotate(
        out,
        tenant_id,
        apply=args.apply,
        new_hmac_key=args.new_hmac_key,
        wrapper=wrapper,
        settings=settings,
        engine=engine,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    wrapper: KeyWrapper | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed the usage error
        return EXIT_OK if exc.code == 0 else EXIT_INVALID
    problem = _invalid(args)
    if problem is not None:
        err.write(f"invalid: {problem}\n")
        return EXIT_INVALID
    settings = settings or get_settings()
    try:
        preflight(engine=engine)
        tenant_id = target(settings, args.tenant, engine=engine)
    except Refused as exc:
        err.write(f"refused: {exc.code}\n")
        log.warning("tenancy.rotate_keys.refused", reason=exc.code)
        return EXIT_REFUSED
    try:
        return _run(args, tenant_id, out=out, settings=settings, engine=engine, wrapper=wrapper)
    except Exception as exc:
        code = exc.code if isinstance(exc, DomainError | rotation.RotationError) else None
        error = code or type(exc).__name__
        out.write(f"tenant={tenant_id} result=failed error={error}\n")
        log.warning("tenancy.rotate_keys.failed", tenant_id=str(tenant_id), error=error)
        return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
