"""Score data-quality findings against the synthetic mismatch manifest (docs/12 §3, §5;
FR-DQ-001, FR-DQ-003; M1 exit check "DQ precision on seeded mismatches" at school scale).

Pure module (no database): the DB-backed run in the tests and the in-memory run here produce
:class:`Observed` findings; :func:`score` compares them with
:meth:`app.devtools.students.SchoolStudents.expected`.

- A **serious** finding is blocker or high (the precision gate of ``tests/dq/test_precision.py``:
  precision >= 0.95 per rule for serious findings).
- Findings are compared per ``(admission number, rule)``: a rule can report several attributes
  or profiles for the same student, which counts once.
- The DQ-005 baseline (``DQ-005-UNVERIFIED``: provisional identity values) is ignored.

:func:`facts_for` turns a :class:`SchoolStudents` into the in-memory facts the checks read,
the way :func:`app.dq.engine.load_context` builds them from the database (canonical value =
first source in the attribute's precedence, identity values provisional because unverified).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

from app.devtools.students import PREVIOUS_YEAR_START, YEAR_START, SchoolStudents
from app.dq.checks import (
    UNVERIFIED_CODE,
    CanonicalFact,
    CheckContext,
    EnrolmentFact,
    SourceFact,
    StudentFacts,
    build_checks,
    evaluate,
)
from app.dq.matching import load_match_policy, load_variant_dictionary
from app.dq.profiles import load_engine_config, load_profiles
from app.dq.rules import Finding, Severity, load_rules

MIN_PRECISION: Final = 0.95  # same gate as tests/dq/test_precision.py
SERIOUS: Final = frozenset({Severity.BLOCKER, Severity.HIGH})
NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_URL, "https://schoolos.invalid/devtools/dq-eval")

# Canonical precedence of the sources the synthetic data uses (app/students/attributes.yaml).
_PRECEDENCE: Final[dict[str, tuple[str, ...]]] = {
    "father_name": ("admission_register", "parent_form"),
    "mother_name": ("admission_register", "parent_form"),
    "udise_pen": ("udise_plus",),
    "apaar_id": ("udise_plus",),  # unverified: DQ-009 and DQ-022 still apply (ADR-0037)
}
_DEFAULT_PRECEDENCE: Final = ("admission_register",)
IDENTITY_KEYS: Final = frozenset(
    {"full_name", "dob", "gender", "father_name", "mother_name", "admission_no", "admission_date"}
)


@dataclass(frozen=True, slots=True)
class Observed:
    admission_no: str
    rule_id: str
    serious: bool
    explanation_code: str | None = None


@dataclass
class RuleScore:
    rule_id: str
    flagged_serious: int = 0
    true_serious: int = 0
    expected_serious: int = 0
    found_serious: int = 0
    expected_any: int = 0
    found_any: int = 0
    unexpected: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.true_serious / self.flagged_serious if self.flagged_serious else 1.0

    @property
    def recall(self) -> float:
        return self.found_serious / self.expected_serious if self.expected_serious else 1.0

    @property
    def any_recall(self) -> float:
        return self.found_any / self.expected_any if self.expected_any else 1.0


def score(
    observed: Iterable[Observed], expected: Mapping[str, Iterable[object]]
) -> dict[str, RuleScore]:
    """Per-rule precision (serious) and recall (serious, and any severity)."""
    want: dict[tuple[str, str], bool] = {}
    for admission_no, expects in expected.items():
        for e in expects:
            rule_id, serious = getattr(e, "rule_id"), bool(getattr(e, "serious"))  # noqa: B009
            want[(admission_no, rule_id)] = want.get((admission_no, rule_id), False) or serious
    got: dict[tuple[str, str], bool] = {}
    for o in observed:
        if o.explanation_code == UNVERIFIED_CODE:
            continue
        key = (o.admission_no, o.rule_id)
        got[key] = got.get(key, False) or o.serious
    scores: dict[str, RuleScore] = defaultdict(lambda: RuleScore(""))
    for (adm, rule), serious in got.items():
        s = scores[rule]
        s.rule_id = rule
        if serious:
            s.flagged_serious += 1
            if want.get((adm, rule)) is True:
                s.true_serious += 1
        if (adm, rule) not in want:
            s.unexpected.append(adm)
    for (adm, rule), serious in want.items():
        s = scores[rule]
        s.rule_id = rule
        s.expected_any += 1
        if (adm, rule) in got:
            s.found_any += 1
        if serious:
            s.expected_serious += 1
            if got.get((adm, rule)) is True:
                s.found_serious += 1
    return dict(sorted(scores.items()))


# --- in-memory facts ---------------------------------------------------------------------------


def student_id_for(tenant_code: str, admission_no: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{tenant_code}/student/{admission_no}")


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "/".join(parts))


def facts_for(school: SchoolStudents) -> dict[uuid.UUID, StudentFacts]:
    current_year, previous_year = (
        _id(school.tenant_code, "year", "current"),
        _id(school.tenant_code, "year", "previous"),
    )
    out: dict[uuid.UUID, StudentFacts] = {}
    for s in school.students:
        sid = student_id_for(school.tenant_code, s.admission_no)
        values: dict[str, dict[str, SourceFact]] = defaultdict(dict)
        for key, source, value in s.values:
            values[key][source] = SourceFact(_id(str(sid), key, source), value)
        canonical: dict[str, CanonicalFact] = {}
        for key, per_source in values.items():
            for source in _PRECEDENCE.get(key, _DEFAULT_PRECEDENCE):
                if source in per_source:
                    canonical[key] = CanonicalFact(
                        per_source[source].value, source, provisional=key in IDENTITY_KEYS
                    )
                    break
        for key in IDENTITY_KEYS - canonical.keys():
            canonical[key] = CanonicalFact(None, None, provisional=True)
        enrolments = [
            EnrolmentFact(
                enrollment_id=_id(str(sid), "enrolment", "current"),
                section_id=_id(school.tenant_code, "section", "current", *s.section),
                academic_year_id=current_year,
                class_code=s.section[0],
                year_starts_on=YEAR_START,
                current=True,
            )
        ]
        if s.previous_section is not None:
            enrolments.append(
                EnrolmentFact(
                    enrollment_id=_id(str(sid), "enrolment", "previous"),
                    section_id=_id(school.tenant_code, "section", "previous", *s.previous_section),
                    academic_year_id=previous_year,
                    class_code=s.previous_section[0],
                    year_starts_on=PREVIOUS_YEAR_START,
                    current=False,
                )
            )
        out[sid] = StudentFacts(
            student_id=sid,
            admission_no=s.admission_no,
            values=dict(values),
            canonical=canonical,
            enrolments=tuple(enrolments),
        )
    return out


def evaluate_in_memory(
    school: SchoolStudents, *, profiles: Iterable[str] | None = None
) -> list[Finding]:
    """Every finding the DQ checks raise for ``school`` (all export pre-check profiles by
    default; readiness profiles, DQ-031, are scored by their own tests: ADR-0040)."""
    known = load_profiles()
    default = [key for key, profile in known.items() if profile.readiness is None]
    chosen = tuple(known[p] for p in (default if profiles is None else profiles))
    facts = facts_for(school)
    context = CheckContext(
        students=facts,
        config=load_engine_config(),
        policy=load_match_policy(),
        variants=load_variant_dictionary(),
        population=facts,
        profiles=chosen,
        identity_keys=IDENTITY_KEYS,
    )
    return evaluate(context, build_checks(load_rules()))


def observed_in_memory(school: SchoolStudents, findings: Iterable[Finding]) -> list[Observed]:
    by_id = {
        student_id_for(school.tenant_code, s.admission_no): s.admission_no for s in school.students
    }
    return [
        Observed(
            admission_no=by_id[f.student_id],
            rule_id=f.rule_id,
            serious=f.severity in SERIOUS,
            explanation_code=f.explanation_code,
        )
        for f in findings
    ]


def school_score(school: SchoolStudents) -> dict[str, RuleScore]:
    """In-memory school-scale run of every rule and profile, scored against the manifest."""
    findings = evaluate_in_memory(school)
    return score(observed_in_memory(school, findings), school.expected())


def summary(scores: Mapping[str, RuleScore]) -> dict[str, dict[str, float | int]]:
    """JSON-ready per-rule numbers (for the seed manifest and eval reports)."""
    return {
        rule: {
            "flagged_serious": s.flagged_serious,
            "precision": round(s.precision, 4),
            "recall": round(s.recall, 4),
            "any_recall": round(s.any_recall, 4),
            "expected": s.expected_any,
            "unexpected": len(s.unexpected),
        }
        for rule, s in scores.items()
    }
