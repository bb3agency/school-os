"""Disclosure format and value checks (R3-1C; ADR-0051; FR-DISC-001, FR-DISC-002,
FR-DISC-004, FR-DISC-006; NFR-PRV-006, SEC-050). Pure: no database. Synthetic data only."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.disclosure import formats, templates, values
from app.disclosure.formats import DisclosureFormat

FMT = formats.current()


def _item(key: str) -> formats.FormatItem:
    found = FMT.item(key)
    assert found is not None
    return found


# --- format -------------------------------------------------------------------------------------


def test_FR_DISC_001_the_format_is_cbse_appendix_ix_and_unverified() -> None:
    assert FMT.key == "cbse-appendix-ix"
    assert FMT.board == "CBSE"
    assert FMT.verified is False  # D3: not confirmed with a CBSE school yet
    assert FMT.sources
    assert [s.key for s in FMT.sections] == [
        "general",
        "documents",
        "academics",
        "staff",
        "infrastructure",
    ]


def test_FR_DISC_002_every_item_declares_a_known_fill() -> None:
    for item in FMT.items():
        assert item.fill in ("school", "derived", "input", "document", "link")
        if item.fill == "school":
            assert item.source in formats.SCHOOL_SOURCES
        if item.fill == "derived":
            assert item.source in formats.DERIVED_SOURCES


def test_FR_DISC_002_an_unknown_fill_or_source_fails_the_load() -> None:
    raw = yaml.safe_load(
        (Path(formats.FORMATS_DIR) / "cbse-appendix-ix.yaml").read_text(encoding="utf-8")
    )
    bad_fill = dict(raw)
    bad_fill["sections"] = [
        {"key": "x", "title": "X", "items": [{"key": "a_b", "label": "A", "fill": "magic"}]}
    ]
    with pytest.raises(ValidationError):
        DisclosureFormat.model_validate(bad_fill)
    bad_source = dict(raw)
    bad_source["sections"] = [
        {
            "key": "x",
            "title": "X",
            "items": [{"key": "a_b", "label": "A", "fill": "derived", "source": "students"}],
        }
    ]
    with pytest.raises(ValidationError, match="unknown derived source"):
        DisclosureFormat.model_validate(bad_source)


def test_FR_DISC_006_no_item_may_describe_an_individual_child() -> None:
    raw = yaml.safe_load(
        (Path(formats.FORMATS_DIR) / "cbse-appendix-ix.yaml").read_text(encoding="utf-8")
    )
    raw["sections"] = [
        {
            "key": "x",
            "title": "X",
            "items": [
                {
                    "key": "toppers",
                    "label": "Student's name of the topper",
                    "fill": "input",
                    "type": "text",
                    "max_length": 40,
                }
            ],
        }
    ]
    with pytest.raises(ValidationError, match="individual child"):
        DisclosureFormat.model_validate(raw)


def test_FR_DISC_004_only_the_two_contact_items_are_contact_items() -> None:
    contact = sorted(i.key for i in FMT.items() if i.contact)
    assert contact == ["contact_phone", "school_email"]


# --- inputs -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("affiliation_no", "Aadhaar 2345 6789 0123", "aadhaar_not_allowed"),
        ("affiliation_no", "234567890123", "aadhaar_not_allowed"),
        ("principal_qualification", "Call 9876543210", "phone_not_allowed"),
        ("principal_qualification", "+91 98765 43210", "phone_not_allowed"),
        ("principal_qualification", "<b>M.Sc.</b>", "markup_not_allowed"),
        ("principal_qualification", "M.Sc.\u200b", "invalid_characters"),
        ("school_code", "x" * 41, "too_long"),
        ("school_email", "not-an-email", "invalid_email"),
        ("contact_phone", "call us", "invalid_phone"),
        ("girls_toilets", -1, "out_of_range"),
        ("girls_toilets", "12", "invalid"),
        ("girls_toilets", True, "invalid"),
        ("campus_area", "12.345", "too_precise"),
        ("internet", "yes", "invalid"),
        ("infrastructure_video", "http://video.example/school", "https_only"),
        ("infrastructure_video", "https://user:pw@video.example/x", "https_only"),
        ("infrastructure_video", "javascript:alert(1)", "https_only"),
    ],
)
def test_FR_DISC_004_inputs_refuse_unsafe_values(key: str, value: Any, code: str) -> None:
    _clean, problems = values.check_inputs(FMT, {key: value})
    assert problems.errors == [
        {"field": f"inputs.{key}", "code": code, "message_key": f"errors.{code}"}
    ]


def test_FR_DISC_004_contact_items_may_hold_a_phone_number() -> None:
    clean, problems = values.check_inputs(FMT, {"contact_phone": "+91 98765 43210, 040-2345 6789"})
    assert problems.errors == []
    assert clean["contact_phone"] == "+91 98765 43210, 040-2345 6789"


def test_FR_DISC_004_contact_items_still_refuse_aadhaar_like_numbers() -> None:
    _clean, problems = values.check_inputs(FMT, {"contact_phone": "2345 6789 0123"})
    assert [e["code"] for e in problems.errors] == ["aadhaar_not_allowed"]


def test_FR_DISC_004_inputs_are_normalised_and_empty_values_dropped() -> None:
    clean, problems = values.check_inputs(
        FMT,
        {
            "principal_qualification": "  M.Sc.   B.Ed. ",
            "school_code": "",
            "campus_area": 1250.5,
            "internet": True,
            "girls_toilets": 0,
        },
    )
    assert problems.errors == []
    assert clean == {
        "principal_qualification": "M.Sc. B.Ed.",
        "campus_area": "1250.5",
        "internet": True,
        "girls_toilets": 0,
    }


def test_FR_DISC_004_unknown_and_derived_items_cannot_be_typed() -> None:
    _clean, problems = values.check_inputs(
        FMT, {"teachers_total": "99", "student_names": "x", "school_name": "Other"}
    )
    assert sorted(e["field"] for e in problems.errors) == [
        "inputs.school_name",
        "inputs.student_names",
        "inputs.teachers_total",
    ]
    assert {e["code"] for e in problems.errors} == {"unknown_item"}


def test_FR_DISC_004_documents_only_on_document_items() -> None:
    problems = values.check_document_keys(
        FMT, {"fire_safety": ["a"], "fee_structure": ["b"], "affiliation_no": ["c"]}
    )
    assert problems.errors == [
        {
            "field": "documents.affiliation_no",
            "code": "unknown_item",
            "message_key": "errors.unknown_item",
        }
    ]


def test_FR_DISC_005_missing_required_items_are_listed() -> None:
    assert values.missing_required(FMT, {}) == [
        "affiliation_no",
        "school_code",
        "principal_qualification",
        "school_email",
        "contact_phone",
    ]


# --- board results and small counts --------------------------------------------------------------

RESULTS = _item("board_results")


def test_FR_DISC_006_results_are_numbers_per_year_and_class() -> None:
    rows = [
        {"year": "2025-26", "class_level": "XII", "appeared": 120, "passed": 118},
        {"year": "2025-26", "class_level": "X", "appeared": 9, "passed": 9},
        {"year": "2024-25", "class_level": "X", "appeared": 40, "passed": 7},
    ]
    clean = values.check_value(RESULTS, rows)
    table = values.results_table(clean)  # type: ignore[arg-type]
    assert table.columns == ("Year", "Class", "Appeared", "Passed", "Pass percentage")
    assert table.rows == (
        ("2024-25", "Class X", "40", "fewer than 10", ""),
        ("2025-26", "Class X", "fewer than 10", "fewer than 10", ""),
        ("2025-26", "Class XII", "120", "118", "98.3%"),
    )


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ({"year": "2025-27", "class_level": "X", "appeared": 1, "passed": 1}, "invalid_year"),
        ({"year": "2025-26", "class_level": "IX", "appeared": 1, "passed": 1}, "invalid"),
        (
            {"year": "2025-26", "class_level": "X", "appeared": 5, "passed": 6},
            "passed_more_than_appeared",
        ),
        (
            {"year": "2025-26", "class_level": "X", "appeared": 5, "passed": 1, "names": "x"},
            "invalid",
        ),
    ],
)
def test_FR_DISC_006_result_rows_are_checked(row: dict[str, Any], code: str) -> None:
    with pytest.raises(values.Rejected) as caught:
        values.check_value(RESULTS, [row])
    assert caught.value.code == code


def test_NFR_PRV_006_counts_below_ten_are_fewer_than_ten() -> None:
    assert [values.count_text(n) for n in (0, 1, 9, 10, 250)] == [
        "fewer than 10",
        "fewer than 10",
        "fewer than 10",
        "10",
        "250",
    ]


def test_FR_DISC_003_money_uses_indian_grouping() -> None:
    assert values.rupees(Decimal("1234567.5")) == "₹12,34,567.50"
    assert values.rupees(Decimal("999")) == "₹999.00"
    assert values.rupees(Decimal("100000")) == "₹1,00,000.00"


# --- content and the file -----------------------------------------------------------------------


def _content() -> dict[str, Any]:
    return values.build_content(
        FMT,
        school_name="Synthetic Model School",
        inputs={
            "affiliation_no": "1130001",
            "internet": True,
            "infrastructure_video": "https://video.example/tour",
        },
        derived={"teachers_total": values.Derived(value="12")},
        school={"school.name": "Synthetic Model School", "school.address": None},
        documents=[
            values.PublishedDocument(
                n=1, item_key="fire_safety", document_id="d", version_id="v", title="Fire NOC"
            )
        ],
    )


def test_SEC_050_content_is_an_allowlist_of_items() -> None:
    content = _content()
    assert set(content) == {"format_key", "format_version", "title", "school_name", "sections"}
    for section in content["sections"]:
        assert set(section) == {"key", "title", "items"}
        for item in section["items"]:
            assert set(item) == {
                "key",
                "label",
                "from_schoolos",
                "value",
                "table",
                "documents",
                "link",
            }
    flat = {i["key"]: i for s in content["sections"] for i in s["items"]}
    assert flat["internet"]["value"] == "Yes"
    assert flat["teachers_total"]["value"] == "12"
    assert flat["teachers_total"]["from_schoolos"] is True
    assert flat["fire_safety"]["documents"] == [{"n": 1, "title": "Fire NOC"}]
    assert flat["infrastructure_video"]["link"] == "https://video.example/tour"


def test_FR_DISC_011_the_file_has_no_scripts_and_escapes_values() -> None:
    content = _content()
    content["school_name"] = "Synthetic <script>alert(1)</script> School"
    page = templates.render_page(
        content,
        published_at=dt.datetime(2026, 10, 21, 5, 0, tzinfo=dt.UTC),
        document_url="https://my.example.test/d/KEY/documents/{n}",
    )
    assert "<script" not in page.lower()
    assert "&lt;script&gt;" in page
    assert 'href="https://my.example.test/d/KEY/documents/1"' in page
    assert (
        "Published by Synthetic &lt;script&gt;alert(1)&lt;/script&gt; School on 21 Oct 2026" in page
    )
    assert "Prepared with SchoolOS." in page
    assert 'name="robots" content="noindex, nofollow"' in page
