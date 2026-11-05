"""Export completeness: every column a format file promises has a source (FR-EXP-001,
FR-EXP-007, FR-EXP-008; audit follow-up 2026-11-03).

A column of a pre-check layout or of the generic student list is filled from the canonical view
(an attribute of the student catalog, app/students/attributes.yaml) or from the current-year
placement (class, section, roll number). Every field the board's or portal's own form asks for
(the DQ profile's board_fields / portal_fields) that SchoolOS holds is in the layout or listed in
the layout's ``omitted`` with a reason; fields SchoolOS does not hold are ``attribute: null`` /
``maps_to: null`` there. Every allowed value of a mapped enum has a mapping, and every allowed
value of a coded field is either coded or deliberately ``no_code``. Pure test: no database.
"""

from __future__ import annotations

from typing import Any, get_args

import yaml

from app.dq.profiles import load_profiles
from app.exports.config import STRUCTURE_COLUMNS, StructureColumn, load_config


def _catalog() -> dict[str, Any]:
    from importlib import resources

    text = resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    data: dict[str, Any] = yaml.safe_load(text)["attributes"]
    return data


def _promised(profile: Any) -> set[str]:
    """Attributes (and structure columns) the board's or portal's form asks for and SchoolOS
    holds."""
    keys = {f.attribute for f in profile.board_fields if f.attribute}
    keys |= {f.maps_to for f in profile.portal_fields if f.maps_to}
    return keys


def test_FR_EXP_001_every_layout_column_has_a_source() -> None:
    cfg = load_config()
    catalog = _catalog()
    for key, layout in cfg.profiles.items():
        for field in layout.fields:
            assert field in STRUCTURE_COLUMNS or field in catalog, (
                f"{key}: column {field!r} has no source (not an attribute, not a placement column)"
            )
            assert field not in cfg.never_exported, (key, field)
        for field in layout.omitted:
            assert field in catalog or field in STRUCTURE_COLUMNS, (key, field)
            assert layout.omitted[field].strip(), (key, field)


def test_FR_EXP_001_student_list_structure_columns_are_all_filled() -> None:
    cfg = load_config()
    allowed = set(get_args(StructureColumn))
    assert set(cfg.student_list.structure_columns) <= allowed == set(STRUCTURE_COLUMNS)


def test_FR_EXP_007_every_field_the_form_promises_is_in_the_sheet_or_omitted_with_a_reason() -> (
    None
):
    cfg = load_config()
    profiles = load_profiles()
    catalog = _catalog()
    for key, layout in cfg.profiles.items():
        promised = _promised(profiles[key])
        for field in promised:
            assert field in catalog or field in STRUCTURE_COLUMNS, (key, field)
        gaps = promised - set(layout.fields) - set(layout.omitted) - cfg.never_exported
        assert not gaps, f"{key}: the form asks for {sorted(gaps)} but the sheet leaves them out"


def test_FR_EXP_008_every_enum_value_has_a_mapping_or_is_deliberately_uncoded() -> None:
    cfg = load_config()
    catalog = _catalog()
    for key, layout in cfg.profiles.items():
        for field, mapping in layout.value_maps.items():
            allowed = set(catalog[field]["validation"].get("values") or ())
            assert allowed, (key, field, "value_maps only for enum attributes")
            assert set(mapping) == allowed, (key, field, allowed ^ set(mapping))
        for field, coded in layout.coded_values.items():
            allowed = set(catalog[field]["validation"].get("values") or ())
            assert allowed, (key, field, "coded_values only for enum attributes")
            assert set(coded.codes) | set(coded.no_code) == allowed, (key, field)


def test_FR_EXP_008_bseap_category_is_an_explicit_unverified_code_map() -> None:
    layout = load_config().profiles["bseap-ssc-2027"]
    assert "category" not in layout.value_maps
    coded = layout.coded_values["category"]
    assert coded.verified is False  # owner decision D3: confirm against the current format
    assert coded.version >= 1
    assert coded.codes == {"general": "OC", "sc": "SC", "st": "ST"}
    # The BC group (BC-A..BC-E) is not derivable from "obc": never guessed.
    assert set(coded.no_code) == {"obc", "ews"}
    assert layout.layout_version >= 2
