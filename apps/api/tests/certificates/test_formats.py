"""TC formats, verification codes, QR and the v2 template without a database (R2-1A; ADR-0045;
FR-CERT-015..020, FR-CERT-022; owner decisions D3, D15, D16)."""

from __future__ import annotations

import datetime as dt
import re
import typing

import pytest

from app.certificates import codes, qr, templates
from app.certificates import formats as tc_formats
from app.certificates.config import load_config
from app.certificates.schemas import CertificateContent, ContentLine
from app.tenancy.schemas import RESTRICTED_TC_FIELDS, TcFormatKey

EN_DASH = chr(0x2013)  # a phone keyboard's dash


def test_FR_CERT_015_school_choices_are_exactly_the_generic_and_the_format_files() -> None:
    assert set(typing.get_args(TcFormatKey)) == set(tc_formats.format_keys())
    assert tc_formats.format_keys()[0] == tc_formats.GENERIC_KEY


def test_FR_CERT_016_ap_format_lists_the_ap_fields_in_order_unverified() -> None:
    ap = tc_formats.get_format("ap-tc-2027")
    assert ap is not None
    assert ap.verified is False  # D3: built from public sources, not confirmed with a school
    assert all(s.url is None or s.url.startswith("https://") for s in ap.sources)
    keys = [line.key for line in ap.lines]
    # The unofficial template's order: names, nationality, religion, caste, date of birth in
    # figures and words, ..., class at leaving, languages studied.
    assert keys.index("full_name") < keys.index("nationality") < keys.index("religion")
    assert keys.index("religion") < keys.index("caste") < keys.index("dob")
    assert keys.index("dob") < keys.index("dob_in_words") < keys.index("class_at_leaving")
    assert keys.index("class_at_leaving") < keys.index("languages_studied")
    assert {"identification_mark_1", "languages_studied", "medium", "promotion"} <= set(ap.inputs)
    assert {"application_date", "leaving_date"} <= set(ap.inputs)
    assert set(ap.required) == {"full_name", "father_name", "dob", "admission_no"}


def test_FR_CERT_018_restricted_fields_are_religion_and_caste_only() -> None:
    ap = tc_formats.get_format("ap-tc-2027")
    assert ap is not None
    assert set(ap.restricted) == set(RESTRICTED_TC_FIELDS) == tc_formats.RESTRICTED_ATTRIBUTES
    assert not set(ap.attributes) & tc_formats.RESTRICTED_ATTRIBUTES


def test_FR_CERT_018_a_format_cannot_print_a_restricted_or_aadhaar_value_directly() -> None:
    bad = {"key": "x", "label_en": "Caste", "label_te": "కులం", "fill": "attribute"}
    with pytest.raises(ValueError, match="never printed"):
        tc_formats.FormatLine.model_validate({**bad, "attribute": "caste"})
    with pytest.raises(ValueError, match="never printed"):
        tc_formats.FormatLine.model_validate({**bad, "attribute": "aadhaar_last4"})
    with pytest.raises(ValueError, match="restricted"):
        tc_formats.FormatLine.model_validate(
            {**bad, "fill": "restricted", "attribute": "health_notes"}
        )


def test_D16_no_tc_format_carries_fees_or_dues() -> None:
    fee = re.compile(r"\b(fee|fees|dues|paid)\b", re.IGNORECASE)
    for fmt in tc_formats.formats().values():
        assert not any(fee.search(line.label_en) for line in fmt.lines)
    generic = load_config().spec("transfer")
    assert not any("fee" in key for key in generic.official_format_todo)
    with pytest.raises(ValueError, match="D16"):
        tc_formats.FormatLine.model_validate(
            {
                "key": "fees_paid_up_to",
                "label_en": "Month up to which fees were paid",
                "label_te": "ఫీజు",
                "fill": "input",
                "input": "fees_paid_up_to",
            }
        )


def test_FR_CERT_019_codes_are_80_random_bits_in_a_32_symbol_alphabet() -> None:
    assert len(codes.ALPHABET) == 32
    assert not set("ILOU") & set(codes.ALPHABET)
    sample = {codes.new_code() for _ in range(2000)}
    assert len(sample) == 2000
    assert all(codes.CODE_RE.fullmatch(c) for c in sample)
    # Every symbol is used (no structure, no fixed prefix).
    assert set("".join(sample)) == set(codes.ALPHABET)
    assert len({c[:4] for c in sample}) > 1000


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("7K4M-Q2XP-9HD3-RT6W", "7K4MQ2XP9HD3RT6W"),
        ("7k4m q2xp 9hd3 rt6w", "7K4MQ2XP9HD3RT6W"),
        (f"7K4M{EN_DASH}Q2XP{EN_DASH} 9HD3-RT6W", "7K4MQ2XP9HD3RT6W"),
        ("OK4M-Q2XP-9HD3-RT6W", "0K4MQ2XP9HD3RT6W"),  # O reads as 0
        ("1K4M-Q2XP-9HD3-RTLI", "1K4MQ2XP9HD3RT11"),  # I and L read as 1
        ("7K4M-Q2XP-9HD3-RT6U", None),  # U is never in a code
        ("7K4M-Q2XP-9HD3", None),
        ("TC/2026-27/0001", None),  # a serial is never a code
        ("", None),
        (None, None),
        ("7" * 65, None),
    ],
)
def test_FR_CERT_022_typed_codes_ignore_case_spaces_and_dashes(
    typed: str | None, expected: str | None
) -> None:
    assert codes.normalise(typed) == expected


def test_FR_CERT_020_codes_print_in_four_groups() -> None:
    assert codes.grouped("7K4MQ2XP9HD3RT6W") == "7K4M-Q2XP-9HD3-RT6W"


def test_FR_CERT_020_qr_is_an_inline_svg_of_the_verification_link_only() -> None:
    svg = qr.qr_svg("https://verify.example.test/v/7K4MQ2XP9HD3RT6W", label='QR "x" <y>')
    assert svg.startswith('<svg role="img" aria-label="QR &quot;x&quot; &lt;y>"')
    assert "<script" not in svg
    assert "href" not in svg
    with pytest.raises(ValueError, match="verification links"):
        qr.qr_svg("https://evil.example/anything", label="x")
    with pytest.raises(ValueError, match="verification links"):
        qr.qr_svg('https://verify.example.test/v/7K4M"><script>', label="x")


def _content() -> CertificateContent:
    return CertificateContent(
        certificate_type="transfer",
        title_en="Transfer certificate",
        title_te="",
        serial="TC/2026-27/0001",
        academic_year_label="2026-27",
        issued_on=dt.date(2026, 10, 17),
        school_name_en="Synthetic High School",
        school_name_te="",
        school_address_en="Synthetic Road, Vijayawada",
        school_address_te="",
        school_affiliation="",
        school_place="Vijayawada",
        student_name="Synthetica Student",
        admission_no="A/1",
        class_label_en="Class IX A",
        class_label_te=None,
        fields=[
            ContentLine(key="full_name", label_en="Name of the pupil", label_te="", value="X"),
            ContentLine(key="religion", label_en="Religion", label_te="", value=None),
        ],
        details=[],
        blanks=[],
    )


def test_FR_CERT_020_template_v2_prints_code_qr_and_host_but_never_on_a_draft() -> None:
    assert templates.TEMPLATE_VERSION == "v2"
    assert load_config().template_version == "v2"
    mark = templates.VerificationMark(
        code="7K4M-Q2XP-9HD3-RT6W",
        host="verify.example.test",
        qr_svg=qr.qr_svg("https://verify.example.test/v/7K4MQ2XP9HD3RT6W", label="QR"),
    )
    page = templates.render_certificate(_content(), reference="ABC", verification=mark)
    assert "7K4M-Q2XP-9HD3-RT6W" in page
    assert "Check this certificate at verify.example.test/v" in page
    assert '<svg role="img"' in page
    assert "template v2" in page
    # A restricted line the school does not print is a labelled blank (FR-CERT-018).
    assert 'Religion</th><td><span class="blank">' in page
    draft = templates.render_certificate(
        _content(), reference="ABC", mark="draft", verification=mark
    )
    assert "7K4M" not in draft
    assert "<svg" not in draft


def test_FR_CERT_020_print_view_csp_still_allows_only_the_hashed_styles() -> None:
    assert "img-src 'none'" in templates.STYLE_CSP
    assert "script-src" not in templates.STYLE_CSP
