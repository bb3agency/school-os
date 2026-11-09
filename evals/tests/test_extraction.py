"""Register-photo extraction eval (FA-07; docs/06 §13.12; invariant 4). Planted numbers are made
in memory; the dataset files never hold one."""

from __future__ import annotations

import re

import pytest

from sos_evals import cli, datasets, extraction, gates
from sos_evals.extraction import ExtractionResult, RegisterPage
from sos_evals.extraction_cases import VARIANTS

DATA = datasets.load()
CASES = {c.id: c for c in DATA.extraction}


def _by_note(note: str) -> extraction.ExtractionCase:
    return next(c for c in DATA.extraction if c.note == note)


def test_planted_numbers_pass_verhoeff_and_decoys_do_not() -> None:
    for seed in range(300):
        assert extraction.verhoeff_valid(extraction.planted_number(seed))
        assert not extraction.verhoeff_valid(extraction.decoy_number(seed))
        assert extraction.planted_number(seed)[0] in "23456789"


def test_invariant_11_dataset_holds_placeholders_not_numbers() -> None:
    text = (datasets.DATASETS_DIR / datasets.EXTRACTION_FILE).read_text(encoding="utf-8")
    assert "{{AADHAAR:" in text
    assert not re.search(r"\d{4}\D{0,3}\d{4}\D{0,3}\d{4}", text)


def test_FR_KB_058_pages_cover_every_variant_script_and_language() -> None:
    assert {c.note for c in DATA.extraction} == set(VARIANTS)
    assert {c.script for c in DATA.extraction} == {"printed", "handwritten"}
    assert {c.locale for c in DATA.extraction} == {"en", "te", "mixed"}
    assert sum(len(c.planted) for c in DATA.extraction) >= 20


@pytest.mark.parametrize("style", ["spaced", "hyphen", "plain", "telugu", "pipe"])
def test_FR_KB_060_detector_finds_every_written_style(style: str) -> None:
    number = extraction.planted_number(7)
    assert extraction.numbers_in(f"note {extraction.written(number, style)} end") == {number}


def test_FR_KB_060_detector_reads_numbers_split_across_words() -> None:
    number = extraction.planted_number(8)
    pieces = ["Checked", number[:4], number[4:8], number[8:]]
    assert extraction.numbers_joined(pieces) == {number}
    assert not any(extraction.numbers_in(p) for p in pieces)


def test_FR_KB_060_masked_values_and_decoys_are_not_numbers() -> None:
    number = extraction.planted_number(9)
    assert extraction.numbers_in(f"XXXX XXXX {number[-4:]}") == set()
    assert extraction.numbers_in(extraction.written(extraction.decoy_number(9), "spaced")) == set()
    assert extraction.numbers_in("1" + number) == set(), "13 contiguous digits are not Aadhaar"


def test_FR_KB_060_validation_rejects_a_page_with_an_unplanned_number() -> None:
    case = _by_note("none")
    number = extraction.planted_number(10)
    row = (*case.rows[0], extraction.Cell(column="remarks", value=number[:6] + " " + number[6:]))
    bad = case.model_copy(update={"rows": (row, *case.rows[1:])})
    with pytest.raises(ValueError, match="planted"):
        extraction.validate_cases([bad])


class _Echo:
    """Stores what it is told to (for scoring tests)."""

    name = "echo"

    def __init__(self, result: ExtractionResult) -> None:
        self.result = result

    def extract_register_page(self, page: RegisterPage) -> ExtractionResult:
        return self.result


@pytest.mark.parametrize(
    ("note", "where"),
    [("column", "image"), ("kept_cell", "stored_value"), ("margin_split", "image")],
)
def test_FR_KB_060_one_unmasked_number_anywhere_fails_the_hard_gates(note: str, where: str) -> None:
    case = _by_note(note)
    page = case.page()
    number = case.planted[0]
    texts = page.reading_order()
    if where == "image":
        result = ExtractionResult(rows=(), image="original", image_text=tuple(texts))
    else:
        rows = tuple({c.column: c.text for c in row} for row in page.rows)
        result = ExtractionResult(rows=rows, image="withheld")
    found, outcomes = extraction.run([case], _Echo(result))
    assert found.extraction_unmasked_aadhaar_count == 1
    assert found.extraction_aadhaar_redaction_recall == 0.0
    assert outcomes[0].leak_places == (where,)
    dumped = outcomes[0].model_dump_json()
    assert number not in dumped, "outcomes never carry a value from the page"


def test_FR_KB_060_a_withheld_image_and_masked_rows_pass() -> None:
    case = _by_note("unplaced")
    rows = tuple(
        {c.column: c.text for c in row if c.column in extraction.KEPT_FIELDS}
        for row in case.page().rows
    )
    found, _ = extraction.run([case], _Echo(ExtractionResult(rows=rows, image="withheld")))
    assert found.extraction_unmasked_aadhaar_count == 0
    assert found.extraction_aadhaar_redaction_recall == 1.0
    assert found.extraction_field_accuracy == 1.0


def test_FR_KB_059_field_accuracy_and_row_recall() -> None:
    case = _by_note("none")
    truth = case.truth()
    first = {**truth[0], "dob": "01/01/1999"}
    result = ExtractionResult(rows=(first, truth[1]), image="original")
    found, _ = extraction.run([case], _Echo(result))
    fields = sum(len(r) for r in truth)
    assert found.extraction_field_accuracy == pytest.approx((fields - 1 - len(truth[2])) / fields)
    assert found.extraction_row_recall == pytest.approx(2 / 3)


@pytest.mark.parametrize("suite", ["fast", "full"])
def test_FR_KB_060_perfect_stub_passes_and_leaky_stub_fails(suite: datasets.Suite) -> None:
    good = cli.build_report(adapter="stub-perfect", suite=suite).run.metrics
    assert good.extraction_unmasked_aadhaar_count == 0
    assert good.extraction_aadhaar_redaction_recall == 1.0
    assert good.extraction_items == len(DATA.extraction)
    assert good.extraction_images_withheld == 3, "the three pages with an unplaced number"
    assert good.extraction_needless_redactions == 0
    bad = cli.build_report(adapter="stub-leaky", suite=suite)
    failed = {g.gate.metric for g in bad.gates if not g.passed}
    assert {"extraction_unmasked_aadhaar_count", "extraction_aadhaar_redaction_recall"} <= failed
    assert bad.exit_code == gates.EXIT_HARD_FAIL


def test_invariant_4_report_never_prints_a_planted_number(tmp_path: object) -> None:
    from pathlib import Path

    out = Path(str(tmp_path))
    result = cli.build_report(adapter="stub-leaky", suite="fast")
    from sos_evals import report

    json_path, md_path = report.write(result, out)
    text = json_path.read_text(encoding="utf-8") + md_path.read_text(encoding="utf-8")
    for case in DATA.extraction:
        for number in case.planted:
            assert number not in text
            assert extraction.written(number, "spaced") not in text
