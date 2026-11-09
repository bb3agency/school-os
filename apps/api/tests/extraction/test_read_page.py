"""``service.read_page``: the worker's in-memory reading of one page, without a database
(FR-IMP-022, PRV-015, PRV-016; the register-extraction eval scores it, FA-07, FR-KB-061)."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from app.core.config import Environment, Settings
from app.extraction import service
from app.extraction.providers import FakeExtractionProvider

X = sys.modules["sos_test_extraction_support"]
SETTINGS = Settings(env=Environment.CI)


def _page(spans: list[dict[str, Any]], father: str = "Synthetica Father Rao") -> bytes:
    row = X.register_row("Synthetica Reader", admission_no="R-1001")
    row["father_name"] = X.cell(father, bbox=[0.49, 0.65, 0.24, 0.1])
    page: bytes = X.rendered_page([row], spans)
    return page


def test_FR_IMP_022_a_clean_page_keeps_its_image() -> None:
    reading = service.read_page(
        _page([{"text": "Synthetica Reader", "box": [20, 20, 200, 40]}]),
        FakeExtractionProvider(SETTINGS),
        settings=SETTINGS,
    )
    assert reading.image == "original"
    assert reading.redacted_image is None
    assert reading.clean.rows[0].fields["full_name"]["value"] == "Synthetica Reader"


def test_PRV_016_a_number_on_the_page_is_masked_and_blacked_out() -> None:
    number = X.valid_aadhaar_like(61)
    spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
    image = _page(X.number_spans(number), father=f"Synthetica Father {spaced}")
    reading = service.read_page(image, FakeExtractionProvider(SETTINGS), settings=SETTINGS)
    assert reading.clean.aadhaar_detected
    father = reading.clean.rows[0].fields["father_name"]
    assert father["masked"] is True
    assert number not in str(reading.clean.rows)
    assert reading.image == "redacted"
    assert reading.redacted_image is not None
    assert b"sos-fake-extraction" not in reading.redacted_image


@pytest.mark.parametrize("box", [None])
def test_PRV_016_a_number_without_a_position_withholds_the_image(box: Any) -> None:
    number = X.valid_aadhaar_like(62)
    image = _page([{"text": f"Aadhaar {number}", "box": box}])
    reading = service.read_page(image, FakeExtractionProvider(SETTINGS), settings=SETTINGS)
    assert reading.image == "withheld"
    assert reading.redacted_image is None
