"""Data-quality rule registry types (docs/02 §5, FR-DQ-001, FR-DQ-004, FR-DQ-006).

Rules are declarative: ``config/rules.yaml`` lists DQ-001..DQ-012, DQ-021, DQ-022, DQ-030 and
DQ-031 with their version, check kind, scope, attributes, sources compared, severity policy,
explanation template key and suggested correction routes. The checks themselves belong to the
DQ engine (M1 wave 2), which implements :class:`RuleCheck` per ``check`` kind and emits
:class:`Finding` values.

A finding is identified by its :func:`finding_fingerprint` (student, rule, attribute, sorted
sources), which makes findings idempotent across runs and lets a re-run reopen a resolved
finding when the same conflict returns (FR-DQ-004).
"""

from __future__ import annotations

import functools
import hashlib
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dq.explanations import ROUTE_CODES, ExplanationCatalog, load_explanations

if TYPE_CHECKING:
    from app.dq.matching import MatchClass, MatchPolicy


class Severity(StrEnum):
    """Finding severity, most severe first (sis.dq_findings.severity CHECK constraint)."""

    BLOCKER = "blocker"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Higher is more severe: info 0 ... blocker 4."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: Final[dict[Severity, int]] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.BLOCKER: 4,
}


class RuleScope(StrEnum):
    """What one evaluation of the rule looks at."""

    STUDENT = "student"  # one student's attribute values
    STUDENT_PAIR = "student_pair"  # two students (duplicates)
    ENROLMENT = "enrolment"  # a student's enrolments across sections/years


class CheckKind(StrEnum):
    """Check implementations the DQ engine provides (wave 2)."""

    NAME_MATCH = "name_match"
    VALUE_EQUAL = "value_equal"
    CROSS_SOURCE = "cross_source"
    REQUIRED_FIELDS = "required_fields"
    NAME_FORMAT = "name_format"
    AGE_BAND = "age_band"
    DUPLICATE = "duplicate"
    AADHAAR_DETAILS = "aadhaar_details"
    ENROLMENT_OVERLAP = "enrolment_overlap"
    APAAR_ID = "apaar_id"  # DQ-021 (ADR-0037)
    APAAR_DEMOGRAPHICS = "apaar_demographics"  # DQ-022 (ADR-0037)
    READINESS_DIFF = "readiness_diff"  # DQ-031 (ADR-0040)


# sis.attribute_values.source (docs/05 §5) plus the resolved canonical value (docs/05 §10).
KNOWN_SOURCES: Final[frozenset[str]] = frozenset(
    {
        "admission_register",
        "aadhaar_as_printed",
        "udise_plus",
        "board_registration",
        "birth_certificate",
        "parent_form",
        "tc_incoming",
        "manual_entry",
        "canonical",
    }
)

_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_RULE_ID_RE: Final = r"^DQ-\d{3}$"


class SeverityPolicy(BaseModel):
    """``fixed`` severity, or the name-match class severity clamped to ``floor``..``cap``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["fixed", "match_class"]
    level: Severity | None = None
    floor: Severity | None = None
    cap: Severity | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SeverityPolicy:
        if self.mode == "fixed":
            if self.level is None or self.floor is not None or self.cap is not None:
                raise ValueError("fixed severity needs `level` and no floor/cap")
        else:
            if self.level is not None:
                raise ValueError("match_class severity takes floor/cap, not `level`")
            if self.floor and self.cap and self.floor.rank > self.cap.rank:
                raise ValueError("severity floor is above the cap")
        return self

    def clamp(self, severity: Severity) -> Severity:
        if self.floor is not None and severity.rank < self.floor.rank:
            severity = self.floor
        if self.cap is not None and severity.rank > self.cap.rank:
            severity = self.cap
        return severity


class Rule(BaseModel):
    """One catalog entry (metadata only; the engine supplies the check)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_RULE_ID_RE)
    version: int = Field(ge=1)
    check: CheckKind
    scope: RuleScope
    attribute_keys: tuple[str, ...]
    sources: tuple[str, ...]
    severity: SeverityPolicy
    explanation_key: str
    routes: tuple[str, ...] = Field(min_length=1)
    requires_profile: bool = False

    @field_validator("attribute_keys")
    @classmethod
    def _attribute_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or not all(_KEY_RE.match(k) for k in value):
            raise ValueError("attribute keys must be unique lowercase identifiers")
        return value

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - KNOWN_SOURCES)
        if unknown or len(set(value)) != len(value):
            raise ValueError(f"unknown or repeated sources: {unknown or list(value)}")
        return value

    @field_validator("routes")
    @classmethod
    def _routes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(ROUTE_CODES))
        if unknown or len(set(value)) != len(value):
            raise ValueError(f"unknown or repeated routes: {unknown or list(value)}")
        return value

    @model_validator(mode="after")
    def _shape(self) -> Rule:
        if self.check is CheckKind.NAME_MATCH and (
            len(self.sources) < 2 or not self.attribute_keys
        ):
            raise ValueError(f"{self.id}: a name_match rule compares >= 2 sources")
        if self.severity.mode == "match_class" and self.check not in (
            CheckKind.NAME_MATCH,
            CheckKind.CROSS_SOURCE,
            CheckKind.APAAR_DEMOGRAPHICS,
        ):
            raise ValueError(f"{self.id}: only name comparisons take match_class severity")
        return self

    @property
    def route(self) -> str:
        """The primary suggested correction route."""
        return self.routes[0]

    @property
    def default_severity(self) -> Severity | None:
        """The catalog severity, or ``None`` when it follows the match class (docs/02 §5)."""
        return self.severity.level

    def severity_for(
        self, match_class: MatchClass | str | None = None, *, policy: MatchPolicy | None = None
    ) -> Severity | None:
        """Severity of a finding of this rule; ``None`` means "raise no finding".

        ``match_class`` is needed for ``match_class`` rules (EXACT and MISSING give ``None``).
        ``policy`` defaults to the packaged match policy (tenants override it later).
        """
        if self.severity.mode == "fixed":
            return self.severity.level
        if match_class is None:
            raise ValueError(f"{self.id}: severity depends on the name-match class")
        if policy is None:
            from app.dq.matching import load_match_policy  # noqa: PLC0415 (import cycle)

            policy = load_match_policy()
        base = policy.severity_for(match_class)
        return None if base is None else self.severity.clamp(base)


# --- findings ---------------------------------------------------------------------------------


def finding_fingerprint(
    student_id: uuid.UUID | str,
    rule_id: str,
    attribute_key: str | None,
    sources: Iterable[str],
) -> str:
    """``sha256(student_id|rule_id|attribute|sorted sources)`` as hex (FR-DQ-004).

    Source order and repetition do not matter. Parts are codes and IDs, never values.
    """
    source_set = sorted(set(sources))
    parts = [str(student_id), rule_id, attribute_key or "", *source_set]
    if not rule_id or any(sep in part for part in parts for sep in "|,"):
        raise ValueError("fingerprint parts must be non-empty codes without '|' or ','")
    joined = ",".join(source_set)
    payload = f"{student_id}|{rule_id}|{attribute_key or ''}|{joined}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Finding:
    """A rule violation for one student (value object; persistence is the engine's job).

    ``details`` holds masked values, match-class token reasons, codes and counts only.
    """

    student_id: uuid.UUID
    rule_id: str
    rule_version: int
    severity: Severity
    explanation_code: str
    route_codes: tuple[str, ...]
    attribute_key: str | None = None
    sources: tuple[str, ...] = ()
    match_class: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(sorted(set(self.sources))))
        if not self.route_codes:
            raise ValueError("a finding needs at least one correction route")

    @property
    def fingerprint(self) -> str:
        return finding_fingerprint(self.student_id, self.rule_id, self.attribute_key, self.sources)

    @classmethod
    def for_rule(
        cls,
        rule: Rule,
        *,
        student_id: uuid.UUID,
        severity: Severity,
        attribute_key: str | None = None,
        sources: Iterable[str] | None = None,
        match_class: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> Finding:
        return cls(
            student_id=student_id,
            rule_id=rule.id,
            rule_version=rule.version,
            severity=severity,
            explanation_code=rule.explanation_key,
            route_codes=rule.routes,
            attribute_key=attribute_key,
            sources=tuple(rule.sources if sources is None else sources),
            match_class=match_class,
            details=dict(details or {}),
        )


ContextT_contra = TypeVar("ContextT_contra", contravariant=True)


class RuleCheck(Protocol[ContextT_contra]):
    """What the DQ engine implements per check kind (wave 2)."""

    @property
    def rule(self) -> Rule: ...

    def evaluate(self, context: ContextT_contra, /) -> Iterable[Finding]: ...


# --- registry ---------------------------------------------------------------------------------


class RuleRegistry(Mapping[str, Rule]):
    """Rules by id, validated against the explanation catalog."""

    def __init__(self, rules: Iterable[Rule], catalog: ExplanationCatalog) -> None:
        by_id: dict[str, Rule] = {}
        for rule in rules:
            if rule.id in by_id:
                raise ValueError(f"duplicate rule id {rule.id}")
            by_id[rule.id] = rule
        validate_rules(by_id.values(), catalog)
        self._rules = dict(sorted(by_id.items()))

    def __getitem__(self, rule_id: str) -> Rule:
        return self._rules[rule_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._rules)

    def __len__(self) -> int:
        return len(self._rules)

    def for_check(self, check: CheckKind) -> tuple[Rule, ...]:
        return tuple(r for r in self._rules.values() if r.check is check)


def validate_rules(rules: Iterable[Rule], catalog: ExplanationCatalog) -> None:
    """Every rule has EN and TE text in ``catalog.rules`` and only known routes."""
    for rule in rules:
        if rule.explanation_key not in catalog.rules:
            raise ValueError(f"{rule.id}: no EN/TE explanation {rule.explanation_key!r}")
        for route in rule.routes:
            if route not in catalog.routes:
                raise ValueError(f"{rule.id}: no EN/TE text for route {route}")


def parse_rules(raw: Any, catalog: ExplanationCatalog) -> RuleRegistry:
    if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
        raise ValueError("rules.yaml needs a `rules` list")
    return RuleRegistry((Rule.model_validate(item) for item in raw["rules"]), catalog)


@functools.cache
def load_rules() -> RuleRegistry:
    """The packaged catalog (``app/dq/config/rules.yaml``), validated once."""
    text = resources.files("app.dq").joinpath("config/rules.yaml").read_text("utf-8")
    return parse_rules(yaml.safe_load(text), load_explanations())
