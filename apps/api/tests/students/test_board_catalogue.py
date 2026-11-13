"""Board candidate catalogue and its readiness rules (FO-10; FR-STU-020..025, FR-STU-030..033).

Pure: no database. Codes are board subject codes (reference data), never personal data.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

import pytest
import yaml

from app.dq.profiles import load_profiles
from app.exports.config import load_config
from app.students.board_catalogue import (
    CANDIDATE_KEYS,
    PHOTO_KEY,
    SUBJECTS_KEY,
    BoardScheme,
    candidate_issues,
    load_board_catalogue,
    normalise_codes,
    parse_catalogue,
    split_codes,
)


def _attributes() -> dict[str, Any]:
    raw = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    attributes: dict[str, Any] = raw["attributes"]
    return attributes


def _scheme(key: str) -> BoardScheme:
    return load_board_catalogue().schemes[key]


BSEAP_READY = {
    "exam_medium": "telugu",
    "first_language": "TELUGU",
    "second_language": "HINDI",
    "third_language": "ENGLISH",
    "photo_signature_ready": "yes",
}
CBSE_READY = {
    "exam_medium": None,
    "first_language": "184",
    "second_language": "089",
    "board_subjects": "041,086,087",
    "photo_signature_ready": "yes",
}


def test_FR_STU_020_catalogue_is_unverified_with_sources() -> None:
    catalogue = load_board_catalogue()
    assert set(catalogue.schemes) == {"bseap-ssc", "cbse-secondary"}
    for scheme in catalogue.schemes.values():
        assert scheme.verified is False, "owner decision D3: unverified until a school confirms"
        assert scheme.source
        assert scheme.source_note
    assert _scheme("bseap-ssc").board == "BSEAP"
    assert _scheme("cbse-secondary").board == "CBSE"
    assert _scheme("cbse-secondary").classes == ("IX", "X")


def test_FR_STU_021_candidate_attributes_are_c2_and_not_identity() -> None:
    attributes = _attributes()
    for key in CANDIDATE_KEYS:
        spec = attributes[key]
        assert spec["classification"] == "C2", key
        assert spec["is_identity"] is False, key
        assert "admission_register" not in spec["validation"].get("sources", []), key
    # FR-STU-025: the photo tick is a staff observation only; no photo is ever stored.
    assert attributes[PHOTO_KEY]["validation"]["sources"] == ["manual_entry"]
    assert attributes[PHOTO_KEY]["validation"]["values"] == ["yes", "no"]


def test_FR_STU_022_medium_options_are_attribute_values() -> None:
    allowed = set(_attributes()["exam_medium"]["validation"]["values"])
    for scheme in load_board_catalogue().schemes.values():
        assert set(scheme.medium.options) <= allowed


def test_FR_STU_030_a_complete_candidate_has_no_issues() -> None:
    assert candidate_issues(_scheme("bseap-ssc"), BSEAP_READY) == ()
    assert candidate_issues(_scheme("cbse-secondary"), CBSE_READY) == ()
    # Optional skill subject, lower case and spaces are accepted.
    cbse = {**CBSE_READY, "board_subjects": " 241, 086 ,087,402"}
    assert candidate_issues(_scheme("cbse-secondary"), cbse) == ()


def test_FR_STU_031_missing_medium_language_and_photo() -> None:
    issues = candidate_issues(_scheme("bseap-ssc"), {})
    assert [(i.field, i.reason) for i in issues] == [
        ("exam_medium", "missing"),
        ("first_language", "missing"),
        ("second_language", "missing"),
        ("third_language", "missing"),
        ("photo_signature_ready", "photo"),
    ]
    no_photo = {**BSEAP_READY, "photo_signature_ready": "no"}
    assert [i.reason for i in candidate_issues(_scheme("bseap-ssc"), no_photo)] == ["photo"]


def test_FR_STU_032_codes_outside_the_list_and_repeats() -> None:
    values = {**BSEAP_READY, "second_language": "TELUGU", "third_language": "FRENCH"}
    issues = candidate_issues(_scheme("bseap-ssc"), values)
    assert [(i.field, i.reason, i.code) for i in issues] == [
        ("second_language", "repeated", "TELUGU"),
        ("third_language", "not_in_list", "FRENCH"),
    ]
    hindi = {**CBSE_READY, "exam_medium": "hindi"}
    assert candidate_issues(_scheme("cbse-secondary"), hindi) == ()
    bad_medium = {**CBSE_READY, "exam_medium": "telugu"}
    assert [i.reason for i in candidate_issues(_scheme("cbse-secondary"), bad_medium)] == [
        "not_in_list"
    ]


def test_FR_STU_033_cbse_subject_combination() -> None:
    scheme = _scheme("cbse-secondary")

    def reasons(subjects: str | None) -> list[tuple[str, str, str | None]]:
        values = {**CBSE_READY, SUBJECTS_KEY: subjects}
        return [(i.field, i.reason, i.group) for i in candidate_issues(scheme, values)]

    assert reasons(None) == [(SUBJECTS_KEY, "missing", None)]
    assert reasons("086,087") == [(SUBJECTS_KEY, "group_missing", "mathematics")]
    assert reasons("041,241,086,087") == [(SUBJECTS_KEY, "too_many", "mathematics")]
    assert reasons("041,086,087,999") == [(SUBJECTS_KEY, "not_in_list", None)]
    # Sanskrit as a language and again as the optional subject is a repeat.
    values = {**CBSE_READY, "second_language": "122", SUBJECTS_KEY: "041,086,087,122"}
    assert [(i.reason, i.code) for i in candidate_issues(scheme, values)] == [("repeated", "122")]


def test_codes_are_normalised() -> None:
    assert split_codes(" 041, 086 ,,087 ") == ["041", "086", "087"]
    assert normalise_codes(["041", " 086", "041", ""]) == "041,086"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"board": "XYZ"}, "board must be"),
        ({"medium": {"options": ["latin"]}}, "unknown media"),
        ({"codes": {}}, "without a label"),
        ({"source": ["http://insecure.example"]}, "https"),
    ],
)
def test_catalogue_validation_refuses_bad_entries(change: dict[str, Any], message: str) -> None:
    raw = yaml.safe_load(
        resources.files("app.students").joinpath("board_catalogue.yaml").read_text("utf-8")
    )
    raw["schemes"]["bseap-ssc"].update(change)
    with pytest.raises(ValueError, match=message):
        parse_catalogue(raw)


def test_FR_STU_034_profiles_name_a_scheme_of_their_board_and_sheets_hold_its_keys() -> None:
    catalogue = load_board_catalogue()
    layouts = load_config().profiles
    named = {p.key: p for p in load_profiles().values() if p.candidate_scheme}
    assert set(named) == {"bseap-ssc-2027", "cbse-loc-2027"}
    for profile in named.values():
        assert profile.candidate_scheme is not None
        scheme = catalogue.schemes[profile.candidate_scheme]
        assert scheme.board == profile.board
        assert set(profile.classes) & set(scheme.classes)
        fields = set(layouts[profile.key].fields)
        wanted = {k for k in scheme.keys if k != "exam_medium" or scheme.medium.required}
        assert wanted <= fields, (profile.key, wanted - fields)
        columns = {f.attribute for f in profile.board_fields}
        assert wanted <= columns, (profile.key, wanted - columns)
