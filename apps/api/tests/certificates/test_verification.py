"""Verification codes and the public verification route (R2-1A; ADR-0045, ADR-0047;
FR-CERT-019..024, FR-PUB-003, FR-PUB-011; US-2002..US-2004). Synthetic data only.

Public calls go through the same app the way the BFF makes them: a fresh service token, no user
token. Role x route and cross-school cases of the staff routes are also in tests/security."""

from __future__ import annotations

import dataclasses
import hashlib
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.certificates import service as certificates
from app.certificates.schemas import VerificationReasonIn
from app.core import ratelimit as rl
from app.core.config import get_settings
from app.core.errors import StepUpRequired
from app.identity.service_token import SERVICE_TOKEN_HEADER, issue_service_token

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]
VERIFY_HOST = "verify.synthetic.test"
REASON = "Posted on a social media group by mistake"
_REAL_CREATE = certificates._create_verification


@pytest.fixture
def verify_on(monkeypatch: pytest.MonkeyPatch) -> str:
    """Verification switched on (``SOS_VERIFY_BASE_URL``) for the service only."""
    settings = get_settings().model_copy(update={"verify_base_url": f"https://{VERIFY_HOST}"})
    monkeypatch.setattr(certificates, "get_settings", lambda: settings)
    return VERIFY_HOST


def _codes(admin: Engine, certificate_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT * FROM sis.certificate_verifications WHERE certificate_id = :c "
                "ORDER BY created_at"
            ),
            {"c": certificate_id},
        )
        return [dict(r._mapping) for r in rows]


def _code(admin: Engine, certificate_id: uuid.UUID) -> str:
    live = [r for r in _codes(admin, certificate_id) if r["withdrawn_at"] is None]
    assert len(live) == 1
    value: str = live[0]["code"]
    return value


def _lookup(api: Any, code: str, **headers: str) -> httpx.Response:
    h = {SERVICE_TOKEN_HEADER: issue_service_token(), **headers}
    res: httpx.Response = api.client.request(
        "GET", f"/api/v1/public/certificates/{code}", headers=h
    )
    return res


def _without_request_id(res: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = dict(res.json())
    body.pop("request_id", None)
    body.pop("instance", None)  # the path asked for
    return body


@pytest.fixture
def pre_r2(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Certificates issued while this is active get no code (as before R2)."""
    original = certificates._create_verification
    monkeypatch.setattr(certificates, "_create_verification", lambda *a, **k: None)
    yield
    monkeypatch.setattr(certificates, "_create_verification", original)


# --- FR-CERT-019: a code at issue, for originals and duplicates ---------------------------------


def test_FR_CERT_019_issue_creates_a_random_code_and_a_hashed_handle(
    school: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    rows = _codes(admin_engine, cert.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["created_reason"] == "issued"
    assert row["check_count"] == 0
    code = row["code"]
    assert len(code) == 16
    assert cert.serial is not None
    assert cert.serial.replace("/", "") not in code
    with admin_engine.connect() as c:
        handle = c.execute(
            text("SELECT * FROM core.public_handles WHERE id = :h"), {"h": row["handle_id"]}
        ).one()
    assert handle.kind == "cert_verify"
    assert handle.target_id == cert.id
    assert bytes(handle.key_hash) == hashlib.sha256(code.encode()).digest()
    assert handle.expires_at is None
    events = C.audit_rows(admin_engine, school.tenant_id, cert.id)
    added = [e for e in events if e["action"] == "certificate.verification_added"]
    assert len(added) == 1
    assert added[0]["summary"]["created_reason"] == "issued"
    assert code not in str(events), "the code never goes into the audit log"


def test_FR_CERT_019_a_duplicate_gets_its_own_code(school: Any, admin_engine: Engine) -> None:
    cert = C.issue(school, C.student(school), "study")
    dup = C.duplicate(school, cert.id)
    assert dup.status == "issued"
    first, second = _code(admin_engine, cert.id), _code(admin_engine, dup.id)
    assert first != second
    assert _codes(admin_engine, dup.id)[0]["created_reason"] == "duplicate"


def test_FR_CERT_019_a_tc_gets_its_code_when_approved_not_while_pending(
    school: Any, admin_engine: Engine
) -> None:
    tc = C.pending_tc(school)
    assert _codes(admin_engine, tc.id) == []
    C.approve(school, tc)
    assert len(_codes(admin_engine, tc.id)) == 1


# --- FR-CERT-020: printed on the print view and the PDF -----------------------------------------


def test_FR_CERT_020_print_view_and_pdf_carry_code_qr_and_host(
    school: Any, api: Any, admin_engine: Engine, verify_on: str
) -> None:
    cert = C.issue(school, C.student(school), "conduct")
    code = _code(admin_engine, cert.id)
    grouped = "-".join(code[i : i + 4] for i in range(0, 16, 4))
    res = api.call(school.people["office_staff"], "GET", f"/api/v1/certificates/{cert.id}/print")
    assert res.status_code == 200
    assert grouped in res.text
    assert f"Check this certificate at {verify_on}/v" in res.text
    assert '<svg role="img"' in res.text
    assert "template v2" in res.text
    renderer = C.FakeRenderer()
    assert C.render(school, cert, renderer) == "ready"
    assert grouped in renderer.pages[0]
    assert '<svg role="img"' in renderer.pages[0]
    # A draft never carries a code.
    tc = C.pending_tc(school)
    draft = api.call(school.people["principal"], "GET", f"/api/v1/certificates/{tc.id}/print")
    assert "Check this certificate" not in draft.text


def test_FR_CERT_020_without_a_verification_host_nothing_is_printed(school: Any, api: Any) -> None:
    cert = C.issue(school, C.student(school), "conduct")
    res = api.call(school.people["office_staff"], "GET", f"/api/v1/certificates/{cert.id}/print")
    assert "Check this certificate" not in res.text
    assert "<svg" not in res.text


# --- FR-CERT-021, FR-CERT-024: the public lookup -----------------------------------------------


def test_FR_CERT_021_valid_code_shows_exactly_the_frozen_printed_content(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    code = _code(admin_engine, cert.id)
    res = _lookup(api, code)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {
        "status",
        "certificate_type",
        "title_en",
        "school_name",
        "school_place",
        "serial",
        "issued_on",
        "duplicate",
        "cancelled_on",
        "content",
    }
    assert body["status"] == "valid"
    assert body["serial"] == cert.serial
    assert body["duplicate"] is None
    assert body["cancelled_on"] is None
    assert cert.content is not None
    assert body["content"] == cert.content.model_dump(mode="json")
    assert str(cert.student_id) not in res.text, "no record ids on the public page"
    assert str(school.tenant_id) not in res.text
    assert res.headers["Cache-Control"] == "private, no-store"
    assert res.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_FR_CERT_021_duplicate_says_copy_and_original_serial(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "study")
    dup = C.duplicate(school, cert.id)
    body = _lookup(api, _code(admin_engine, dup.id)).json()
    assert body["status"] == "valid"
    assert body["duplicate"]["copy_no"] == dup.duplicate_no == 1
    assert body["duplicate"]["original_serial"] == cert.serial
    assert body["serial"] == cert.serial


def test_FR_CERT_021_cancelled_shows_the_date_and_no_content(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "study")
    dup = C.duplicate(school, cert.id)
    C.cancel(school, cert)
    for target in (cert.id, dup.id):  # a copy of a cancelled original is void with it
        body = _lookup(api, _code(admin_engine, target)).json()
        assert body["status"] == "cancelled"
        assert body["content"] is None
        assert body["cancelled_on"] is not None
        assert body["serial"] == cert.serial


def test_FR_CERT_024_each_check_is_counted_and_audited_with_a_hashed_address(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    code = _code(admin_engine, cert.id)
    for _ in range(3):
        assert _lookup(api, code).status_code == 200
    row = _codes(admin_engine, cert.id)[0]
    assert row["check_count"] == 3
    assert row["last_checked_at"] is not None
    checks = [
        e
        for e in C.audit_rows(admin_engine, school.tenant_id, cert.id)
        if e["action"] == "certificate.verified_publicly"
    ]
    assert len(checks) == 3
    assert checks[0]["actor_type"] == "system"
    assert checks[0]["summary"]["status_shown"] == "valid"
    assert len(checks[0]["summary"]["ip_hash"]) >= 16
    assert code not in str(checks)
    seen = api.call(
        school.people["auditor_readonly"], "GET", f"/api/v1/certificates/{cert.id}/verification"
    ).json()
    assert seen["check_count"] == 3
    assert seen["last_checked_at"] is not None


def test_FR_CERT_022_unknown_mistyped_and_malformed_codes_get_one_404(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    real = _code(admin_engine, cert.id)
    mistyped = real[:-1] + ("0" if real[-1] != "0" else "1")
    answers = [
        _lookup(api, x) for x in (mistyped, "Z" * 16, real.lower(), "TC-2026-27-0001", "x" * 40)
    ]
    assert {r.status_code for r in answers} == {404}
    bodies = [_without_request_id(r) for r in answers]
    assert all(b == bodies[0] for b in bodies)
    assert "detail" not in bodies[0]
    assert _codes(admin_engine, cert.id)[0]["check_count"] == 0


def test_FR_PUB_001_lookup_refuses_user_tokens_and_missing_service_tokens(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    code = _code(admin_engine, C.issue(school, C.student(school), "bonafide").id)
    res = api.client.request("GET", f"/api/v1/public/certificates/{code}")
    assert res.status_code == 401
    res = _lookup(api, code, Authorization="Bearer synthetic-user-token")
    assert res.status_code == 401


def test_FR_PUB_011_twenty_lookups_per_ten_minutes_then_429_for_any_code(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    code = _code(admin_engine, C.issue(school, C.student(school), "bonafide").id)
    quota = rl.load_config().policies["cert_verify_ip"].quota
    assert quota == 20
    assert [_lookup(api, code).status_code for _ in range(quota)] == [200] * quota
    refused = _lookup(api, code)
    assert refused.status_code == 429
    assert refused.json()["code"] == "rate_limited"
    # A used-up budget answers the same for an unknown code (no oracle).
    assert _lookup(api, "Z" * 16).status_code == 429


def test_FR_PUB_011_failed_lookups_spend_the_failure_budget_first(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    code = _code(admin_engine, C.issue(school, C.student(school), "bonafide").id)
    failures = rl.load_config().policies["public_lookup_fail"].quota // 2
    assert [_lookup(api, "Z" * 16).status_code for _ in range(failures)] == [404] * failures
    assert _lookup(api, code).status_code == 429


# --- FR-CERT-023: add later and withdraw (principal, step-up) ---------------------------------


def test_FR_CERT_023_principal_adds_a_code_to_a_certificate_issued_before_r2(
    school: Any, api: Any, admin_engine: Engine, pre_r2: None
) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    assert _codes(admin_engine, cert.id) == []
    path = f"/api/v1/certificates/{cert.id}/verification"
    principal = school.people["principal"]
    assert api.call(principal, "GET", path).json()["status"] == "none"
    body = {"reason": "The receiving school asked how to check the paper"}
    clerk = api.call(school.people["office_admin"], "POST", path, json=body)
    assert clerk.status_code == 403
    stale = api.call(principal, "POST", path, json=body, auth_age_s=301)
    assert stale.status_code == 428
    short = api.call(principal, "POST", path, json={"reason": "short"})
    assert short.status_code == 422
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(certificates, "_create_verification", _REAL_CREATE)
        added = api.call(principal, "POST", path, json=body)
        assert added.status_code == 201, added.text
        out = added.json()
        assert out["status"] == "active"
        assert out["created_reason"] == "added_later"
        assert len(out["code"]) == 19  # four groups
        again = api.call(principal, "POST", path, json=body)
        assert again.status_code == 409
        assert again.json()["code"] == "verification_exists"
    assert _codes(admin_engine, cert.id)[0]["created_note"] == body["reason"]
    actions = C.actions(admin_engine, school.tenant_id, cert.id)
    assert actions.count("certificate.verification_added") == 1


def test_FR_CERT_023_withdrawn_code_answers_not_found_and_stays_withdrawn(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    code = _code(admin_engine, cert.id)
    assert _lookup(api, code).status_code == 200
    principal = school.people["principal"]
    path = f"/api/v1/certificates/{cert.id}/verification"
    current = api.call(principal, "GET", path)
    etag = current.headers["ETag"]
    withdraw = f"{path}/withdraw"
    body = {"reason": REASON}
    missing = api.call(principal, "POST", withdraw, json=body)
    assert missing.status_code == 400
    wrong = api.call(principal, "POST", withdraw, json=body, headers={"If-Match": 'W/"9"'})
    assert wrong.status_code == 412
    clerk = api.call(
        school.people["office_admin"], "POST", withdraw, json=body, headers={"If-Match": etag}
    )
    assert clerk.status_code == 403
    ok = api.call(principal, "POST", withdraw, json=body, headers={"If-Match": etag})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "withdrawn"
    assert ok.json()["withdraw_reason"] == REASON
    gone = _lookup(api, code)
    assert gone.status_code == 404
    again = api.call(
        principal, "POST", withdraw, json=body, headers={"If-Match": ok.headers["ETag"]}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "verification_not_active"
    with admin_engine.connect() as c:
        revoked: str = c.execute(
            text(
                "SELECT h.revoked_reason FROM core.public_handles h "
                "JOIN sis.certificate_verifications v ON v.handle_id = h.id "
                "WHERE v.certificate_id = :c"
            ),
            {"c": cert.id},
        ).scalar_one()
    assert revoked == "withdrawn"
    assert "certificate.verification_withdrawn" in C.actions(
        admin_engine, school.tenant_id, cert.id
    )
    # A duplicate issued later carries a new, working code.
    dup = C.duplicate(school, cert.id)
    assert _lookup(api, _code(admin_engine, dup.id)).status_code == 200


def test_FR_CERT_023_service_refuses_without_recent_mfa(school: Any) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    stale = dataclasses.replace(
        C.ctx(school, school.people["principal"], "principal"), auth_time=None
    )
    with pytest.raises(StepUpRequired):
        C.call(
            school,
            school.people["principal"],
            "principal",
            certificates.withdraw_verification,
            cert.id,
            VerificationReasonIn(reason=REASON),
            expected_version=1,
            as_ctx=stale,
        )


def test_SEC_015_out_of_scope_and_other_school_verification_is_404(
    school: Any, api: Any, world: Any
) -> None:
    cert = C.issue(school, C.student(school, "section_10a"), "bonafide")
    path = f"/api/v1/certificates/{cert.id}/verification"
    assert api.call(school.people["scoped_clerk"], "GET", path).status_code == 404
    other = world.a.people["principal"]
    assert api.call(other, "GET", path).status_code == 404


# --- SEC-037: codes never reach the logs ------------------------------------------------------


def test_SEC_037_codes_never_reach_the_logs(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    cert = C.issue(school, C.student(school), "bonafide")
    code = _code(admin_engine, cert.id)
    _lookup(api, code)
    _lookup(api, "Z" * 16)
    api.call(
        school.people["principal"],
        "POST",
        f"/api/v1/certificates/{cert.id}/verification/withdraw",
        json={"reason": REASON},
        headers={"If-Match": 'W/"1"'},
    )
    out, err = capsys.readouterr()
    logged = out + err
    assert code not in logged
    assert "-".join(code[i : i + 4] for i in range(0, 16, 4)) not in logged
    assert "certificate.verified_publicly" in logged
    assert "public.lookup_failed" in logged
