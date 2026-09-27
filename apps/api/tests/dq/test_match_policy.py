"""Match policy: default severities and thresholds from YAML, validated (FR-DQ-003, docs/02 §6)."""

from __future__ import annotations

from importlib import resources
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.dq.matching import (
    MATCH_CLASS_ORDER,
    MatchClass,
    MatchPolicy,
    Thresholds,
    load_match_policy,
)
from app.dq.rules import Severity


def _raw() -> dict[str, Any]:
    text = resources.files("app.dq").joinpath("config/match_classes.yaml").read_text("utf-8")
    raw: dict[str, Any] = yaml.safe_load(text)
    return raw


def test_FR_DQ_003_classes_are_evaluated_in_the_specified_order() -> None:
    assert [c.value for c in MATCH_CLASS_ORDER] == [
        "EXACT",
        "ORDER",
        "SPACING",
        "INITIALS",
        "VARIANT",
        "TYPO",
        "DIFFERENT",
    ]
    assert list(_raw()["classes"])[:7] == [c.value for c in MATCH_CLASS_ORDER]


@pytest.mark.parametrize(
    ("match_class", "severity"),
    [
        (MatchClass.EXACT, None),
        (MatchClass.ORDER, Severity.INFO),
        (MatchClass.SPACING, Severity.LOW),
        (MatchClass.INITIALS, Severity.MEDIUM),
        (MatchClass.VARIANT, Severity.MEDIUM),
        (MatchClass.TYPO, Severity.HIGH),
        (MatchClass.DIFFERENT, Severity.BLOCKER),
        (MatchClass.MISSING, None),
    ],
)
def test_default_severities_follow_docs_02_section_6(
    match_class: MatchClass, severity: Severity | None
) -> None:
    policy = load_match_policy()
    assert policy.severity_for(match_class) == severity
    assert policy.severity_for(match_class.value) == severity
    assert policy.classes[match_class].explanation_code == f"NM-{match_class.value}"


def test_default_thresholds() -> None:
    thresholds = load_match_policy().thresholds
    assert thresholds.jaro_winkler_min == pytest.approx(0.92)
    assert thresholds.trigram_min == pytest.approx(0.80)
    # the code defaults are the packaged values, so Thresholds() behaves like the config
    assert Thresholds() == thresholds


def test_tenant_overrides_are_merged_and_validated() -> None:
    base = load_match_policy()
    tuned = base.with_overrides(
        {"thresholds": {"trigram_min": 0.85}, "classes": {"INITIALS": {"severity": "low"}}}
    )
    assert tuned.thresholds.trigram_min == pytest.approx(0.85)
    assert tuned.thresholds.jaro_winkler_min == base.thresholds.jaro_winkler_min
    assert tuned.severity_for(MatchClass.INITIALS) is Severity.LOW
    assert tuned.severity_for(MatchClass.TYPO) is Severity.HIGH
    assert base.severity_for(MatchClass.INITIALS) is Severity.MEDIUM  # unchanged


@pytest.mark.parametrize(
    "overrides",
    [
        {"thresholds": {"jaro_winkler_min": 1.5}},
        {"thresholds": {"trigram_min": 0}},
        {"thresholds": {"unknown": 1}},
        {"thresholds": {"digraph_initials": ["C"]}},
        {"classes": {"TYPO": {"severity": "urgent"}}},
        {"classes": {"TYPO": {"explanation_code": "NM-OTHER"}}},
        {"classes": {"EXACT": {"severity": "info"}}},
        {"version": 0},
    ],
)
def test_invalid_overrides_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        load_match_policy().with_overrides(overrides)


def test_every_class_needs_a_policy() -> None:
    raw = _raw()
    del raw["classes"]["TYPO"]
    with pytest.raises(ValidationError, match="TYPO"):
        MatchPolicy.model_validate(raw)


def test_packaged_config_round_trips() -> None:
    assert MatchPolicy.model_validate(_raw()) == load_match_policy()
