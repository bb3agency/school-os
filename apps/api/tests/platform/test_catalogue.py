"""Commercial catalogue: plans, one-time fee, AI answer bundles and overage (ADR-0038).

FR-PLT-010 (plans), FR-PLT-013 (subscriptions), FR-PLT-015..017 (invoices, GST), FR-PLT-020
(usage metering). Owner-approved prices of 2026-10-01, ex-GST: Shared ₹4,999 a month plus a
one-time ₹15,000; Dedicated ₹9,900 a month plus a one-time ₹49,000; AI bundles Lite 300 answers
₹699, Standard 1,000 ₹1,499, High 3,000 ₹3,499; ₹1.50 per extra answer. Synthetic data only;
far-future periods so each test owns its months.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.platform import billing, usage
from app.platform import repository as repo
from app.platform.common import today_ist

from .conftest import Api, MakeOperator, Operator, billing_account_payload, provision_payload

pytestmark = pytest.mark.db

D = Decimal

# The owner's catalogue (2026-10-01). Changing a price needs a new catalogue migration.
PLANS = {
    "shared": ("Shared", "shared", D("4999.00"), D("15000.00")),
    "dedicated": ("Dedicated", "dedicated", D("9900.00"), D("49000.00")),
}
BUNDLES = {
    "ai-lite": ("Lite", 300, D("699.00"), D("1.50")),
    "ai-standard": ("Standard", 1000, D("1499.00"), D("1.50")),
    "ai-high": ("High", 3000, D("3499.00"), D("1.50")),
}


# --- pure maths -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answers", "included", "extra", "amount"),
    [
        (1250, 1000, 250, "375.00"),  # Standard: 250 x 1.50
        (1000, 1000, 0, "0.00"),
        (0, 300, 0, "0.00"),
        (301, 300, 1, "1.50"),
        (3999, 3000, 999, "1498.50"),
    ],
)
def test_FR_PLT_015_ai_overage_maths(answers: int, included: int, extra: int, amount: str) -> None:
    got = billing.ai_overage(answers, included, D("1.50"))
    assert got == (extra, D(amount))


def test_FR_PLT_015_overage_line_wording() -> None:
    text_ = billing.overage_description("Standard", dt.date(2031, 3, 1), 250, D("1.50"))
    assert text_ == (
        "AI answers above the Standard bundle, March 2031: 250 extra answers "
        "\N{MULTIPLICATION SIGN} ₹1.50"
    )
    assert "token" not in text_.lower()
    assert "unlimited" not in text_.lower()


# --- the seeded catalogue ---------------------------------------------------------------------


def test_FR_PLT_010_catalogue_matches_the_owner_prices(platform_engine: Engine) -> None:
    with platform_session() as s:
        plans = {
            r["code"]: r
            for r in s.execute(
                text(
                    "SELECT DISTINCT ON (code) * FROM platform.plans "
                    "WHERE created_by IS NULL AND status = 'published' "
                    "ORDER BY code, version DESC"
                )
            ).mappings()
        }
        bundles = {
            r["code"]: r
            for r in s.execute(
                text("SELECT * FROM platform.ai_bundles WHERE status = 'published'")
            ).mappings()
        }
    assert set(plans) == set(PLANS)
    for code, (name, tier, monthly, fee) in PLANS.items():
        row = plans[code]
        assert (row["name"], row["tier"], row["billing_period"], row["pricing_model"]) == (
            name,
            tier,
            "monthly",
            "flat",
        )
        assert (row["base_price_inr"], row["one_time_fee_inr"]) == (monthly, fee)
        assert (row["gst_rate"], row["sac_code"]) == (D("18.00"), "998314")
    dedicated = plans["dedicated"]["description"]
    assert dedicated == (
        "A managed, isolated SchoolOS environment with your own domain, a dedicated database "
        "and a documented data export."
    )
    assert "server" not in dedicated.lower()
    assert set(bundles) == set(BUNDLES)
    for code, (name, answers, price, rate) in BUNDLES.items():
        row = bundles[code]
        assert (row["name"], row["included_answers"], row["price_inr"]) == (name, answers, price)
        assert row["overage_rate_inr"] == rate


def test_FR_PLT_010_bundles_are_frozen(platform_engine: Engine) -> None:
    from sqlalchemy.exc import DBAPIError

    with platform_session() as s:
        bid: Any = s.execute(text("SELECT id FROM platform.ai_bundles LIMIT 1")).scalar_one()
    for sql in (
        "UPDATE platform.ai_bundles SET price_inr = 1 WHERE id = :i",
        "DELETE FROM platform.ai_bundles WHERE id = :i",
    ):
        with (
            pytest.raises(DBAPIError, match=r"immutable|cannot be deleted"),
            platform_session() as s,
        ):
            s.execute(text(sql), {"i": bid})


def test_FR_PLT_010_plans_api_shows_one_time_fee_and_bundles(api: Api, owner: Operator) -> None:
    plans = api.call("GET", "/plans?status=published", owner).json()["data"]
    shared = [p for p in plans if p["code"] == "shared"]
    assert shared
    assert shared[0]["one_time_fee_inr"] == "15000.00"
    res = api.call("GET", "/ai-bundles", owner)
    assert res.status_code == 200, res.text
    got = {b["code"]: b for b in res.json()["data"]}
    assert got["ai-standard"]["included_answers"] == 1000
    assert got["ai-standard"]["price_inr"] == "1499.00"
    assert got["ai-standard"]["overage_rate_inr"] == "1.50"


def test_FR_PLT_010_plan_accepts_a_one_time_fee(api: Api, owner: Operator) -> None:
    from .conftest import plan_payload

    body = plan_payload(one_time_fee_inr="12000.00", description="Synthetic plan wording.")
    res = api.call("POST", "/plans", owner, json=body)
    assert res.status_code == 201, res.text
    assert (res.json()["one_time_fee_inr"], res.json()["description"]) == (
        "12000.00",
        "Synthetic plan wording.",
    )
    patched = api.call("PATCH", f"/plans/{res.json()['id']}", owner, json={"one_time_fee_inr": "0"})
    assert patched.json()["one_time_fee_inr"] == "0.00"
    bad = api.call("POST", "/plans", owner, json={**body, "one_time_fee_inr": "-1"})
    assert bad.status_code == 422


# --- helpers ----------------------------------------------------------------------------------


def _school(api: Api, op: Operator, plan_id: uuid.UUID, **kw: Any) -> dict[str, Any]:
    state = kw.pop("state", "37")
    body = provision_payload(plan_id, start_as=kw.pop("start_as", "active"), **kw)
    body["billing_account"] = billing_account_payload(state_code=state)
    res = api.call("POST", "/tenants", op, json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _lines(invoice: Any) -> list[tuple[str, str, str]]:
    return [
        (ln.kind, f"{ln.quantity:f}".rstrip("0").rstrip("."), str(ln.amount_inr))
        for ln in invoice.lines
    ]


def _bundle_id(code: str) -> uuid.UUID:
    with platform_session() as s:
        return uuid.UUID(
            str(
                s.execute(
                    text(
                        "SELECT id FROM platform.ai_bundles WHERE code = :c AND status = "
                        "'published' ORDER BY version DESC LIMIT 1"
                    ),
                    {"c": code},
                ).scalar_one()
            )
        )


def _answers(tenant_id: uuid.UUID, month: dt.date, per_day: list[int]) -> None:
    """Daily AI answer counts for ``month`` (as the collector or a heartbeat records them)."""
    with platform_session() as s:
        for i, n in enumerate(per_day):
            repo.upsert_usage(
                s,
                {
                    "tenant_id": tenant_id,
                    "usage_date": month + dt.timedelta(days=i),
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
                    "ai_cost_usd": D("0"),
                    "ai_cost_inr": D("0"),
                },
            )


def _bundle_from(sub_id: uuid.UUID, month: dt.date) -> None:
    with platform_session() as s:
        s.execute(
            text("UPDATE platform.subscriptions SET ai_bundle_from = :m WHERE id = :i"),
            {"m": month, "i": sub_id},
        )


@pytest.fixture
def billing_admin(make_operator: MakeOperator) -> Operator:
    return make_operator("billing_admin")


# --- one-time fee -----------------------------------------------------------------------------


def test_FR_PLT_015_one_time_fee_on_the_first_invoice_only(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    plan = make_plan(base_price_inr="4999.00", one_time_fee_inr="15000.00")
    sub = uuid.UUID(_school(api, owner, plan)["subscription_id"])
    with platform_session() as s:
        first_id: Any = s.execute(
            text("SELECT id FROM platform.invoices WHERE subscription_id = :s"), {"s": sub}
        ).scalar_one()
    first = billing.get_invoice(first_id)
    assert _lines(first)[:2] == [
        ("subscription", "1", "4999.00"),
        ("one_time_fee", "1", "15000.00"),
    ]
    assert first.lines[1].description == "Implementation and data verification (one-time)"
    # GST 18% on the whole taxable value, intra-state (37 = 37): CGST + SGST.
    assert (first.taxable_value_inr, first.cgst_inr, first.sgst_inr, first.igst_inr) == (
        D("19999.00"),
        D("1799.91"),
        D("1799.91"),
        D("0.00"),
    )
    assert first.total_inr == D("23598.82")

    # Later invoices never repeat it, and a rerun of the monthly job creates nothing new.
    second = billing.create_manual_draft(billing_admin.actor, sub, dt.date(2072, 2, 1))
    assert [k for k, _, _ in _lines(second)] == ["subscription"]
    third = billing.create_manual_draft(billing_admin.actor, sub, dt.date(2072, 3, 1))
    assert "one_time_fee" not in [k for k, _, _ in _lines(third)]

    # A voided first invoice did not charge it: the next new invoice does, once.
    billing.issue_invoice(billing_admin.actor, first_id, today=dt.date(2091, 1, 5))
    billing.void_invoice(billing_admin.actor, first_id, "Synthetic correction")
    fourth = billing.create_manual_draft(billing_admin.actor, sub, dt.date(2072, 4, 1))
    assert ("one_time_fee", "1", "15000.00") in _lines(fourth)
    fifth = billing.create_manual_draft(billing_admin.actor, sub, dt.date(2072, 5, 1))
    assert "one_time_fee" not in [k for k, _, _ in _lines(fifth)]


def test_FR_PLT_015_trial_pays_the_fee_at_activation(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    plan = make_plan(one_time_fee_inr="49000.00", base_price_inr="9900.00")
    sub = uuid.UUID(_school(api, owner, plan, start_as="trial")["subscription_id"])
    with platform_session() as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM platform.invoices WHERE subscription_id = :s"),
                {"s": sub},
            ).scalar_one()
            == 0
        )
    res = api.call("POST", f"/subscriptions/{sub}/activate", billing_admin)
    assert res.status_code == 200, res.text
    invoices, _ = billing.list_invoices(tenant_id=uuid.UUID(res.json()["tenant_id"]))
    assert ("one_time_fee", "1", "49000.00") in _lines(invoices[0])


def test_FR_PLT_015_no_fee_line_when_the_plan_has_none(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = uuid.UUID(_school(api, owner, make_plan())["subscription_id"])
    invoices, _ = billing.list_invoices(tenant_id=billing.get_subscription(sub).tenant_id)
    assert [k for k, _, _ in _lines(invoices[0])] == ["subscription"]


# --- AI bundles and overage -------------------------------------------------------------------


def test_FR_PLT_015_bundle_and_overage_lines_with_gst(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    plan = make_plan(base_price_inr="4999.00")
    school = _school(api, owner, plan)
    sub, tenant = uuid.UUID(school["subscription_id"]), uuid.UUID(school["tenant_id"])
    res = api.call(
        "PUT",
        f"/subscriptions/{sub}/ai-bundle",
        billing_admin,
        json={"ai_bundle_id": str(_bundle_id("ai-standard"))},
    )
    assert res.status_code == 200, res.text
    assert res.json()["ai_bundle_id"] == str(_bundle_id("ai-standard"))
    # The bundle counts from the first full calendar month after it was chosen.
    today = today_ist()
    assert dt.date.fromisoformat(res.json()["ai_bundle_from"]) == billing.next_month(today)

    march = dt.date(2073, 3, 1)
    _bundle_from(sub, march)
    _answers(tenant, march, [600, 400, 250])  # 1,250 answers in March
    _answers(tenant, dt.date(2073, 2, 1), [5000])  # February: before the bundle counted
    april = billing.create_manual_draft(billing_admin.actor, sub, dt.date(2073, 4, 10))
    assert _lines(april) == [
        ("subscription", "1", "4999.00"),
        ("addon", "1", "1499.00"),
        ("usage_overage", "250", "375.00"),
    ]
    assert april.lines[1].description.startswith("AI answers: Standard bundle, 1,000 answers")
    assert april.lines[2].description == (
        "AI answers above the Standard bundle, March 2073: 250 extra answers "
        "\N{MULTIPLICATION SIGN} ₹1.50"
    )
    assert april.lines[2].usage_month == march
    assert (april.taxable_value_inr, april.cgst_inr, april.sgst_inr, april.igst_inr) == (
        D("6873.00"),
        D("618.57"),
        D("618.57"),
        D("0.00"),
    )
    assert april.total_inr == D("8110.14")

    # A second invoice starting in April never bills March again.
    again = billing.create_manual_draft(billing_admin.actor, sub, dt.date(2073, 4, 20))
    assert "usage_overage" not in [k for k, _, _ in _lines(again)]
    # February was before the bundle counted: no overage.
    march_inv = billing.create_manual_draft(billing_admin.actor, sub, dt.date(2073, 3, 10))
    assert "usage_overage" not in [k for k, _, _ in _lines(march_inv)]


def test_FR_PLT_017_overage_invoice_inter_state_is_igst(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    school = _school(api, owner, make_plan(base_price_inr="4999.00"), state="29")
    sub, tenant = uuid.UUID(school["subscription_id"]), uuid.UUID(school["tenant_id"])
    billing.set_ai_bundle(billing_admin.actor, sub, _bundle_id("ai-lite"))
    month = dt.date(2074, 6, 1)
    _bundle_from(sub, month)
    _answers(tenant, month, [310])
    inv = billing.create_manual_draft(billing_admin.actor, sub, dt.date(2074, 7, 1))
    assert _lines(inv) == [
        ("subscription", "1", "4999.00"),
        ("addon", "1", "699.00"),
        ("usage_overage", "10", "15.00"),
    ]
    assert (inv.tax_type, inv.igst_inr, inv.cgst_inr) == ("igst", D("1028.34"), D("0.00"))
    assert inv.total_inr == D("6741.34")


def test_FR_PLT_013_bundle_rules_and_removal(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan())["subscription_id"]
    path = f"/subscriptions/{sub}/ai-bundle"
    unknown = api.call("PUT", path, billing_admin, json={"ai_bundle_id": str(uuid.uuid4())})
    assert unknown.status_code == 422
    missing = api.call(
        "PUT",
        f"/subscriptions/{uuid.uuid4()}/ai-bundle",
        billing_admin,
        json={"ai_bundle_id": str(_bundle_id("ai-high"))},
    )
    assert missing.status_code == 404
    ok = api.call("PUT", path, billing_admin, json={"ai_bundle_id": str(_bundle_id("ai-high"))})
    assert ok.status_code == 200, ok.text
    removed = api.call("DELETE", path, billing_admin)
    assert removed.status_code == 200, removed.text
    assert (removed.json()["ai_bundle_id"], removed.json()["ai_bundle_from"]) == (None, None)
    with platform_session() as s:
        actions: Any = s.execute(
            text(
                "SELECT action FROM platform.audit_events WHERE resource_id = :s "
                "AND action LIKE 'subscription.ai_bundle%' ORDER BY seq"
            ),
            {"s": uuid.UUID(sub)},
        ).scalars()
        assert list(actions) == ["subscription.ai_bundle_set", "subscription.ai_bundle_removed"]


def test_FR_PLT_013_bundle_needs_a_monthly_plan(
    api: Api, billing_admin: Operator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    sub = _school(api, owner, make_plan(billing_period="annual"))["subscription_id"]
    res = api.call(
        "PUT",
        f"/subscriptions/{sub}/ai-bundle",
        billing_admin,
        json={"ai_bundle_id": str(_bundle_id("ai-lite"))},
    )
    assert (res.status_code, res.json()["code"]) == (409, "ai_bundle_needs_monthly_plan")


def test_FR_PLT_028_bundle_routes_authz(
    api: Api, make_operator: MakeOperator, make_plan: Callable[..., uuid.UUID]
) -> None:
    admin = make_operator("billing_admin")
    sub = _school(api, make_operator("platform_owner"), make_plan())["subscription_id"]
    body = {"ai_bundle_id": str(_bundle_id("ai-lite"))}
    for role in ("platform_engineer", "support_agent", "platform_viewer"):
        op = make_operator(role)
        assert api.call("PUT", f"/subscriptions/{sub}/ai-bundle", op, json=body).status_code == 403
        assert api.call("DELETE", f"/subscriptions/{sub}/ai-bundle", op).status_code == 403
    assert api.call("GET", "/ai-bundles", make_operator("platform_viewer")).status_code == 200
    assert api.call("GET", "/ai-bundles", make_operator("support_agent")).status_code == 403
    stale = api.call("PUT", f"/subscriptions/{sub}/ai-bundle", admin, json=body, fresh=False)
    assert stale.status_code == 428


# --- metering ---------------------------------------------------------------------------------


def test_FR_PLT_020_ai_answers_are_counted_in_the_schools_own_session(
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    tid = uuid.UUID(_school(api, owner, make_plan())["tenant_id"])
    other = uuid.UUID(_school(api, owner, make_plan())["tenant_id"])
    day = dt.date(2075, 1, 15)
    noon = dt.datetime.combine(day, dt.time(12), ist)
    rows = [
        (tid, "answered", noon),
        (tid, "answered", noon),
        (tid, "not_found", noon),
        (tid, "refused", noon),
        (tid, "search_only", noon),
        (tid, "answered", noon - dt.timedelta(days=1)),  # the day before
        (other, "answered", noon),  # another school
    ]
    with admin_engine.begin() as c:
        for tenant, status, at in rows:
            qid = uuid.uuid4()
            c.execute(
                text(
                    "INSERT INTO kb.queries (id, tenant_id, session_id, user_id, "
                    "question_ciphertext, question_hmac, key_version, mode, status, created_at) "
                    "VALUES (:i, :t, :s, :u, :q, :h, 1, :m, :st, :at)"
                ),
                {
                    "i": qid,
                    "t": tenant,
                    "s": uuid.uuid4(),
                    "u": uuid.uuid4(),
                    "q": b"synthetic-ciphertext",
                    "h": hashlib.sha256(qid.bytes).digest(),
                    "m": "search_only" if status == "search_only" else "full",
                    "st": status,
                    "at": at,
                },
            )
    values = usage.collect_tenant(tid, day)
    assert (values["ai_queries"], values["ai_answers"]) == (5, 2)
    with platform_session() as s:
        stored: Any = s.execute(
            text(
                "SELECT ai_answers FROM platform.usage_daily WHERE tenant_id = :t "
                "AND usage_date = :d"
            ),
            {"t": tid, "d": day},
        ).scalar_one()
    assert stored == 2
    assert billing.ai_answers_in_month(tid, dt.date(2075, 1, 1)) == 2


def test_FR_PLT_020_heartbeat_carries_ai_answers() -> None:
    from app.platform.schemas import HbUsage

    base: dict[str, Any] = {
        "date": "2075-01-15",
        "active_users": 1,
        "staff_users": 1,
        "students_active": 0,
        "storage_bytes": 0,
        "documents": 0,
        "ai_queries": 3,
        "ai_input_tokens": 0,
        "ai_output_tokens": 0,
        "ai_cost_usd": "0",
    }
    assert HbUsage.model_validate(base).ai_answers == 0  # hosts older than 0041
    assert HbUsage.model_validate({**base, "ai_answers": 2}).ai_answers == 2


# --- the school's AI budget follows its bundle (owner decision 2026-10-03; ADR-0020 B3) -------


def _allowance(admin: Engine, tenant_id: uuid.UUID) -> Any:
    with admin.connect() as c:
        settings: Any = c.execute(
            text("SELECT settings FROM core.tenants WHERE id = :t"), {"t": tenant_id}
        ).scalar_one()
    return dict(settings or {}).get("ai_answers_per_month")


def test_FR_KB_011_bundle_sets_the_schools_ai_answer_allowance(
    api: Api,
    billing_admin: Operator,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
) -> None:
    school = _school(api, owner, make_plan())
    sub, tid = school["subscription_id"], uuid.UUID(school["tenant_id"])
    path = f"/subscriptions/{sub}/ai-bundle"
    assert _allowance(admin_engine, tid) is None
    for code, answers in (("ai-standard", 1000), ("ai-high", 3000)):
        res = api.call("PUT", path, billing_admin, json={"ai_bundle_id": str(_bundle_id(code))})
        assert res.status_code == 200, res.text
        assert _allowance(admin_engine, tid) == answers
    assert api.call("DELETE", path, billing_admin).status_code == 200
    assert _allowance(admin_engine, tid) is None
    # The school's own audit chain records each change (system actor, the count only).
    with admin_engine.connect() as c:
        rows: Any = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t "
                "AND action = 'tenant.ai_allowance_set' ORDER BY occurred_at, id"
            ),
            {"t": tid},
        ).scalars()
        summaries = [dict(r) for r in rows]
    assert [s_["included_answers"] for s_ in summaries] == [1000, 3000, 0]


def test_FR_KB_011_a_failed_allowance_write_does_not_fail_the_operator_and_is_reconciled(  # noqa: PLR0917 - pytest fixtures
    api: Api,
    billing_admin: Operator,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from structlog.testing import capture_logs

    from app.core.logging import ALLOWED_FIELDS
    from app.tenancy import service as tenancy_service

    school = _school(api, owner, make_plan())
    sub, tid = school["subscription_id"], uuid.UUID(school["tenant_id"])
    real = tenancy_service.set_ai_answer_allowance

    def down(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("synthetic outage")

    monkeypatch.setattr(tenancy_service, "set_ai_answer_allowance", down)
    with capture_logs() as logs:
        res = api.call(
            "PUT",
            f"/subscriptions/{sub}/ai-bundle",
            billing_admin,
            json={"ai_bundle_id": str(_bundle_id("ai-lite"))},
        )
    assert res.status_code == 200, res.text  # the bundle is set; the school catches up later
    assert _allowance(admin_engine, tid) is None
    (failed,) = [e for e in logs if e["event"] == "platform.ai_allowance.sync_failed"]
    assert set(failed) - {"event", "log_level", "exc_info"} <= set(ALLOWED_FIELDS)
    monkeypatch.setattr(tenancy_service, "set_ai_answer_allowance", real)
    # The daily collector reconciles every live shared school from its subscription (the school
    # goes live after provisioning; set here directly).
    with admin_engine.begin() as c:
        c.execute(text("UPDATE core.tenants SET status = 'active' WHERE id = :t"), {"t": tid})
    usage.collect_daily(dt.date(2075, 2, 1))
    assert _allowance(admin_engine, tid) == 300
    # And it repairs drift (e.g. a restored settings backup).
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenants SET settings = settings - 'ai_answers_per_month' WHERE id = :t"
            ),
            {"t": tid},
        )
    assert billing.sync_ai_allowance(tid) is True
    assert _allowance(admin_engine, tid) == 300
    assert billing.sync_ai_allowance(tid) is False  # nothing to change
