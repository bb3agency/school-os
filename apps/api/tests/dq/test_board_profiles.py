"""R1 board registration profiles: CBSE and CISCE (FR-DQ-030..032, FR-EXP-007; ADR-0041).

Pure: the packaged YAML is loaded and validated; no database. Owner decision D3 (docs/18 §8):
every board format names public sources and stays unverified until a school confirms it.
"""

from __future__ import annotations

import copy
from importlib import resources
from typing import Any

import pytest
import yaml

from app.dq.profiles import BOARD_CODES, load_profiles, parse_profiles, superseded_keys
from app.exports.config import load_config

R1_BOARD_PROFILES = ("cbse-registration-2027", "cbse-loc-2027", "cisce-registration-2027")


def _catalog() -> set[str]:
    raw = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    return set(raw["attributes"])


def test_FR_DQ_030_r1_profiles_load_with_board_classes_and_sources() -> None:
    profiles = load_profiles()
    for key in R1_BOARD_PROFILES:
        profile = profiles[key]
        assert profile.board in BOARD_CODES
        assert profile.classes
        assert profile.source, f"{key}: D3 needs public source URLs"
        assert all(url.startswith("https://") for url in profile.source)
        assert profile.verified is False, f"{key}: unverified until a school confirms it"
        assert profile.parent_verification_slip is True
    assert profiles["cbse-registration-2027"].classes == ("IX", "XI")
    assert profiles["cbse-loc-2027"].classes == ("X", "XII")
    assert profiles["cisce-registration-2027"].classes == ("IX", "X", "XI", "XII")


def test_FR_DQ_030_cbse_needs_apaar_and_category() -> None:
    profiles = load_profiles()
    for key in ("cbse-registration-2027", "cbse-loc-2027"):
        profile = profiles[key]
        assert profile.needs_apaar is True  # APAAR is a registration/LOC field from 2026-27
        assert "category" in profile.required_fields
        # Consent may be refused (SC 20 Jul 2026): the APAAR ID is not a DQ-005 blocker.
        assert "apaar_id" not in profile.required_fields
        apaar = next(f for f in profile.board_fields if f.attribute == "apaar_id")
        assert "REFUSED" in apaar.note


def test_FR_DQ_031_board_fields_and_required_fields_name_catalog_attributes() -> None:
    catalog = _catalog()
    for profile in load_profiles().values():
        assert set(profile.required_fields) <= catalog, profile.key
        for field in profile.board_fields:
            assert field.attribute is None or field.attribute in catalog, (profile.key, field)


def test_FR_DQ_030_cisce_2027_supersedes_2026_and_keeps_it_working() -> None:
    profiles = load_profiles()
    assert profiles["cisce-registration-2027"].supersedes == "cisce-registration-2026"
    assert superseded_keys(profiles) == {"cisce-registration-2026"}
    old = profiles["cisce-registration-2026"]  # history unchanged (version 1, same fields)
    assert old.version == 1
    assert old.required_fields == (
        "full_name",
        "dob",
        "gender",
        "father_name",
        "mother_name",
        "admission_no",
    )


def test_FR_EXP_007_every_board_profile_has_an_export_layout_with_its_required_fields() -> None:
    layouts = load_config().profiles
    for key in R1_BOARD_PROFILES:
        layout = layouts[key]
        assert layout.kind == "board"
        assert set(load_profiles()[key].required_fields) <= set(layout.fields)
        assert "apaar_id" in layout.fields


def _raw(key: str) -> dict[str, Any]:
    text = resources.files("app.dq").joinpath(f"config/profiles/{key}.yaml").read_text("utf-8")
    data: dict[str, Any] = yaml.safe_load(text)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"board": "SSC"}, "board must be one of"),
        ({"classes": ["IX", "IX"]}, "unique class codes"),
        ({"source": ["http://example.org/plain"]}, "https URLs"),
        ({"supersedes": "no-such-profile"}, "supersedes unknown profile"),
        ({"board_fields": [{"name": "X", "attribute": "Not A Key"}]}, "attribute key"),
    ],
)
def test_FR_DQ_030_invalid_profiles_are_refused(change: dict[str, Any], message: str) -> None:
    raw = copy.deepcopy(_raw("cbse-registration-2027"))
    raw.update(change)
    with pytest.raises(ValueError, match=message):
        parse_profiles({"cbse-registration-2027": raw})
