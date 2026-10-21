"""Pulling SchoolOS's incident notices into each school's breach register (R3-1B; ADR-0050
decision 2; FR-DPDP-032; US-2802).

The break-glass pattern: the school-side worker reads this school's confirmed notices through
``app.platform.service`` (as ``sos_platform``; codes, counts and times only), stores each one in
the school's own ``tenant_session`` (``breach_service.store_processor_notice``: idempotent per
incident), and after that transaction commits tells the control plane, which records when the
school was notified. A notice stored but not reported is reported by the next sweep; a
control-plane outage only delays the pull. The control plane never writes tenant tables
(ADR-0020). This is the only file of ``app.privacy`` that imports the control plane.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from app.core.db import tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.platform import service as control_plane
from app.privacy import breach_service

log = get_logger(__name__)


def _report_pulled(tenant_id: uuid.UUID, incident_ids: Sequence[uuid.UUID]) -> int:
    done = 0
    for incident_id in incident_ids:
        try:
            if control_plane.record_incident_notice_pulled(tenant_id, incident_id):
                done += 1
        except (SQLAlchemyError, DomainError) as exc:  # the next sweep retries
            log.warning("privacy.breach_report_failed", error_type=type(exc).__name__)
    return done


def sync_school(tenant_id: uuid.UUID) -> int:
    """Pull this school's confirmed SchoolOS notices into its register (shared platform only).
    Returns how many were new."""
    if not breach_service.control_plane_here():
        return 0
    notices = control_plane.incident_notices_for_school(tenant_id)
    if not notices:
        return 0
    received = 0
    with tenant_session(tenant_id) as session:
        for n in notices:
            created = breach_service.store_processor_notice(
                session,
                tenant_id,
                incident_id=n.incident_id,
                severity=n.severity,
                summary=n.summary,
                opened_at=n.opened_at,
                confirmed_at=n.confirmed_at,
                categories=n.categories,
                approx_people=n.approx_people,
                window_from=n.window_from,
                window_to=n.window_to,
            )
            if created is not None:
                received += 1
    _report_pulled(tenant_id, [n.incident_id for n in notices])
    if received:
        log.info("privacy.breach.notices_received", count=received)
    return received


__all__ = ["sync_school"]
