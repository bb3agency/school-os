"""A withdrawn notice's parent page is the uniform 404 at once (R2-3J; FR-NOTICE-009,
FR-PUB-007, FR-PUB-011; ADR-0046 decision 5). Synthetic data only."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.circulars import service as circulars
from app.circulars.schemas import NoticeWithdrawIn
from app.core.db import tenant_session
from app.public import service, tokens

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]
SW = sys.modules["sos_test_student_world"]


def test_FR_NOTICE_009_withdrawn_notice_page_is_the_uniform_404(
    world: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    _, guardian = P.student_with_guardian(world.a)
    owner = world.a.people["owner"]
    with tenant_session(world.a.tenant_id, owner.user_id) as db:
        notice_id = P.approved_notice(db, owner.user_id)
        issued = service.issue_parent_link(
            db,
            kind="parent_notice",
            target_id=notice_id,
            guardian_id=guardian,
            created_by=owner.user_id,
        )
    assert issued.key is not None
    assert P.current(public_client, issued.key).status_code == 200

    ctx = SW.ctx_for(world.a.tenant_id, owner, "owner")
    with tenant_session(world.a.tenant_id, owner.user_id) as db:
        version = circulars.get_notice(db, ctx, notice_id).version
        circulars.withdraw_notice(
            db, ctx, notice_id, NoticeWithdrawIn(reason="Sent with the wrong date"), version
        )

    gone = P.current(public_client, issued.key)
    unknown = P.current(public_client, tokens.new_parent_token())
    assert gone.status_code == 404
    # The same body as any dead link (only the request id differs).
    assert {k: v for k, v in gone.json().items() if k != "request_id"} == {
        k: v for k, v in unknown.json().items() if k != "request_id"
    }
    row = P.handle_row(admin_engine, issued.handle_id)
    assert row["revoked_at"] is not None
    assert row["revoked_reason"] == "notice_withdrawn"
