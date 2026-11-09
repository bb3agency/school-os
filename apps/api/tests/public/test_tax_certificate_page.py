"""The "Tax certificate FY ..." button of the parent receipt and dues pages (FM-06; FR-FEE-057,
FR-FEE-058; ADR-0046/0047; lead decision D84). Synthetic data only.

Called the way the BFF calls them (service token, the cookie's token, no user token). Covers: the
button's block on both pages (year and whether issued), the PDF link of an issued certificate
(audited ``via: parent_link``), 202 while the PDF is made, the uniform 404 when nothing is
issued, for another guardian's link and while fees are off, and that opening it never issues a
certificate (a read, not a write).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.core import ratelimit as rl
from app.core.db import tenant_session
from app.fees import service as fees
from app.fees import tax_certificates
from app.fees.schemas import AllocationIn
from app.fees.tax_schemas import TaxCertificateIssue
from app.public import service, tokens
from app.public.config import ParentKind

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]
W = sys.modules["sos_test_api_world"]
TESTS = Path(__file__).resolve().parents[1]
TAX = "/api/v1/public/links/current/tax-certificate"
FY = "2026-27"


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


@pytest.fixture(scope="module")
def fee_school(world: Any, admin_engine: Engine) -> Any:
    F.install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("principal", "office_admin", "office_staff", "accountant"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    F.enable(admin_engine, s.tenant_id)
    return s


@pytest.fixture(autouse=True)
def _window(monkeypatch: pytest.MonkeyPatch) -> None:
    """The button's window is open (as from 1 January 2027) whatever today is."""
    F.install()
    monkeypatch.setattr(tax_certificates, "parent_link_year", lambda today, start: FY)


def _link(school: Any, kind: ParentKind, target_id: uuid.UUID, guardian_id: uuid.UUID) -> str:
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as db:
        issued = service.issue_parent_link(
            db, kind=kind, target_id=target_id, guardian_id=guardian_id, created_by=owner.user_id
        )
    assert issued.key is not None
    return issued.key


def _same_404(client: TestClient, res: Any) -> None:
    assert res.status_code == 404, res.text
    rl.get_rate_limiter().store.reset()  # type: ignore[attr-defined]
    unknown = P.current(client, tokens.new_parent_token())
    got, want = P.comparable(res), P.comparable(unknown)
    got.pop("instance", None)
    want.pop("instance", None)
    assert got == want


def _paid_child(school: Any) -> tuple[uuid.UUID, uuid.UUID, Any]:
    student = F.student(school)
    account = F.account(school, student)
    guardian: uuid.UUID = P.SW.add_guardian(school, student, full_name="Synthetic Parent")
    tui = F.ensure_setup(school).tui
    receipt = F.issue(
        school,
        account,
        "900.00",
        allocations=[AllocationIn(instalment_no=1, head_id=tui, amount=Decimal("900.00"))],
    )
    return student, guardian, receipt


def _count(admin: Engine, tenant_id: uuid.UUID) -> int:
    with admin.connect() as c:
        value: int = c.execute(
            text("SELECT count(*) FROM fin.tax_certificates WHERE tenant_id = :t"),
            {"t": tenant_id},
        ).scalar_one()
    return value


def test_FR_FEE_057_button_shows_the_year_and_opens_only_an_issued_certificate(
    fee_school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    student, guardian, receipt = _paid_child(fee_school)
    key = _link(fee_school, "parent_receipt", receipt.id, guardian)
    before = _count(admin_engine, fee_school.tenant_id)
    body = P.current(public_client, key).json()
    assert body["receipt"]["tax_certificate"] == {"financial_year": FY, "issued": False}
    # Nothing issued: the uniform 404, and opening it issues nothing (a read, not a write).
    _same_404(public_client, P.public_call(public_client, "GET", TAX, token=key))
    assert _count(admin_engine, fee_school.tenant_id) == before

    cert = F.call(
        fee_school,
        "office_staff",
        fees.issue_tax_certificate,
        TaxCertificateIssue(student_id=student, financial_year=FY),
    )
    body = P.current(public_client, key).json()
    assert body["receipt"]["tax_certificate"] == {"financial_year": FY, "issued": True}
    # Issued but the PDF is still being made: 202 preparing.
    res = P.public_call(public_client, "GET", TAX, token=key)
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "preparing"
    fees.render_tax_certificate_pdf(
        fee_school.tenant_id,
        cert.id,
        fee_school.people["office_staff"].user_id,
        renderer=F.FakeRenderer(),
    )
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE kb.document_versions SET status = 'ready' WHERE document_id = "
                "(SELECT document_id FROM fin.tax_certificates WHERE id = :i)"
            ),
            {"i": cert.id},
        )
    res = P.public_call(public_client, "GET", TAX, token=key)
    assert res.status_code == 200, res.text
    assert set(res.json()) == {"url", "expires_at"}
    events = [
        e
        for e in W.audit_events(
            admin_engine, fee_school.tenant_id, "fee.tax_certificate.downloaded"
        )
        if e["resource_id"] == cert.id
    ]
    assert events
    assert events[-1]["summary"]["via"] == "parent_link"

    # The dues link of the same child carries the same button and PDF.
    account = F.scalar(
        admin_engine,
        "SELECT account_id FROM fin.receipts WHERE id = :r",
        r=receipt.id,
    )
    dues_key = _link(fee_school, "parent_dues", account, guardian)
    dues = P.current(public_client, dues_key).json()["dues"]
    assert dues["tax_certificate"] == {"financial_year": FY, "issued": True}
    assert P.public_call(public_client, "GET", TAX, token=dues_key).status_code == 200


def test_FR_FEE_058_other_guardians_and_fees_off_get_the_uniform_404(
    fee_school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    student, _, receipt = _paid_child(fee_school)
    F.call(
        fee_school,
        "office_staff",
        fees.issue_tax_certificate,
        TaxCertificateIssue(student_id=student, financial_year=FY),
    )
    other = F.student(fee_school)
    stranger: uuid.UUID = P.SW.add_guardian(fee_school, other, full_name="Synthetic Other")
    stolen = _link(fee_school, "parent_receipt", receipt.id, stranger)
    _same_404(public_client, P.public_call(public_client, "GET", TAX, token=stolen))
    F.enable(admin_engine, fee_school.tenant_id, on=False)
    try:
        own = _link(fee_school, "parent_receipt", receipt.id, P.SW.add_guardian(
            fee_school, student, full_name="Synthetic Parent Two"))  # fmt: skip
        body = P.current(public_client, own).json()
        assert body["receipt"]["tax_certificate"] is None  # no button while fees are off
        _same_404(public_client, P.public_call(public_client, "GET", TAX, token=own))
    finally:
        F.enable(admin_engine, fee_school.tenant_id)
