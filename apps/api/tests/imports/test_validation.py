"""Row validation rules (FR-IMP-003, SEC-013, SEC-015, SEC-017). Pure tests with a synthetic
catalog and school structure (no database)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import sys
from typing import Any

from app.imports.config import import_config
from app.imports.sheet import read_sheet
from app.imports.validation import (
    AttributeSpec,
    ExistingStudent,
    SectionInfo,
    ValidationContext,
    mapping_problems,
    validate_sheet,
)
from app.imports.values import ClassInfo, ClassResolver

S = sys.modules["sos_test_imports_support"]
CFG = import_config()
NAME_SOURCES = (
    "admission_register",
    "udise_plus",
    "board_registration",
    "birth_certificate",
    "parent_form",
    "tc_incoming",
    "manual_entry",
)
SPECS = {
    "full_name": AttributeSpec("full_name", "text", "C2", True, NAME_SOURCES, None),
    "father_name": AttributeSpec("father_name", "text", "C2", True, NAME_SOURCES, None),
    "dob": AttributeSpec("dob", "date", "C2", True, NAME_SOURCES, None),
    "gender": AttributeSpec(
        "gender", "enum", "C2", True, NAME_SOURCES, ("female", "male", "transgender")
    ),
    "admission_no": AttributeSpec(
        "admission_no",
        "text",
        "C2",
        True,
        ("admission_register", "tc_incoming", "manual_entry"),
        None,
    ),
    "mother_tongue": AttributeSpec("mother_tongue", "text", "C2", False, None, None),
    "caste": AttributeSpec("caste", "text", "C3", False, None, None),
    "aadhaar_last4": AttributeSpec(
        "aadhaar_last4", "digits4", "C3", False, ("aadhaar_as_printed",), None
    ),
}
SECTIONS = [
    SectionInfo("s9a", "c9", "A", "IX-A"),
    SectionInfo("s9b", "c9", "B", "IX-B"),
    SectionInfo("s10a", "c10", "A", "X-A"),
]
RESOLVER = ClassResolver(
    [ClassInfo("c9", "IX", "Class IX", "9వ తరగతి"), ClassInfo("c10", "X", "Class X", "10వ తరగతి")],
    CFG.class_aliases,
    CFG.class_noise_words,
)
MAPPING = {
    "0": "admission_no",
    "1": "full_name",
    "2": "father_name",
    "3": "dob",
    "4": "gender",
    "5": "class",
    "6": "section",
}


def _ctx(**changes: Any) -> ValidationContext:
    base = ValidationContext(
        source="admission_register",
        specs=SPECS,
        classes=RESOLVER,
        sections=SECTIONS,
        has_current_year=True,
        allowed_sections=None,
        can_create=True,
        existing={},
        config=CFG,
        today=dt.date(2026, 9, 27),
    )
    return dataclasses.replace(base, **changes)


def _rows(*data: list[Any]) -> Any:
    return read_sheet(S.csv_bytes([S.HEADER, *data]), "csv", CFG.limits)


def _codes(result: Any) -> list[set[str]]:
    return [{f"{e['field']}:{e['code']}" for e in r.errors} for r in result.rows]


def test_FR_IMP_003_valid_rows_convert_types() -> None:
    sheet = _rows(
        ["A-1", "  Synthetica   Venkata ", "Synthetica Ramana", "14/03/2012", "M", "9", "a"]
    )
    result = validate_sheet(sheet, MAPPING, _ctx())
    row = result.rows[0]
    assert row.status == "valid"
    assert row.action == "create"
    assert row.values == {
        "admission_no": "A-1",
        "full_name": "Synthetica Venkata",
        "father_name": "Synthetica Ramana",
        "dob": "2012-03-14",
        "gender": "male",
    }
    assert row.section_id == "s9a"
    assert row.class_label == "IX-A"
    assert result.stats["valid"] == 1
    assert result.stats["create"] == 1


def test_FR_IMP_003_row_level_errors_with_field_code_and_message_key() -> None:
    sheet = _rows(
        ["", "Synthetica One", "", "14/03/2012", "M", "IX", "A"],
        ["A-2", "", "", "31/02/2012", "unknown", "XI", "A"],
        ["A-3", "Synthetica Three", "", "01/01/2031", "F", "IX", "Z"],
        ["A-4", "Synthetica Four", "", "", "F", "IX", "A"],
    )
    result = validate_sheet(sheet, MAPPING, _ctx())
    assert _codes(result) == [
        {"admission_no:missing"},
        {"dob:invalid_date", "gender:not_allowed", "class:class_not_found", "full_name:missing"},
        {"dob:date_in_future", "section:section_not_found"},
        {"dob:missing"},  # admission register rows need a date of birth
    ]
    first = result.rows[0].errors[0]
    assert first == {"field": "admission_no", "code": "missing", "message_key": "errors.missing"}
    assert result.stats["errors"] == 4


def test_FR_IMP_003_duplicates_within_the_file_are_errors_on_every_copy() -> None:
    sheet = _rows(
        ["A-1", "Synthetica One", "", "14/03/2012", "M", "IX", "A"],
        ["a-1 ", "Synthetica Two", "", "14/03/2012", "M", "IX", "A"],
        ["A-3", "Synthetica Three", "", "14/03/2012", "M", "IX", "A"],
    )
    result = validate_sheet(sheet, MAPPING, _ctx())
    assert [r.status for r in result.rows] == ["error", "error", "valid"]
    assert result.rows[0].errors == [
        {
            "field": "admission_no",
            "code": "duplicate_in_file",
            "message_key": "errors.duplicate_in_file",
            "ref": "3",
        }
    ]


def test_FR_IMP_003_existing_students_are_updates_and_register_identity_is_protected() -> None:
    existing = {
        "a-1": ExistingStudent(
            "sid-1", "s9a", {"full_name": "Synthetica One", "dob": "2012-03-14"}
        ),
        "a-2": ExistingStudent("sid-2", "s9b", {"full_name": "Synthetica Two"}),
    }
    sheet = _rows(
        ["A-1", "Synthetica One", "Synthetica Father", "14/03/2012", "M", "IX", "A"],
        ["A-2", "Synthetica Changed", "", "14/03/2012", "M", "IX", "A"],
    )
    result = validate_sheet(sheet, MAPPING, _ctx(existing=existing))
    one, two = result.rows
    assert (one.status, one.action, one.student_id) == ("valid", "update", "sid-1")
    assert two.errors == [
        {
            "field": "full_name",
            "code": "identity_change_required",
            "message_key": "errors.identity_change_required",
        }
    ]
    assert two.warnings == [
        {
            "field": "section",
            "code": "enrolment_unchanged",
            "message_key": "errors.enrolment_unchanged",
        }
    ]
    # Other sources are observations: a different name is recorded (a conflict for DQ).
    other = validate_sheet(sheet, MAPPING, _ctx(existing=existing, source="udise_plus")).rows[1]
    assert other.status == "valid"
    assert "admission_no" not in other.values


def test_FR_IMP_003_sources_that_cannot_create_only_match() -> None:
    sheet = _rows(["A-9", "Synthetica Nine", "", "14/03/2012", "M", "IX", "A"])
    result = validate_sheet(sheet, MAPPING, _ctx(source="udise_plus"))
    assert _codes(result) == [{"admission_no:no_matching_student"}]


def test_SEC_013_full_aadhaar_anywhere_is_a_row_error_and_never_kept() -> None:
    number = S.valid_aadhaar()
    header = [*S.HEADER, "Caste", "Remarks"]
    sheet = read_sheet(
        S.csv_bytes(
            [
                header,
                ["A-1", "Synthetica One", "", "14/03/2012", "M", "IX", "A", "", f"UID {number}"],
                ["A-2", number, "", "14/03/2012", "M", "IX", "A", "", ""],
                [
                    "A-3",
                    "Synthetica Three",
                    "",
                    "14/03/2012",
                    "M",
                    "IX",
                    "A",
                    "",
                    "+91 98765 43210",
                ],
            ]
        ),
        "csv",
        CFG.limits,
    )
    mapping = {**MAPPING, "7": "caste"}
    result = validate_sheet(sheet, mapping, _ctx())
    one, two, three = result.rows
    assert one.errors == [
        {
            "field": "column_9",
            "code": "aadhaar_full_number_rejected",
            "message_key": "errors.aadhaar_last4_only",
        }
    ]
    assert {e["field"] for e in two.errors} >= {"full_name"}
    assert "full_name" not in two.values
    assert three.status == "valid"  # an explicit +91 mobile number is not an Aadhaar number
    stored = repr([r.parsed(SPECS) for r in result.rows]) + repr([r.errors for r in result.rows])
    assert number not in stored
    assert number[-4:] not in stored


def test_SEC_013_aadhaar_column_keeps_last_four_digits_only() -> None:
    header = ["Adm No", "Aadhaar", "Name"]
    sheet = read_sheet(
        S.csv_bytes(
            [
                header,
                ["A-1", "4821", "x"],
                ["A-2", "XXXX XXXX 4821", "x"],
                ["A-3", "2345 6789 0123", "x"],  # 12 digits, even with a wrong checksum
                ["A-4", "48", "x"],
            ]
        ),
        "csv",
        CFG.limits,
    )
    existing = {f"a-{i}": ExistingStudent(f"sid-{i}", None, {}) for i in range(1, 5)}
    result = validate_sheet(
        sheet,
        {"0": "admission_no", "1": "aadhaar_last4"},
        _ctx(source="aadhaar_as_printed", existing=existing),
    )
    assert [r.values.get("aadhaar_last4") for r in result.rows] == ["4821", "4821", None, None]
    assert [sorted(e["code"] for e in r.errors) for r in result.rows] == [
        [],
        [],
        ["aadhaar_full_number_rejected"],
        ["digits4_required"],
    ]
    # C3 values never reach the stored preview.
    assert result.rows[0].parsed(SPECS) == {
        "admission_no": "A-1",
        "values": {},
        "sensitive": ["aadhaar_last4"],
        "section_id": None,
        "class_label": None,
        "roll_no": None,
    }


def test_SEC_017_mapped_formula_cells_are_errors_unmapped_ones_warnings() -> None:
    header = [*S.HEADER, "Notes"]
    sheet = read_sheet(
        S.csv_bytes(
            [header, ["A-1", '=HYPERLINK("https://x")', "", "14/03/2012", "M", "IX", "A", "=1+1"]]
        ),
        "csv",
        CFG.limits,
    )
    row = validate_sheet(sheet, MAPPING, _ctx()).rows[0]
    assert {(e["field"], e["code"]) for e in row.errors} == {("full_name", "formula_not_evaluated")}
    assert row.warnings == [
        {
            "field": "column_8",
            "code": "formula_not_evaluated",
            "message_key": "errors.formula_not_evaluated",
        }
    ]
    assert "full_name" not in row.values


def test_SEC_015_scope_of_the_importer() -> None:
    sheet = _rows(
        ["A-1", "Synthetica One", "", "14/03/2012", "M", "IX", "A"],
        ["A-2", "Synthetica Two", "", "14/03/2012", "M", "X", "A"],
        ["A-3", "Synthetica Three", "", "14/03/2012", "M", "", ""],
    )
    result = validate_sheet(sheet, MAPPING, _ctx(allowed_sections=frozenset({"s9a"})))
    assert _codes(result) == [
        set(),
        {"section:section_out_of_scope"},
        {"section:section_required"},
    ]
    cannot = validate_sheet(sheet, MAPPING, _ctx(can_create=False))
    assert all("admission_no:student_create_not_permitted" in c for c in _codes(cannot))


def test_FR_IMP_003_ambiguous_dates_are_flagged_unless_the_column_proves_the_order() -> None:
    sheet = _rows(
        ["A-1", "Synthetica One", "", "03/04/2012", "M", "IX", "A"],
        ["A-2", "Synthetica Two", "", "05/06/2012", "M", "IX", "A"],
    )
    result = validate_sheet(sheet, MAPPING, _ctx())
    assert [w["code"] for r in result.rows for w in r.warnings] == ["ambiguous_date"] * 2
    assert result.stats["ambiguous_dates"] == 2
    proven = _rows(
        ["A-1", "Synthetica One", "", "03/04/2012", "M", "IX", "A"],
        ["A-2", "Synthetica Two", "", "25/06/2012", "M", "IX", "A"],
    )
    assert validate_sheet(proven, MAPPING, _ctx()).stats["ambiguous_dates"] == 0


def test_FR_IMP_003_no_current_year_and_class_section_column() -> None:
    header = ["Adm No", "Name", "DOB", "Class-Section"]
    sheet = read_sheet(
        S.csv_bytes([header, ["A-1", "Synthetica", "14/03/2012", "9-B"]]), "csv", CFG.limits
    )
    mapping = {"0": "admission_no", "1": "full_name", "2": "dob", "3": "class_section"}
    assert validate_sheet(sheet, mapping, _ctx()).rows[0].section_id == "s9b"
    no_year = validate_sheet(sheet, mapping, _ctx(has_current_year=False, sections=[]))
    assert _codes(no_year) == [{"class:no_current_academic_year"}]


def test_FR_IMP_002_mapping_problems() -> None:
    assert mapping_problems(MAPPING, 7, SPECS, "admission_register") == []
    problems = mapping_problems(
        {"0": "admission_no", "1": "admission_no", "2": "aadhaar_last4", "3": "bogus", "9": "dob"},
        7,
        SPECS,
        "admission_register",
    )
    assert {(p["field"], p["code"]) for p in problems} == {
        ("columns.1", "duplicate_target"),
        ("columns.2", "source_not_allowed"),
        ("columns.3", "unknown_target"),
        ("columns.9", "unknown_column"),
    }
    both = mapping_problems({"0": "class_section", "1": "class"}, 7, SPECS, "admission_register")
    assert [p["code"] for p in both] == ["class_section_conflict"]
