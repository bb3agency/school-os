"""Rule registry, severities and finding fingerprints (FR-DQ-001, FR-DQ-004, FR-DQ-006)."""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.dq.explanations import ROUTE_CODES, load_explanations, parse_catalog
from app.dq.matching import MatchClass, classify, load_match_policy
from app.dq.rules import (
    CheckKind,
    Finding,
    Rule,
    RuleRegistry,
    Severity,
    finding_fingerprint,
    load_rules,
    parse_rules,
)

PRD = Path(__file__).resolve().parents[4] / "docs" / "02-PRD.md"
STUDENT = uuid.UUID("01926f00-0000-7000-8000-000000000001")


@pytest.fixture(scope="module")
def registry() -> RuleRegistry:
    return load_rules()


def _rule_data(registry: RuleRegistry, rule_id: str) -> dict[str, Any]:
    return copy.deepcopy(registry[rule_id].model_dump(mode="json"))


def test_FR_DQ_001_catalog_has_dq_001_to_012(registry: RuleRegistry) -> None:
    # DQ-021 and DQ-022: APAAR ID rules (ADR-0037); DQ-030: board and portal readiness (ADR-0040).
    assert list(registry) == [f"DQ-{n:03d}" for n in (*range(1, 13), 21, 22, 30)]
    for rule in registry.values():
        assert rule.version >= 1
        assert rule.routes
        assert rule.route == rule.routes[0]


def test_FR_DQ_001_every_rule_has_bilingual_text_and_known_routes(registry: RuleRegistry) -> None:
    catalog = load_explanations()
    for rule in registry.values():
        text = catalog.rules[rule.explanation_key]
        assert text.en
        assert text.te
        for route in rule.routes:
            assert route in ROUTE_CODES
            assert catalog.routes[route].te


def test_fixed_severities_match_the_prd(registry: RuleRegistry) -> None:
    text = PRD.read_text("utf-8")
    rows = re.findall(r"^\| (DQ-\d{3}) \| [^|]+ \| ([^|]+) \|", text, flags=re.MULTILINE)
    documented = {rule_id: severity.strip() for rule_id, severity in rows}
    assert len(documented) == 14
    for rule_id, severity in documented.items():
        rule = registry[rule_id]
        if rule.severity.mode == "fixed":
            assert rule.default_severity is not None
            assert rule.default_severity.value == severity, rule_id
        else:
            assert rule.default_severity is None
            assert severity in ("by match class (see §6)", "medium/high"), rule_id


@pytest.mark.parametrize(
    ("match_class", "expected"),
    [
        (MatchClass.EXACT, None),
        (MatchClass.MISSING, None),
        (MatchClass.ORDER, Severity.INFO),
        (MatchClass.SPACING, Severity.LOW),
        (MatchClass.INITIALS, Severity.MEDIUM),
        (MatchClass.VARIANT, Severity.MEDIUM),
        (MatchClass.TYPO, Severity.HIGH),
        (MatchClass.DIFFERENT, Severity.BLOCKER),
    ],
)
def test_dq_001_severity_follows_the_match_class(
    registry: RuleRegistry, match_class: MatchClass, expected: Severity | None
) -> None:
    assert registry["DQ-001"].severity_for(match_class) == expected


@pytest.mark.parametrize(
    ("match_class", "expected"),
    [
        (MatchClass.ORDER, Severity.MEDIUM),  # raised to the floor
        (MatchClass.SPACING, Severity.MEDIUM),
        (MatchClass.VARIANT, Severity.MEDIUM),
        (MatchClass.TYPO, Severity.HIGH),
        (MatchClass.DIFFERENT, Severity.HIGH),  # lowered to the cap
        (MatchClass.EXACT, None),
    ],
)
def test_dq_004_parent_names_are_medium_or_high(
    registry: RuleRegistry, match_class: MatchClass, expected: Severity | None
) -> None:
    assert registry["DQ-004"].severity_for(match_class) == expected


def test_severity_from_a_real_classification(registry: RuleRegistry) -> None:
    result = classify("K. VENKATA SAI", "KOMMINENI VENKATA SAI")
    assert registry["DQ-001"].severity_for(result.match_class) is Severity.MEDIUM


def test_tenant_policy_changes_name_rule_severity(registry: RuleRegistry) -> None:
    policy = load_match_policy().with_overrides({"classes": {"INITIALS": {"severity": "low"}}})
    assert registry["DQ-001"].severity_for(MatchClass.INITIALS, policy=policy) is Severity.LOW
    assert registry["DQ-002"].severity_for(MatchClass.INITIALS, policy=policy) is Severity.BLOCKER


def test_name_rule_needs_a_match_class(registry: RuleRegistry) -> None:
    with pytest.raises(ValueError, match="match class"):
        registry["DQ-001"].severity_for()
    assert registry["DQ-002"].severity_for() is Severity.BLOCKER


def test_for_check(registry: RuleRegistry) -> None:
    assert [r.id for r in registry.for_check(CheckKind.NAME_MATCH)] == ["DQ-001", "DQ-004"]


def test_severity_rank_orders_most_severe_first() -> None:
    ranks = [s.rank for s in Severity]
    assert ranks == sorted(ranks, reverse=True)
    assert Severity.BLOCKER.rank > Severity.INFO.rank


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "DQ-1"),
        ("version", 0),
        ("routes", []),
        ("routes", ["ROUTE-ELSEWHERE"]),
        ("routes", ["ROUTE-UIDAI", "ROUTE-UIDAI"]),
        ("sources", ["admission_register", "whatsapp"]),
        ("sources", ["admission_register"]),  # a name comparison needs two sources
        ("attribute_keys", ["Full Name"]),
        ("check", "guess"),
        ("scope", "school"),
        ("severity", {"mode": "fixed"}),
        ("severity", {"mode": "fixed", "level": "high", "cap": "high"}),
        ("severity", {"mode": "match_class", "level": "high"}),
        ("severity", {"mode": "match_class", "floor": "high", "cap": "low"}),
        ("unexpected", True),
    ],
)
def test_invalid_rules_are_rejected(registry: RuleRegistry, field: str, value: object) -> None:
    data = _rule_data(registry, "DQ-001")
    data[field] = value
    with pytest.raises(ValidationError):
        Rule.model_validate(data)


def test_match_class_severity_only_for_name_comparisons(registry: RuleRegistry) -> None:
    data = _rule_data(registry, "DQ-002")
    data["severity"] = {"mode": "match_class"}
    with pytest.raises(ValidationError, match="match_class"):
        Rule.model_validate(data)


def test_registry_rejects_duplicates_and_missing_texts(registry: RuleRegistry) -> None:
    rules = [_rule_data(registry, "DQ-001"), _rule_data(registry, "DQ-001")]
    with pytest.raises(ValueError, match="duplicate"):
        parse_rules({"rules": rules}, load_explanations())
    raw_catalog = load_explanations().model_dump(mode="json")
    del raw_catalog["rules"]["DQ-003"]
    with pytest.raises(ValueError, match="DQ-003"):
        parse_rules({"rules": [_rule_data(registry, "DQ-003")]}, parse_catalog(raw_catalog))
    with pytest.raises(ValueError, match="rules"):
        parse_rules({"items": []}, load_explanations())


# --- findings ---------------------------------------------------------------------------------


def test_FR_DQ_004_fingerprint_is_sha256_of_student_rule_attribute_sorted_sources() -> None:
    expected = hashlib.sha256(
        f"{STUDENT}|DQ-001|full_name|aadhaar_as_printed,admission_register".encode()
    ).hexdigest()
    got = finding_fingerprint(
        STUDENT, "DQ-001", "full_name", ["admission_register", "aadhaar_as_printed"]
    )
    assert got == expected
    # order and repetition of sources do not matter
    assert got == finding_fingerprint(
        str(STUDENT),
        "DQ-001",
        "full_name",
        ["aadhaar_as_printed", "admission_register", "aadhaar_as_printed"],
    )


def test_fingerprint_changes_with_each_part() -> None:
    base = finding_fingerprint(STUDENT, "DQ-001", "full_name", ["a", "b"])
    other_student = uuid.UUID("01926f00-0000-7000-8000-000000000002")
    assert finding_fingerprint(other_student, "DQ-001", "full_name", ["a", "b"]) != base
    assert finding_fingerprint(STUDENT, "DQ-004", "full_name", ["a", "b"]) != base
    assert finding_fingerprint(STUDENT, "DQ-001", "father_name", ["a", "b"]) != base
    assert finding_fingerprint(STUDENT, "DQ-001", "full_name", ["a", "c"]) != base
    assert finding_fingerprint(STUDENT, "DQ-012", None, []) != base


@pytest.mark.parametrize(
    ("rule_id", "attribute", "sources"),
    [("DQ|001", "x", []), ("DQ-001", "a,b", []), ("DQ-001", "x", ["a|b"]), ("", "x", [])],
)
def test_fingerprint_parts_are_codes(rule_id: str, attribute: str, sources: list[str]) -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        finding_fingerprint(STUDENT, rule_id, attribute, sources)


def test_finding_for_rule(registry: RuleRegistry) -> None:
    rule = registry["DQ-001"]
    result = classify("K. VENKATA SAI", "KOMMINENI VENKATA SAI")
    severity = rule.severity_for(result.match_class)
    assert severity is not None
    finding = Finding.for_rule(
        rule,
        student_id=STUDENT,
        severity=severity,
        attribute_key="full_name",
        match_class=result.match_class.value,
        details={"match": dict(result.details)},
    )
    assert finding.rule_version == rule.version
    assert finding.route_codes == ("ROUTE-UIDAI", "ROUTE-SCHOOL-CR")
    assert finding.explanation_code == "DQ-001"
    assert finding.sources == ("aadhaar_as_printed", "admission_register")
    assert finding.fingerprint == finding_fingerprint(STUDENT, "DQ-001", "full_name", rule.sources)


def test_finding_needs_a_route() -> None:
    with pytest.raises(ValueError, match="route"):
        Finding(
            student_id=STUDENT,
            rule_id="DQ-002",
            rule_version=1,
            severity=Severity.BLOCKER,
            explanation_code="DQ-002",
            route_codes=(),
        )
