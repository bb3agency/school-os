"""Board and portal readiness: exact diff, fix owners, profiles and the DQ-030 check
(FR-DQ-030..FR-DQ-034, US-503..US-505, ADR-0040). Pure, in memory; synthetic names only
(Telugu-origin names written in Latin script, as AP school registers do)."""

from __future__ import annotations

import copy
import sys
from importlib import resources
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.dq import readiness as rd
from app.dq.checks import CanonicalFact, assess_readiness, build_checks, evaluate, fingerprint_of
from app.dq.explanations import load_explanations
from app.dq.matching import classify
from app.dq.profiles import ReadinessField, load_engine_config, load_profiles
from app.dq.rules import CheckKind, load_rules

CS = sys.modules["sos_test_dq_checks_support"]
REG, AAD, UDISE, BOARD = (
    "admission_register",
    "aadhaar_as_printed",
    "udise_plus",
    "board_registration",
)
BIRTH = "birth_certificate"
CFG = rd.load_readiness_config()


def kinds(a: str, b: str) -> tuple[str, ...]:
    return rd.name_kinds(a, b, lambda x, y: classify(x, y).match_class)


def classifier(a: str, b: str) -> Any:
    return classify(a, b).match_class


NAME = ReadinessField(
    attribute="full_name",
    sources=(REG, AAD, UDISE, BOARD),
    required=(REG, AAD, UDISE),
)


def assess(values: dict[str, str | None], spec: ReadinessField = NAME) -> rd.FieldAssessment:
    return rd.assess_field(spec, "name", values, CFG, classifier, advisory_kinds={"case"})


# --- exact diff -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("KOMMINENI VENKATA SAI", "KOMMINENI VENKATA SAI", ()),
        ("Kommineni Venkata Sai", "KOMMINENI VENKATA SAI", ("case",)),
        ("VENKATA SAI KUMAR", "VENKATA SAIKUMAR", ("spacing",)),
        ("VENKATA SAI KUMAR", "VENKATA  SAI KUMAR", ("spacing",)),
        ("Venkata Sai Kumar", "VENKATA SAIKUMAR", ("spacing", "case")),
        ("K. RAVI TEJA", "K RAVI TEJA", ("punctuation",)),
        ("K VENKATA SAI", "KOMMINENI VENKATA SAI", ("initials",)),
        ("VENKATA SAI KOMMINENI", "KOMMINENI VENKATA SAI", ("order",)),
        ("KOMMINENI VENKATA SAI", "KOMMINENI VENKATA SIA", ("transposed",)),
        ("LAKSHMI PRASANNA", "LAXMI PRASANNA", ("variant",)),
        ("SRINIVAS RAO", "SREENIVAS RAO", ("variant",)),
        ("BHARGAVI", "BHARGAVI REDDY", ("word_missing",)),
        ("కొమ్మినేని సాయి", "KOMMINENI SAI", ("script",)),
        ("RAVI TEJA", "SUDHA RANI", ("different",)),
    ],
)
def test_FR_DQ_030_name_differences_are_exact_and_named(
    a: str, b: str, expected: tuple[str, ...]
) -> None:
    assert kinds(a, b) == expected


def test_FR_DQ_030_nfc_forms_of_the_same_name_are_equal() -> None:
    composed, decomposed = "RENÉE", "RENÉE"
    assert composed != decomposed
    assert kinds(composed, decomposed) == ()


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("2012-03-14", "2012-03-14", ()),
        ("2012-03-04", "2012-04-03", ("day_month_swapped",)),
        ("2012-03-14", "2012-03-15", ("day",)),
        ("2012-03-14", "2011-03-14", ("year",)),
        ("2012-03-14", "2011-04-14", ("month", "year")),
        ("2012-03-14", "14/03/2012", ("unreadable",)),
    ],
)
def test_FR_DQ_030_dates_compare_as_dates(a: str, b: str, expected: tuple[str, ...]) -> None:
    assert rd.diff("date", a, b, classifier) == expected


def test_FR_DQ_030_gender_compares_exactly() -> None:
    assert rd.diff("enum", "male", "male", classifier) == ()
    assert rd.diff("enum", "male", "female", classifier) == ("different",)


def test_FR_DQ_030_changes_say_exactly_what_differs() -> None:
    def said(a: str, b: str) -> list[str]:
        return [c.text(CFG, "en") for c in rd.describe("name", a, b, kinds(a, b))]

    assert said("VENKATA SAI KUMAR", "VENKATA SAIKUMAR") == ["space missing after “SAI”"]
    assert said("VENKATA SAI KUMAR", "VENKATA  SAI KUMAR") == ["extra space after “VENKATA”"]
    assert said("KOMMINENI VENKATA SAI", "KOMMINENI VENKATA SIA") == [
        "“AI” written as “IA” in “SAI”"
    ]
    assert said("SRINIVAS RAO", "SREENIVAS RAO") == ["“EE” instead of “I” in “SRINIVAS”"]
    # Reordered words and initials are described by their kind only.
    assert said("VENKATA SAI KOMMINENI", "KOMMINENI VENKATA SAI") == []
    segments = rd.segments("name", "SAI KUMAR", "SAIKUMAR")
    assert [(s.op, s.reference, s.other) for s in segments] == [
        ("equal", "SAI", "SAI"),
        ("delete", " ", ""),
        ("equal", "KUMAR", "KUMAR"),
    ]
    dates = rd.segments("date", "2012-03-04", "2012-04-03")
    assert "".join(s.reference for s in dates) == "04/03/2012"
    assert "".join(s.other for s in dates) == "03/04/2012"


# --- fix owners -------------------------------------------------------------------------------


def owners(result: rd.FieldAssessment) -> dict[str | None, str]:
    return {i.source: i.owner for i in result.items}


def test_FR_DQ_031_aadhaar_differs_from_an_undisputed_register_parent_fixes_aadhaar() -> None:
    result = assess({REG: "VENKATA SAI KUMAR", AAD: "VENKATA SAIKUMAR", UDISE: "VENKATA SAI KUMAR"})
    assert result.reference == REG
    (item,) = result.items
    assert (item.source, item.against, item.owner, item.kinds) == (
        AAD,
        REG,
        "parent_aadhaar",
        ("spacing",),
    )
    assert rd.student_status(result.items, CFG) == "needs_parent"


def test_FR_DQ_031_udise_and_board_differences_go_to_the_school_through_udise() -> None:
    result = assess(
        {
            REG: "KOMMINENI VENKATA SAI",
            AAD: "KOMMINENI VENKATA SAI",
            UDISE: "K VENKATA SAI",
            BOARD: "KOMMINENI VENKATA SIA",
        }
    )
    assert owners(result) == {UDISE: "school_udise", BOARD: "school_udise"}
    assert {i.kinds for i in result.items} == {("initials",), ("transposed",)}
    assert rd.student_status(result.items, CFG) == "needs_school"


def test_FR_DQ_031_register_contradicted_by_birth_certificate_is_the_record_to_correct() -> None:
    result = assess(
        {
            REG: "SRINIVAS RAO",
            AAD: "SREENIVAS RAO",
            UDISE: "SRINIVAS RAO",
            BIRTH: "SREENIVAS RAO",
        }
    )
    assert result.reference == BIRTH
    assert owners(result) == {REG: "school_register", UDISE: "school_udise"}
    register = next(i for i in result.items if i.source == REG)
    assert register.against == BIRTH
    assert register.kinds == ("variant",)


def test_FR_DQ_031_corroborated_register_keeps_the_parent_as_owner() -> None:
    result = assess(
        {REG: "SRINIVAS RAO", AAD: "SREENIVAS RAO", UDISE: "SRINIVAS RAO", BIRTH: "SRINIVAS RAO"}
    )
    assert owners(result) == {AAD: "parent_aadhaar"}


def test_FR_DQ_031_disputed_register_without_agreement_is_undecided() -> None:
    result = assess(
        {REG: "SRINIVAS RAO", AAD: "SREENIVAS RAO", UDISE: "SRINIVASA RAO", BIRTH: "SRINU RAO"}
    )
    (item,) = result.items
    assert (item.reason, item.owner, item.source) == ("undecided", "unknown", None)
    assert rd.student_status(result.items, CFG) == "blocked"


def test_FR_DQ_031_missing_values_name_who_must_record_them() -> None:
    result = assess({REG: None, AAD: "VENKATA SAI", UDISE: "VENKATA  SAI"})
    reasons = sorted((i.reason, i.owner, i.source) for i in result.items)
    assert reasons == [("missing", "school_register", REG), ("undecided", "unknown", None)]
    missing_aadhaar = assess({REG: "VENKATA SAI", AAD: None, UDISE: "VENKATA SAI"})
    (item,) = missing_aadhaar.items
    assert (item.reason, item.owner) == ("missing", "unknown")
    assert rd.student_status(missing_aadhaar.items, CFG) == "blocked"


def test_FR_DQ_031_capital_letters_only_are_advisory() -> None:
    result = assess({REG: "VENKATA SAI", AAD: "Venkata Sai", UDISE: "VENKATA SAI"})
    (item,) = result.items
    assert item.advisory
    assert item.kinds == ("case",)
    assert rd.student_status(result.items, CFG) == "ready"
    assert rd.item_severity(item, CFG).value == "info"


def test_FR_DQ_031_matching_records_are_ready() -> None:
    result = assess({REG: "VENKATA SAI", AAD: "VENKATA SAI", UDISE: "VENKATA SAI", BOARD: None})
    assert result.items == ()
    assert rd.student_status(result.items, CFG) == "ready"


def test_FR_DQ_034_apaar_compares_udise_with_aadhaar_using_the_register_as_referee() -> None:
    spec = ReadinessField(attribute="full_name", sources=(UDISE, AAD), required=(UDISE, AAD))
    udise_wrong = rd.assess_field(
        spec,
        "name",
        {REG: "VENKATA SAI", AAD: "VENKATA SAI", UDISE: "VENKATA SIA"},
        CFG,
        classifier,
    )
    assert owners(udise_wrong) == {UDISE: "school_udise"}
    aadhaar_wrong = rd.assess_field(
        spec, "name", {REG: "VENKATA SAI", AAD: "VENKATASAI", UDISE: "VENKATA SAI"}, CFG, classifier
    )
    assert owners(aadhaar_wrong) == {AAD: "parent_aadhaar"}
    # UDISE+ and Aadhaar agree: APAAR generation will not fail, whatever the register says.
    agree = rd.assess_field(
        spec, "name", {REG: "VENKATA SAI", AAD: "VENKATASAI", UDISE: "VENKATASAI"}, CFG, classifier
    )
    assert agree.items == ()
    no_register = rd.assess_field(
        spec, "name", {AAD: "VENKATASAI", UDISE: "VENKATA SAI"}, CFG, classifier
    )
    assert [i.owner for i in no_register.items] == ["unknown"]


# --- configuration and profiles ---------------------------------------------------------------


def test_CLAUDE_6_13_readiness_rules_live_in_versioned_config() -> None:
    raw = yaml.safe_load(
        resources.files("app.dq").joinpath("config/readiness.yaml").read_text("utf-8")
    )
    broken = copy.deepcopy(raw)
    del broken["owners"]["by_source"]["udise_plus"]
    with pytest.raises(ValidationError, match="by_source"):
        rd.parse_readiness_config(broken)
    unknown_route = copy.deepcopy(raw)
    unknown_route["routes"]["unknown"] = ["ROUTE-NOWHERE"]
    with pytest.raises(ValidationError, match="correction routes"):
        rd.parse_readiness_config(unknown_route)
    assert set(CFG.owner_labels) == set(rd.OWNERS)


def test_D3_readiness_profiles_name_public_sources_and_stay_unverified() -> None:
    profiles = load_profiles()
    for key in ("bseap-ssc-2027", "apaar"):
        profile = profiles[key]
        assert profile.readiness is not None
        assert profile.verified is False
        assert profile.source
        assert all(u.startswith("https://") for u in profile.source)
        CFG.check_profile(profile.readiness)
    assert profiles["bseap-ssc-2027"].readiness.classes == ("IX", "X")  # type: ignore[union-attr]
    assert profiles["apaar"].readiness.skip_when_verified == "apaar_id"  # type: ignore[union-attr]


# --- DQ-030 check -----------------------------------------------------------------------------


def _student(class_code: str = "IX", **values: str | None) -> Any:
    raw = {
        ("full_name", REG): values.get("name", "KOMMINENI VENKATA SAI"),
        ("aadhaar_name_as_printed", AAD): values.get("aadhaar", "KOMMINENI VENKATASAI"),
        ("full_name", UDISE): values.get("udise", "KOMMINENI VENKATA SAI"),
        ("dob", REG): "2012-03-04",
        ("aadhaar_dob_as_printed", AAD): values.get("aadhaar_dob", "2012-04-03"),
        ("dob", UDISE): "2012-03-04",
        ("gender", REG): "male",
        ("aadhaar_gender_as_printed", AAD): "male",
        ("gender", UDISE): "male",
    }
    return CS.facts(values=raw, enrolments=[CS.enrolment(class_code)])


def test_FR_DQ_033_check_raises_one_finding_per_difference_with_masked_values() -> None:
    facts = _student()
    rule = load_rules()["DQ-030"]
    assert rule.check is CheckKind.READINESS_DIFF
    assert rule.requires_profile
    found = [
        f
        for f in evaluate(
            CS.context([facts], profiles=["bseap-ssc-2027"]), build_checks(load_rules())
        )
        if f.rule_id == "DQ-030"
    ]
    by_attribute = {f.attribute_key: f for f in found}
    assert set(by_attribute) == {"full_name", "dob"}
    name = by_attribute["full_name"]
    assert name.explanation_code == "DQ-030-PARENT"
    assert name.route_codes == ("ROUTE-UIDAI",)
    assert name.severity.value == "blocker"
    assert name.details["owner"] == "parent_aadhaar"
    assert name.details["kinds"] == ["spacing"]
    assert name.details["profile_key"] == "bseap-ssc-2027"
    assert by_attribute["dob"].details["kinds"] == ["day_month_swapped"]
    text = repr(found)
    assert "VENKATASAI" not in text
    assert "VENKATA" not in text
    # Idempotent: the same facts give the same fingerprints.
    again = evaluate(CS.context([facts], profiles=["bseap-ssc-2027"]), build_checks(load_rules()))
    assert {fingerprint_of(f) for f in found} <= {fingerprint_of(f) for f in again}


def test_FR_DQ_032_ssc_applies_to_classes_ix_and_x_only() -> None:
    profile = load_profiles()["bseap-ssc-2027"]
    cfg = load_engine_config()
    assert assess_readiness(_student("IX"), profile, cfg, classifier).applies
    assert assess_readiness(_student("X"), profile, cfg, classifier).applies
    assert not assess_readiness(_student("VIII"), profile, cfg, classifier).applies


def test_FR_DQ_034_apaar_skips_students_with_a_verified_apaar_id() -> None:
    profile = load_profiles()["apaar"]
    cfg = load_engine_config()
    pending = _student("V")
    assert assess_readiness(pending, profile, cfg, classifier).items
    done = CS.facts(
        values={("full_name", UDISE): "A", ("aadhaar_name_as_printed", AAD): "B"},
        canonical={"apaar_id": CanonicalFact("123456789012", "udise_plus", False, verified=True)},
    )
    assert not assess_readiness(done, profile, cfg, classifier).applies


@pytest.mark.parametrize(
    "values",
    [
        {("full_name", REG): None},  # register missing: missing + undecided
        {("aadhaar_name_as_printed", AAD): None},  # Aadhaar missing
        {("full_name", BIRTH): "KOMMINENI VENKATASAI"},  # register contradicted
        {("full_name", BIRTH): "SRINU", ("full_name", UDISE): "KOMMINENI V SAI"},  # undecided
        {("aadhaar_name_as_printed", AAD): "Kommineni Venkata Sai"},  # advisory
        {("full_name", UDISE): "KOMMINENI VENKATA SIA"},  # UDISE+
    ],
)
def test_FR_DQ_033_every_readiness_finding_has_a_complete_explanation(
    values: dict[tuple[str, str], str | None],
) -> None:
    raw: dict[tuple[str, str], str | None] = {
        ("full_name", REG): "KOMMINENI VENKATA SAI",
        ("aadhaar_name_as_printed", AAD): "KOMMINENI VENKATA SAI",
        ("full_name", UDISE): "KOMMINENI VENKATA SAI",
        **values,
    }
    facts = CS.facts(
        values={k: v for k, v in raw.items() if v is not None}, enrolments=[CS.enrolment("X")]
    )
    found = [
        f
        for f in evaluate(
            CS.context([facts], profiles=["bseap-ssc-2027"]), build_checks(load_rules())
        )
        if f.rule_id == "DQ-030"
    ]
    assert found
    catalog = load_explanations()
    for finding in found:
        params = dict(finding.details["params"])  # type: ignore[call-overload]
        # Raises when a placeholder is missing or extra (the API renders the same way).
        assert catalog.render(finding.explanation_code, "en", **params)
