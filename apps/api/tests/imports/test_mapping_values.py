"""Column mapping suggestions (FR-IMP-002) and cell conversion (FR-IMP-003). Pure tests."""

from __future__ import annotations

import datetime as dt

import pytest

from app.imports.config import import_config
from app.imports.mapping import (
    header_signature,
    mapping_from_template,
    normalize_header,
    suggest,
    template_payload,
)
from app.imports.values import ClassInfo, ClassResolver, cell_text, day_first_proven, parse_date

CFG = import_config()
ALL_TARGETS = set(CFG.synonyms)


def _suggest(headers: list[str], targets: set[str] | None = None) -> dict[str, str | None]:
    out = suggest(
        headers,
        CFG.synonyms,
        allowed_targets=targets or ALL_TARGETS,
        threshold=CFG.suggest_threshold,
    )
    return {headers[s.index]: s.target for s in out}


@pytest.mark.parametrize(
    ("header", "target"),
    [
        ("Name of the Student", "full_name"),
        ("Student Name", "full_name"),
        ("విద్యార్థి పేరు", "full_name"),
        ("Father Name", "father_name"),
        ("Father's Name", "father_name"),
        ("తండ్రి పేరు", "father_name"),
        ("Mother's Name", "mother_name"),
        ("తల్లి పేరు", "mother_name"),
        ("DOB", "dob"),
        ("D.O.B.", "dob"),
        ("Date of Birth", "dob"),
        ("పుట్టిన తేదీ", "dob"),
        ("Adm No", "admission_no"),
        ("Adm. No.", "admission_no"),
        ("Admission Number", "admission_no"),
        ("ప్రవేశ సంఖ్య", "admission_no"),
        ("Class", "class"),
        ("తరగతి", "class"),
        ("Section", "section"),
        ("Class & Section", "class_section"),
        ("Gender", "gender"),
        ("Sex", "gender"),
        ("లింగం", "gender"),
        ("Aadhaar", "aadhaar_last4"),
        ("Aadhar No", "aadhaar_last4"),
        ("ఆధార్", "aadhaar_last4"),
        ("Mother Tongue", "mother_tongue"),
        ("Roll No.", "roll_no"),
        ("Studnet Name", "full_name"),  # typo still reaches the fuzzy threshold
    ],
)
def test_FR_IMP_002_english_and_telugu_headers(header: str, target: str) -> None:
    assert _suggest([header])[header] == target


def test_FR_IMP_002_a_whole_register_layout() -> None:
    headers = [
        "S.No",
        "Adm No",
        "విద్యార్థి పేరు",
        "తండ్రి పేరు",
        "Mother Name",
        "DOB",
        "లింగం",
        "Class",
        "Section",
        "Remarks",
    ]
    assert _suggest(headers) == {
        "S.No": None,
        "Adm No": "admission_no",
        "విద్యార్థి పేరు": "full_name",
        "తండ్రి పేరు": "father_name",
        "Mother Name": "mother_name",
        "DOB": "dob",
        "లింగం": "gender",
        "Class": "class",
        "Section": "section",
        "Remarks": None,
    }


def test_FR_IMP_002_each_target_once_and_only_allowed_targets() -> None:
    got = _suggest(["Name", "Student Name"])
    assert sorted(v for v in got.values() if v) == ["full_name"]
    # Aadhaar-as-printed fields are not suggested for a register import.
    assert _suggest(["Aadhaar"], {"full_name", "admission_no"})["Aadhaar"] is None


def test_FR_IMP_002_unrelated_headers_are_not_forced() -> None:
    got = _suggest(["Bus Route", "Fee Paid", "Remarks"])
    assert set(got.values()) == {None}


def test_FR_IMP_002_templates_by_header_signature() -> None:
    headers = ["Adm No", "Name of the Student", "Class", "Section"]
    assert header_signature(headers) == header_signature(
        ["ADM. NO", "name of the student ", "CLASS", "Section"]
    )
    assert header_signature(headers) != header_signature(["Adm No", "Name", "Class", "Section"])
    payload = template_payload(headers, {"0": "admission_no", "1": "full_name", "3": "section"})
    assert payload == {
        "adm no": "admission_no",
        "name of the student": "full_name",
        "section": "section",
    }
    shuffled = ["Section", "Adm No", "Name of the Student", "Class"]
    applied = mapping_from_template(shuffled, payload, ALL_TARGETS)
    assert applied == {"0": "section", "1": "admission_no", "2": "full_name"}
    assert mapping_from_template(shuffled, payload, {"section"}) == {"0": "section"}


def test_FR_IMP_002_header_normalisation() -> None:
    assert normalize_header("  Father’s   Name: ") == "fathers name"
    assert normalize_header("Class/Section") == "class section"


# --- dates ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected", "error", "ambiguous"),
    [
        ("14/03/2012", dt.date(2012, 3, 14), None, False),
        ("14-03-2012", dt.date(2012, 3, 14), None, False),
        ("14.03.2012", dt.date(2012, 3, 14), None, False),
        ("2012-03-14", dt.date(2012, 3, 14), None, False),
        ("14-Mar-2012", dt.date(2012, 3, 14), None, False),
        ("14 March 2012", dt.date(2012, 3, 14), None, False),
        ("03/04/2012", dt.date(2012, 4, 3), None, True),  # 3 April (day first), flagged
        ("05/05/2012", dt.date(2012, 5, 5), None, False),  # same either way
        (40982, dt.date(2012, 3, 14), None, False),  # Excel serial (1900 system)
        (40982.0, dt.date(2012, 3, 14), None, False),
        ("40982", dt.date(2012, 3, 14), None, False),
        (dt.datetime(2012, 3, 14, 0, 0), dt.date(2012, 3, 14), None, False),
        (dt.date(2012, 3, 14), dt.date(2012, 3, 14), None, False),
        ("03/14/2012", None, "invalid_date", False),  # month-first is not guessed
        ("31/02/2012", None, "invalid_date", False),
        ("14/03/12", None, "year_needs_four_digits", False),
        ("2012", None, "invalid_date", False),
        (2012, None, "invalid_date", False),
        ("yesterday", None, "invalid_date", False),
        (None, None, "missing", False),
    ],
)
def test_FR_IMP_003_date_parsing(
    value: object, expected: dt.date | None, error: str | None, ambiguous: bool
) -> None:
    result = parse_date(value)  # type: ignore[arg-type]
    assert (result.value, result.error, result.ambiguous) == (expected, error, ambiguous)


def test_FR_IMP_003_a_column_can_prove_day_first() -> None:
    column = ["03/04/2012", "25/12/2011", None, 40982]
    assert day_first_proven(column) is True
    assert parse_date("03/04/2012", day_first_proven=True).ambiguous is False
    assert day_first_proven(["03/04/2012", "05/06/2012"]) is False


def test_FR_IMP_003_cell_text() -> None:
    assert cell_text(1001.0) == "1001"
    assert cell_text("  Synthetica   Venkata ") == "Synthetica Venkata"
    assert cell_text("") is None
    assert cell_text(dt.datetime(2012, 3, 14)) == "2012-03-14"


# --- classes ------------------------------------------------------------------------------------------

CLASSES = [
    ClassInfo("c9", "IX", "Class IX", "9వ తరగతి"),
    ClassInfo("c10", "X", "Class X", "10వ తరగతి"),
    ClassInfo("c12", "XII", "Class XII", "12వ తరగతి"),
    ClassInfo("c1", "I", "Class I", "1వ తరగతి"),
    ClassInfo("nur", "NUR", "Nursery", "నర్సరీ"),
]
RESOLVER = ClassResolver(CLASSES, CFG.class_aliases, CFG.class_noise_words)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("IX", "c9"),
        ("ix", "c9"),
        ("9", "c9"),
        ("9th", "c9"),
        ("Class 9", "c9"),
        ("Class IX", "c9"),
        ("9వ తరగతి", "c9"),
        ("9th Std", "c9"),
        ("X", "c10"),
        ("10", "c10"),
        ("Nursery", "nur"),
        ("XIII", None),
        ("Ninth grade B", None),
    ],
)
def test_FR_IMP_003_class_cells(text: str, expected: str | None) -> None:
    assert RESOLVER.resolve_class(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("9-A", ("c9", "A")),
        ("IX A", ("c9", "A")),
        ("9A", ("c9", "A")),
        ("ixb", ("c9", "B")),
        ("Class 9 - A", ("c9", "A")),
        ("12a", ("c12", "A")),
        ("1-C", ("c1", "C")),
        ("10/B", ("c10", "B")),
        ("Z", None),
    ],
)
def test_FR_IMP_003_class_section_cells(text: str, expected: tuple[str, str] | None) -> None:
    assert RESOLVER.resolve_class_section(text) == expected
