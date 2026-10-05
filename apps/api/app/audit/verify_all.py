"""Daily chain verification across tenants (SEC-007, FR-AUD-004, docs/11 §6).

Each tenant is verified in its own ``tenant_session`` (never all tenants in one transaction).
Tenant IDs come from the allowlisted definer function ``core.list_tenant_ids`` (migration
0003_core_schema). Log events (metric filters and alerts key on them):

- ``audit.chain.verified`` (info) and ``audit.chain.broken`` (error, **P1 security alert**);
- ``audit.partitions.low_runway`` (warning) when partitions run out within 90 days.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from app.audit import repository
from app.audit.partitions import RUNWAY_WARN_DAYS, partition_upper_bound
from app.audit.schemas import VerifyResult
from app.audit.service import verify_chain, verify_platform_chain
from app.core.db import context_free_session, platform_session, tenant_session

logger = logging.getLogger("app.audit.verify")

TENANT_STATUSES = ("provisioning", "active", "suspended", "offboarding", "deleted")
"""Every status whose school can hold an audit chain (audit 2026-10-05 DP-03). Offboarding and
deleted schools keep their chain, including the evidence of the deletion, until the audit
retention ends (ADR-0029), so the daily verification and the signed archive cover them too. A
school whose chain was purged verifies as empty and archives nothing."""


class TenantListingUnavailable(RuntimeError):
    """``core.list_tenant_ids`` does not exist yet (migration 0003_core_schema not applied)."""


def list_tenant_ids(*, engine: Engine | None = None) -> list[uuid.UUID]:
    """IDs of every school that can hold a chain (:data:`TENANT_STATUSES`), via the
    allowlisted definer function."""
    try:
        with context_free_session(engine=engine) as session:
            rows: Iterable[object] = session.execute(
                text("SELECT tenant_id FROM core.list_tenant_ids(CAST(:statuses AS text[]))"),
                {"statuses": list(TENANT_STATUSES)},
            ).scalars()
            return [uuid.UUID(str(r)) for r in rows]
    except ProgrammingError as exc:
        if "list_tenant_ids" in str(exc.orig):
            raise TenantListingUnavailable(
                "core.list_tenant_ids() is missing: apply migration 0003_core_schema"
            ) from exc
        raise


def _log_result(result: VerifyResult, **ids: str) -> None:
    if result.ok:
        logger.info("audit.chain.verified", extra={**ids, "checked": result.checked})
    else:
        logger.error(
            "audit.chain.broken",
            extra={
                **ids,
                "checked": result.checked,
                "first_bad_seq": result.first_bad_seq,
                "reason": result.reason,
                "severity": "P1",
            },
        )


def verify_all(
    tenant_ids: Iterable[uuid.UUID],
    *,
    engine: Engine | None = None,
    statement_timeout_ms: int | None = None,
) -> dict[uuid.UUID, VerifyResult]:
    """Verify each tenant's chain in its own transaction; one failure never stops the rest."""
    results: dict[uuid.UUID, VerifyResult] = {}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(
                tenant_id, statement_timeout_ms=statement_timeout_ms, engine=engine
            ) as session:
                result = verify_chain(session, tenant_id)
        except Exception:
            logger.exception("audit.chain.verify_error", extra={"tenant_id": str(tenant_id)})
            result = VerifyResult(False, 0, None, "verify_error")
        results[tenant_id] = result
        _log_result(result, tenant_id=str(tenant_id))
    return results


def verify_platform(
    *, engine: Engine | None = None, statement_timeout_ms: int | None = None
) -> VerifyResult:
    with platform_session(statement_timeout_ms=statement_timeout_ms, engine=engine) as session:
        result = verify_platform_chain(session)
    _log_result(result, chain="platform")
    return result


def check_partition_runway(
    *, engine: Engine | None = None, today: date | None = None
) -> date | None:
    """Return the first uncovered day; warn when it is fewer than 90 days away."""
    with context_free_session(engine=engine) as session:
        bound = partition_upper_bound(repository.partition_names(session))
    today = today or datetime.now(UTC).date()
    if bound is None or bound - today < timedelta(days=RUNWAY_WARN_DAYS):
        logger.warning(
            "audit.partitions.low_runway",
            extra={"covered_until": bound.isoformat() if bound else None},
        )
    return bound


def main(*, engine: Engine | None = None, platform_engine: Engine | None = None) -> int:
    """CLI for restore drills: ``python -m app.audit.verify_all``; exit 1 if any chain is broken."""
    results = verify_all(list_tenant_ids(engine=engine), engine=engine)
    ok = all(r.ok for r in results.values())
    ok = verify_platform(engine=platform_engine).ok and ok
    logger.info("audit.chain.verify_all_done", extra={"count": len(results)})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
