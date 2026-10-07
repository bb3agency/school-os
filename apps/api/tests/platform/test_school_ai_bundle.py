"""School-side "Plan and billing": the school's AI answer bundle (FR-PLT-030, FR-PLT-013,
FR-PLT-020; ADR-0038, docs/16 §5.18).

The bundle, its prices and the month's answer count are platform data, read for the caller's
own subscription only; a school without a bundle gets ``null``. Prices are compared with the
catalogue rows, never hard-coded here. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.platform import repository as repo
from app.platform.billing import next_month
from app.platform.common import today_ist

from .conftest import Api, Operator, provision_payload

pytestmark = pytest.mark.db

PATH = "/api/v1/tenant/billing"


def _live_school(
    api: Api, owner: Operator, plan: uuid.UUID, admin: Engine
) -> tuple[uuid.UUID, uuid.UUID, str]:
    body = provision_payload(plan, start_as="active")
    out = api.call("POST", "/tenants", owner, json=body).json()
    tid = uuid.UUID(out["tenant_id"])
    api.call("POST", f"/tenants/{tid}/activate", owner)
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'active' WHERE tenant_id = :t"), {"t": tid}
        )
    return tid, uuid.UUID(out["subscription_id"]), body["owner"]["idp_subject"]


def _get(api: Api, subject: str) -> Any:
    return api.client.get(PATH, headers=api.headers(None, tenant_subject=subject))


def _bundle(code: str) -> dict[str, Any]:
    with platform_session() as s:
        row = (
            s.execute(
                text(
                    "SELECT id, code, name, included_answers, price_inr, overage_rate_inr "
                    "FROM platform.ai_bundles WHERE code = :c AND status = 'published' "
                    "ORDER BY version DESC LIMIT 1"
                ),
                {"c": code},
            )
            .mappings()
            .one()
        )
    return dict(row)


def _set_bundle(api: Api, owner: Operator, sub: uuid.UUID, code: str) -> dict[str, Any]:
    bundle = _bundle(code)
    res = api.call(
        "PUT",
        f"/subscriptions/{sub}/ai-bundle",
        owner,
        json={"ai_bundle_id": str(bundle["id"])},
        headers=api.if_match(f"/subscriptions/{sub}", owner),
    )
    assert res.status_code == 200, res.text
    return bundle


def _counts_from(sub: uuid.UUID, month: dt.date) -> None:
    with platform_session() as s:
        s.execute(
            text("UPDATE platform.subscriptions SET ai_bundle_from = :m WHERE id = :i"),
            {"m": month, "i": sub},
        )


def _answers(tenant_id: uuid.UUID, day: dt.date, n: int) -> None:
    with platform_session() as s:
        repo.upsert_usage(
            s,
            {
                "tenant_id": tenant_id,
                "usage_date": day,
                "source": "shared_collector",
                "active_users": 1,
                "staff_users": 1,
                "students_active": 0,
                "storage_bytes": 0,
                "documents": 0,
                "ai_queries": n,
                "ai_answers": n,
                "ai_input_tokens": 0,
                "ai_output_tokens": 0,
                "ai_cost_usd": Decimal("0"),
                "ai_cost_inr": Decimal("0"),
            },
        )


def _add_member(admin: Engine, tenant_id: uuid.UUID, role: str) -> str:
    uid, mid, subject = uuid.uuid4(), uuid.uuid4(), f"sub-{uuid.uuid4().hex}"
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO core.users (id, idp_subject, display_name) VALUES (:u, :s, :n)"),
            {"u": uid, "s": subject, "n": f"Synthetic Staff {uid.hex[:6]}"},
        )
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                "VALUES (:m, :t, :u, 'active')"
            ),
            {"m": mid, "t": tenant_id, "u": uid},
        )
        c.execute(
            text(
                "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
                "SELECT :t, :m, r.id FROM core.roles r WHERE r.tenant_id = :t AND r.key = :k"
            ),
            {"t": tenant_id, "m": mid, "k": role},
        )
    return subject


def test_FR_PLT_030_school_sees_its_ai_bundle_and_this_months_answers(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    tid, sub, subject = _live_school(api, owner, make_plan(), admin_engine)
    bundle = _set_bundle(api, owner, sub, "ai-standard")
    month = today_ist().replace(day=1)
    _counts_from(sub, month)
    _answers(tid, month, 40)
    _answers(tid, month - dt.timedelta(days=1), 900)  # last month: not this month's count
    res = _get(api, subject)
    assert res.status_code == 200, res.text
    got = res.json()["ai_bundle"]
    assert got == {
        "code": bundle["code"],
        "name": bundle["name"],
        "included_answers": bundle["included_answers"],
        "price_inr": str(bundle["price_inr"]),
        "overage_rate_inr": str(bundle["overage_rate_inr"]),
        "counts_from": month.isoformat(),
        "month_start": month.isoformat(),
        "answers_used": 40,
        "answers_counted_to": month.isoformat(),
    }


def test_FR_PLT_030_bundle_not_yet_counting_shows_no_answer_count(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    tid, sub, subject = _live_school(api, owner, make_plan(), admin_engine)
    _set_bundle(api, owner, sub, "ai-lite")  # counts from next month
    month = today_ist().replace(day=1)
    _answers(tid, month, 12)
    got = _get(api, subject).json()["ai_bundle"]
    assert got["counts_from"] == next_month(today_ist()).isoformat()
    assert got["answers_used"] is None
    assert got["answers_counted_to"] is None


def test_FR_PLT_030_no_bundle_is_null_and_other_schools_bundle_is_never_shown(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    _tid_a, sub_a, subject_a = _live_school(api, owner, make_plan(), admin_engine)
    _tid_b, _sub_b, subject_b = _live_school(api, owner, make_plan(), admin_engine)
    _set_bundle(api, owner, sub_a, "ai-high")
    res_b = _get(api, subject_b)
    assert res_b.status_code == 200, res_b.text
    assert res_b.json()["available"] is True
    assert res_b.json()["ai_bundle"] is None
    assert "ai-high" not in res_b.text
    assert _get(api, subject_a).json()["ai_bundle"]["code"] == "ai-high"


def test_FR_PLT_030_ai_bundle_needs_tenant_billing_read(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], admin_engine: Engine
) -> None:
    tid, sub, _subject = _live_school(api, owner, make_plan(), admin_engine)
    _set_bundle(api, owner, sub, "ai-lite")
    accountant = _add_member(admin_engine, tid, "accountant")
    allowed = _get(api, accountant)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["ai_bundle"]["code"] == "ai-lite"
    staff = _add_member(admin_engine, tid, "office_staff")  # no tenant.billing.read
    denied = _get(api, staff)
    assert denied.status_code == 403, denied.text
    assert "ai-lite" not in denied.text
