"""DQ-032 board candidate choices on in-memory facts (FO-10; FR-STU-030..033).

Synthetic facts only. Findings carry codes and labels, never a typed value outside the list.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any, cast

from app.dq.checks import build_checks, evaluate
from app.dq.explanations import load_explanations
from app.dq.rules import Finding, load_rules
from app.students import service as students

CS = sys.modules["sos_test_dq_checks_support"]
CHECKS_ALL = build_checks(load_rules())
SCHEMES = {s.key: s for s in students.board_schemes()}

BSEAP_READY = {
    "exam_medium": "telugu",
    "first_language": "TELUGU",
    "second_language": "HINDI",
    "third_language": "ENGLISH",
    "photo_signature_ready": "yes",
}


def dq032(*facts: Any, profiles: tuple[str, ...] = ("bseap-ssc-2027",)) -> list[Finding]:
    context = CS.context(facts, profiles=profiles, candidate_schemes=SCHEMES)
    return [f for f in evaluate(context, CHECKS_ALL) if f.rule_id == "DQ-032"]


def candidate(values: Mapping[str, str | None], class_code: str = "X", current: bool = True) -> Any:
    return CS.facts(
        canonical=values, enrolments=[CS.enrolment(class_code=class_code, current=current)]
    )


def params_of(finding: Finding) -> dict[str, Any]:
    return cast("dict[str, Any]", finding.details["params"])


def test_FR_STU_030_ready_candidate_raises_nothing() -> None:
    assert dq032(candidate(BSEAP_READY)) == []


def test_FR_STU_031_one_finding_per_missing_field_with_explanation() -> None:
    findings = dq032(candidate({"exam_medium": "telugu", "first_language": "TELUGU"}))
    by_key = {f.attribute_key: f for f in findings}
    assert set(by_key) == {"second_language", "third_language", "photo_signature_ready"}
    second = by_key["second_language"]
    assert second.severity == "high"
    assert second.explanation_code == "DQ-032"
    assert second.route_codes == ("ROUTE-CANDIDATE",)
    assert second.details["profile_key"] == "bseap-ssc-2027"
    assert second.details["params"] == {"profile": "bseap-ssc-2027", "field": "second_language"}
    assert by_key["photo_signature_ready"].explanation_code == "DQ-032-PHOTO"
    catalog = load_explanations()
    for finding in findings:
        raw = params_of(finding)
        params = {k: str(v) for k, v in raw.items()}
        assert catalog.render(finding.explanation_code, "en", **params)
        assert catalog.render(finding.explanation_code, "te", **params)


def test_FR_STU_032_typed_codes_outside_the_list_never_reach_the_finding() -> None:
    values = {**BSEAP_READY, "third_language": "SYNTHETICA", "second_language": "TELUGU"}
    by_key = {f.attribute_key: f for f in dq032(candidate(values))}
    third = by_key["third_language"]
    assert third.explanation_code == "DQ-032-LIST"
    assert "SYNTHETICA" not in repr(third.details)
    second = by_key["second_language"]
    assert second.explanation_code == "DQ-032-REPEATED"
    assert params_of(second)["code"] == "TELUGU"


def test_FR_STU_033_cbse_subject_group_and_class_limits() -> None:
    values = {
        "first_language": "184",
        "second_language": "089",
        "board_subjects": "086,087",
        "photo_signature_ready": "yes",
    }
    (finding,) = dq032(candidate(values), profiles=("cbse-loc-2027",))
    assert finding.attribute_key == "board_subjects"
    assert finding.explanation_code == "DQ-032-GROUP"
    assert params_of(finding)["group"] == "Mathematics (standard or basic)"
    # cbse-loc-2027 is Class X and XII; the scheme covers IX and X: Class IX is not checked.
    assert dq032(candidate(values, class_code="IX"), profiles=("cbse-loc-2027",)) == []


def test_FR_STU_030_only_current_board_classes_and_profiles_with_a_scheme() -> None:
    assert dq032(candidate({}, class_code="VIII")) == []
    assert dq032(candidate({}, current=False)) == []
    assert dq032(candidate({}), profiles=("udise-plus",)) == []
    # No schemes passed (e.g. an older engine): nothing raised.
    context = CS.context([candidate({})], profiles=("bseap-ssc-2027",))
    assert [f for f in evaluate(context, CHECKS_ALL) if f.rule_id == "DQ-032"] == []
