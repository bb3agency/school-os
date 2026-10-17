"""A parent's STOP is honoured while the school is suspended (FR-MSG-010, PRV-025; ADR-0047
amendment of R2-1C): the webhook still reaches the school, only STOP messages are taken in, the
opt-out and link revocation apply, and nothing is sent. A fresh synthetic school.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.messaging import service
from app.messaging.providers.fake import STATE
from app.ops.service import RUN_WHILE_SUSPENDED
from app.public import service as public

pytestmark = pytest.mark.db
M = sys.modules["sos_test_messaging_support"]
W = sys.modules["sos_test_api_world"]
P_STATUS = "UPDATE core.tenants SET status = :s WHERE id = :t"


@pytest.fixture
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    fresh = W.School(W.provision_school())
    fresh.people["owner"] = W.add_member(admin_engine, fresh.tenant_id, ["owner"])
    W.build_structure(fresh, fresh.people["owner"])
    return fresh


def _status(admin: Engine, tenant_id: uuid.UUID, status: str) -> None:
    from sqlalchemy import text

    with admin.begin() as c:
        c.execute(text(P_STATUS), {"s": status, "t": tenant_id})


def test_FR_MSG_010_stop_is_honoured_while_the_school_is_suspended(
    school: Any, api: Any, admin_engine: Engine, fake_state: Any
) -> None:
    _, waba = M.connect(school)
    _, guardian, mobile = M.guardian(school)
    _, other_guardian, other_mobile = M.guardian(school)
    with tenant_session(school.tenant_id) as db:
        link = public.issue_handle(
            db,
            kind="parent_notice",
            target_type="notice",
            target_id=uuid.uuid4(),
            guardian_id=guardian,
        )
    assert "messaging.process_inbox" in RUN_WHILE_SUSPENDED
    _status(admin_engine, school.tenant_id, "suspended")
    try:
        before = len(STATE.sent)
        assert (
            M.post(api.client, M.inbound(waba, "91" + mobile, text_body="STOP")).status_code == 200
        )
        question = M.inbound(waba, "91" + other_mobile, text_body="Is school open?")
        assert M.post(api.client, question).status_code == 200
        service.process_inbox(school.tenant_id)
        stops = M.rows(
            admin_engine,
            "SELECT category FROM msg.optins WHERE guardian_id = :g AND method = 'whatsapp_stop'",
            g=guardian,
        )
        assert sorted(r["category"] for r in stops) == ["attendance", "fees", "notices"]
        revoked = M.rows(
            admin_engine,
            "SELECT revoked_reason FROM core.public_handles WHERE id = :i",
            i=link.handle_id,
        )
        assert revoked == [{"revoked_reason": "opted_out"}]
        # Nothing is sent while suspended, and the other message was not taken in at all.
        assert len(STATE.sent) == before
        stored = M.rows(
            admin_engine,
            "SELECT count(*) AS n FROM msg.inbound_events WHERE tenant_id = :t",
            t=school.tenant_id,
        )
        assert stored == [{"n": 1}]
        assert M.rows(
            admin_engine,
            "SELECT count(*) AS n FROM msg.optins WHERE guardian_id = :g",
            g=other_guardian,
        ) == [{"n": 0}]
    finally:
        _status(admin_engine, school.tenant_id, "active")
