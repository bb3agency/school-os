"""Fee routes for the security suites (authorization matrix, BOLA): request builders for school A
and fee objects of school B, created through the real services (synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py`` and ``tests/security/test_bola.py``.
Fees are switched on for a school only around each call (``fees_on``), as the Tally connector
flag is, so the shared world stays as other suites expect it.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import itertools
import sys
import uuid
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine

from app.fees import service as fees
from app.fees.schemas import (
    AdjustmentCreate,
    CancellationRequest,
    ConcessionCreate,
    ConcessionTarget,
    DecisionIn,
    FeePlanCreate,
    InstalmentIn,
    PlanLineIn,
    RefundCreate,
)

_HERE = Path(__file__).resolve()


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


F = _load("sos_test_fees_support", _HERE.with_name("support.py"))
P = _load("sos_test_fees_online_support", _HERE.with_name("online_support.py"))
W = F.W

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]
REASON = "Synthetic matrix reason for this fee request"
_seq = itertools.count(1)
_days = itertools.count(1)


def is_fee_path(path: str) -> bool:
    return path.startswith(("/api/v1/fees/", "/api/v1/payments/")) or path.endswith("/fee-accounts")


@contextlib.contextmanager
def on(admin: Engine, tenant_id: uuid.UUID, path: str) -> Iterator[None]:
    if not is_fee_path(path):
        yield
        return
    with F.fees_on(admin, tenant_id):
        yield


def ensure_people(admin: Engine, school: Any) -> None:
    """School B of the shared world has only an owner: add the fee roles once."""
    for role in ("principal", "office_staff", "accountant", "office_admin"):
        if role not in school.people:
            school.people[role] = W.add_member(admin, school.tenant_id, [role])


# --- school A objects (shared per session unless fresh) ------------------------------------------


def _shared(w: Any, key: str, make: Callable[[], uuid.UUID]) -> uuid.UUID:
    if key not in w.a.ids:
        w.a.ids[key] = make()
    value: uuid.UUID = w.a.ids[key]
    return value


def _account(w: Any) -> uuid.UUID:
    return _shared(w, "fees_matrix_account", lambda: F.account(w.a))


def _receipt(w: Any, *, fresh: bool = False, role: str = "office_staff") -> Any:
    out = F.issue(w.a, _account(w), "1.00", role=role)
    return out


def _shared_receipt(w: Any, admin: Engine) -> uuid.UUID:
    def make() -> uuid.UUID:
        r = _receipt(w)
        fees.render_pdf(
            w.a.tenant_id, r.id, w.a.people["office_staff"].user_id, renderer=F.FakeRenderer()
        )
        doc = F.scalar(admin, "SELECT document_id FROM fin.receipts WHERE id = :i", i=r.id)
        with admin.begin() as c:
            from sqlalchemy import text

            c.execute(
                text("UPDATE kb.document_versions SET status = 'ready' WHERE document_id = :d"),
                {"d": doc},
            )
        value: uuid.UUID = r.id
        return value

    return _shared(w, "fees_matrix_receipt", make)


def _maker(role: str, makers: tuple[str, ...]) -> str:
    """A requester who is not the caller (maker-checker), from roles allowed to ask."""
    return next(m for m in makers if m != role)


def _cancellation(w: Any, role: str, *, own: bool = False) -> Any:
    maker = (
        role
        if own and role in ("principal", "office_admin", "office_staff", "accountant")
        else _maker(role, ("office_staff", "accountant"))
    )
    r = _receipt(w, role="office_staff")
    return F.call(
        w.a,
        maker,
        fees.request_cancellation,
        r.id,
        CancellationRequest(reason_code="other", reason=REASON),
    )


def _concession(w: Any, role: str, *, own: bool = False) -> Any:
    setup = F.ensure_setup(w.a)
    maker = (
        role
        if own and role in ("principal", "office_admin", "accountant")
        else _maker(role, ("office_admin", "accountant"))
    )
    return F.call(
        w.a,
        maker,
        fees.request_concession,
        _account(w),
        ConcessionCreate(
            concession_type="other",
            basis="amount",
            value=Decimal("1.00"),
            targets=[ConcessionTarget(instalment_no=2, head_id=setup.tui)],
            reason=REASON,
        ),
    )


def _adjustment(w: Any, role: str, *, own: bool = False) -> Any:
    setup = F.ensure_setup(w.a)
    maker = (
        role
        if own and role in ("principal", "accountant")
        else _maker(role, ("accountant", "principal"))
    )
    return F.call(
        w.a,
        maker,
        fees.request_adjustment,
        _account(w),
        AdjustmentCreate(
            instalment_no=2,
            head_id=setup.tui,
            amount=Decimal("1.00"),
            reason_code="other",
            reason=REASON,
        ),
    )


def _refund(w: Any, role: str, *, own: bool = False) -> Any:
    maker = (
        role
        if own and role in ("principal", "accountant")
        else _maker(role, ("accountant", "principal"))
    )
    r = _receipt(w)
    return F.call(
        w.a,
        maker,
        fees.request_refund,
        r.id,
        RefundCreate(amount=Decimal("1.00"), mode="cash", reason=REASON),
    )


def _shared_voucher(w: Any) -> uuid.UUID:
    def make() -> uuid.UUID:
        refund = _refund(w, "office_staff")
        out = F.call(
            w.a, "principal", fees.approve_refund, refund.id, DecisionIn(), expected_version=None
        )
        value: uuid.UUID = out.id
        return value

    return _shared(w, "fees_matrix_voucher", make)


def _draft(w: Any) -> Any:
    setup = F.ensure_setup(w.a)
    return F.call(
        w.a,
        "accountant",
        fees.create_plan,
        FeePlanCreate(
            academic_year_id=w.a.ids["year"],
            class_id=w.a.ids["class_x"],
            category_code=f"m{next(_seq)}_{uuid.uuid4().hex[:6]}",
            name="Matrix plan",
            lines=[PlanLineIn(head_id=setup.tui, annual_amount=Decimal("100.00"))],
            instalments=[
                InstalmentIn(
                    instalment_no=1,
                    label="T1",
                    due_on=setup.second_due,
                    amounts={setup.tui: Decimal("100.00")},
                )
            ],
        ),
    )


def _active(w: Any) -> Any:
    d = _draft(w)
    return F.call(w.a, "accountant", fees.activate_plan, d.id, expected_version=d.version)


def _new_version_active(w: Any) -> Any:
    plan = _active(w)
    d = F.call(w.a, "accountant", fees.new_version, plan.id)
    return F.call(w.a, "accountant", fees.activate_plan, d.id, expected_version=d.version)


def _ifm(version: int) -> dict[str, str]:
    return {"If-Match": f'W/"{version}"'}


def _plan_body(w: Any) -> dict[str, Any]:
    setup = F.ensure_setup(w.a)
    return {
        "academic_year_id": str(w.a.ids["year"]),
        "class_id": str(w.a.ids["class_x"]),
        "category_code": f"n{next(_seq)}_{uuid.uuid4().hex[:6]}",
        "name": "Matrix plan",
        "lines": [{"head_id": str(setup.tui), "annual_amount": "100.00"}],
        "instalments": [
            {
                "instalment_no": 1,
                "label": "T1",
                "due_on": str(setup.second_due),
                "amounts": {str(setup.tui): "100.00"},
            }
        ],
    }


def _settings_put(w: Any, r: str, a: Engine) -> Request:
    current = F.call(w.a, "accountant", fees.get_settings)
    body = {
        "paper_size": "A5",
        "gst_note": "Education services: exempt from GST",
        "reminder_offsets": [-3, 0, 7],
        "min_reminder_amount": "0.00",
    }
    return "/api/v1/fees/settings", body, _ifm(current.version)


def _head_patch(w: Any, r: str, a: Engine) -> Request:
    setup = F.ensure_setup(w.a)
    head = next(h for h in F.call(w.a, "accountant", fees.list_heads) if h.id == setup.exm)
    return f"/api/v1/fees/heads/{head.id}", {"sort_order": next(_seq) % 1000}, _ifm(head.version)


def _plan_patch(w: Any, r: str, a: Engine) -> Request:
    d = _draft(w)
    return f"/api/v1/fees/plans/{d.id}", {"name": "Matrix plan edited"}, _ifm(d.version)


def _plan_activate(w: Any, r: str, a: Engine) -> Request:
    d = _draft(w)
    return f"/api/v1/fees/plans/{d.id}/activate", None, _ifm(d.version)


def _decide(kind: str, action: str) -> Builder:
    make = {
        "cancellation": _cancellation,
        "concession": _concession,
        "adjustment": _adjustment,
        "refund": _refund,
    }[kind]
    plural = {
        "cancellation": "cancellations",
        "concession": "concessions",
        "adjustment": "adjustments",
        "refund": "refunds",
    }[kind]

    def build(w: Any, r: str, a: Engine) -> Request:
        row = make(w, r, own=action == "withdraw")
        body: dict[str, Any] | None = None
        if action == "approve":
            body = {}
        elif action == "reject":
            body = {"reason": "Synthetic matrix rejection reason"}
        return f"/api/v1/fees/{plural}/{row.id}/{action}", body, {}

    return build


def _day_close(w: Any, r: str, a: Engine) -> Request:
    day = F.today() - dt.timedelta(days=next(_days))
    return "/api/v1/fees/day-closes", {"date": str(day)}, {}


# --- R2-2D online payments (school A connected with synthetic manual keys) ----------------------


def _connection_live(w: Any) -> bool:
    return bool(
        F.scalar(
            _ADMIN_ENGINE[0],
            "SELECT count(*) FROM fin.payment_connections WHERE tenant_id = :t "
            "AND status = 'active'",
            t=w.a.tenant_id,
        )
    )


_ADMIN_ENGINE: list[Engine] = []


def _connected(w: Any, a: Engine) -> None:
    """School A has an active Razorpay connection (fake provider, synthetic keys)."""
    F.install()
    _ADMIN_ENGINE[:] = [a]
    if not _connection_live(w):
        P.connect(w.a)  # the caller has fees on for school A


def _disconnected(w: Any, a: Engine) -> None:
    del a
    F.install()
    P.disconnect_if_connected(w.a)


def _open_intent(w: Any, a: Engine) -> uuid.UUID:
    _connected(w, a)
    value: uuid.UUID = P.link(w.a, _account(w), [2]).id
    return value


def _open_item(w: Any) -> uuid.UUID:
    from app.core.db import tenant_session
    from app.fees import online

    with tenant_session(w.a.tenant_id) as s:
        online.open_exception(
            s,
            kind="paid_not_receipted",
            reason="no_receipt",
            dedupe=f"matrix{next(_seq)}{uuid.uuid4().hex[:8]}",
        )
        value: object = s.execute(
            __import__("sqlalchemy").text(
                "SELECT id FROM fin.reconciliation_items WHERE status = 'open' "
                "ORDER BY created_at DESC LIMIT 1"
            )
        ).scalar_one()
    return uuid.UUID(str(value))


def _create_link(w: Any, r: str, a: Engine) -> Request:
    _connected(w, a)
    return f"/api/v1/fees/accounts/{_account(w)}/payment-intents", {"instalments": [2]}, {}


def _online_specs() -> dict[tuple[str, str], Builder]:
    def connected(path: str, body: dict[str, Any] | None = None) -> Builder:
        def build(w: Any, r: str, a: Engine) -> Request:
            _connected(w, a)
            return path, body, {}

        return build

    def manual(w: Any, r: str, a: Engine) -> Request:
        _disconnected(w, a)
        return (
            "/api/v1/payments/connection/manual",
            {"key_id": P.key_id(), "key_secret": "synthetic-matrix-secret"},
            {},
        )

    return {
        ("GET", "/api/v1/payments/connection"): connected("/api/v1/payments/connection"),
        ("POST", "/api/v1/payments/connection/manual"): manual,
        ("POST", "/api/v1/payments/connection/oauth/start"): connected(
            "/api/v1/payments/connection/oauth/start"
        ),
        ("POST", "/api/v1/payments/connection/oauth/callback"): connected(
            "/api/v1/payments/connection/oauth/callback",
            {"code": "synthetic-code", "state": "s" * 43},
        ),
        ("POST", "/api/v1/payments/connection/test"): connected("/api/v1/payments/connection/test"),
        ("POST", "/api/v1/payments/connection/rotate-webhook-secret"): connected(
            "/api/v1/payments/connection/rotate-webhook-secret"
        ),
        ("DELETE", "/api/v1/payments/connection"): connected("/api/v1/payments/connection"),
        ("POST", "/api/v1/fees/accounts/{account_id}/payment-intents"): _create_link,
        ("GET", "/api/v1/fees/accounts/{account_id}/payment-intents"): lambda w, r, a: (
            f"/api/v1/fees/accounts/{_account(w)}/payment-intents",
            None,
            {},
        ),
        ("GET", "/api/v1/fees/payment-intents/{intent_id}"): lambda w, r, a: (
            "/api/v1/fees/payment-intents/"
            + str(_shared(w, "fees_matrix_intent", lambda: _open_intent(w, a))),
            None,
            {},
        ),
        ("POST", "/api/v1/fees/payment-intents/{intent_id}/cancel"): lambda w, r, a: (
            f"/api/v1/fees/payment-intents/{_open_intent(w, a)}/cancel",
            None,
            {},
        ),
        ("GET", "/api/v1/fees/reconciliation/online"): lambda w, r, a: (
            "/api/v1/fees/reconciliation/online",
            None,
            {},
        ),
        ("GET", "/api/v1/fees/reconciliation/online/summary"): lambda w, r, a: (
            "/api/v1/fees/reconciliation/online/summary",
            None,
            {},
        ),
        (
            "POST",
            "/api/v1/fees/reconciliation/online/items/{recon_item_id}/explain",
        ): lambda w, r, a: (
            f"/api/v1/fees/reconciliation/online/items/{_open_item(w)}/explain",
            {"note": "Synthetic matrix explanation note"},
            {},
        ),
    }


# Routes whose guard passes for its holders but whose service refuses in tests (no partner
# application configured: 409 oauth_not_available).
ONLINE_REFUSED = frozenset(
    {
        ("POST", "/api/v1/payments/connection/oauth/start"),
        ("POST", "/api/v1/payments/connection/oauth/callback"),
    }
)


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per fee route (school A)."""
    acc = _account

    def g(path: Callable[[Any, Engine], str]) -> Builder:
        return lambda w, r, a: (path(w, a), None, {})

    return {
        ("GET", "/api/v1/fees/status"): g(lambda w, a: "/api/v1/fees/status"),
        ("GET", "/api/v1/fees/settings"): g(lambda w, a: "/api/v1/fees/settings"),
        ("PUT", "/api/v1/fees/settings"): _settings_put,
        ("GET", "/api/v1/fees/heads"): g(lambda w, a: "/api/v1/fees/heads"),
        ("POST", "/api/v1/fees/heads"): lambda w, r, a: (
            "/api/v1/fees/heads",
            {
                "code": f"M{next(_seq)}{uuid.uuid4().hex[:4]}".upper()[:20],
                "name": "Matrix head",
                "kind": "other",
            },
            {},
        ),
        ("PATCH", "/api/v1/fees/heads/{head_id}"): _head_patch,
        ("GET", "/api/v1/fees/plans"): g(lambda w, a: "/api/v1/fees/plans"),
        ("POST", "/api/v1/fees/plans"): lambda w, r, a: ("/api/v1/fees/plans", _plan_body(w), {}),
        ("GET", "/api/v1/fees/plans/{plan_id}"): g(
            lambda w, a: f"/api/v1/fees/plans/{F.ensure_setup(w.a).plan_id}"
        ),
        ("PATCH", "/api/v1/fees/plans/{plan_id}"): _plan_patch,
        ("POST", "/api/v1/fees/plans/{plan_id}/activate"): _plan_activate,
        ("POST", "/api/v1/fees/plans/{plan_id}/new-version"): lambda w, r, a: (
            f"/api/v1/fees/plans/{_active(w).id}/new-version",
            None,
            {},
        ),
        ("POST", "/api/v1/fees/plans/{plan_id}/apply"): lambda w, r, a: (
            f"/api/v1/fees/plans/{F.ensure_setup(w.a).plan_id}/apply",
            {"dry_run": True},
            {},
        ),
        ("GET", "/api/v1/fees/plans/{plan_id}/differences"): g(
            lambda w, a: f"/api/v1/fees/plans/{F.ensure_setup(w.a).plan_id}/differences"
        ),
        ("POST", "/api/v1/fees/plans/{plan_id}/adjustments"): lambda w, r, a: (
            f"/api/v1/fees/plans/{_new_version_active(w).id}/adjustments",
            {"reason": REASON},
            {},
        ),
        ("POST", "/api/v1/fees/accounts/search"): lambda w, r, a: (
            "/api/v1/fees/accounts/search",
            {"query": ""},
            {},
        ),
        ("GET", "/api/v1/fees/accounts/{account_id}"): g(
            lambda w, a: f"/api/v1/fees/accounts/{acc(w)}"
        ),
        ("GET", "/api/v1/fees/accounts/{account_id}/statement"): g(
            lambda w, a: f"/api/v1/fees/accounts/{acc(w)}/statement"
        ),
        ("GET", "/api/v1/students/{student_id}/fee-accounts"): g(
            lambda w, a: (
                "/api/v1/students/"
                + str(
                    F.scalar(
                        a, "SELECT student_id FROM fin.student_fee_accounts WHERE id = :i", i=acc(w)
                    )
                )
                + "/fee-accounts"
            )
        ),
        ("POST", "/api/v1/fees/receipts"): lambda w, r, a: (
            "/api/v1/fees/receipts",
            {"account_id": str(acc(w)), "amount": "1.00", "mode": "cash"},
            {},
        ),
        ("GET", "/api/v1/fees/receipts"): g(lambda w, a: "/api/v1/fees/receipts"),
        ("GET", "/api/v1/fees/receipts/{receipt_id}"): g(
            lambda w, a: f"/api/v1/fees/receipts/{_shared_receipt(w, a)}"
        ),
        ("GET", "/api/v1/fees/receipts/{receipt_id}/print"): g(
            lambda w, a: f"/api/v1/fees/receipts/{_shared_receipt(w, a)}/print"
        ),
        ("GET", "/api/v1/fees/receipts/{receipt_id}/download-url"): g(
            lambda w, a: f"/api/v1/fees/receipts/{_shared_receipt(w, a)}/download-url"
        ),
        ("POST", "/api/v1/fees/receipts/{receipt_id}/render"): lambda w, r, a: (
            f"/api/v1/fees/receipts/{_shared_receipt(w, a)}/render",
            None,
            {},
        ),
        ("POST", "/api/v1/fees/receipts/{receipt_id}/cancellation"): lambda w, r, a: (
            f"/api/v1/fees/receipts/{_receipt(w).id}/cancellation",
            {"reason_code": "other", "reason": REASON},
            {},
        ),
        ("POST", "/api/v1/fees/cancellations/{cancellation_id}/approve"): _decide(
            "cancellation", "approve"
        ),
        ("POST", "/api/v1/fees/cancellations/{cancellation_id}/reject"): _decide(
            "cancellation", "reject"
        ),
        ("POST", "/api/v1/fees/cancellations/{cancellation_id}/withdraw"): _decide(
            "cancellation", "withdraw"
        ),
        ("GET", "/api/v1/fees/accounts/{account_id}/concessions"): g(
            lambda w, a: f"/api/v1/fees/accounts/{acc(w)}/concessions"
        ),
        ("POST", "/api/v1/fees/accounts/{account_id}/concessions"): lambda w, r, a: (
            f"/api/v1/fees/accounts/{acc(w)}/concessions",
            {
                "concession_type": "other",
                "basis": "amount",
                "value": "1.00",
                "targets": [{"instalment_no": 2, "head_id": str(F.ensure_setup(w.a).tui)}],
                "reason": REASON,
            },
            {},
        ),
        ("POST", "/api/v1/fees/concessions/{concession_id}/approve"): _decide(
            "concession", "approve"
        ),
        ("POST", "/api/v1/fees/concessions/{concession_id}/reject"): _decide(
            "concession", "reject"
        ),
        ("POST", "/api/v1/fees/concessions/{concession_id}/withdraw"): _decide(
            "concession", "withdraw"
        ),
        ("GET", "/api/v1/fees/accounts/{account_id}/adjustments"): g(
            lambda w, a: f"/api/v1/fees/accounts/{acc(w)}/adjustments"
        ),
        ("POST", "/api/v1/fees/accounts/{account_id}/adjustments"): lambda w, r, a: (
            f"/api/v1/fees/accounts/{acc(w)}/adjustments",
            {
                "instalment_no": 2,
                "head_id": str(F.ensure_setup(w.a).tui),
                "amount": "1.00",
                "reason_code": "other",
                "reason": REASON,
            },
            {},
        ),
        ("POST", "/api/v1/fees/adjustments/{adjustment_id}/approve"): _decide(
            "adjustment", "approve"
        ),
        ("POST", "/api/v1/fees/adjustments/{adjustment_id}/reject"): _decide(
            "adjustment", "reject"
        ),
        ("POST", "/api/v1/fees/adjustments/{adjustment_id}/withdraw"): _decide(
            "adjustment", "withdraw"
        ),
        ("POST", "/api/v1/fees/receipts/{receipt_id}/refunds"): lambda w, r, a: (
            f"/api/v1/fees/receipts/{_receipt(w).id}/refunds",
            {"amount": "1.00", "mode": "cash", "reason": REASON},
            {},
        ),
        ("GET", "/api/v1/fees/refunds/{refund_id}/print"): g(
            lambda w, a: f"/api/v1/fees/refunds/{_shared_voucher(w)}/print"
        ),
        ("POST", "/api/v1/fees/refunds/{refund_id}/approve"): _decide("refund", "approve"),
        ("POST", "/api/v1/fees/refunds/{refund_id}/reject"): _decide("refund", "reject"),
        ("POST", "/api/v1/fees/refunds/{refund_id}/withdraw"): _decide("refund", "withdraw"),
        ("GET", "/api/v1/fees/approvals"): g(lambda w, a: "/api/v1/fees/approvals"),
        ("GET", "/api/v1/fees/reports/daily"): g(lambda w, a: "/api/v1/fees/reports/daily"),
        ("GET", "/api/v1/fees/reports/daily/print"): g(
            lambda w, a: "/api/v1/fees/reports/daily/print"
        ),
        ("GET", "/api/v1/fees/reports/daily.csv"): g(lambda w, a: "/api/v1/fees/reports/daily.csv"),
        ("POST", "/api/v1/fees/day-closes"): _day_close,
        ("GET", "/api/v1/fees/dues"): g(lambda w, a: "/api/v1/fees/dues"),
        ("GET", "/api/v1/fees/dues.csv"): g(lambda w, a: "/api/v1/fees/dues.csv"),
        **_online_specs(),
    }


MATRIX_CREATES = frozenset(
    {
        "/api/v1/fees/heads",
        "/api/v1/fees/plans",
        "/api/v1/fees/plans/{plan_id}/new-version",
        "/api/v1/fees/receipts",
        "/api/v1/fees/receipts/{receipt_id}/cancellation",
        "/api/v1/fees/accounts/{account_id}/concessions",
        "/api/v1/fees/accounts/{account_id}/adjustments",
        "/api/v1/fees/receipts/{receipt_id}/refunds",
        # R2-2D
        "/api/v1/payments/connection/manual",
        "/api/v1/fees/accounts/{account_id}/payment-intents",
    }
)


# --- BOLA: fee objects of school B ---------------------------------------------------------------


def b_objects(admin: Engine, w: Any) -> dict[str, uuid.UUID]:
    """A head, plan, account, receipt and one pending request of each kind in school B."""
    key = "bola_fees"
    found: dict[str, uuid.UUID] | None = _B.get(w.b.tenant_id)
    if found is not None:
        return found
    ensure_people(admin, w.b)
    with F.fees_on(admin, w.b.tenant_id):
        setup = F.ensure_setup(w.b)
        account_id = F.account(w.b)
        receipt = F.issue(w.b, account_id, "10.00")
        cancel_receipt = F.issue(w.b, account_id, "1.00")
        out = {
            "head_id": setup.tui,
            "plan_id": setup.plan_id,
            "account_id": account_id,
            "receipt_id": receipt.id,
            "cancellation_id": F.call(
                w.b,
                "office_staff",
                fees.request_cancellation,
                cancel_receipt.id,
                CancellationRequest(reason_code="other", reason=REASON),
            ).id,
            "concession_id": F.call(
                w.b,
                "accountant",
                fees.request_concession,
                account_id,
                ConcessionCreate(
                    concession_type="other",
                    basis="amount",
                    value=Decimal("1.00"),
                    targets=[ConcessionTarget(instalment_no=2, head_id=setup.tui)],
                    reason=REASON,
                ),
            ).id,
            "adjustment_id": F.call(
                w.b,
                "accountant",
                fees.request_adjustment,
                account_id,
                AdjustmentCreate(
                    instalment_no=2,
                    head_id=setup.tui,
                    amount=Decimal("1.00"),
                    reason_code="other",
                    reason=REASON,
                ),
            ).id,
            "refund_id": F.call(
                w.b,
                "accountant",
                fees.request_refund,
                receipt.id,
                RefundCreate(amount=Decimal("1.00"), mode="cash", reason=REASON),
            ).id,
        }
        # R2-2D: a payment link and a reconciliation item of school B.
        P.connect(w.b)
        out["intent_id"] = P.link(w.b, account_id, [2], role="accountant").id
        from app.core.db import tenant_session
        from app.fees import online

        with tenant_session(w.b.tenant_id) as s:
            online.open_exception(s, kind="paid_not_receipted", reason="no_receipt", dedupe="bolab")
            out["recon_item_id"] = uuid.UUID(
                str(
                    s.execute(
                        __import__("sqlalchemy").text(
                            "SELECT id FROM fin.reconciliation_items WHERE dedupe_key = "
                            "'paid_not_receipted:bolab'"
                        )
                    ).scalar_one()
                )
            )
    _B[w.b.tenant_id] = out
    _ = key
    return out


_B: dict[uuid.UUID, dict[str, uuid.UUID]] = {}

BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("PATCH", "/api/v1/fees/heads/{head_id}"): {"sort_order": 1},
    ("PATCH", "/api/v1/fees/plans/{plan_id}"): {"name": "Synthetic"},
    ("POST", "/api/v1/fees/plans/{plan_id}/activate"): None,
    ("POST", "/api/v1/fees/plans/{plan_id}/new-version"): None,
    ("POST", "/api/v1/fees/plans/{plan_id}/apply"): {"dry_run": True},
    ("POST", "/api/v1/fees/plans/{plan_id}/adjustments"): {"reason": REASON},
    ("POST", "/api/v1/fees/receipts/{receipt_id}/render"): None,
    ("POST", "/api/v1/fees/receipts/{receipt_id}/cancellation"): {
        "reason_code": "other",
        "reason": REASON,
    },
    ("POST", "/api/v1/fees/receipts/{receipt_id}/refunds"): {
        "amount": "1.00",
        "mode": "cash",
        "reason": REASON,
    },
    ("POST", "/api/v1/fees/accounts/{account_id}/concessions"): {
        "concession_type": "other",
        "basis": "amount",
        "value": "1.00",
        "targets": [{"instalment_no": 1, "head_id": "01920000-0000-7000-8000-000000000001"}],
        "reason": REASON,
    },
    ("POST", "/api/v1/fees/accounts/{account_id}/adjustments"): {
        "instalment_no": 1,
        "head_id": "01920000-0000-7000-8000-000000000001",
        "amount": "1.00",
        "reason_code": "other",
        "reason": REASON,
    },
    ("POST", "/api/v1/fees/accounts/{account_id}/payment-intents"): {"instalments": [1]},
    ("POST", "/api/v1/fees/payment-intents/{intent_id}/cancel"): None,
    ("POST", "/api/v1/fees/reconciliation/online/items/{recon_item_id}/explain"): {
        "note": "Synthetic BOLA explanation note"
    },
    **{
        ("POST", f"/api/v1/fees/{plural}/{{{param}}}/{action}"): (
            {"reason": "Synthetic BOLA rejection reason"}
            if action == "reject"
            else ({} if action == "approve" else None)
        )
        for plural, param in (
            ("cancellations", "cancellation_id"),
            ("concessions", "concession_id"),
            ("adjustments", "adjustment_id"),
            ("refunds", "refund_id"),
        )
        for action in ("approve", "reject", "withdraw")
    },
}
