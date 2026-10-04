"""Schema ``platform`` (migration 0005_platform): grants, CHECKs, triggers and the two definer
functions it adds (SEC-026, SEC-029, FR-PLT-002, FR-PLT-010, FR-PLT-016, FR-PLT-030).

All data is synthetic. Rows are written as ``sos_platform`` (the control-plane role) or, for
core tables, with the admin engine (test setup only).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app.core.db import context_free_session, platform_session, tenant_session

from .conftest import letters

pytestmark = pytest.mark.db


# --- helpers ---------------------------------------------------------------------------------


def _operator(status: str = "active") -> uuid.UUID:
    oid = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.operators (id, idp_subject, email, display_name, status, "
                "mfa_enrolled) VALUES (:i, :s, :e, 'Synthetic Operator', :st, true)"
            ),
            {"i": oid, "s": f"op-{oid}", "e": f"op-{oid.hex[:10]}@example.test", "st": status},
        )
    return oid


def _deployment(tenant_id: uuid.UUID, mode: str = "shared") -> uuid.UUID:
    did = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, mode, "
                "tenant_status, status, heartbeat_key_id, heartbeat_key_ciphertext) "
                "VALUES (:i, :t, :c, 'Synthetic School', :m, 'active', 'healthy', :k, :kc)"
            ),
            {
                "i": did,
                "t": tenant_id,
                "c": f"t-{letters(12)}",
                "m": mode,
                "k": "hb-1" if mode == "dedicated" else None,
                "kc": b"\x01" * 16 if mode == "dedicated" else None,
            },
        )
    return did


def _plan(operator: uuid.UUID, status: str = "published") -> uuid.UUID:
    pid = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.plans (id, code, version, name, tier, billing_period, "
                "pricing_model, base_price_inr, sac_code, limits, status, published_at, "
                "created_by) VALUES (:i, :c, 1, 'Synthetic Plan', 'shared', 'monthly', 'flat', "
                "5000.00, '998314', '{\"students\": 1000}', :st, "
                "CASE WHEN :st = 'draft' THEN NULL ELSE now() END, :o)"
            ),
            {"i": pid, "c": f"p-{letters(10)}", "st": status, "o": operator},
        )
    return pid


def _billing_account(tenant_id: uuid.UUID) -> uuid.UUID:
    bid = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.billing_accounts (id, tenant_id, legal_name, billing_email, "
                "address_line1, city, postal_code, state_code) VALUES (:i, :t, 'Synthetic Trust', "
                "'billing@example.test', '1 Test Road', 'Vijayawada', '520001', '37')"
            ),
            {"i": bid, "t": tenant_id},
        )
    return bid


def _subscription(tenant_id: uuid.UUID, plan: uuid.UUID, account: uuid.UUID) -> uuid.UUID:
    sid = uuid.uuid4()
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.subscriptions (id, tenant_id, billing_account_id, plan_id, "
                "status, current_period_start, current_period_end) "
                "VALUES (:i, :t, :b, :p, 'active', :s, :e)"
            ),
            {
                "i": sid,
                "t": tenant_id,
                "b": account,
                "p": plan,
                "s": date(2026, 10, 1),
                "e": date(2026, 11, 1),
            },
        )
    return sid


def _invoice(tenant_id: uuid.UUID, sub: uuid.UUID, account: uuid.UUID) -> uuid.UUID:
    iid = uuid.uuid4()
    params: dict[str, object] = {
        "i": iid,
        "t": tenant_id,
        "s": sub,
        "b": account,
        "ps": date(2026, 10, 1),
        "taxable": Decimal("100.00"),
        "cgst": Decimal("9.00"),
        "sgst": Decimal("9.00"),
        "total": Decimal("118.00"),
    }
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.invoices (id, tenant_id, subscription_id, "
                "billing_account_id, status, period_start, period_end, supplier_legal_name, "
                "supplier_gstin, "
                "supplier_state_code, recipient_legal_name, recipient_address, "
                "place_of_supply_state_code, tax_type, taxable_value_inr, cgst_inr, sgst_inr, "
                "total_inr) VALUES (:i, :t, :s, :b, 'draft', :ps, CAST(:ps AS date) + 30, "
                "'Synthetic Supplier', '37AAAAA0000A1Z5', '37', 'Synthetic Trust', '{}', '37', "
                "'cgst_sgst', :taxable, :cgst, :sgst, :total)"
            ),
            params,
        )
    return iid


def _tenant(admin: Engine, status: str = "provisioning") -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'School', :s)"),
            {"i": tid, "c": f"t-{letters(12)}", "s": status},
        )
    return tid


@pytest.fixture
def engines(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db's app and platform engines to the test database."""


# --- privilege separation (SEC-026) -----------------------------------------------------------


def test_SEC_026_platform_role_cannot_read_tenant_tables_live(engines: None) -> None:
    for table in ("core.memberships", "core.users", "audit.events", "ops.outbox"):
        with pytest.raises(ProgrammingError, match="permission denied"), platform_session() as s:
            s.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


def test_SEC_026_app_role_reads_only_feature_flags(engines: None) -> None:
    with context_free_session() as s:
        s.execute(text("SELECT count(*) FROM platform.feature_flags")).scalar_one()
    for table in ("platform.invoices", "platform.subscriptions", "platform.operators"):
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            context_free_session() as s,
        ):
            s.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
    with pytest.raises(ProgrammingError, match="permission denied"), context_free_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.feature_flags (id, key, enabled) "
                "VALUES (gen_random_uuid(), 'x.y', true)"
            )
        )


def test_SEC_026_readonly_role_has_nothing_on_platform(readonly_engine: Engine) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), readonly_engine.begin() as c:
        c.execute(text("SELECT 1 FROM platform.feature_flags LIMIT 1"))


# --- two-person rules (SEC-029) ---------------------------------------------------------------


def test_SEC_029_offboarding_approver_must_differ_from_requester_db_check(engines: None) -> None:
    op = _operator()
    did = _deployment(uuid.uuid4())
    with (
        pytest.raises(IntegrityError, match="deployments_offboard_two_person"),
        platform_session() as s,
    ):
        s.execute(
            text(
                "UPDATE platform.deployments SET offboard_requested_by = :o, "
                "offboard_requested_at = now(), offboard_approved_by = :o, "
                "offboard_approved_at = now() WHERE id = :d"
            ),
            {"o": op, "d": did},
        )


def test_SEC_029_emergency_breakglass_needs_two_distinct_confirmers(engines: None) -> None:
    op = _operator()
    with pytest.raises(IntegrityError, match="breakglass_two_person"), platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.breakglass_requests (id, tenant_id, requested_by, "
                "reason_code, reason, scope, duration_minutes, emergency, status, "
                "emergency_confirmed_by_1, emergency_confirmed_by_2, emergency_confirmed_at) "
                "VALUES (gen_random_uuid(), gen_random_uuid(), :o, 'security_incident', "
                "'synthetic incident reason', '{}', 60, true, 'approved', :o, :o, now())"
            ),
            {"o": op},
        )


def test_FR_PLT_028_operator_cannot_grant_itself_a_role(engines: None) -> None:
    op = _operator()
    with (
        pytest.raises(IntegrityError, match="operator_roles_no_self_grant"),
        platform_session() as s,
    ):
        s.execute(
            text(
                "INSERT INTO platform.operator_roles (operator_id, role_key, granted_by) "
                "VALUES (:o, 'platform_owner', :o)"
            ),
            {"o": op},
        )


def test_FR_PLT_028_active_operator_needs_mfa(engines: None) -> None:
    with pytest.raises(IntegrityError, match="operators_active_needs_mfa"), platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.operators (id, idp_subject, email, display_name, status, "
                "mfa_enrolled) VALUES (gen_random_uuid(), 'sub-no-mfa', 'nomfa@example.test', "
                "'No MFA', 'active', false)"
            )
        )


# --- immutability (FR-PLT-010, FR-PLT-016) ----------------------------------------------------


def test_FR_PLT_010_published_plan_prices_cannot_change(engines: None) -> None:
    pid = _plan(_operator())
    with pytest.raises(DBAPIError, match="immutable"), platform_session() as s:
        s.execute(text("UPDATE platform.plans SET base_price_inr = 1 WHERE id = :p"), {"p": pid})
    with platform_session() as s:  # retiring is allowed
        s.execute(text("UPDATE platform.plans SET status = 'retired' WHERE id = :p"), {"p": pid})
    with pytest.raises(DBAPIError, match="cannot be deleted"), platform_session() as s:
        s.execute(text("DELETE FROM platform.plans WHERE id = :p"), {"p": pid})


def test_FR_PLT_010_plan_row_version_defaults_to_1_and_freezes_with_the_plan(
    engines: None,
) -> None:
    """0043_plan_row_version: the edit counter starts at 1, stays >= 1, and the freeze trigger
    keeps it fixed once the plan is published."""
    operator = _operator()
    draft = _plan(operator, status="draft")
    with platform_session() as s:
        row_version = s.execute(
            text("SELECT row_version FROM platform.plans WHERE id = :p"), {"p": draft}
        ).scalar_one()
        assert row_version == 1
        s.execute(text("UPDATE platform.plans SET row_version = 2 WHERE id = :p"), {"p": draft})
    with pytest.raises(IntegrityError, match="plans_row_version"), platform_session() as s:
        s.execute(text("UPDATE platform.plans SET row_version = 0 WHERE id = :p"), {"p": draft})
    published = _plan(operator)
    with pytest.raises(DBAPIError, match="immutable"), platform_session() as s:
        s.execute(
            text("UPDATE platform.plans SET row_version = 2 WHERE id = :p"), {"p": published}
        )


def _issue_raw(invoice: uuid.UUID, number: str, fy: str = "2090-91", seq: int = 1) -> None:
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.invoice_sequences (financial_year) VALUES (:fy) "
                "ON CONFLICT DO NOTHING"
            ),
            {"fy": fy},
        )
        s.execute(
            text(
                "UPDATE platform.invoices SET status = 'issued', invoice_number = :n, "
                "financial_year = :fy, sequence_no = :q, issue_date = DATE '2090-06-01', "
                "due_date = DATE '2090-06-16', issued_at = now() WHERE id = :i"
            ),
            {"n": number, "fy": fy, "q": seq, "i": invoice},
        )


def test_FR_PLT_016_issued_invoice_is_frozen(engines: None) -> None:
    op = _operator()
    tenant = uuid.uuid4()
    _deployment(tenant)
    account = _billing_account(tenant)
    sub = _subscription(tenant, _plan(op), account)
    inv = _invoice(tenant, sub, account)
    seq = int(uuid.uuid4().int % 900000) + 1
    _issue_raw(inv, f"SOS/90-91/{seq:06d}", seq=seq)
    with pytest.raises(DBAPIError, match="immutable"), platform_session() as s:
        s.execute(
            text("UPDATE platform.invoices SET recipient_legal_name = 'x' WHERE id = :i"),
            {"i": inv},
        )
    with pytest.raises(DBAPIError, match="cannot change"), platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.invoice_lines (id, invoice_id, line_no, kind, description, "
                "sac_code, quantity, unit_price_inr, amount_inr, gst_rate) VALUES "
                "(gen_random_uuid(), :i, 1, 'addon', 'x', '998314', 1, 1, 1, 18)"
            ),
            {"i": inv},
        )
    with pytest.raises(DBAPIError, match="only draft"), platform_session() as s:
        s.execute(text("DELETE FROM platform.invoices WHERE id = :i"), {"i": inv})
    with platform_session() as s:  # payments may still be recorded
        s.execute(
            text(
                "UPDATE platform.invoices SET amount_paid_inr = 118, status = 'paid' WHERE id = :i"
            ),
            {"i": inv},
        )


def test_FR_PLT_016_invoice_number_at_most_16_characters(engines: None) -> None:
    op = _operator()
    tenant = uuid.uuid4()
    _deployment(tenant)
    account = _billing_account(tenant)
    inv = _invoice(tenant, _subscription(tenant, _plan(op), account), account)
    with pytest.raises(IntegrityError, match="invoice_number"):
        _issue_raw(inv, "SOS/2090-91/000001", seq=999_999)


def test_FR_PLT_017_tax_split_is_checked(engines: None) -> None:
    op = _operator()
    tenant = uuid.uuid4()
    _deployment(tenant)
    account = _billing_account(tenant)
    sub = _subscription(tenant, _plan(op), account)
    inv = _invoice(tenant, sub, account)
    with pytest.raises(IntegrityError, match="invoices_tax"), platform_session() as s:
        s.execute(
            text("UPDATE platform.invoices SET place_of_supply_state_code = '29' WHERE id = :i"),
            {"i": inv},
        )


# --- core.current_subscription (FR-PLT-030) ---------------------------------------------------


def test_FR_PLT_030_current_subscription_returns_only_own_tenant(
    admin_engine: Engine, engines: None
) -> None:
    op = _operator()
    tenants = [_tenant(admin_engine, "active"), _tenant(admin_engine, "active")]
    subs = {}
    for t in tenants:
        _deployment(t)
        account = _billing_account(t)
        subs[t] = _subscription(t, _plan(op), account)
        inv = _invoice(t, subs[t], account)
        seq = int(uuid.uuid4().int % 900000) + 1
        _issue_raw(inv, f"SOS/90-91/{seq:06d}", seq=seq)
    for t in tenants:
        with tenant_session(t) as s:
            result: Any = s.execute(text("SELECT core.current_subscription()")).scalar_one()
        assert result["subscription_id"] == str(subs[t])
        assert len(result["invoices"]) == 1
        assert result["invoices"][0]["amount_due_inr"] == 118.0
        assert "gstin" not in str(result)
    with context_free_session() as s:  # no tenant context -> nothing
        assert s.execute(text("SELECT core.current_subscription()")).scalar_one() is None


def test_FR_PLT_030_current_subscription_denied_to_platform_role(engines: None) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), platform_session() as s:
        s.execute(text("SELECT core.current_subscription()"))


# --- core.create_owner_invite (FR-PLT-002) ----------------------------------------------------


def _invite(tenant: uuid.UUID, subject: str | None = None) -> tuple[uuid.UUID, uuid.UUID, bool]:
    with platform_session() as s:
        row = s.execute(
            text(
                "SELECT * FROM core.create_owner_invite(:t, :s, 'Synthetic Owner', "
                "'owner@example.test', 'te')"
            ),
            {"t": tenant, "s": subject or f"owner-{uuid.uuid4()}"},
        ).one()
    return row.user_id, row.membership_id, row.owner_role_assigned


def test_FR_PLT_002_owner_invite_creates_invited_membership(
    admin_engine: Engine, engines: None
) -> None:
    tenant = _tenant(admin_engine)
    user_id, membership_id, assigned = _invite(tenant)
    assert assigned is False  # no role templates cloned yet
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT status, mfa_required, user_id FROM core.memberships WHERE id = :m"),
            {"m": membership_id},
        ).one()
        scopes: Any = c.execute(
            text("SELECT scope_type FROM core.membership_scopes WHERE membership_id = :m"),
            {"m": membership_id},
        ).scalars()
        assert list(scopes) == ["school"]
    assert (row.status, row.mfa_required, row.user_id) == ("invited", True, user_id)


def test_FR_PLT_002_owner_invite_assigns_owner_role_when_present(
    admin_engine: Engine, engines: None
) -> None:
    tenant = _tenant(admin_engine)
    role = uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te, is_system) "
                "VALUES (:r, :t, 'owner', 'Owner', 'యజమాని', true)"
            ),
            {"r": role, "t": tenant},
        )
    _user, membership, assigned = _invite(tenant)
    assert assigned is True
    with admin_engine.connect() as c:
        got: Any = c.execute(
            text("SELECT role_id FROM core.membership_roles WHERE membership_id = :m"),
            {"m": membership},
        ).scalar_one()
    assert got == role


def test_FR_PLT_002_owner_invite_guards(admin_engine: Engine, engines: None) -> None:
    active = _tenant(admin_engine, "active")
    with pytest.raises(DBAPIError, match="being provisioned"):
        _invite(active)
    fresh = _tenant(admin_engine)
    _invite(fresh)
    with pytest.raises(DBAPIError, match="already has members"):
        _invite(fresh)
    with pytest.raises(DBAPIError, match="tenant not found"):
        _invite(uuid.uuid4())


def test_FR_PLT_002_owner_invite_reuses_existing_user_by_subject(
    admin_engine: Engine, engines: None
) -> None:
    subject = f"owner-{uuid.uuid4()}"
    first, _m, _a = _invite(_tenant(admin_engine), subject)
    second, _m2, _a2 = _invite(_tenant(admin_engine), subject)
    assert first == second


def test_ADR_0013_owner_invite_denied_to_app(admin_engine: Engine, engines: None) -> None:
    tenant = _tenant(admin_engine)
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(tenant) as s,
    ):
        s.execute(
            text("SELECT * FROM core.create_owner_invite(:t, 'x', 'y', NULL, 'en')"),
            {"t": tenant},
        )
