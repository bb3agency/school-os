"""Certificate configuration and print templates, without a database (FR-CERT-001, FR-CERT-006,
FR-CERT-009, FR-CERT-011, FR-REG-004; CLAUDE.md §10 print is first-class)."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.certificates import templates
from app.certificates.config import (
    CERTIFICATE_TYPES,
    CONFIG_PATH,
    CertificatesConfig,
    format_serial,
    load_config,
)
from app.certificates.schemas import CertificateContent, ContentLine
from app.core.languages import contains_telugu as templates_contain_telugu
from app.core.redaction import verhoeff_check_digit

ATTRIBUTES = Path(__file__).resolve().parents[2] / "app" / "students" / "attributes.yaml"


def _content(**overrides: object) -> CertificateContent:
    base: dict[str, object] = {
        "certificate_type": "transfer",
        "title_en": "Transfer certificate",
        "title_te": "బదిలీ ధృవీకరణ పత్రం",
        "serial": "TC/2026-27/0007",
        "academic_year_label": "2026-27",
        "issued_on": dt.date(2026, 9, 29),
        "school_name_en": "Synthetic Model School",
        "school_name_te": "కృత్రిమ మోడల్ పాఠశాల",
        "school_address_en": "1 Synthetic Road, Guntur",
        "school_address_te": "1 సింథటిక్ రోడ్, గుంటూరు",
        "school_affiliation": "Recognised by Synthetic Board",
        "school_place": "Guntur",
        "student_name": "Synthetica Student",
        "admission_no": "A/123",
        "class_label_en": "Class IX A",
        "class_label_te": "9వ తరగతి A",
        "fields": [
            ContentLine(
                key="full_name",
                label_en="Full name",
                label_te="పూర్తి పేరు",
                value="Synthetica Student",
            ),
            ContentLine(
                key="father_name",
                label_en="Father's name",
                label_te="తండ్రి పేరు",
                value="Synthetic Father",
            ),
            ContentLine(
                key="dob", label_en="Date of birth", label_te="పుట్టిన తేదీ", value="14/03/2012"
            ),
        ],
        "details": [
            ContentLine(
                key="leaving_date",
                label_en="Date of leaving",
                label_te="పాఠశాల విడిచిన తేదీ",
                value="29/09/2026",
            ),
        ],
        "blanks": [
            ContentLine(
                key="identification_marks",
                label_en="Personal marks of identification",
                label_te="గుర్తింపు చిహ్నాలు",
                value=None,
            ),
        ],
    }
    base.update(overrides)
    return CertificateContent.model_validate(base)


# --- configuration -------------------------------------------------------------------------------


def test_FR_CERT_001_config_loads_with_every_type() -> None:
    cfg = load_config()
    assert tuple(sorted(cfg.types)) == tuple(sorted(CERTIFICATE_TYPES))
    assert cfg.spec("transfer").requires_approval
    assert cfg.spec("transfer").ends_enrolment
    assert not cfg.spec("bonafide").requires_approval
    assert cfg.template_version == templates.TEMPLATE_VERSION


def test_FR_CERT_009_config_prints_only_personal_c2_fields_never_aadhaar_or_c3() -> None:
    catalog = yaml.safe_load(ATTRIBUTES.read_text("utf-8"))["attributes"]
    for key, spec in load_config().types.items():
        for field in spec.printed:
            assert field in catalog, f"{key}: unknown attribute {field}"
            assert catalog[field]["classification"] != "C3", f"{key}: {field} is restricted"
            assert not field.startswith("aadhaar"), f"{key}: {field}"


def test_FR_CERT_009_config_refuses_aadhaar_fields() -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text("utf-8"))
    raw["types"]["bonafide"]["printed"].append("aadhaar_last4")
    with pytest.raises(ValidationError, match="Aadhaar"):
        CertificatesConfig.model_validate(raw)


def test_FR_CERT_006_serial_format() -> None:
    cfg = load_config()
    assert format_serial(cfg.serial, "TC", "2026-27", 1) == "TC/2026-27/0001"
    assert format_serial(cfg.serial, "BC", "2026-27", 12345) == "BC/2026-27/12345"


def test_FR_CERT_006_serial_format_placeholders_are_checked() -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text("utf-8"))
    raw["serial"]["format"] = "{prefix}/{student}/{number}"
    with pytest.raises(ValidationError):
        CertificatesConfig.model_validate(raw)
    raw["serial"]["format"] = "{prefix}/{year}"
    with pytest.raises(ValidationError):
        CertificatesConfig.model_validate(raw)


# --- templates -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "words"),
    [
        (dt.date(2012, 3, 14), "Fourteenth March Two Thousand Twelve"),
        (dt.date(2009, 11, 21), "Twenty-First November Two Thousand Nine"),
        (dt.date(2010, 1, 30), "Thirtieth January Two Thousand Ten"),
        (dt.date(1999, 12, 31), "Thirty-First December One Thousand Nine Hundred Ninety-Nine"),
        (dt.date(2020, 2, 20), "Twentieth February Two Thousand Twenty"),
    ],
)
def test_FR_CERT_001_date_of_birth_in_words(day: dt.date, words: str) -> None:
    assert templates.date_in_words(day) == words


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_CERT_011_certificate_page_is_bilingual_escaped_and_masked() -> None:
    body = "34567890123"
    aadhaar = body + verhoeff_check_digit(body)
    content = _content(
        fields=[
            ContentLine(
                key="full_name",
                label_en="Full name",
                label_te="పూర్తి పేరు",
                value="<script>alert(1)</script>",
            )
        ],
        school_affiliation=f"UDISE {aadhaar}",
    )
    page = templates.render_certificate(content, reference="ABCDEF1234")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert aadhaar not in page, "defence in depth: 12-digit Verhoeff numbers are masked"
    assert "TRANSFER CERTIFICATE" in page
    assert "బదిలీ ధృవీకరణ పత్రం" in page
    assert "TC/2026-27/0007" in page
    assert "29/09/2026" in page
    assert "ప్రధానోపాధ్యాయుల సంతకం" in page
    assert "Personal marks of identification" in page, "blank official lines printed"
    assert "<script" not in page.replace("&lt;script", "")


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_CERT_011_marks_draft_cancelled_and_duplicate() -> None:
    content = _content()
    assert "DRAFT" in templates.render_certificate(content, reference="R", mark="draft")
    assert "CANCELLED" in templates.render_certificate(content, reference="R", mark="cancelled")
    dup = templates.render_certificate(
        content,
        reference="R",
        duplicate=templates.DuplicateMark(copy_no=2, issued_on=dt.date(2026, 10, 1)),
    )
    assert "DUPLICATE (copy 2)" in dup
    assert "01/10/2026" in dup
    assert "నకలు" in dup


@pytest.mark.parametrize("certificate_type", ["bonafide", "study", "conduct"])
@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_CERT_001_statement_certificates_are_bilingual(certificate_type: str) -> None:
    details = [
        ContentLine(key="purpose", label_en="Purpose", label_te="ప్రయోజనం", value="Bus pass"),
        ContentLine(key="purpose_te", label_en="", label_te="", value="బస్ పాస్"),
        ContentLine(key="study_from", label_en="", label_te="", value="Class V (2021-22)"),
        ContentLine(key="study_to", label_en="", label_te="", value="Class IX (2026-27)"),
        ContentLine(key="conduct", label_en="", label_te="", value="good"),
        ContentLine(key="conduct_te", label_en="", label_te="", value="మంచిది"),
    ]
    page = templates.render_certificate(
        _content(certificate_type=certificate_type, details=details, blanks=[]), reference="R"
    )
    assert "This is to certify that" in page
    assert "ధృవీకరిస్తున్నాము" in page
    assert "Synthetica Student" in page


def test_FR_REG_004_print_css_is_a4_telugu_safe_and_csp_pins_the_styles() -> None:
    assert "size: A4;" in templates.CERTIFICATE_STYLE
    assert "size: A4 landscape;" in templates.REGISTER_STYLE
    for style in (templates.CERTIFICATE_STYLE, templates.REGISTER_STYLE):
        heights = [float(h) for h in re.findall(r"line-height: ([0-9.]+)", style)]
        assert heights, "line heights are set"
        assert min(heights) >= 1.6, "Telugu vowel signs are not clipped"
        assert "overflow: hidden" not in style
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in templates.STYLE_CSP
    assert "script-src" not in templates.STYLE_CSP
    assert "default-src 'none'" in templates.STYLE_CSP


def test_ADR_0036_telugu_style_variants_keep_the_print_rules_and_are_pinned_too() -> None:
    for style in (templates.CERTIFICATE_STYLE_TE, templates.REGISTER_STYLE_TE):
        heights = [float(h) for h in re.findall(r"line-height: ([0-9.]+)", style)]
        assert min(heights) >= 1.6
        assert "Noto Sans Telugu" in style
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in templates.STYLE_CSP
    for style in (templates.CERTIFICATE_STYLE, templates.REGISTER_STYLE):
        assert "Telugu" not in style
        assert "Nirmala" not in style
        assert "Gautami" not in style
    assert templates.certificate_style() == templates.CERTIFICATE_STYLE
    assert templates.register_style() == templates.REGISTER_STYLE


@pytest.mark.parametrize("certificate_type", ["transfer", "bonafide", "study", "conduct"])
@pytest.mark.parametrize("mark", [None, "draft", "cancelled"])
def test_ADR_0036_certificate_page_is_english_only_while_telugu_is_hidden(
    certificate_type: str, mark: templates.Mark
) -> None:
    details = [
        ContentLine(key="purpose", label_en="Purpose", label_te="ప్రయోజనం", value="Bus pass"),
        ContentLine(key="purpose_te", label_en="", label_te="", value="బస్ పాస్"),
        ContentLine(key="study_from", label_en="", label_te="", value="Class V (2021-22)"),
        ContentLine(key="study_to", label_en="", label_te="", value="Class IX (2026-27)"),
        ContentLine(key="conduct", label_en="", label_te="", value="good"),
        ContentLine(key="conduct_te", label_en="", label_te="", value="మంచిది"),
        ContentLine(
            key="leaving_reason",
            label_en="Reason for leaving",
            label_te="విడిచిపెట్టడానికి కారణం",
            value="Parent transferred / తల్లిదండ్రుల బదిలీ",
        ),
    ]
    content = _content(certificate_type=certificate_type, details=details)
    for duplicate in (None, templates.DuplicateMark(copy_no=2, issued_on=dt.date(2026, 10, 1))):
        for for_pdf in (False, True):
            page = templates.render_certificate(
                content, reference="R", mark=mark, duplicate=duplicate, for_pdf=for_pdf
            )
            assert not templates_contain_telugu(page), (certificate_type, mark, duplicate)
            assert "@font-face" not in page
            assert "Noto Sans Telugu" not in page
            assert "Synthetic Model School" in page
    shown = templates.shown(content)
    assert shown == templates.english_only(content)
    assert not templates_contain_telugu(shown.model_dump_json())
    assert {d.key: d.value for d in shown.details}["leaving_reason"] == "Parent transferred"
    assert templates.english_only(content).student_name == content.student_name


def test_ADR_0036_telugu_script_names_are_data_and_stay() -> None:
    """A name recorded in Telugu script is data, not presentation: it is printed as recorded."""
    content = _content(
        student_name="రాము",
        fields=[
            ContentLine(key="full_name", label_en="Full name", label_te="పూర్తి పేరు", value="రాము")
        ],
    )
    shown = templates.english_only(content)
    assert shown.student_name == "రాము"
    assert shown.fields[0].value == "రాము"
    assert shown.fields[0].label_te == ""


def test_ADR_0036_register_page_is_english_only_while_telugu_is_hidden() -> None:
    for rows in ([["TC/2026-27/0001", "Issued"]], []):
        page = templates.render_register(
            templates.RegisterPage(
                title_en="Transfer certificate register (counterfoil)",
                title_te="బదిలీ ధృవీకరణ పత్రాల రిజిస్టర్",
                school_name="Synthetic Model School",
                school_name_te="కృత్రిమ మోడల్ పాఠశాల",
                academic_year_label="2026-27",
                printed_at=dt.datetime(2026, 9, 29, 6, 0, tzinfo=dt.UTC),
                header=[("Serial no.", "క్రమ సంఖ్య"), ("Remarks", "వ్యాఖ్యలు")],
                rows=rows,
                cancelled=[False] * len(rows),
                empty_en="None",
                empty_te="లేవు",
            )
        )
        assert "Serial no." in page or "None" in page
        assert "Academic year: " in page
        assert not templates_contain_telugu(page)
        assert "Noto Sans Telugu" not in page


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_REG_004_register_page_headings_and_cancelled_rows() -> None:
    page = templates.render_register(
        templates.RegisterPage(
            title_en="Transfer certificate register (counterfoil)",
            title_te="బదిలీ ధృవీకరణ పత్రాల రిజిస్టర్",
            school_name="Synthetic Model School",
            school_name_te="",
            academic_year_label="2026-27",
            printed_at=dt.datetime(2026, 9, 29, 6, 0, tzinfo=dt.UTC),
            header=[("Serial no.", "క్రమ సంఖ్య"), ("Remarks", "వ్యాఖ్యలు")],
            rows=[["TC/2026-27/0001", "Issued"], ["TC/2026-27/0002", "CANCELLED"]],
            cancelled=[False, True],
            empty_en="None",
            empty_te="లేవు",
        )
    )
    assert "<thead>" in page
    assert "క్రమ సంఖ్య" in page
    assert page.count('<tr class="cancelled">') == 1
    assert "29/09/2026 11:30 IST" in page
    empty = templates.render_register(
        templates.RegisterPage(
            title_en="T",
            title_te="టి",
            school_name="S",
            school_name_te="",
            academic_year_label="2026-27",
            printed_at=dt.datetime(2026, 9, 29, tzinfo=dt.UTC),
            header=[("A", "అ")],
            rows=[],
            cancelled=[],
            empty_en="No certificates",
            empty_te="లేవు",
        )
    )
    assert "No certificates" in empty
