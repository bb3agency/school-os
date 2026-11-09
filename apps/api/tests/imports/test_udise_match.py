"""UDISE+ list rows matched by PEN, then name + DOB + class, then a person's pick (FO-04;
FR-UDI-011..FR-UDI-016). Pure tests with a synthetic catalog and structure (no database)."""

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
    name_key,
    validate_sheet,
)
from app.imports.values import ClassInfo, ClassResolver

S = sys.modules["sos_test_imports_support"]
CFG = import_config()
SOURCES = ("admission_register", "udise_plus", "manual_entry")
SPECS = {
    "full_name": AttributeSpec("full_name", "text", "C2", True, SOURCES, None),
    "dob": AttributeSpec("dob", "date", "C2", True, SOURCES, None),
    "admission_no": AttributeSpec(
        "admission_no", "text", "C2", True, ("admission_register", "manual_entry"), None
    ),
    "udise_pen": AttributeSpec(
        "udise_pen", "text", "C2", False, SOURCES, None, pattern=r"[0-9]{11}", compact=True
    ),
}
SECTIONS = [SectionInfo("s9a", "c9", "A", "IX-A"), SectionInfo("s10a", "c10", "A", "X-A")]
RESOLVER = ClassResolver(
    [ClassInfo("c9", "IX", "Class IX", ""), ClassInfo("c10", "X", "Class X", "")],
    CFG.class_aliases,
    CFG.class_noise_words,
)
HEADER = ["Student Name", "Date of Birth", "Class", "Section", "PEN"]
MAPPING = {"0": "full_name", "1": "dob", "2": "class", "3": "section", "4": "udise_pen"}
PEN_A, PEN_B, PEN_C = "11111111111", "22222222222", "33333333333"


def _student(sid: str, section: str = "s9a") -> ExistingStudent:
    return ExistingStudent(id=sid, section_id=section, anchor_values={})


REACHABLE = {sid: _student(sid) for sid in ("st-a", "st-b", "st-c", "st-d", "st-e")}


def _ctx(**changes: Any) -> ValidationContext:
    base = ValidationContext(
        source="udise_plus",
        specs=SPECS,
        classes=RESOLVER,
        sections=SECTIONS,
        has_current_year=True,
        allowed_sections=None,
        can_create=True,
        existing={},
        config=CFG,
        today=dt.date(2026, 11, 9),
        match_by_pen=True,
        reachable=REACHABLE,
        national_ids={"udise_pen": {PEN_A: "st-a", PEN_B: "st-b"}},
        visible_holders=frozenset(REACHABLE),
        verified_pens=frozenset({PEN_A}),
        identity_index={
            (name_key("Synthetic Charan"), "2012-03-14", "c9"): ("st-c",),
            (name_key("Synthetic Twin"), "2012-05-01", "c9"): ("st-d", "st-e"),
        },
    )
    return dataclasses.replace(base, **changes)


def _sheet(*rows: list[Any]) -> Any:
    return read_sheet(S.csv_bytes([HEADER, *rows]), "csv", CFG.limits)


def test_config_lists_udise_plus_as_a_pen_matching_source() -> None:
    assert "udise_plus" in CFG.pen_match_sources
    assert "admission_register" not in CFG.pen_match_sources


def test_FR_UDI_011_match_order_and_basis_per_row() -> None:
    sheet = _sheet(
        ["Synthetic Asha", "14/03/2012", "IX", "A", PEN_A],  # verified PEN
        ["Synthetic Bala", "14/03/2012", "IX", "A", PEN_B],  # PEN held, not verified
        ["SYNTHETIC  charan", "14/03/2012", "IX", "A", PEN_C],  # unknown PEN, name+DOB+class
        ["Synthetic Nobody", "01/01/2012", "IX", "A", ""],  # no match
    )
    result = validate_sheet(sheet, MAPPING, _ctx())
    basis = [(r.status, r.match_basis, r.student_id, r.action) for r in result.rows]
    assert basis == [
        ("valid", "pen_verified", "st-a", "update"),
        ("valid", "pen", "st-b", "update"),
        ("valid", "name_dob_class", "st-c", "update"),
        ("error", None, None, None),
    ]
    assert result.rows[3].errors == [
        {"field": "student", "code": "udise_unmatched", "message_key": "errors.udise_unmatched"}
    ]
    assert result.stats["matched_pen_verified"] == 1
    assert result.stats["matched_pen"] == 1
    assert result.stats["matched_name_dob_class"] == 1
    assert result.stats["to_review"] == 1
    assert result.stats["create"] == 0
    # The match basis is stored with the row (shown in the validation sheet, FR-UDI-012).
    assert result.rows[0].parsed(SPECS)["match_basis"] == "pen_verified"


def test_FR_UDI_013_name_match_must_be_unique_and_in_the_same_class() -> None:
    sheet = _sheet(
        ["Synthetic Twin", "01/05/2012", "IX", "A", ""],  # two students: ambiguous
        ["Synthetic Charan", "14/03/2012", "X", "A", ""],  # other class: no match
        ["Synthetic Charan", "15/03/2012", "IX", "A", ""],  # other DOB: no match
    )
    result = validate_sheet(sheet, MAPPING, _ctx())
    codes = [[e["code"] for e in r.errors] for r in result.rows]
    assert codes == [["udise_match_ambiguous"], ["udise_unmatched"], ["udise_unmatched"]]
    assert result.stats["to_review"] == 3


def test_FR_UDI_014_a_persons_pick_or_skip_decides_the_row() -> None:
    sheet = _sheet(
        ["Synthetic Twin", "01/05/2012", "IX", "A", ""],
        ["Synthetic Nobody", "bad date", "IX", "A", ""],
        ["Synthetic Gone", "01/01/2012", "IX", "A", ""],
    )
    picks = {2: "st-e", 3: "skip", 4: "st-unknown"}
    result = validate_sheet(sheet, MAPPING, _ctx(row_matches=picks))
    first, skipped, gone = result.rows
    assert (first.status, first.match_basis, first.student_id) == ("valid", "picked", "st-e")
    # A skipped row is neither added nor an error, even with a bad cell.
    assert (skipped.status, skipped.match_basis, skipped.errors) == ("skipped", "skipped", [])
    assert [e["code"] for e in gone.errors] == ["picked_student_unavailable"]
    assert result.stats["skipped"] == 1
    assert result.stats["matched_picked"] == 1
    assert result.stats["errors"] == 1


def test_FR_UDI_014_two_rows_for_one_student_are_both_refused() -> None:
    sheet = _sheet(
        ["Synthetic Asha", "14/03/2012", "IX", "A", PEN_A],
        ["Synthetic Charan", "14/03/2012", "IX", "A", ""],
    )
    result = validate_sheet(sheet, MAPPING, _ctx(row_matches={3: "st-a"}))
    assert all(any(e["code"] == "duplicate_student_in_file" for e in r.errors) for r in result.rows)


def test_FR_UDI_015_never_creates_a_student_and_respects_scope() -> None:
    outside = {"st-b": _student("st-b", "s10a")}
    sheet = _sheet(
        ["Synthetic Bala", "14/03/2012", "IX", "A", PEN_B],
        ["Synthetic Asha", "14/03/2012", "IX", "A", PEN_A],
    )
    result = validate_sheet(
        sheet,
        MAPPING,
        _ctx(reachable=outside, allowed_sections=frozenset({"s9a"})),
    )
    in_reach_other_section, not_reachable = result.rows
    assert [e["code"] for e in in_reach_other_section.errors] == ["student_out_of_scope"]
    # The PEN belongs to a record outside the importer's reach: never matched, never created.
    assert "student_out_of_scope" in {e["code"] for e in not_reachable.errors}
    assert all(r.action != "create" for r in result.rows)


def test_FR_UDI_016_admission_number_still_matches_first() -> None:
    mapping = {**MAPPING, "5": "admission_no"}
    sheet = read_sheet(
        S.csv_bytes(
            [[*HEADER, "Admission No"], ["Synthetic Asha", "14/03/2012", "IX", "A", PEN_A, "A-1"]]
        ),
        "csv",
        CFG.limits,
    )
    existing = {"a-1": _student("st-a")}
    result = validate_sheet(sheet, mapping, _ctx(existing=existing))
    row = result.rows[0]
    assert (row.status, row.match_basis, row.student_id) == ("valid", "admission_no", "st-a")
    assert "admission_no" not in row.values  # a match key only for UDISE+ rows


def test_other_sources_keep_the_admission_number_rule() -> None:
    sheet = _sheet(["Synthetic Asha", "14/03/2012", "IX", "A", PEN_A])
    result = validate_sheet(sheet, MAPPING, _ctx(match_by_pen=False))
    assert "missing" in {e["code"] for e in result.rows[0].errors}
    assert result.rows[0].match_basis is None
    assert "matched_pen" not in result.stats
