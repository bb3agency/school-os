"""Print jobs: parent verification slips and APAAR consent forms as server-rendered PDFs
(FR-DQ-047, FR-APC-007; ADR-0039, ADR-0040 follow-ups; owner decision D9).

The worker prints the same pages as the browser print views through the export pipeline:
same permissions and reach as the print views (404 outside, 403 without the permission, 428
without a recent MFA sign-in), files kept like exports (private store, presigned download),
audited, never an Aadhaar number. PDF bytes come from the fake renderer here; real Chromium is
covered at the end and skipped when it is not installed. Synthetic data only.
"""

from __future__ import annotations

import re
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.apaar import service as apaar_service
from app.core import pdf as core_pdf
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
EX = sys.modules["sos_test_exports_objects"]
SSC = "bseap-ssc-2027"
SLIPS = "/api/v1/exports/readiness-slips"
FORMS = "/api/v1/exports/apaar-consent-forms"
AADHAAR_LIKE = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")


def _aadhaar(name: str) -> list[ValueIn]:
    src = "aadhaar_as_printed"
    return [
        ValueIn(attribute_key="aadhaar_name_as_printed", source=src, value=name),
        ValueIn(attribute_key="aadhaar_dob_as_printed", source=src, value="2012-03-14"),
        ValueIn(attribute_key="aadhaar_gender_as_printed", source=src, value="female"),
        ValueIn(attribute_key="aadhaar_last4", source=src, value="4821"),
    ]


def _udise(name: str) -> list[ValueIn]:
    src = "udise_plus"
    return [
        ValueIn(attribute_key="full_name", source=src, value=name),
        ValueIn(attribute_key="dob", source=src, value="2012-03-14"),
        ValueIn(attribute_key="gender", source=src, value="female"),
    ]


def _ssc_student(school: Any, *, section_key: str = "section_9a", ready: bool = False) -> uuid.UUID:
    """A Class IX/X student whose Aadhaar name lacks the space in "Sai Kumar" (needs_parent),
    or, with ``ready``, whose records all match."""
    word = uuid.uuid4().hex[:6]
    letters = "".join(chr(ord("a") + int(c, 16) % 26) for c in word).capitalize()
    name = f"Synthetica {letters} Sai Kumar"
    aadhaar_name = name if ready else name.replace("Sai Kumar", "Saikumar")
    sid: uuid.UUID = EX.student(
        school,
        section_key=section_key,
        name=name,
        extra=[*_aadhaar(aadhaar_name), *_udise(name)],
    )
    return sid


def _post(api: Any, who: Any, path: str, body: dict[str, Any], **kw: Any) -> Any:
    return api.call(
        who, "POST", path, json=body, headers={"Idempotency-Key": uuid.uuid4().hex}, **kw
    )


def _last_page() -> str:
    page: str = EX.FAKE.pages[-1]
    return page


# --- parent verification slips (FR-DQ-047) -----------------------------------------------------


def test_FR_DQ_047_section_slips_pdf_request_render_download_audit(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    sid = _ssc_student(school)
    office = school.people["office_admin"]
    body = {"profile_key": SSC, "section_id": str(school.ids["section_9a"])}
    res = _post(api, office, SLIPS, body)
    assert res.status_code == 202, res.text
    out = res.json()
    assert out["kind"] == "readiness_slips"
    assert out["formats"] == ["pdf"]
    assert out["profile_key"] == SSC
    assert out["scope"] == {"section_ids": [str(school.ids["section_9a"])]}
    assert out["include_sensitive"] is False
    export_id = uuid.UUID(out["id"])
    row = EX.export_row(admin_engine, export_id)
    assert sid in row["student_ids"]

    assert EX.run(school, export_id) == "ready"
    page = _last_page()
    assert "Parent verification slip" in page
    assert "Aadhaar" in page or "aadhaar" in page.lower()
    # C3 values stay masked without include_sensitive; never an Aadhaar number.
    assert "Saikumar" not in page
    assert not AADHAAR_LIKE.search(page)
    # English only while Telugu is hidden and the school prints parent forms in English.
    assert "@font-face" not in page
    assert 'class="te"' not in page
    stored = EX.stored(export_id, "pdf")
    assert stored.startswith(b"%PDF")

    link = api.call(office, "GET", f"/api/v1/exports/{export_id}/download-url")
    assert link.status_code == 200, link.text
    assert link.json()["filename"].startswith(f"schoolos-verification-slips-{SSC}-")
    assert link.json()["filename"].endswith(".pdf")
    actions = [r["action"] for r in EX.audit_rows(admin_engine, school.tenant_id, export_id)]
    assert actions == ["export.requested", "export.completed", "export.downloaded"]
    requested = EX.audit_rows(admin_engine, school.tenant_id, export_id)[0]["summary"]
    assert requested["kind"] == "readiness_slips"
    assert requested["include_ready"] is True
    assert "student_ids" not in requested


def test_FR_DQ_047_student_slip_with_restricted_values_needs_permission(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    sid = _ssc_student(school)
    principal = school.people["principal"]
    res = _post(
        api,
        principal,
        SLIPS,
        {"profile_key": SSC, "student_id": str(sid), "include_sensitive": True},
    )
    assert res.status_code == 202, res.text
    assert res.json()["scope"] == {"student_ids": [str(sid)]}
    export_id = uuid.UUID(res.json()["id"])
    assert EX.run(school, export_id) == "ready"
    page = _last_page()
    assert "Saikumar" in page  # the Aadhaar-as-printed name, for an allowed person
    assert not AADHAAR_LIKE.search(page)
    assert "4821" not in page  # not even the last four digits on a slip
    summary = EX.audit_rows(admin_engine, school.tenant_id, export_id)[0]["summary"]
    assert summary["include_sensitive"] is True
    assert summary["sensitive_columns"] == ["aadhaar_as_printed"]
    # Restricted values need a fresh sign-in to download (ADR-0021).
    stale = api.call(principal, "GET", f"/api/v1/exports/{export_id}/download-url", auth_age_s=301)
    assert stale.status_code == 428
    # A role without student.read_sensitive cannot ask for them.
    staff = school.people["office_staff"]
    denied = _post(
        api, staff, SLIPS, {"profile_key": SSC, "student_id": str(sid), "include_sensitive": True}
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "sensitive_not_allowed"


def test_FR_DQ_047_slips_need_permission_scope_and_step_up(
    school: Any, api: Any, world: Any
) -> None:
    sid = _ssc_student(school)
    other = _ssc_student(school, section_key="section_9c")
    body = {"profile_key": SSC, "student_id": str(sid)}
    # Without dq.readiness.read: 403 (accountant, subject teacher).
    for role in ("accountant", "teacher"):
        assert _post(api, school.people[role], SLIPS, body).status_code == 403
    # Every export needs a recent MFA sign-in.
    stale = _post(api, school.people["office_admin"], SLIPS, body, auth_age_s=301)
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    # Class teachers: their own section only; others are 404 like other schools' ids.
    teacher = school.people["class_teacher"]
    assert _post(api, teacher, SLIPS, body).status_code == 202
    assert (
        _post(api, teacher, SLIPS, {"profile_key": SSC, "student_id": str(other)}).status_code
        == 404
    )
    nine_c = {"profile_key": SSC, "section_id": str(school.ids["section_9c"])}
    assert _post(api, teacher, SLIPS, nine_c).status_code == 404
    # Another school's section or student: 404.
    foreign = {"profile_key": SSC, "section_id": str(world.a.ids["section_9a"])}
    assert _post(api, school.people["office_admin"], SLIPS, foreign).status_code == 404
    # One student or one section, and a readiness profile.
    office = school.people["office_admin"]
    assert _post(api, office, SLIPS, {"profile_key": SSC}).status_code == 422
    both = {**body, "section_id": str(school.ids["section_9a"])}
    assert _post(api, office, SLIPS, both).status_code == 422
    assert _post(api, office, SLIPS, {**body, "profile_key": "udise-plus"}).status_code == 404


def test_FR_DQ_047_only_students_needing_action(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    ready = _ssc_student(school, section_key="section_10a", ready=True)
    needs = _ssc_student(school, section_key="section_10a")
    office = school.people["office_admin"]
    body = {
        "profile_key": SSC,
        "section_id": str(school.ids["section_10a"]),
        "include_ready": False,
    }
    res = _post(api, office, SLIPS, body)
    assert res.status_code == 202, res.text
    frozen = EX.export_row(admin_engine, uuid.UUID(res.json()["id"]))["student_ids"]
    assert needs in frozen
    assert ready not in frozen


def test_D9_slips_follow_the_school_parent_language(
    school: Any, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A school printing parent forms in Telugu gets bilingual slips with the bundled font,
    while the staff screens stay English (SOS_TELUGU_ENABLED off)."""
    sid = _ssc_student(school)
    monkeypatch.setattr(apaar_service, "parent_language", lambda session: "te")
    res = _post(
        api, school.people["office_admin"], SLIPS, {"profile_key": SSC, "student_id": str(sid)}
    )
    assert res.status_code == 202, res.text
    assert EX.run(school, uuid.UUID(res.json()["id"])) == "ready"
    page = _last_page()
    assert 'class="te"' in page
    assert "@font-face" in page
    assert core_pdf.FONT_URL in page


def test_FR_DQ_047_worker_fails_when_permission_was_revoked(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    sid = _ssc_student(school)
    person = EX.W.add_member(admin_engine, school.tenant_id, ["office_staff"])
    res = _post(api, person, SLIPS, {"profile_key": SSC, "student_id": str(sid)})
    assert res.status_code == 202, res.text
    export_id = uuid.UUID(res.json()["id"])
    with admin_engine.begin() as c:
        c.execute(
            text("DELETE FROM core.membership_roles WHERE membership_id = :m"),
            {"m": person.membership_id},
        )
    assert EX.run(school, export_id) == "failed"
    row = EX.export_row(admin_engine, export_id)
    assert (row["status"], row["error_code"]) == ("failed", "permission_revoked")
    assert EX.files(admin_engine, export_id) == []


# --- APAAR consent forms (FR-APC-007) ----------------------------------------------------------


def test_FR_APC_007_section_forms_pdf(school: Any, api: Any, admin_engine: Engine) -> None:
    sid = EX.student(school, section_key="section_9c")
    staff = school.people["office_staff"]
    body = {"section_id": str(school.ids["section_9c"]), "status": "pending"}
    res = _post(api, staff, FORMS, body)
    assert res.status_code == 202, res.text
    out = res.json()
    assert out["kind"] == "apaar_consent_forms"
    assert out["profile_key"] is None
    assert out["language"] == "en"
    export_id = uuid.UUID(out["id"])
    assert sid in EX.export_row(admin_engine, export_id)["student_ids"]
    assert EX.run(school, export_id) == "ready"
    page = _last_page()
    assert "<article>" in page
    assert "@font-face" not in page
    assert not AADHAAR_LIKE.search(page)
    summary = EX.audit_rows(admin_engine, school.tenant_id, export_id)[0]["summary"]
    assert summary["status_filter"] == "pending"
    assert summary["form_version"]
    link = api.call(staff, "GET", f"/api/v1/exports/{export_id}/download-url")
    assert link.status_code == 200, link.text
    assert "apaar-consent-forms" in link.json()["filename"]


def test_D9_telugu_consent_form_pdf_while_staff_ui_is_english(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    """Owner decision D9: the parent form may be Telugu although SOS_TELUGU_ENABLED is off; the
    PDF declares the bundled Telugu font and the export keeps the language."""
    sid = EX.student(school)
    res = _post(api, school.people["principal"], FORMS, {"student_id": str(sid), "language": "te"})
    assert res.status_code == 202, res.text
    assert res.json()["language"] == "te"
    assert EX.run(school, uuid.UUID(res.json()["id"])) == "ready"
    page = _last_page()
    assert '<html lang="te">' in page
    assert core_pdf.FONT_URL in page
    assert core_pdf.FONT_FAMILY in page


def test_FR_APC_007_forms_permission_scope_and_validation(
    school: Any, api: Any, world: Any
) -> None:
    sid = EX.student(school)
    other = EX.student(school, section_key="section_10a")
    body = {"student_id": str(sid)}
    for role in ("accountant", "teacher"):
        assert _post(api, school.people[role], FORMS, body).status_code == 403
    assert _post(api, school.people["office_admin"], FORMS, body, auth_age_s=301).status_code == 428
    teacher = school.people["class_teacher"]
    assert _post(api, teacher, FORMS, body).status_code == 202
    assert _post(api, teacher, FORMS, {"student_id": str(other)}).status_code == 404
    foreign = {"section_id": str(world.a.ids["section_9a"])}
    assert _post(api, school.people["office_admin"], FORMS, foreign).status_code == 404
    office = school.people["office_admin"]
    assert _post(api, office, FORMS, {}).status_code == 422
    with_status = _post(api, office, FORMS, {"student_id": str(sid), "status": "pending"})
    assert with_status.status_code == 422
    nobody = _post(
        api, office, FORMS, {"section_id": str(school.ids["section_9a"]), "status": "withdrawn"}
    )
    assert nobody.status_code == 409
    assert nobody.json()["code"] == "no_forms"


def test_print_jobs_list_as_own_exports_for_class_teachers(school: Any, api: Any) -> None:
    sid = EX.student(school)
    teacher = school.people["class_teacher"]
    res = _post(api, teacher, FORMS, {"student_id": str(sid)})
    assert res.status_code == 202, res.text
    listed = api.call(teacher, "GET", "/api/v1/exports")
    assert listed.status_code == 200
    mine = {e["id"]: e for e in listed.json()["data"]}
    assert res.json()["id"] in mine
    assert mine[res.json()["id"]]["own"] is True
    # requested_by=all stays export.read_all only.
    assert (
        api.call(teacher, "GET", "/api/v1/exports", params={"requested_by": "all"}).status_code
        == 403
    )


# --- renderer: the parent page font (D9) --------------------------------------------------------


def test_D9_render_document_asks_chromium_for_the_font_only_for_telugu_parent_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def fake_run(self: Any, html: str, work: Any, *, load: bool = True, handler: Any) -> bytes:
        calls.append(handler)
        return b"%PDF-x"

    monkeypatch.setattr(core_pdf.ChromiumRenderer, "_run", fake_run)
    renderer = core_pdf.ChromiumRenderer(sandbox=False, timeout_ms=1000)
    core_pdf.render_document(core_pdf.PrintDocument("<p>x</p>", telugu_font=True), renderer)
    core_pdf.render_document(core_pdf.PrintDocument("<p>x</p>"), renderer)
    assert calls == [core_pdf._route_telugu_page, core_pdf._route]


class _Request:
    def __init__(self, url: str) -> None:
        self.url = url


class _Route:
    def __init__(self, url: str) -> None:
        self.request = _Request(url)
        self.fulfilled: bytes | None = None
        self.aborted = False

    def fulfill(self, *, status: int, body: bytes, content_type: str) -> None:
        self.fulfilled = body

    def abort(self, error_code: str) -> None:
        self.aborted = True


def test_D9_telugu_parent_page_gets_the_font_and_nothing_else_while_telugu_is_hidden() -> None:
    assert core_pdf.font_face_css() == ""
    assert core_pdf.FONT_URL in core_pdf.font_face_css(telugu=True)
    font = _Route(core_pdf.FONT_URL)
    core_pdf._route_telugu_page(font)  # type: ignore[arg-type]
    assert font.fulfilled == core_pdf.font_bytes()
    other = _Route("https://example.invalid/x.css")
    core_pdf._route_telugu_page(other)  # type: ignore[arg-type]
    assert other.aborted
    staff = _Route(core_pdf.FONT_URL)
    core_pdf._route(staff)  # type: ignore[arg-type]
    assert staff.aborted


def _chromium_available() -> bool:
    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(chromium_sandbox=False)
            browser.close()
    except Error:
        return False
    return True


@pytest.mark.chromium
def test_D9_real_chromium_embeds_the_font_in_a_telugu_form_while_telugu_is_hidden() -> None:
    if not _chromium_available():
        pytest.skip("headless Chromium is not installed on this machine")
    from app.apaar import templates
    from app.apaar.config import load_config

    form = templates.FormStudent("Synthetica Pupil", "EX/1", "IX-A", None, None)
    renderer = core_pdf.ChromiumRenderer(sandbox=False, timeout_ms=60_000)
    telugu = templates.render_forms(
        load_config(), language="te", school="Synthetic School", students=[form], for_pdf=True
    )
    blob = core_pdf.render_document(core_pdf.PrintDocument(telugu, telugu_font=True), renderer)
    assert blob.startswith(b"%PDF-")
    assert b"NotoSansTelugu" in blob, "the bundled Telugu font is embedded"
    english = templates.render_forms(
        load_config(), language="en", school="Synthetic School", students=[form], for_pdf=True
    )
    blob = core_pdf.render_document(core_pdf.PrintDocument(english), renderer)
    assert blob.startswith(b"%PDF-")
    assert b"NotoSansTelugu" not in blob
