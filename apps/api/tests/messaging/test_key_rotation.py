"""DEK rotation covers messaging (SEC-012; R2-1C): message variables and the provider inbox are
re-encrypted, and opt-ins stay valid for the guardian's number under a new HMAC key (their
phone index is recomputed in the same pass). A fresh synthetic school, so the shared world's
keys are untouched.
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.messaging import repository as repo
from app.messaging import service
from app.messaging.webhook import parse_entries
from app.students import rotation

pytestmark = pytest.mark.db
M = sys.modules["sos_test_messaging_support"]
W = sys.modules["sos_test_api_world"]


@pytest.fixture
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    fresh = W.School(W.provision_school())
    fresh.people["owner"] = W.add_member(admin_engine, fresh.tenant_id, ["owner"])
    W.build_structure(fresh, fresh.people["owner"])
    return fresh


def test_SEC_012_rotation_reencrypts_messaging_and_keeps_optins_valid(
    school: Any, admin_engine: Engine
) -> None:
    _, waba = M.connect(school)
    student, guardian, mobile = M.guardian(school)
    owner = school.people["owner"]
    ctx = M.SW.ctx_for(school.tenant_id, owner, "principal")
    from app.messaging.schemas import OptinIn

    with tenant_session(school.tenant_id, owner.user_id) as db:
        service.record_optin(
            db,
            ctx,
            guardian,
            OptinIn(
                category="attendance",
                status="opted_in",
                method="in_person",
                decided_on=service._today(db),
            ),
        )
        service.queue_message(
            db,
            purpose="absence_alert",
            guardian_id=guardian,
            student_id=student,
            source_type="attendance_day",
            source_id=uuid.uuid4(),
            dedupe_key="2026-10-17",
            variables={"school_name": "S", "child_first_name": "R", "date": "17/10/2026"},
        )
    payload = M.inbound(waba, "91" + M.synthetic_mobile(), text_body="Hi")
    for waba_id, events in parse_entries(json.dumps(payload).encode()).items():
        handle = M.live_account(school).waba_handle_id
        service.store_events(school.tenant_id, handle, waba_id, events)

    new_version = rotation.rotate(school.tenant_id, wrapper=W.wrapper(), new_hmac_key=True)
    rotation.run_reencryption(school.tenant_id)

    with tenant_session(school.tenant_id) as db:
        assert set(rotation.census(db)) == {new_version}
        optin = repo.current_optins(db, [guardian])[(guardian, "attendance")]
        assert optin.phone_key_version == new_version
        out = service.get_guardian_optins(db, ctx, guardian)
    attendance = next(c for c in out.categories if c.category == "attendance")
    assert attendance.for_current_number is True
    assert mobile[-4:] == out.phone_last4
