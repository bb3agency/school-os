"""Break-glass objects for the security suites (authz matrix, BOLA). Synthetic data only.

Loaded by file path (tests are not an importable package): see ``load_objects`` in
``tests/security/test_authz_matrix.py`` and ``test_bola.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import text

from app.authz.context import Scopes, UserContext
from app.breakglass import service
from app.core.db import platform_session, tenant_session


def pending_grant(tenant_id: uuid.UUID) -> uuid.UUID:
    """A fresh request from a fresh operator, pulled into the school (status requested)."""
    op, request_id = uuid.uuid4(), uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.operators (id, idp_subject, email, display_name, status, "
                "mfa_enrolled) VALUES (:i, :s, :e, 'Synthetic Operator', 'active', true)"
            ),
            {"i": op, "s": f"op-sub-{op}", "e": f"op-{op.hex[:12]}@example.test"},
        )
        s.execute(
            text(
                "INSERT INTO platform.breakglass_requests (id, tenant_id, requested_by, "
                "reason_code, reason, scope, duration_minutes, status) VALUES (:r, :t, :o, "
                "'support_request', 'Synthetic support reason for a test', '{}', 30, 'requested')"
            ),
            {"r": request_id, "t": tenant_id, "o": op},
        )
    service.sync_school(tenant_id)
    with tenant_session(tenant_id) as s:
        value: object = s.execute(
            text("SELECT id FROM ops.break_glass_grants WHERE platform_request_id = :r"),
            {"r": request_id},
        ).scalar_one()
    return uuid.UUID(str(value))


def active_grant(tenant_id: uuid.UUID, approver: Any) -> uuid.UUID:
    """A pending grant approved by ``approver`` (a world Person holding breakglass.approve)."""
    grant_id = pending_grant(tenant_id)
    ctx = UserContext(
        user_id=approver.user_id,
        tenant_id=tenant_id,
        membership_id=approver.membership_id,
        roles=frozenset({"owner"}),
        permissions=frozenset({"breakglass.approve"}),
        scopes=Scopes(school=True),
        mfa=True,
        auth_time=dt.datetime.now(dt.UTC),
    )
    with tenant_session(tenant_id, approver.user_id) as s:
        service.approve(s, ctx, grant_id)
    return grant_id
