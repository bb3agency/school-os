"""Helpers for the WhatsApp sending tests (R2-3G; synthetic data only, invariant 11).

Loaded by path. One separate synthetic school per test session (``make_school``): the structure
of tests/api/world.py, a member per role, fees on, WhatsApp connected through the fake provider
with every template approved (URL-button templates need ``SOS_PUBLIC_BASE_URL``, patched here
for messaging only). Times are pinned with ``at(hh, mm)`` (IST on today's date).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.messaging import runtime
from app.messaging import service as messaging

IST = ZoneInfo("Asia/Kolkata")
TESTS = Path(__file__).resolve().parents[1]
PUBLIC_BASE = "https://my.example.test"


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load("sos_test_api_world", TESTS / "api" / "world.py")
SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
M = _load("sos_test_messaging_support", TESTS / "messaging" / "support.py")
F = _load("sos_test_fees_support", TESTS / "fees" / "support.py")
C = _load("sos_test_circulars_support", TESTS / "circulars" / "support.py")


def public_origin_on() -> None:
    current = runtime.current_patch() or {}
    runtime.patch_for_tests({**current, "public_base_url": PUBLIC_BASE})


def make_school(admin: Engine) -> Any:
    F.install()
    public_origin_on()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in (
        "principal",
        "office_admin",
        "office_staff",
        "accountant",
        "auditor_readonly",
        "teacher",
    ):
        s.people[role] = W.add_member(admin, s.tenant_id, [role])
    s.people["class_teacher"] = W.add_member(
        admin, s.tenant_id, ["class_teacher"], scopes=[("section", s.ids["section_9a"])]
    )
    F.enable(admin, s.tenant_id)
    account_id, _ = M.connect(s)
    messaging.provision_templates(s.tenant_id, account_id)
    messaging.sync_templates(s.tenant_id)
    settings(s)
    return s


def settings(school: Any, **values: Any) -> None:
    """Every switch on, default times, no cap, no no-message dates, then ``values``."""
    owner = school.people["owner"]
    ctx = M.owner_ctx(school)
    from app.messaging.schemas import MessagingSettingsIn

    body: dict[str, Any] = {
        "notices_enabled": True,
        "absence_alerts_enabled": True,
        "fee_reminders_enabled": True,
        "fee_receipts_enabled": True,
        "absence_alert_time": dt.time(11, 0),
        "quiet_start": dt.time(21, 0),
        "quiet_end": dt.time(7, 0),
        "daily_cap": 1_000_000,
        "office_phone": None,
        "no_message_dates": [],
        **values,
    }
    with tenant_session(school.tenant_id, owner.user_id) as db:
        version = messaging.get_messaging_settings(db, ctx).version
        messaging.update_messaging_settings(
            db, ctx, MessagingSettingsIn(**body), expected_version=version
        )


def today() -> dt.date:
    return dt.datetime.now(IST).date()


def at(hour: int, minute: int = 0, *, days: int = 0) -> dt.datetime:
    return dt.datetime.combine(today() + dt.timedelta(days=days), dt.time(hour, minute), tzinfo=IST)


def ctx(school: Any, role: str) -> Any:
    base = SW.ctx_for(school.tenant_id, school.people[role], role)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def notice(school: Any) -> uuid.UUID:
    """An approved notice (owner drafted and approved it; FR-NOTICE-004)."""
    notice_id, _ = C.complete_notice(school, approved=True)
    value: uuid.UUID = notice_id
    return value


def opted_guardian(
    api: Any, school: Any, *categories: str, section_key: str = "section_9a"
) -> tuple[uuid.UUID, uuid.UUID, str]:
    student, guardian, mobile = M.guardian(school, section_key=section_key)
    assert mobile is not None
    for category in categories:
        res = M.opt_in(api, school.people["office_staff"], guardian, category)
        assert res.status_code == 201, res.text
    return student, guardian, mobile


def message_rows(admin: Engine, **where: Any) -> list[dict[str, Any]]:
    clauses = " AND ".join(f"{k} = :{k}" for k in where)
    sql = "SELECT * FROM msg.messages" + (f" WHERE {clauses}" if clauses else "")
    with admin.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql + " ORDER BY queued_at"), where)]


def handle(admin: Engine, handle_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT * FROM core.public_handles WHERE id = :i"), {"i": handle_id}
        ).one()
    return dict(row._mapping)


def set_status(admin: Engine, tenant_id: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET status = :s WHERE id = :t"), {"s": status, "t": tenant_id}
        )


__all__ = [
    "at",
    "ctx",
    "handle",
    "make_school",
    "message_rows",
    "notice",
    "opted_guardian",
    "public_origin_on",
    "set_status",
    "settings",
    "today",
]
