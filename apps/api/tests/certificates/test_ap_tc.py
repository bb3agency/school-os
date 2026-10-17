"""The Andhra Pradesh TC format end to end (R2-1A; ADR-0045 decisions 1-3; FR-CERT-015..018;
US-2001, US-2006; owner decisions D3, D15, D16). Synthetic data only.

The school's format is switched in ``core.tenants.settings`` for the duration of one test and
switched back, so the other certificate tests keep the generic format."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.apaar import service as apaar_service
from app.certificates import formats as tc_formats
from app.certificates import service as certificates
from app.core.db import tenant_session
from app.core.errors import ValidationFailed
from app.devtools.fake_ids import synthetic_apaar_id, synthetic_udise_pen
from app.students.schemas import ValueIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import TenantSettingsPatch

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]


def _settings(admin: Engine, tenant_id: uuid.UUID, patch: dict[str, Any]) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenants SET settings = COALESCE(settings, '{}'::jsonb) "
                "|| CAST(:p AS jsonb) WHERE id = :t"
            ),
            {"p": json.dumps(patch), "t": tenant_id},
        )


@pytest.fixture
def ap(school: Any, admin_engine: Engine) -> Iterator[None]:
    _settings(admin_engine, school.tenant_id, {"certificate_format": {"transfer": "ap-tc-2027"}})
    yield
    _settings(
        admin_engine,
        school.tenant_id,
        {"certificate_format": {"transfer": "generic-v1"}, "print_restricted_tc_fields": False},
    )


def ap_inputs(**overrides: str) -> dict[str, str]:
    return {
        "identification_mark_1": "A mole on the left cheek",
        "languages_studied": "Telugu, Hindi, English",
        "medium": "medium_english",
        "promotion": "promoted",
        "working_days": "200",
        "days_attended": "187",
        "application_date": C.today().isoformat(),
        "leaving_date": C.today().isoformat(),
        "leaving_reason": "parent_transferred",
        "conduct": "good",
        **overrides,
    }


def _student(school: Any, *, pen: str | None = None, apaar: str | None = None) -> uuid.UUID:
    import random

    rng = random.Random()
    extra = [
        ValueIn(attribute_key="caste", source="admission_register", value="Synthetic Caste"),
        ValueIn(attribute_key="religion", source="admission_register", value="Synthetic Faith"),
        ValueIn(
            attribute_key="udise_pen", source="udise_plus", value=pen or synthetic_udise_pen(rng)
        ),
        ValueIn(
            attribute_key="apaar_id", source="udise_plus", value=apaar or synthetic_apaar_id(rng)
        ),
    ]
    sid: uuid.UUID = C.student(school, extra=extra)
    return sid


def _verify(admin: Engine, student_id: uuid.UUID, key: str) -> str:
    with admin.begin() as c:
        value: str = c.execute(
            text(
                "UPDATE sis.attribute_values SET verification_status = 'verified', "
                "verified_at = now() WHERE student_id = :s AND attribute_key = :k "
                "RETURNING value_text"
            ),
            {"s": student_id, "k": key},
        ).scalar_one()
    return value


def _ap_tc(school: Any, sid: uuid.UUID, **inputs: str) -> Any:
    return C.approve(school, C.issue(school, sid, "transfer", inputs=ap_inputs(**inputs)))


def _lines(cert: Any) -> dict[str, str | None]:
    assert cert.content is not None
    return {line.key: line.value for line in cert.content.fields}


def test_US_2001_preview_lists_the_ap_lines_in_order_with_their_source(
    school: Any, ap: None
) -> None:
    sid = _student(school)
    preview = C.call(
        school,
        school.people["office_admin"],
        "office_admin",
        certificates.preview,
        sid,
        "transfer",
    )
    fmt = tc_formats.get_format("ap-tc-2027")
    assert fmt is not None
    assert preview.format_key == "ap-tc-2027"
    assert [f.key for f in preview.fields] == [line.key for line in fmt.lines]
    fills = {f.key: f.fill for f in preview.fields}
    assert fills["full_name"] == "record"
    assert fills["identification_mark_1"] == "input"
    assert fills["dob_in_words"] == "derived"
    assert fills["udise_pen"] == "identifier"
    assert fills["religion"] == fills["caste"] == "restricted"
    by_key = {f.key: f for f in preview.fields}
    assert by_key["full_name"].source == "admission_register"
    assert by_key["religion"].value is None  # off by default (D15)
    assert preview.can_issue is True


def test_FR_CERT_016_ap_inputs_are_validated(school: Any, ap: None) -> None:
    sid = _student(school)
    bad = ap_inputs(working_days="two hundred", identification_mark_1="234123412346")
    del bad["languages_studied"]
    with pytest.raises(ValidationFailed) as err:
        C.issue(school, sid, "transfer", inputs=bad)
    codes = {e["field"]: e["code"] for e in err.value.errors}
    assert codes["inputs.languages_studied"] == "missing"
    assert codes["inputs.working_days"] == "invalid_number"
    assert codes["inputs.identification_mark_1"] == "aadhaar_full_number_rejected"
    with pytest.raises(ValidationFailed) as err:
        C.issue(school, sid, "transfer", inputs=ap_inputs(days_attended="400"))
    assert err.value.errors[0]["code"] == "too_large"


def test_FR_CERT_015_ap_tc_is_frozen_in_the_format_with_its_key_and_version(
    school: Any, ap: None, admin_engine: Engine
) -> None:
    cert = _ap_tc(school, _student(school))
    fmt = tc_formats.get_format("ap-tc-2027")
    assert fmt is not None
    assert (cert.format_key, cert.format_version) == ("ap-tc-2027", fmt.version)
    row = C.row(admin_engine, cert.id)
    assert (row["format_key"], row["format_version"]) == ("ap-tc-2027", fmt.version)
    assert row["template_version"] == "v2"
    lines = _lines(cert)
    assert list(lines) == [line.key for line in fmt.lines]
    assert lines["medium"] == "English"
    assert lines["promotion"] == "Yes, promoted"
    assert lines["working_days"] == "200"
    assert lines["identification_mark_2"] is None  # optional, left blank to fill by hand
    assert lines["dob_in_words"] == "Fourteenth March Two Thousand Twelve"
    assert cert.content.title_en == "Transfer certificate"
    # The register still finds the leaving date among the format's lines.
    assert lines["leaving_date"] is not None


def test_FR_CERT_018_religion_and_caste_are_blank_unless_the_school_prints_them(
    school: Any, ap: None, admin_engine: Engine
) -> None:
    sid = _student(school)
    off = _ap_tc(school, sid)
    assert _lines(off)["religion"] is None
    assert _lines(off)["caste"] is None
    page = C.call(
        school, school.people["office_admin"], "office_admin", certificates.print_page, off.id
    )
    assert "Synthetic Caste" not in page
    _settings(admin_engine, school.tenant_id, {"print_restricted_tc_fields": True})
    on = _ap_tc(school, _student(school))
    assert _lines(on)["religion"] == "Synthetic Faith"
    assert _lines(on)["caste"] == "Synthetic Caste"


def test_FR_CERT_018_preview_masks_printed_restricted_values_without_read_sensitive(
    school: Any, ap: None, admin_engine: Engine
) -> None:
    _settings(admin_engine, school.tenant_id, {"print_restricted_tc_fields": True})
    sid = _student(school)
    clerk = C.call(
        school, school.people["office_staff"], "office_staff", certificates.preview, sid, "transfer"
    )
    caste = next(f for f in clerk.fields if f.key == "caste")
    assert (caste.value, caste.masked) == (None, True)


def test_FR_CERT_017_pen_only_when_verified(school: Any, ap: None, admin_engine: Engine) -> None:
    sid = _student(school)
    unverified = _ap_tc(school, sid)
    assert _lines(unverified)["udise_pen"] == "—"
    sid2 = _student(school)
    pen = _verify(admin_engine, sid2, "udise_pen")
    assert _lines(_ap_tc(school, sid2))["udise_pen"] == pen


@pytest.mark.parametrize(
    ("consent", "printed"),
    [("given", True), ("refused", False), ("withdrawn", False), (None, False)],
)
def test_FR_CERT_017_apaar_id_only_verified_and_with_consent_given(  # noqa: PLR0917
    school: Any,
    ap: None,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    consent: str | None,
    printed: bool,
) -> None:
    sid = _student(school)
    apaar_id = _verify(admin_engine, sid, "apaar_id")
    statuses = {sid: consent} if consent else {}
    monkeypatch.setattr(apaar_service, "consent_statuses", lambda s, ids: statuses)
    lines = _lines(_ap_tc(school, sid))
    assert lines["apaar_id"] == (apaar_id if printed else "—")


def test_FR_CERT_017_an_unverified_apaar_id_is_never_printed_even_with_consent(
    school: Any, ap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _student(school)
    monkeypatch.setattr(apaar_service, "consent_statuses", lambda s, ids: {sid: "given"})
    assert _lines(_ap_tc(school, sid))["apaar_id"] == "—"


def test_US_2006_switching_back_keeps_issued_certificates_and_new_ones_are_generic(
    school: Any, ap: None, admin_engine: Engine
) -> None:
    ap_cert = _ap_tc(school, _student(school))
    _settings(admin_engine, school.tenant_id, {"certificate_format": {"transfer": "generic-v1"}})
    generic = C.issued_tc(school)
    assert generic.format_key == "generic-v1"
    assert generic.content is not None
    assert generic.content.details  # the generic layout
    again = C.call(
        school, school.people["principal"], "principal", certificates.get_certificate, ap_cert.id
    )
    assert again.format_key == "ap-tc-2027"
    assert again.content == ap_cert.content


def test_FR_CERT_015_a_pending_request_in_an_older_format_version_must_be_prepared_again(
    school: Any, ap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = C.issue(school, _student(school), "transfer", inputs=ap_inputs())
    fmt = tc_formats.get_format("ap-tc-2027")
    assert fmt is not None
    newer = fmt.model_copy(update={"version": fmt.version + 1})
    monkeypatch.setattr(tc_formats, "formats", lambda: {"ap-tc-2027": newer})
    with pytest.raises(certificates.FormatChanged):
        C.approve(school, pending)


def test_FR_CERT_015_type_catalog_and_formats_follow_the_school(
    school: Any, ap: None, api: Any
) -> None:
    with tenant_session(school.tenant_id) as db:
        catalog = {t.key: t for t in certificates.types_catalog(db)}
    tc = catalog["transfer"]
    assert (tc.format_key, tc.format_verified) == ("ap-tc-2027", False)
    assert {i.key for i in tc.inputs} == {*ap_inputs(), "identification_mark_2", "remarks"}
    assert catalog["bonafide"].format_key is None
    res = api.call(school.people["office_staff"], "GET", "/api/v1/certificates/formats")
    assert res.status_code == 200, res.text
    formats = {f["key"]: f for f in res.json()}
    assert set(formats) == {"generic-v1", "ap-tc-2027"}
    assert formats["ap-tc-2027"]["verified"] is False
    assert formats["ap-tc-2027"]["sources"]
    assert {ln["key"] for ln in formats["ap-tc-2027"]["lines"] if ln["restricted"]} == {
        "religion",
        "caste",
    }
    assert (
        api.call(school.people["accountant"], "GET", "/api/v1/certificates/formats").status_code
        == 403
    )


def test_FR_CERT_018_settings_change_is_audited_with_the_field_keys(
    school: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    try:
        with tenant_session(school.tenant_id, owner.user_id) as db:
            version = tenancy.get_tenant(db).version
            out = tenancy.update_tenant_settings(
                db,
                TenantSettingsPatch.model_validate(
                    {
                        "certificate_format": {"transfer": "ap-tc-2027"},
                        "print_restricted_tc_fields": True,
                    }
                ),
                expected_version=version,
            )
        assert out.settings.certificate_format.transfer == "ap-tc-2027"
        assert out.settings.print_restricted_tc_fields is True
        with admin_engine.connect() as c:
            summary: dict[str, Any] = c.execute(
                text(
                    "SELECT summary FROM audit.events WHERE tenant_id = :t "
                    "AND action = 'tenant.settings_updated' ORDER BY seq DESC LIMIT 1"
                ),
                {"t": school.tenant_id},
            ).scalar_one()
        assert summary["certificate_format"] == {"transfer": "ap-tc-2027"}
        assert summary["print_restricted_tc_fields"] is True
        assert summary["restricted_tc_fields"] == ["caste", "religion"]
        with pytest.raises(ValueError, match="transfer"):
            TenantSettingsPatch.model_validate({"certificate_format": {"transfer": "cbse-v9"}})
    finally:
        _settings(
            admin_engine,
            school.tenant_id,
            {"certificate_format": {"transfer": "generic-v1"}, "print_restricted_tc_fields": False},
        )
