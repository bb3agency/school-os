"""UDISE+ 2026-27 student profile (ADR-0039; FR-EXP-006; owner decision D3). Pure tests."""

from __future__ import annotations

from importlib import resources
from typing import Any

import yaml

from app.dq.profiles import load_profiles
from app.exports.config import load_config

STRUCTURE = {"class", "section", "roll_no"}


def _catalog() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    attributes: dict[str, Any] = raw["attributes"]
    return attributes


def test_D3_udise_profile_names_its_public_sources_and_is_unverified() -> None:
    profile = load_profiles()["udise-plus"]
    assert profile.verified is False
    assert profile.source, "every board/portal profile carries its sources"
    assert all(str(u).startswith("https://") for u in profile.source)


def test_FR_EXP_006_portal_fields_map_to_what_schoolos_holds() -> None:
    profile = load_profiles()["udise-plus"]
    catalog = _catalog()
    mapped = [f.maps_to for f in profile.portal_fields if f.maps_to is not None]
    assert set(mapped) <= set(catalog) | STRUCTURE
    for key in ("udise_pen", "apaar_id", "full_name", "dob", "gender", "admission_no"):
        assert key in mapped
    # Invariant 4: the Aadhaar number and the Aadhaar-as-printed name never come from SchoolOS.
    aadhaar = [f for f in profile.portal_fields if "Aadhaar" in f.label]
    assert aadhaar
    assert all(f.never_from_schoolos and f.maps_to is None for f in aadhaar)


def test_FR_EXP_006_ready_sheet_covers_every_mapped_attribute_in_portal_order() -> None:
    profile = load_profiles()["udise-plus"]
    layout = load_config().profiles["udise-plus"]
    assert layout.layout_version == 3
    attributes = [
        f.maps_to for f in profile.portal_fields if f.maps_to and f.maps_to not in STRUCTURE
    ]
    # The sheet lists every attribute the portal fields name, except restricted ones that never
    # leave SchoolOS in clear (address), in the portal's order.
    expected = [a for a in attributes if a in layout.fields]
    assert expected == [f for f in layout.fields if f in attributes]
    assert set(profile.required_fields) <= set(layout.fields)
    assert layout.fields[-2:] == ("udise_pen", "apaar_id")
