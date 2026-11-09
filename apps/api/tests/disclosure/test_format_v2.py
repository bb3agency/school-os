"""CBSE Appendix IX format version 2 (FC-02; ADR-0051 amendment 2026-11-09; FR-DISC-013..
FR-DISC-019; lead decision D84: staff as counts only). Pure: no database. Synthetic data only."""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.disclosure import formats, templates, values
from app.disclosure.formats import DisclosureFormat


def _version(n: int) -> DisclosureFormat:
    found = formats.get("cbse-appendix-ix", n)
    assert found is not None
    return found


V1 = _version(1)
V2 = _version(2)

V1_ITEMS = [
    "school_name",
    "affiliation_no",
    "school_code",
    "address",
    "principal_name",
    "principal_qualification",
    "school_email",
    "contact_phone",
    "affiliation_letter",
    "trust_registration",
    "noc",
    "rte_recognition",
    "building_safety",
    "fire_safety",
    "deo_certificate",
    "sanitation_certificates",
    "academic_session",
    "fee_structure",
    "academic_calendar",
    "smc_list",
    "pta_list",
    "board_results",
    "principal",
    "teachers_total",
    "pgt_count",
    "tgt_count",
    "prt_count",
    "sections_count",
    "teacher_section_ratio",
    "staff_by_role",
    "special_educator",
    "counsellor",
    "campus_area",
    "classrooms",
    "labs",
    "internet",
    "girls_toilets",
    "boys_toilets",
    "infrastructure_video",
]


def _raw(version: int) -> dict[str, Any]:
    path = Path(formats.FORMATS_DIR) / f"cbse-appendix-ix.v{version}.yaml"
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _item(key: str) -> formats.FormatItem:
    found = V2.item(key)
    assert found is not None
    return found


# --- versions -----------------------------------------------------------------------------------


def test_FR_DISC_013_new_drafts_use_version_2_and_version_1_stays_unchanged() -> None:
    assert formats.current() is V2
    assert V2.version == 2
    assert V1.version == 1
    assert [i.key for i in V1.items()] == V1_ITEMS  # version 1 is frozen for its snapshots
    assert sorted(formats.formats()["cbse-appendix-ix"]) == [1, 2]
    assert formats.get("cbse-appendix-ix", 3) is None


def test_FR_DISC_013_version_2_is_unverified_with_secondary_sources() -> None:
    assert V2.verified is False  # circular 1/2026 annexure not read (D3)
    titles = " ".join(s.title for s in V2.sources)
    assert "1/2026" in titles
    assert all(s.url is None or s.url.startswith("https://") for s in V2.sources)


def test_FR_DISC_013_file_names_must_match_key_and_version(tmp_path: Path) -> None:
    raw = _raw(2)
    bad = tmp_path / "cbse-appendix-ix.v3.yaml"
    bad.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="must match the file name"):
        formats.load_file(bad)
    unnamed = tmp_path / "cbse-appendix-ix.yaml"
    unnamed.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match=r"named <key>.v<version>.yaml"):
        formats.load_file(unnamed)


def test_FR_DISC_013_versions_run_without_gaps() -> None:
    gap = DisclosureFormat.model_validate({**_raw(2), "version": 3})
    with pytest.raises(ValueError, match="without gaps"):
        formats.collect([V1, gap])
    with pytest.raises(ValueError, match="defined twice"):
        formats.collect([V1, V1])


# --- new items ----------------------------------------------------------------------------------


def test_FR_DISC_015_registered_students_are_derived() -> None:
    item = _item("registered_students")
    assert item.fill == "derived"
    assert item.source == "registered_students"


def test_FR_DISC_016_staff_are_counts_only_never_names() -> None:
    assert _item("staff_by_category").fill == "derived"
    for key in ("vice_principal_count", "head_teacher_count"):
        assert _item(key).type == "integer"
    table = _item("staff_qualifications")
    assert table.type == "count_table"
    assert table.rows is not None
    assert [r.key for r in table.rows] == [
        "principal",
        "vice_principal",
        "head_teacher",
        "pgt",
        "tgt",
        "prt",
        "special_educator",
        "counsellor",
    ]
    # D84: no new free-text staff item (the only text items in D are the two v1 "details").
    staff = next(s for s in V2.sections if s.key == "staff")
    texts = sorted(i.key for i in staff.items if i.type == "text")
    assert texts == ["counsellor", "special_educator"]


def test_FR_DISC_017_cwsn_toilets_library_and_self_certification() -> None:
    assert _item("cwsn_toilets").type == "integer"
    assert _item("library_area").type == "number"
    assert _item("library_books").type == "integer"
    deo = _item("deo_certificate")
    assert deo.fill == "document"
    assert "self-certification" in deo.label


def test_FR_DISC_016_count_table_labels_may_not_describe_a_child() -> None:
    raw = copy.deepcopy(_raw(2))
    staff = next(s for s in raw["sections"] if s["key"] == "staff")
    table = next(i for i in staff["items"] if i["key"] == "staff_qualifications")
    table["rows"][0]["label"] = "Student's name"
    with pytest.raises(ValidationError, match="individual child"):
        DisclosureFormat.model_validate(raw)


def test_FR_DISC_016_count_tables_need_rows_and_columns() -> None:
    raw = copy.deepcopy(_raw(2))
    staff = next(s for s in raw["sections"] if s["key"] == "staff")
    table = next(i for i in staff["items"] if i["key"] == "staff_qualifications")
    del table["columns"]
    with pytest.raises(ValidationError, match="rows, columns and max_value"):
        DisclosureFormat.model_validate(raw)
    raw = copy.deepcopy(_raw(2))
    staff = next(s for s in raw["sections"] if s["key"] == "staff")
    pgt = next(i for i in staff["items"] if i["key"] == "pgt_count")
    pgt["rows"] = [{"key": "a", "label": "A"}]
    with pytest.raises(ValidationError, match="only count tables"):
        DisclosureFormat.model_validate(raw)


# --- count tables -------------------------------------------------------------------------------


def test_FR_DISC_016_count_table_values_are_normalised() -> None:
    clean, problems = values.check_inputs(
        V2,
        {
            "staff_qualifications": {
                "tgt": {"graduate_bed": 12, "postgraduate_bed": 3, "other": None},
                "principal": {"postgraduate_bed": 1},
                "prt": {"diploma": None},
            }
        },
    )
    assert problems.errors == []
    assert clean == {
        "staff_qualifications": {
            "principal": {"postgraduate_bed": 1},
            "tgt": {"postgraduate_bed": 3, "graduate_bed": 12},
        }
    }


def test_FR_DISC_016_an_empty_count_table_is_dropped() -> None:
    clean, problems = values.check_inputs(
        V2, {"staff_qualifications": {"pgt": {"doctorate": None}}}
    )
    assert problems.errors == []
    assert clean == {}


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ({"teachers": {"doctorate": 1}}, "invalid"),
        ({"pgt": {"masters": 1}}, "invalid"),
        ({"pgt": {"doctorate": -1}}, "out_of_range"),
        ({"pgt": {"doctorate": 1001}}, "out_of_range"),
        ({"pgt": {"doctorate": True}}, "invalid"),
        ({"pgt": {"doctorate": "Synthetica Rao"}}, "invalid"),
        ({"pgt": ["Synthetica Rao"]}, "invalid"),
        (["Synthetica Rao"], "invalid"),
    ],
)
def test_FR_DISC_016_count_tables_hold_numbers_only(value: Any, code: str) -> None:
    _clean, problems = values.check_inputs(V2, {"staff_qualifications": value})
    assert [e["code"] for e in problems.errors] == [code]


def test_FR_DISC_018_count_tables_are_published_as_a_table_with_totals() -> None:
    item = _item("staff_qualifications")
    table = values.count_table(
        item, {"pgt": {"doctorate": 1, "postgraduate_bed": 9}, "principal": {"doctorate": 1}}
    )
    assert table.columns == (
        "Category",
        "Doctorate",
        "Post-graduate with B.Ed.",
        "Graduate with B.Ed.",
        "D.El.Ed. or equivalent diploma",
        "Other",
        "Total",
    )
    assert table.rows == (
        ("Principal", "1", "", "", "", "", "1"),
        ("PGT", "1", "9", "", "", "", "10"),
    )


# --- carrying a draft over ----------------------------------------------------------------------


def test_FR_DISC_014_a_version_1_draft_keeps_every_matching_item() -> None:
    inputs = {
        "affiliation_no": "1130001",
        "principal_qualification": "M.Sc., B.Ed.",
        "pgt_count": 12,
        "girls_toilets": 4,
        "internet": True,
        "infrastructure_video": "https://video.example/school",
        "board_results": [{"year": "2025-26", "class_level": "X", "appeared": 40, "passed": 39}],
    }
    docs = {"fire_safety": ["0190a8f0-0000-7000-8000-000000000001"]}
    moved = values.carry_over(V1, V2, inputs, docs)
    assert moved.inputs == inputs
    assert moved.documents == docs
    assert moved.dropped == ()


def test_FR_DISC_014_items_that_changed_or_vanished_are_dropped() -> None:
    raw = copy.deepcopy(_raw(2))
    raw["version"] = 3
    infra = next(s for s in raw["sections"] if s["key"] == "infrastructure")
    infra["items"] = [i for i in infra["items"] if i["key"] != "labs"]
    girls = next(i for i in infra["items"] if i["key"] == "girls_toilets")
    girls["max_value"] = 10  # narrower: 40 is no longer accepted
    internet = next(i for i in infra["items"] if i["key"] == "internet")
    internet["type"] = "text"  # filled differently
    internet["max_length"] = 20
    docs_section = next(s for s in raw["sections"] if s["key"] == "documents")
    docs_section["items"] = [i for i in docs_section["items"] if i["key"] != "noc"]
    v3 = DisclosureFormat.model_validate(raw)
    moved = values.carry_over(
        V2,
        v3,
        {"labs": "2 labs", "girls_toilets": 40, "internet": True, "boys_toilets": 5, "x": "y"},
        {"noc": ["a"], "fire_safety": ["b"], "smc_list": []},
    )
    assert moved.inputs == {"boys_toilets": 5}
    assert moved.documents == {"fire_safety": ["b"]}
    assert moved.dropped == ("girls_toilets", "internet", "labs", "noc", "x")


# --- page ---------------------------------------------------------------------------------------


def _content(fmt: DisclosureFormat, inputs: dict[str, Any]) -> dict[str, Any]:
    return values.build_content(
        fmt,
        school_name="Synthetic Model School",
        inputs=inputs,
        derived={
            "registered_students": values.Derived(
                table=values.Table(
                    columns=("Class", "Registered students"),
                    rows=(("Class IX", "fewer than 10"), ("Total", "fewer than 10")),
                )
            )
        },
        school={"school.name": "Synthetic Model School"},
        documents=[],
    )


def test_FR_DISC_018_version_2_content_and_page_show_the_new_items() -> None:
    clean, problems = values.check_inputs(
        V2,
        {
            "cwsn_toilets": 2,
            "library_books": 4500,
            "staff_qualifications": {"pgt": {"postgraduate_bed": 11}},
        },
    )
    assert problems.errors == []
    content = _content(V2, clean)
    assert content["format_version"] == 2
    items = {i["key"]: i for s in content["sections"] for i in s["items"]}
    assert items["cwsn_toilets"]["value"] == "2"
    assert items["staff_qualifications"]["table"]["rows"] == [["PGT", "", "11", "", "", "", "11"]]
    assert items["registered_students"]["table"]["rows"][0] == ["Class IX", "fewer than 10"]
    page = templates.render_page(
        content,
        published_at=dt.datetime(2026, 11, 9, 6, 0, tzinfo=dt.UTC),
        document_url="https://schoolos.example/d/k/documents/{n}",
    )
    assert "CWSN-friendly toilets" in page
    assert "Post-graduate with B.Ed." in page
    assert "<script" not in page


def test_FR_DISC_019_version_1_content_still_renders() -> None:
    content = _content(V1, {"girls_toilets": 3})
    assert content["format_version"] == 1
    page = templates.render_page(
        content,
        published_at=dt.datetime(2026, 10, 21, 6, 0, tzinfo=dt.UTC),
        document_url="https://schoolos.example/d/k/documents/{n}",
    )
    assert "Number of girls&#x27; toilets" in page
    assert "CWSN" not in page
