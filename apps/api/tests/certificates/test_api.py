"""Certificate and register routes over HTTP (docs/09 Certificates; US-1101..US-1106; SEC-005,
FR-CERT-011, FR-REG-004). Role x route and cross-tenant cases are in tests/security."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]


def _issue(api: Any, school: Any, sid: uuid.UUID, body: dict[str, Any], **kw: Any) -> Any:
    return api.call(
        school.people[kw.pop("role", "office_admin")],
        "POST",
        f"/api/v1/students/{sid}/certificates",
        json=body,
        **kw,
    )


def test_US_1101_issue_over_http_returns_location_etag_and_replays(school: Any, api: Any) -> None:
    sid = C.student(school)
    body = {"certificate_type": "bonafide", "inputs": {"purpose": "scholarship"}}
    key = {"Idempotency-Key": f"cert-{uuid.uuid4().hex}"}
    res = _issue(api, school, sid, body, headers=key)
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["status"] == "issued"
    assert out["serial"].startswith("BC/2026-27/")
    assert res.headers["Location"] == f"/api/v1/certificates/{out['id']}"
    assert res.headers["ETag"] == 'W/"2"'
    again = _issue(api, school, sid, body, headers=key)
    assert again.status_code == 201
    assert again.json()["id"] == out["id"], "same key + body replays, no second certificate"
    assert again.headers.get("Idempotent-Replayed") == "true"


def test_US_1101_types_and_preview(school: Any, api: Any) -> None:
    res = api.call(school.people["office_staff"], "GET", "/api/v1/certificates/types")
    assert res.status_code == 200
    keys = {t["key"] for t in res.json()}
    assert keys == {"transfer", "bonafide", "study", "conduct"}
    sid = C.student(school)
    res = api.call(
        school.people["office_staff"],
        "GET",
        f"/api/v1/students/{sid}/certificates/preview",
        params={"certificate_type": "transfer"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["requires_approval"] is True
    assert res.json()["can_issue"] is True


def test_US_1101_blocked_issue_is_409(school: Any, api: Any) -> None:
    sid = C.student(school, admission_no=None)
    res = _issue(api, school, sid, {"certificate_type": "study", "inputs": {}})
    assert res.status_code == 409
    assert res.json()["code"] == "certificate_blocked"


def _dq_support() -> Any:
    name = "sos_test_dq_support"
    if name not in sys.modules:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "dq" / "dq_support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def test_DL_06_a_clerk_cannot_note_away_a_blocker_and_issue(school: Any, api: Any) -> None:
    """DL-06: office staff (certificate.issue, dq.findings.resolve, no waive) cannot resolve a
    blocker with a note and then print the mismatched record; the certificate stays blocked."""
    dq = _dq_support()
    sid = C.student(school, extra=dq.aadhaar(dob="2012-05-14"))
    dq.run(school, sid)  # DQ-002 blocker on the printed date of birth
    clerk = school.people["office_staff"]
    listed = api.call(
        clerk, "GET", "/api/v1/dq/findings", params={"student_id": str(sid), "limit": 50}
    )
    assert listed.status_code == 200, listed.text
    blocker = next(f for f in listed.json()["data"] if f["rule_id"] == "DQ-002")
    resolved = api.call(
        clerk,
        "POST",
        f"/api/v1/dq/findings/{blocker['id']}/resolve",
        json={"note": "Looks fine to me"},
    )
    assert (resolved.status_code, resolved.json()["code"]) == (403, "blocker_needs_waive")
    body = {"certificate_type": "bonafide", "inputs": {"purpose": "passport"}}
    res = _issue(api, school, sid, body, role="office_staff")
    assert (res.status_code, res.json()["code"]) == (409, "certificate_blocked")


def test_SEC_005_approve_needs_if_match_and_recent_mfa(school: Any, api: Any) -> None:
    tc = C.pending_tc(school)
    principal = school.people["principal"]
    path = f"/api/v1/certificates/{tc.id}/approve"
    stale = api.call(principal, "POST", path, headers={"If-Match": 'W/"1"'}, auth_age_s=301)
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    missing = api.call(principal, "POST", path)
    assert missing.status_code == 400
    assert missing.json()["code"] == "if_match_required"
    wrong = api.call(
        principal, "POST", path, headers={"If-Match": 'W/"9"'}, json={"draft_sha256": "0" * 64}
    )
    assert wrong.status_code == 412
    ok = api.call(
        principal,
        "POST",
        path,
        headers={"If-Match": f'W/"{tc.version}"'},
        json={"draft_sha256": C.draft_hash(school, tc.id)},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "issued"
    self_try = api.call(
        school.people["office_admin"],
        "POST",
        f"/api/v1/certificates/{C.pending_tc(school).id}/withdraw",
        headers={"If-Match": 'W/"1"'},
    )
    assert self_try.status_code == 200


def test_FR_CERT_011_print_view_is_locked_down_html(school: Any, api: Any) -> None:
    cert = C.issue(school, C.student(school), "conduct")
    res = api.call(
        school.people["auditor_readonly"], "GET", f"/api/v1/certificates/{cert.id}/print"
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["Cache-Control"] == "no-store"
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    csp = res.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src" not in csp
    assert res.headers["Content-Disposition"].startswith("inline;")
    assert "CONDUCT CERTIFICATE" in res.text


def test_FR_CERT_011_download_waits_for_the_pdf(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "study")
    path = f"/api/v1/certificates/{cert.id}/download-url"
    res = api.call(school.people["office_staff"], "GET", path)
    assert res.status_code == 409
    assert res.json()["code"] == "pdf_not_ready"
    C.render(school, cert)
    C.mark_document_ready(admin_engine, C.row(admin_engine, cert.id)["document_id"])
    res = api.call(school.people["office_staff"], "GET", path)
    assert res.status_code == 200, res.text
    assert res.json()["filename"].endswith(".pdf")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/registers/transfer-certificates",
        "/api/v1/registers/certificates",
        "/api/v1/registers/admission-withdrawal",
    ],
)
def test_FR_REG_004_registers_need_step_up_and_print_html(school: Any, api: Any, path: str) -> None:
    admin = school.people["office_admin"]
    stale = api.call(admin, "GET", path, auth_age_s=301)
    assert stale.status_code == 428
    res = api.call(admin, "GET", path)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert "size: A4 landscape" in res.text
    staff = api.call(school.people["office_staff"], "GET", path)
    assert staff.status_code == 403, "office staff print certificates, not registers"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/registers/transfer-certificates",
        "/api/v1/registers/certificates",
        "/api/v1/registers/admission-withdrawal",
    ],
)
def test_FR_REG_004_a_register_view_is_audited_once(
    school: Any, api: Any, admin_engine: Engine, path: str
) -> None:
    """The registers screen first checks that the register can be opened now (step-up, year,
    type, size) with ``check=true``: 204, nothing printed, nothing audited. Opening the print
    view is the one view, audited once (``register.viewed``)."""
    admin = school.people["office_admin"]

    def viewed() -> int:
        return len(C.W.audit_events(admin_engine, school.tenant_id, "register.viewed"))

    before = viewed()
    stale = api.call(admin, "GET", path, params={"check": "true"}, auth_age_s=301)
    assert stale.status_code == 428, "the check asks for the same step-up as the view"
    checked = api.call(admin, "GET", path, params={"check": "true"})
    assert checked.status_code == 204, checked.text
    assert checked.content == b""
    assert viewed() == before, "a check is not a view"
    unknown = {"check": "true", "academic_year_id": str(uuid.uuid4())}
    assert api.call(admin, "GET", path, params=unknown).status_code == 404
    assert viewed() == before
    shown = api.call(admin, "GET", path)
    assert shown.status_code == 200, shown.text
    assert viewed() == before + 1


def test_SEC_003_accountant_has_no_certificate_access(school: Any, api: Any) -> None:
    res = api.call(school.people["accountant"], "GET", "/api/v1/certificates")
    assert res.status_code == 403
