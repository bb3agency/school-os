"""Provider output is masked and reduced before anything is stored (FR-IMP-021, FR-IMP-022,
PRV-015, PRV-016, SEC-013, invariant 4). No database."""

from __future__ import annotations

import json
import random
import sys

import pytest

from app.core.redaction import contains_full_aadhaar
from app.devtools.fake_ids import invalid_aadhaar_like
from app.extraction.providers import FieldReading, PageExtraction
from app.extraction.sanitize import clean_page
from app.extraction.settings import extraction_config

X = sys.modules["sos_test_extraction_support"]
CFG = extraction_config()


def _page(rows: list[dict[str, FieldReading]], raw_text: str = "") -> PageExtraction:
    return PageExtraction(rows=rows, raw_text=raw_text)


def test_generator_makes_valid_checksum_numbers() -> None:
    for seed in range(20):
        number = X.valid_aadhaar_like(seed)
        assert len(number) == 12
        assert contains_full_aadhaar(number)


@pytest.mark.parametrize("separator", ["", " ", "-", "\n"])
def test_FR_IMP_022_aadhaar_in_a_cell_is_masked_and_the_page_flagged(separator: str) -> None:
    number = X.valid_aadhaar_like(3)
    written = separator.join((number[0:4], number[4:8], number[8:12]))
    page = _page(
        [
            {
                "full_name": FieldReading("Synthetica Kumari", 0.9),
                "admission_no": FieldReading(f"Adm {written}", 0.9),
            }
        ]
    )
    clean = clean_page(page, CFG, threshold=0.8)
    assert clean.aadhaar_detected
    cell = clean.rows[0].fields["admission_no"]
    assert cell["masked"] is True
    assert cell["value"].endswith(number[-4:])
    assert "XXXX XXXX" in cell["value"]
    assert clean.rows[0].masked
    stored = json.dumps([r.fields for r in clean.rows])
    assert number not in stored.replace(" ", "").replace("-", "")
    assert not contains_full_aadhaar(stored)


def test_PRV_016_number_only_in_page_text_or_dropped_column_flags_the_page() -> None:
    number = X.valid_aadhaar_like(5)
    in_text = clean_page(
        _page([{"full_name": FieldReading("Synthetica A", 0.9)}], raw_text=f"UID {number}"),
        CFG,
        threshold=0.8,
    )
    assert in_text.aadhaar_detected
    in_dropped = clean_page(
        _page([{"full_name": FieldReading("Synthetica B", 0.9), "uid": FieldReading(number)}]),
        CFG,
        threshold=0.8,
    )
    assert in_dropped.aadhaar_detected
    assert in_dropped.dropped_fields == 1
    assert "uid" not in in_dropped.rows[0].fields


def test_PRV_015_invalid_checksum_numbers_are_neither_masked_nor_flagged() -> None:
    fake = invalid_aadhaar_like(random.Random(7))
    clean = clean_page(
        _page([{"admission_no": FieldReading(fake[:10], 0.9)}], raw_text=fake), CFG, threshold=0.8
    )
    assert not clean.aadhaar_detected
    assert clean.rows[0].fields["admission_no"]["value"] == fake[:10]
    assert clean.rows[0].fields["admission_no"]["masked"] is False


def test_FR_IMP_021_confidence_threshold_and_regions() -> None:
    clean = clean_page(
        _page(
            [
                {
                    "full_name": FieldReading("Synthetica Low", 0.42, (0.1, 0.2, 0.3, 0.04)),
                    "dob": FieldReading("2012-01-01", 0.97, (0.5, 0.2, 2.0, 0.04)),
                    "gender": FieldReading("male", None),
                },
                {"full_name": FieldReading("Synthetica High", 0.99)},
            ]
        ),
        CFG,
        threshold=0.8,
    )
    low, high = clean.rows
    assert low.low_confidence
    assert low.fields["full_name"]["low_confidence"] is True
    assert low.fields["full_name"]["bbox"] == [0.1, 0.2, 0.3, 0.04]
    assert low.fields["dob"]["low_confidence"] is False
    assert low.fields["dob"]["bbox"] is None, "regions outside the page are dropped"
    assert low.fields["gender"]["low_confidence"] is True, "unknown confidence counts as low"
    assert not high.low_confidence
    assert clean.low_confidence_rows == 1


def test_values_are_normalised_cut_and_empty_rows_skipped() -> None:
    long_value = "Synthetica " + "a" * 400
    clean = clean_page(
        _page(
            [
                {"full_name": FieldReading("  Synthética\x00  Name \n", 0.9)},
                {"full_name": FieldReading("   ", 0.9)},
                {"caste": FieldReading("synthetic C3 value", 0.9)},
                {"full_name": FieldReading(long_value, 0.9)},
            ]
        ),
        CFG,
        threshold=0.8,
    )
    assert len(clean.rows) == 2
    assert clean.rows[0].fields["full_name"]["value"] == "Synthética Name"
    assert len(clean.rows[1].fields["full_name"]["value"]) == CFG.value_max_length
    assert clean.dropped_fields == 1, "C3 columns are never kept in the queue"


def test_number_on_the_length_boundary_is_masked_before_cutting() -> None:
    number = X.valid_aadhaar_like(11)
    value = "x" * (CFG.value_max_length - 6) + " " + number
    clean = clean_page(_page([{"full_name": FieldReading(value, 0.9)}]), CFG, threshold=0.8)
    kept = clean.rows[0].fields["full_name"]["value"]
    assert number[:6] not in kept
    assert clean.aadhaar_detected
