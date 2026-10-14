"""Import template library: presets, auto-mapping and blank templates (FR-IMP-030..033,
ADR-0041). Pure: packaged YAML only, no database."""

from __future__ import annotations

import copy
import io
from importlib import resources
from typing import Any

import pytest
import yaml
from openpyxl import load_workbook

from app.core.spreadsheet import write_xlsx
from app.core.textnorm import has_telugu
from app.imports.mapping import SPECIAL_TARGETS
from app.imports.presets import (
    load_presets,
    missing_columns,
    parse_presets,
    preset_mapping,
    template_header,
)

EXPECTED = {"schoolos-blank", "register-excel", "udise-plus-student-list", "erp-student-export"}


def _catalog() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    attributes: dict[str, Any] = raw["attributes"]
    return attributes


def test_FR_IMP_020_library_has_the_common_starting_points() -> None:
    presets = load_presets()
    assert set(presets) == EXPECTED
    for preset in presets.values():
        assert has_telugu(preset.label_te), preset.key
        assert preset.template


def test_FR_IMP_020_every_target_is_an_attribute_or_structure_column_its_source_may_record() -> (
    None
):
    catalog = _catalog()
    for preset in load_presets().values():
        for column in preset.columns:
            if column.target in SPECIAL_TARGETS:
                continue
            assert column.target in catalog, (preset.key, column.target)
            sources = catalog[column.target].get("validation", {}).get("sources")
            if column.target != "admission_no" and sources is not None:
                assert preset.import_source in sources, (preset.key, column.target)


def test_ADR_0041_formats_owned_by_others_are_unverified_and_sourced() -> None:
    presets = load_presets()
    assert presets["schoolos-blank"].verified is True  # our own format
    assert presets["udise-plus-student-list"].verified is False
    assert presets["udise-plus-student-list"].source
    assert presets["erp-student-export"].verified is False
    # The ERP refresh never writes as the admission register (BR-01, invariant 6).
    assert presets["erp-student-export"].import_source == "manual_entry"
    assert presets["udise-plus-student-list"].import_source == "udise_plus"


def test_ADR_0041_blank_templates_never_ask_for_aadhaar() -> None:
    for preset in load_presets().values():
        for column in preset.columns:
            assert not column.target.startswith("aadhaar"), (preset.key, column.target)


def test_FR_IMP_021_preset_maps_headers_and_aliases_after_normalisation() -> None:
    preset = load_presets()["register-excel"]
    headers = ["ADM NO", "name of the pupil", "Father / Guardian Name", "D.O.B.", "Remarks"]
    allowed = {"admission_no", "full_name", "father_name", "dob"}
    got = [(m.index, m.target) for m in preset_mapping(headers, preset, allowed)]
    assert got == [(0, "admission_no"), (1, "full_name"), (2, "father_name"), (3, "dob"), (4, None)]


def test_FR_IMP_021_targets_the_source_cannot_record_stay_unmapped_and_used_once() -> None:
    preset = load_presets()["erp-student-export"]
    headers = ["Admission No", "Adm No", "Religion", "Student Name"]
    matches = preset_mapping(headers, preset, {"admission_no", "full_name"})
    assert [m.target for m in matches] == ["admission_no", None, None, "full_name"]


def test_FR_IMP_021_missing_columns_lists_preset_columns_absent_from_the_file() -> None:
    preset = load_presets()["schoolos-blank"]
    headers = template_header(preset)[:3]
    missing = missing_columns(headers, preset)
    assert "Admission number" not in missing
    assert "Gender" in missing


def test_FR_IMP_020_blank_template_round_trips_through_the_import_mapping() -> None:
    preset = load_presets()["schoolos-blank"]
    content = write_xlsx(template_header(preset), [], title=preset.label_en)
    sheet = load_workbook(io.BytesIO(content)).active
    assert sheet is not None
    header = [c.value for c in sheet[1]]
    assert header == template_header(preset)
    assert sheet.max_row == 1, "headers only, no school data"
    targets = {c.target for c in preset.columns}
    mapped = [m.target for m in preset_mapping([str(h) for h in header], preset, targets)]
    assert mapped == [c.target for c in preset.columns]


def _raw(key: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(
        resources.files("app.imports").joinpath(f"templates/{key}.yaml").read_text("utf-8")
    )
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"key": "other"}, "declares key"),
        ({"source": ["http://insecure.example"]}, "https URLs"),
        ({"import_source": "school_erp"}, "import_source"),
    ],
)
def test_FR_IMP_020_invalid_presets_are_refused(change: dict[str, Any], message: str) -> None:
    raw = copy.deepcopy(_raw("schoolos-blank"))
    raw.update(change)
    with pytest.raises(ValueError, match=message):
        parse_presets({"schoolos-blank": raw})


def test_FR_IMP_020_a_preset_needs_the_admission_number_and_unique_targets() -> None:
    raw = copy.deepcopy(_raw("schoolos-blank"))
    raw["columns"] = [c for c in raw["columns"] if c["target"] != "admission_no"]
    with pytest.raises(ValueError, match="admission number"):
        parse_presets({"schoolos-blank": raw})
    raw = copy.deepcopy(_raw("schoolos-blank"))
    raw["columns"].append({"header": "Name again", "target": "full_name"})
    with pytest.raises(ValueError, match="each target may appear once"):
        parse_presets({"schoolos-blank": raw})
