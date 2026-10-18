"""Parent page content and actions through the real app (wave R2-3H; ADR-0046 decisions 2, 5, 7
and 9; ADR-0044 decision 4; FR-PUB-007..011, FR-PAY-005, FR-LANG-003; US-2401..US-2405).
Synthetic data only.

Called the way the BFF calls them (service token, the cookie's token in ``X-SOS-Public-Token``,
no user token). Covers: each page's content and its field allowlist (data minimisation:
invariants 4 and 5, ADR-0046 decision 2), the uniform 404 for wrong target types, links of
another kind, guardians no longer linked to the child (BOLA across guardians), other schools'
targets (RLS), fees switched off and suspended schools (D31); the PDF links and their audit;
"Pay online" with the fake provider (idempotent per link and instalments, only new links spend
``public_pay``), the return page's status, the opt-in page's language choice, and log lines
without keys or names.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.core import ratelimit as rl
from app.core.db import tenant_session
from app.fees import online
from app.public import service, tokens
from app.public.config import PARENT_TARGET_TYPES, ParentKind

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]
W = sys.modules["sos_test_api_world"]
TESTS = Path(__file__).resolve().parents[1]
DOCUMENT = "/api/v1/public/links/current/document"
PAY = "/api/v1/public/links/current/pay"
PAYMENT = "/api/v1/public/links/current/payment"
OPTIN = "/api/v1/public/links/current/optin"
# Keys a parent response must never carry, anywhere (ADR-0046 decision 2; invariants 4 and 5).
FORBIDDEN_KEYS = {
    "phone", "mobile", "address", "date_of_birth", "dob", "aadhaar", "aadhaar_last4",
    "guardian_id", "guardian_name", "student_id", "account_id", "tenant_id", "handle_id",
    "mother_name", "father_name", "marks", "attendance", "email", "utr", "collected_by_name",
}  # fmt: skip


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


F = _load("sos_test_fees_support", TESTS / "fees" / "support.py")
O = _load("sos_test_fees_online_support", TESTS / "fees" / "online_support.py")  # noqa: E741


@pytest.fixture(scope="module")
def fee_school(world: Any, admin_engine: Engine) -> Any:
    """A school of its own with fees on and the fee roles (receipts and links of the shared
    world stay untouched)."""
    F.install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("principal", "office_admin", "office_staff", "accountant"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    F.enable(admin_engine, s.tenant_id)
    return s


@pytest.fixture(autouse=True)
def _store() -> None:
    F.install()


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _all_keys(v)}
    return set()


def _minimal(body: dict[str, Any]) -> None:
    leaked = _all_keys(body) & FORBIDDEN_KEYS
    assert not leaked, leaked


def _link(
    school: Any,
    kind: ParentKind,
    target_id: uuid.UUID,
    guardian_id: uuid.UUID | None,
    *,
    target_type: str | None = None,
) -> str:
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as db:
        if target_type is None and guardian_id is not None:
            issued = service.issue_parent_link(
                db,
                kind=kind,
                target_id=target_id,
                guardian_id=guardian_id,
                created_by=owner.user_id,
            )
        else:
            issued = service.issue_handle(
                db,
                kind=kind,
                target_type=target_type or PARENT_TARGET_TYPES[kind],
                target_id=target_id,
                guardian_id=guardian_id,
                created_by=owner.user_id,
            )
    assert issued.key is not None
    return issued.key


def _unknown_404(client: TestClient) -> dict[str, Any]:
    res = P.current(client, tokens.new_parent_token())
    assert res.status_code == 404
    body: dict[str, Any] = P.comparable(res)
    return body


def _same_404(client: TestClient, res: Any) -> None:
    """The same status and body as an unknown key (only ``instance``, the path, differs)."""
    assert res.status_code == 404, res.text
    rl.get_rate_limiter().store.reset()  # type: ignore[attr-defined]  # many dead keys per test
    got, unknown = P.comparable(res), _unknown_404(client)
    got.pop("instance", None)
    unknown.pop("instance", None)
    assert got == unknown


def _guardian_for(school: Any, student_id: uuid.UUID) -> uuid.UUID:
    guardian: uuid.UUID = P.SW.add_guardian(school, student_id, full_name="Synthetic Parent")
    return guardian


def _account_with_guardian(school: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    student = F.student(school)
    account = F.account(school, student)
    return student, account, _guardian_for(school, student)


def _audit(admin: Engine, tenant_id: uuid.UUID, action: str, resource_id: Any) -> list[Any]:
    return [
        e
        for e in W.audit_events(admin, tenant_id, action)
        if str(e["resource_id"]) == str(resource_id)
    ]


# --- notice -------------------------------------------------------------------------------------


def test_US_2401_notice_page_shows_the_approved_notice_only(
    world: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    _, guardian = P.student_with_guardian(world.a)
    owner = world.a.people["owner"]
    with tenant_session(world.a.tenant_id, owner.user_id) as db:
        approved = P.approved_notice(db, owner.user_id, pdf=True)
        draft = P.approved_notice(db, owner.user_id, status="draft")
    key = _link(world.a, "parent_notice", approved, guardian)
    res = P.current(public_client, key)
    assert res.status_code == 200, res.text
    body = res.json()
    _minimal(body)
    assert body["notice"]["title"] == "Sports day"
    assert body["notice"]["has_pdf"] is True
    assert body["notice"]["date"] is not None
    # A notice that is not approved is the uniform 404 and is not counted as a view.
    draft_key = _link(world.a, "parent_notice", draft, guardian)
    _same_404(public_client, P.current(public_client, draft_key))
    # The PDF: a presigned link, audited as a system download via the parent link.
    doc = P.public_call(public_client, "GET", DOCUMENT, token=key)
    assert doc.status_code == 200, doc.text
    assert set(doc.json()) == {"url", "expires_at"}
    events = _audit(admin_engine, world.a.tenant_id, "notice.downloaded", approved)
    assert events
    assert events[-1]["summary"]["via"] == "parent_link"


def test_FR_PUB_011_wrong_target_type_kind_and_school_are_the_uniform_404(
    world: Any, public_client: TestClient
) -> None:
    _, guardian = P.student_with_guardian(world.a)
    owner = world.a.people["owner"]
    with tenant_session(world.a.tenant_id, owner.user_id) as db:
        notice_a = P.approved_notice(db, owner.user_id)
    # A handle of the right kind pointing at another type of thing.
    odd = _link(world.a, "parent_notice", notice_a, guardian, target_type="notice")
    _same_404(public_client, P.current(public_client, odd))
    # School B's link naming school A's notice id: RLS hides it (no cross-school read).
    _, guardian_b = P.student_with_guardian(world.b)
    cross = _link(world.b, "parent_notice", notice_a, guardian_b)
    _same_404(public_client, P.current(public_client, cross))
    # A notice link on the dues, pay, payment and opt-in routes: refused at the guard.
    good = _link(world.a, "parent_notice", notice_a, guardian)
    assert P.current(public_client, good).status_code == 200
    for method, path, body in (
        ("POST", PAY, {"instalments": [1]}),
        ("GET", PAYMENT, None),
        ("POST", OPTIN, {"language": "en"}),
    ):
        _same_404(public_client, P.public_call(public_client, method, path, token=good, body=body))
    # Unknown keys on every new route: the same body.
    unknown = tokens.new_parent_token()
    for method, path, body in (
        ("GET", DOCUMENT, None),
        ("POST", PAY, {"instalments": [1]}),
        ("GET", PAYMENT, None),
        ("POST", OPTIN, {"language": "en"}),
    ):
        _same_404(
            public_client, P.public_call(public_client, method, path, token=unknown, body=body)
        )


# --- receipt ------------------------------------------------------------------------------------


def test_US_2402_receipt_page_shows_the_receipt_card_and_refuses_other_guardians(
    fee_school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    student, account, guardian = _account_with_guardian(fee_school)
    receipt = F.issue(fee_school, account, "1500.00")
    key = _link(fee_school, "parent_receipt", receipt.id, guardian)
    res = P.current(public_client, key)
    assert res.status_code == 200, res.text
    body = res.json()
    _minimal(body)
    card = body["receipt"]
    assert set(card) == {
        "receipt_number", "receipt_date", "student_name", "class_section", "admission_no",
        "lines", "amount", "amount_in_words", "mode", "cancelled_on", "has_pdf",
    }  # fmt: skip
    assert card["receipt_number"] == receipt.receipt_number
    assert card["amount"] == "1500.00"
    assert card["mode"] == "cash"
    assert card["cancelled_on"] is None
    assert all(set(x) == {"label", "head_name", "amount"} for x in card["lines"])
    # No PDF stored yet (the render job did not run): no link, the uniform 404.
    _same_404(public_client, P.public_call(public_client, "GET", DOCUMENT, token=key))
    # BOLA across guardians: a guardian of ANOTHER child holding a link to this receipt.
    other_child = F.student(fee_school)
    stranger = _guardian_for(fee_school, other_child)
    stolen = _link(fee_school, "parent_receipt", receipt.id, stranger)
    _same_404(public_client, P.current(public_client, stolen))
    # A receipt link without a guardian is never shown.
    bare = _link(fee_school, "parent_receipt", receipt.id, None)
    _same_404(public_client, P.current(public_client, bare))
    # Unlinking the guardian from the child stops the page at once (FR-PUB-007).
    with admin_engine.begin() as c:
        c.execute(
            text("DELETE FROM sis.student_guardians WHERE guardian_id = :g AND student_id = :s"),
            {"g": guardian, "s": student},
        )
    _same_404(public_client, P.current(public_client, key))


def test_FR_PUB_011_receipt_links_still_open_while_the_school_is_suspended(
    fee_school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    _, account, guardian = _account_with_guardian(fee_school)
    receipt = F.issue(fee_school, account, "500.00")
    key = _link(fee_school, "parent_receipt", receipt.id, guardian)
    dues_key = _link(fee_school, "parent_dues", account, guardian)
    P.set_tenant_status(admin_engine, fee_school.tenant_id, "suspended")
    try:
        assert P.current(public_client, key).status_code == 200
        _same_404(public_client, P.current(public_client, dues_key))
    finally:
        P.set_tenant_status(admin_engine, fee_school.tenant_id, "active")


# --- dues and paying ----------------------------------------------------------------------------


def test_US_2403_dues_page_shows_first_name_and_class_only(
    fee_school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    O.disconnect_if_connected(fee_school)
    _, account, guardian = _account_with_guardian(fee_school)
    key = _link(fee_school, "parent_dues", account, guardian)
    res = P.current(public_client, key)
    assert res.status_code == 200, res.text
    body = res.json()
    _minimal(body)
    dues = body["dues"]
    assert set(dues) == {
        "child_first_name", "class_name", "as_of", "instalments", "total_due", "late_fee_total",
        "pay_online", "upi", "office_phone",
    }  # fmt: skip
    assert dues["child_first_name"] == "Synthetica"  # first name only, never the full name
    assert "Fee Student" not in json.dumps(body)
    assert "admission_no" not in json.dumps(body)
    assert dues["class_name"]
    assert [i["instalment_no"] for i in dues["instalments"]] == [1, 2]
    # Taxable transport is never collected here: 3,500 per instalment.
    assert dues["total_due"] == "7000.00"
    assert dues["pay_online"] is False  # not connected
    refused = P.public_call(public_client, "POST", PAY, token=key, body={"instalments": [1]})
    assert refused.status_code == 409
    assert refused.json()["code"] == "payments_not_connected"
    # Fees switched off: the dues page is the uniform 404 (no "module hidden" oracle).
    F.enable(admin_engine, fee_school.tenant_id, on=False)
    try:
        _same_404(public_client, P.current(public_client, key))
        _same_404(
            public_client,
            P.public_call(public_client, "POST", PAY, token=key, body={"instalments": [1]}),
        )
    finally:
        F.enable(admin_engine, fee_school.tenant_id)


def test_FR_PAY_005_pay_online_is_idempotent_and_only_new_links_spend_the_budget(
    fee_school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    conn = O.connect(fee_school)
    _, account, guardian = _account_with_guardian(fee_school)
    key = _link(fee_school, "parent_dues", account, guardian)
    assert P.current(public_client, key).json()["dues"]["pay_online"] is True
    quota = rl.load_config().policies["public_pay"].quota
    first = P.public_call(public_client, "POST", PAY, token=key, body={"instalments": [1]})
    assert first.status_code == 200, first.text
    out = first.json()
    assert set(out) == {"redirect_url", "amount", "reused"}
    assert out["redirect_url"].startswith("https://rzp.example.test/")
    assert out["amount"] == "3500.00"
    assert out["reused"] is False
    # The same choice again and again: the same link, no new intent, no budget spent.
    for _ in range(quota + 2):
        again = P.public_call(public_client, "POST", PAY, token=key, body={"instalments": [1]})
        assert again.status_code == 200, again.text
        assert again.json()["redirect_url"] == out["redirect_url"]
        assert again.json()["reused"] is True
    count = O.scalar(
        admin_engine,
        "SELECT count(*) FROM fin.payment_intents WHERE account_id = :a AND created_via = "
        "'parent_page'",
        a=account,
    )
    assert count == 1
    # The return page: the newest link of this dues link, or the one Razorpay names.
    status = P.public_call(public_client, "GET", PAYMENT, token=key)
    assert status.status_code == 200, status.text
    assert status.json() == {"status": "created", "amount": "3500.00", "receipt_issued": False}
    intent_id = O.scalar(
        admin_engine,
        "SELECT id FROM fin.payment_intents WHERE account_id = :a AND created_via = 'parent_page'",
        a=account,
    )
    ref = uuid.UUID(str(intent_id)).hex
    named = P.public_call(public_client, "GET", f"{PAYMENT}?intent={ref}", token=key)
    assert named.status_code == 200
    # Another account's intent through this link: the uniform 404.
    _, other_account, other_guardian = _account_with_guardian(fee_school)
    other_key = _link(fee_school, "parent_dues", other_account, other_guardian)
    _same_404(
        public_client,
        P.public_call(public_client, "GET", f"{PAYMENT}?intent={ref}", token=other_key),
    )
    # The parent pays at the fake provider; the webhook makes the receipt.
    O.pay(fee_school, SimpleNamespace(client=public_client), conn, SimpleNamespace(id=intent_id))
    paid = P.public_call(public_client, "GET", f"{PAYMENT}?intent={ref}", token=key).json()
    assert paid["status"] == "paid"
    assert paid["receipt_issued"] is True
    # Audit: the link was made by the system for the parent page.
    created = _audit(admin_engine, fee_school.tenant_id, "fee.payment_link.created", intent_id)
    assert created


def test_FR_PUB_011_public_pay_budget_refuses_new_links_once_used_up(
    fee_school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    O.connect(fee_school)
    _, account, guardian = _account_with_guardian(fee_school)
    key = _link(fee_school, "parent_dues", account, guardian)
    quota = rl.load_config().policies["public_pay"].quota
    for _ in range(quota):  # each new link (the previous one cancelled by the office) costs one
        made = P.public_call(public_client, "POST", PAY, token=key, body={"instalments": [2]})
        assert made.status_code == 200, made.text
        assert made.json()["reused"] is False
        intent_id = O.scalar(
            admin_engine,
            "SELECT id FROM fin.payment_intents WHERE account_id = :a AND status = 'created'",
            a=account,
        )
        F.call(fee_school, "office_staff", online.cancel_intent, intent_id)
    refused = P.public_call(public_client, "POST", PAY, token=key, body={"instalments": [2]})
    assert refused.status_code == 429
    assert refused.json()["code"] == "rate_limited"
    rl.get_rate_limiter().store.reset()  # type: ignore[attr-defined]
    again = P.public_call(public_client, "POST", PAY, token=key, body={"instalments": [2]})
    assert again.status_code == 200


# --- opt-in -------------------------------------------------------------------------------------


def test_FR_LANG_003_opt_in_page_shows_choices_and_sets_the_language(
    world: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    _, guardian = P.student_with_guardian(world.a)
    key = _link(world.a, "parent_optin", guardian, guardian)
    res = P.current(public_client, key)
    assert res.status_code == 200, res.text
    body = res.json()
    _minimal(body)
    optin = body["optin"]
    assert [c["category"] for c in optin["categories"]] == ["notices", "attendance", "fees"]
    assert {c["status"] for c in optin["categories"]} == {"not_asked"}
    assert optin["language_choice"] == "school"
    changed = P.public_call(public_client, "POST", OPTIN, token=key, body={"language": "te"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["language_choice"] == "te"
    assert P.current(public_client, key).json()["language"] == "te"
    events = _audit(admin_engine, world.a.tenant_id, "guardian.updated", guardian)
    assert events[-1]["summary"]["via"] == "parent_link"
    assert events[-1]["actor_id"] is None  # the parent, not a member (system actor)
    back = P.public_call(public_client, "POST", OPTIN, token=key, body={"language": "school"})
    assert back.json()["language_choice"] == "school"
    bad = P.public_call(public_client, "POST", OPTIN, token=key, body={"language": "hi"})
    assert bad.status_code == 422
    # An opt-in link that points at another guardian: the uniform 404.
    _, other = P.student_with_guardian(world.a)
    odd = _link(world.a, "parent_optin", other, guardian, target_type="guardian")
    _same_404(public_client, P.current(public_client, odd))


def test_D31_nothing_is_processed_while_the_school_is_suspended(
    world: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    suspended = W.School(W.provision_school())
    suspended.people["owner"] = W.add_member(admin_engine, suspended.tenant_id, ["owner"])
    W.build_structure(suspended, suspended.people["owner"])
    _, guardian = P.student_with_guardian(suspended)
    key = _link(suspended, "parent_optin", guardian, guardian)
    P.set_tenant_status(admin_engine, suspended.tenant_id, "suspended")
    res = P.public_call(public_client, "POST", OPTIN, token=key, body={"language": "te"})
    _same_404(public_client, res)
    with admin_engine.connect() as c:
        value = c.execute(
            text("SELECT preferred_language FROM sis.guardians WHERE id = :g"), {"g": guardian}
        ).scalar()
    assert value is None


# --- logs ---------------------------------------------------------------------------------------


def test_invariant_5_parent_pages_log_no_keys_names_or_amounts(
    fee_school: Any, public_client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    O.connect(fee_school)
    _, account, guardian = _account_with_guardian(fee_school)
    receipt = F.issue(fee_school, account, "1234.00")
    keys = [
        _link(fee_school, "parent_receipt", receipt.id, guardian),
        _link(fee_school, "parent_dues", account, guardian),
    ]
    capsys.readouterr()
    for key in keys:
        P.current(public_client, key)
    P.public_call(public_client, "POST", PAY, token=keys[1], body={"instalments": [2]})
    P.public_call(public_client, "GET", PAYMENT, token=keys[1])
    out, err = capsys.readouterr()
    logged = out + err
    for key in keys:
        assert key not in logged
    assert "Synthetica" not in logged
    assert receipt.receipt_number not in logged
    assert "rzp.example.test" not in logged


@pytest.mark.parametrize(
    ("full_name", "first"),
    [
        ("K. VENKATA SAI", "Venkata"),
        ("Ravi Kumar", "Ravi"),
        ("B.Sai", "Sai"),
        ("ADDEPALLI PADMA KAVYA", "Addepalli"),
        ("K", ""),
        ("", ""),
    ],
)
def test_ADR_0046_child_first_name_drops_initials(full_name: str, first: str) -> None:
    from app.students import service as students

    assert students.first_name_of(full_name) == first
