"""Rule checks DQ-001..DQ-012, DQ-021, DQ-022 (docs/02 §5, FR-DQ-001, FR-DQ-003, FR-DQ-004,
FR-DQ-006, FR-DQ-021, FR-DQ-022).

Pure module: every check implements :class:`app.dq.rules.RuleCheck` over a
:class:`CheckContext` of in-memory facts that the engine (:mod:`app.dq.engine`) loads in bulk
through ``app.students.service``. Nothing here touches the database, so the checks are unit-tested
on labelled synthetic sets.

Findings (:class:`app.dq.rules.Finding`) carry **masked values only** (:mod:`app.dq.masking`)
plus value ids, match-class token details, codes and counts. Three reserved ``details`` keys are
lifted into their own columns by the engine and take part in the fingerprint:

- ``params``: explanation template parameters as codes (``profile`` key, ``field`` attribute key,
  ``issue`` code, ``n`` age, ``c`` class code, ``student`` admission number); the service turns
  codes into English/Telugu labels when it renders the explanation;
- ``profile_key``: the export profile a profile rule (DQ-005, DQ-006, DQ-009) was checked for;
- ``related_student_id``: the other student of a DQ-008 pair.

Check kinds and their rules:

============================  =====================================================================
``name_match`` (DQ-001, 004)  admission register vs each other source; severity by match class
``value_equal`` (DQ-002, 003) admission register vs Aadhaar-as-printed (C3, compared in memory)
``cross_source`` (DQ-010, 011) register vs board / UDISE+ per attribute; names by match class
``required_fields`` (DQ-005)  profile fields without a canonical value (blocker), or identity
                              fields whose canonical value is provisional (profile severity)
``name_format`` (DQ-006)      canonical names against the profile's length/character rules
``age_band`` (DQ-007)         age on the first day of the academic year vs the class band
``duplicate`` (DQ-008)        same date of birth + matching name + a matching parent name
``aadhaar_details`` (DQ-009)  Aadhaar last 4 / as-printed fields missing when APAAR is needed,
                              for students without a verified APAAR ID (ADR-0037)
``enrolment_overlap`` (DQ-012) more than one active enrolment
``apaar_id`` (DQ-021)         an APAAR ID that is not 12 digits (any source), or the student's
                              APAAR ID on another student of the school (one finding per student,
                              naming the first other record; each side is raised when its own
                              student is checked, since only DQ-008 findings may pair students)
``apaar_demographics``        UDISE+ vs Aadhaar-as-printed (as ``cross_source``) for students
(DQ-022)                      without a verified APAAR ID
============================  =====================================================================

The APAAR ID itself never appears in a finding: values are masked (``••••``) and a duplicate
names the other record by admission number only (PRV-020).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from app.dq.masking import mask_value
from app.dq.matching import MatchClass, MatchPolicy, VariantDictionary, classify, match_key
from app.dq.profiles import FORMAT_ISSUES, EngineConfig, NameFormat, Profile
from app.dq.rules import (
    CheckKind,
    Finding,
    Rule,
    RuleCheck,
    RuleRegistry,
    Severity,
    finding_fingerprint,
)

REGISTER: Final = "admission_register"
AADHAAR: Final = "aadhaar_as_printed"
CANONICAL: Final = "canonical"
UNVERIFIED_CODE: Final = "DQ-005-UNVERIFIED"

NAME_KEYS: Final = frozenset({"full_name", "father_name", "mother_name", "aadhaar_name_as_printed"})
DATE_KEYS: Final = frozenset({"dob", "admission_date", "aadhaar_dob_as_printed"})
PARENT_KEYS: Final = ("father_name", "mother_name")
# Keys the engine lifts out of ``Finding.details`` into their own columns.
RESERVED_DETAILS: Final = ("params", "profile_key", "related_student_id")
APAAR_DUPLICATE_CODE: Final = "DQ-021-DUPLICATE"
_APAAR_RE: Final = re.compile(r"[0-9]{12}")


def value_kind(attribute_key: str) -> str:
    if attribute_key in NAME_KEYS:
        return "name"
    if attribute_key in DATE_KEYS:
        return "date"
    return "other"


# --- facts --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceFact:
    """The current value of one attribute from one source (plaintext, in memory only)."""

    value_id: uuid.UUID
    value: str | None


@dataclass(frozen=True, slots=True)
class CanonicalFact:
    value: str | None
    source: str | None = None
    provisional: bool = False
    verified: bool = False


@dataclass(frozen=True, slots=True)
class EnrolmentFact:
    """An *active* enrolment; ``current`` = in the school's current academic year."""

    enrollment_id: uuid.UUID
    section_id: uuid.UUID
    academic_year_id: uuid.UUID
    class_code: str | None = None
    year_starts_on: dt.date | None = None
    current: bool = False


@dataclass(frozen=True, slots=True)
class StudentFacts:
    student_id: uuid.UUID
    admission_no: str | None = None
    # physical attribute key -> source -> current value
    values: Mapping[str, Mapping[str, SourceFact]] = field(default_factory=dict)
    canonical: Mapping[str, CanonicalFact] = field(default_factory=dict)
    enrolments: tuple[EnrolmentFact, ...] = ()

    def value(self, attribute_key: str, source: str) -> SourceFact | None:
        fact = self.values.get(attribute_key, {}).get(source)
        if fact is None or fact.value is None or not fact.value.strip():
            return None
        return fact

    def canonical_value(self, attribute_key: str) -> str | None:
        fact = self.canonical.get(attribute_key)
        if fact is None or fact.value is None or not fact.value.strip():
            return None
        return fact.value

    def has_verified(self, attribute_key: str) -> bool:
        """A canonical value a person verified (ADR-0037: an APAAR ID counts only then)."""
        fact = self.canonical.get(attribute_key)
        return (
            fact is not None and fact.verified and self.canonical_value(attribute_key) is not None
        )


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Everything one run needs, loaded in bulk (no per-student queries)."""

    students: Mapping[uuid.UUID, StudentFacts]
    config: EngineConfig
    policy: MatchPolicy
    variants: VariantDictionary
    # Tenant-wide canonical facts for DQ-008 (full_name, dob, father_name, mother_name).
    population: Mapping[uuid.UUID, StudentFacts] = field(default_factory=dict)
    profiles: tuple[Profile, ...] = ()
    identity_keys: frozenset[str] = frozenset()

    def classify(self, a: str, b: str) -> Any:
        return classify(a, b, variants=self.variants, thresholds=self.policy.thresholds)


# --- finding helpers ----------------------------------------------------------------------------


def value_entry(attribute_key: str, source: str, fact: SourceFact) -> dict[str, Any]:
    """A compared value as stored: ids and the masked form, never the value itself."""
    return {
        "attribute_key": attribute_key,
        "source": source,
        "value_id": str(fact.value_id),
        "masked": mask_value(fact.value, kind=value_kind(attribute_key)),
    }


def make_finding(
    rule: Rule,
    student_id: uuid.UUID,
    severity: Severity,
    *,
    attribute_key: str | None = None,
    sources: Iterable[str] | None = None,
    match_class: str | None = None,
    details: Mapping[str, Any] | None = None,
    params: Mapping[str, str | int] | None = None,
    profile_key: str | None = None,
    related_student_id: uuid.UUID | None = None,
    explanation_code: str | None = None,
) -> Finding:
    out: dict[str, Any] = dict(details or {})
    out["params"] = dict(params or {})
    if profile_key is not None:
        out["profile_key"] = profile_key
    if related_student_id is not None:
        out["related_student_id"] = str(related_student_id)
    return Finding(
        student_id=student_id,
        rule_id=rule.id,
        rule_version=rule.version,
        severity=severity,
        explanation_code=explanation_code or rule.explanation_key,
        route_codes=rule.routes,
        attribute_key=attribute_key,
        sources=tuple(rule.sources if sources is None else sources),
        match_class=match_class,
        details=out,
    )


def fingerprint_of(finding: Finding) -> str:
    """:func:`finding_fingerprint` plus the profile (profile rules) and the pair (DQ-008)."""
    extra: list[str] = []
    profile = finding.details.get("profile_key")
    if profile:
        extra.append(f"profile:{profile}")
    related = finding.details.get("related_student_id")
    if related:
        extra.append(f"student:{related}")
    return finding_fingerprint(
        finding.student_id, finding.rule_id, finding.attribute_key, [*finding.sources, *extra]
    )


def _match_details(result: Any) -> dict[str, Any]:
    return {
        "explanation_code": result.explanation_code,
        "similarity": round(float(result.similarity), 3),
        **dict(result.details),
    }


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _same(attribute_key: str, a: str, b: str) -> bool:
    if attribute_key in DATE_KEYS:
        da, db = _parse_date(a), _parse_date(b)
        if da is not None and db is not None:
            return da == db
    return a.strip().casefold() == b.strip().casefold()


def _date_parts_differing(a: str, b: str) -> list[str]:
    da, db = _parse_date(a), _parse_date(b)
    if da is None or db is None:
        return ["unreadable"]
    return [
        part
        for part, x, y in (
            ("day", da.day, db.day),
            ("month", da.month, db.month),
            ("year", da.year, db.year),
        )
        if x != y
    ]


def _fixed(rule: Rule) -> Severity:
    """The catalog severity of a fixed-severity rule."""
    severity = rule.severity_for()
    if severity is None:  # pragma: no cover - rules.yaml validation makes this unreachable
        raise ValueError(f"{rule.id} has no fixed severity")
    return severity


# --- checks -------------------------------------------------------------------------------------


class _Check:
    def __init__(self, rule: Rule) -> None:
        self._rule = rule

    @property
    def rule(self) -> Rule:
        return self._rule


class NameMatchCheck(_Check):
    """DQ-001, DQ-004: the admission register value against every other listed source."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule, cfg = self.rule, context.config
        anchor, others = rule.sources[0], rule.sources[1:]
        for facts in context.students.values():
            for key in rule.attribute_keys:
                a_key = cfg.physical_key(key, anchor)
                a = facts.value(a_key, anchor)
                if a is None or a.value is None:
                    continue
                for other in others:
                    b_key = cfg.physical_key(key, other)
                    b = facts.value(b_key, other)
                    if b is None or b.value is None:
                        continue
                    result = context.classify(a.value, b.value)
                    severity = rule.severity_for(result.match_class, policy=context.policy)
                    if severity is None:
                        continue
                    yield make_finding(
                        rule,
                        facts.student_id,
                        severity,
                        attribute_key=key,
                        sources=(anchor, other),
                        match_class=result.match_class.value,
                        details={
                            "values": [value_entry(a_key, anchor, a), value_entry(b_key, other, b)],
                            "match": _match_details(result),
                        },
                    )


class ValueEqualCheck(_Check):
    """DQ-002, DQ-003: register value vs Aadhaar-as-printed (C3 decrypted in memory only)."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule, cfg = self.rule, context.config
        anchor, other = rule.sources[0], rule.sources[1]
        for facts in context.students.values():
            for key in rule.attribute_keys:
                a_key, b_key = cfg.physical_key(key, anchor), cfg.physical_key(key, other)
                a, b = facts.value(a_key, anchor), facts.value(b_key, other)
                if a is None or b is None or a.value is None or b.value is None:
                    continue
                if _same(key, a.value, b.value):
                    continue
                details: dict[str, Any] = {
                    "values": [value_entry(a_key, anchor, a), value_entry(b_key, other, b)]
                }
                if key in DATE_KEYS:
                    details["differs_in"] = _date_parts_differing(a.value, b.value)
                severity = _fixed(rule)
                yield make_finding(
                    rule,
                    facts.student_id,
                    severity,
                    attribute_key=key,
                    sources=(anchor, other),
                    details=details,
                )


class CrossSourceCheck(_Check):
    """DQ-010, DQ-011: register vs board registration / UDISE+ for each identity attribute."""

    def _students(self, context: CheckContext) -> Iterable[StudentFacts]:
        return context.students.values()

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule, cfg = self.rule, context.config
        anchor, other = rule.sources[0], rule.sources[1]
        severity = _fixed(rule)
        for facts in self._students(context):
            for key in rule.attribute_keys:
                a_key, b_key = cfg.physical_key(key, anchor), cfg.physical_key(key, other)
                a, b = facts.value(a_key, anchor), facts.value(b_key, other)
                if a is None or b is None or a.value is None or b.value is None:
                    continue
                details: dict[str, Any] = {
                    "values": [value_entry(a_key, anchor, a), value_entry(b_key, other, b)]
                }
                match_class: str | None = None
                if key in NAME_KEYS:
                    result = context.classify(a.value, b.value)
                    if result.match_class in (MatchClass.EXACT, MatchClass.MISSING):
                        continue
                    match_class = result.match_class.value
                    details["match"] = _match_details(result)
                elif _same(key, a.value, b.value):
                    continue
                elif key in DATE_KEYS:
                    details["differs_in"] = _date_parts_differing(a.value, b.value)
                yield make_finding(
                    rule,
                    facts.student_id,
                    severity,
                    attribute_key=key,
                    sources=(anchor, other),
                    match_class=match_class,
                    details=details,
                )


class RequiredFieldsCheck(_Check):
    """DQ-005 per profile: missing canonical value (blocker) or provisional identity value."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule = self.rule
        blocker = _fixed(rule)
        for profile in context.profiles:
            for facts in context.students.values():
                for key in profile.required_fields:
                    fact = facts.canonical.get(key)
                    params = {"profile": profile.key, "field": key}
                    if facts.canonical_value(key) is None:
                        yield make_finding(
                            rule,
                            facts.student_id,
                            blocker,
                            attribute_key=key,
                            details={"reason": "missing"},
                            params=params,
                            profile_key=profile.key,
                        )
                    elif (
                        fact is not None
                        and fact.provisional
                        and key in context.identity_keys
                        and profile.unverified_identity_severity is not None
                    ):
                        yield make_finding(
                            rule,
                            facts.student_id,
                            profile.unverified_identity_severity,
                            attribute_key=key,
                            details={"reason": "unverified", "canonical_source": fact.source},
                            params=params,
                            profile_key=profile.key,
                            explanation_code=UNVERIFIED_CODE,
                        )


def format_issues(value: str, name_format: NameFormat) -> list[str]:
    """DQ-006 issue codes (in :data:`FORMAT_ISSUES` order) for one name."""
    text = " ".join(unicodedata.normalize("NFC", value).split())
    found: set[str] = set()
    if len(text) > name_format.max_length:
        found.add("too_long")
    if sum(1 for ch in text if ch.isalpha()) < name_format.min_length:
        found.add("too_short")
    for ch in text:
        category = unicodedata.category(ch)
        if ch.isdigit():
            found.add("digits")
        elif ch.isalpha() or category in ("Mn", "Mc"):
            if name_format.latin_only and not ("A" <= ch.upper() <= "Z" and ch.isascii()):
                found.add("not_latin")
        elif ch not in name_format.allowed_punctuation:
            found.add("symbols")
    return [code for code in FORMAT_ISSUES if code in found]


class NameFormatCheck(_Check):
    """DQ-006 per profile: canonical names the board/portal would refuse."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule = self.rule
        severity = _fixed(rule)
        for profile in context.profiles:
            nf = profile.name_format
            if nf is None:
                continue
            for facts in context.students.values():
                for key in nf.fields:
                    value = facts.canonical_value(key)
                    if value is None:
                        continue  # DQ-005 reports missing values
                    issues = format_issues(value, nf)
                    if not issues:
                        continue
                    source = facts.canonical[key].source
                    source_fact = facts.value(key, source) if source else None
                    details: dict[str, Any] = {
                        "issues": issues,
                        "length": len(" ".join(value.split())),
                        "min_length": nf.min_length,
                        "max_length": nf.max_length,
                    }
                    if source is not None and source_fact is not None:
                        details["values"] = [value_entry(key, source, source_fact)]
                    yield make_finding(
                        rule,
                        facts.student_id,
                        severity,
                        attribute_key=key,
                        details=details,
                        params={"profile": profile.key, "issue": issues[0]},
                        profile_key=profile.key,
                    )


def completed_years(born: dt.date, on: dt.date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


class AgeBandCheck(_Check):
    """DQ-007: age on the first day of the academic year outside the class's band."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule, bands = self.rule, context.config.age_bands
        severity = _fixed(rule)
        for facts in context.students.values():
            current = [e for e in facts.enrolments if e.current and e.class_code in bands]
            born = _parse_date(facts.canonical_value("dob"))
            if not current or born is None:
                continue
            enrolment = current[0]
            if enrolment.year_starts_on is None or enrolment.class_code is None:
                continue
            low, high = bands[enrolment.class_code]
            age = completed_years(born, enrolment.year_starts_on)
            if low <= age <= high:
                continue
            source = facts.canonical["dob"].source
            source_fact = facts.value("dob", source) if source else None
            details: dict[str, Any] = {
                "class_code": enrolment.class_code,
                "section_id": str(enrolment.section_id),
                "min_age": low,
                "max_age": high,
                "reference": "academic_year_start",
            }
            if source is not None and source_fact is not None:
                details["values"] = [value_entry("dob", source, source_fact)]
            yield make_finding(
                rule,
                facts.student_id,
                severity,
                attribute_key="dob",
                details=details,
                params={"n": age, "c": enrolment.class_code},
            )


def student_label(facts: StudentFacts) -> str:
    """How DQ-008 names the other record: its admission number, else a short id."""
    if facts.admission_no:
        return facts.admission_no
    return f"#{str(facts.student_id)[:8]}"


BLOCK_PREFIX: Final = 3


def blocking_keys(name: str, variants: VariantDictionary) -> frozenset[str]:
    """Cheap candidate keys for DQ-008: the first letters of every full word's variant key.

    Two names in the duplicate classes (EXACT..VARIANT) always pair at least one full word
    (``require_full_word``) by equality, joining, dictionary or phonetic variant, and each of
    those keeps the first letters of the word's variant key, so pairs without a shared key can
    be skipped without changing the result. It keeps a school where many records share one
    date of birth (a placeholder date, say) from comparing every pair.
    """
    keys: set[str] = set()
    for token in match_key(name).split():
        if len(token) > 1 and not token.endswith("."):
            keys.add(variants.variant_key(token)[:BLOCK_PREFIX])
    return frozenset(keys)


class DuplicateCheck(_Check):
    """DQ-008: pairs with the same date of birth, name and a parent's name (both directions)."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        # date of birth -> blocking key -> students
        index: dict[dt.date, dict[str, list[StudentFacts]]] = defaultdict(lambda: defaultdict(list))
        population = {**context.population, **context.students}
        keys_of: dict[uuid.UUID, frozenset[str]] = {}
        for facts in population.values():
            born = _parse_date(facts.canonical_value("dob"))
            name = facts.canonical_value("full_name")
            if born is None or name is None:
                continue
            keys_of[facts.student_id] = blocking_keys(name, context.variants)
            for key in keys_of[facts.student_id]:
                index[born][key].append(facts)
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for facts in context.students.values():
            born = _parse_date(facts.canonical_value("dob"))
            name = facts.canonical_value("full_name")
            if born is None or name is None:
                continue
            group = index.get(born, {})
            candidates = {
                other.student_id: other
                for key in keys_of.get(facts.student_id, ())
                for other in group.get(key, ())
            }
            for other in candidates.values():
                if other.student_id == facts.student_id:
                    continue
                pair = (
                    min(facts.student_id, other.student_id),
                    max(facts.student_id, other.student_id),
                )
                if pair in seen:
                    continue
                seen.add(pair)
                yield from self._pair(context, facts, other, name)

    def _pair(
        self, context: CheckContext, facts: StudentFacts, other: StudentFacts, name: str
    ) -> Iterator[Finding]:
        rule, policy = self.rule, context.config.duplicate
        severity = _fixed(rule)
        other_name = other.canonical_value("full_name")
        if other_name is None:
            return
        name_class = context.classify(name, other_name).match_class
        if name_class not in policy.name_classes:
            return
        parents: dict[str, str] = {}
        for key in PARENT_KEYS:
            mine, theirs = facts.canonical_value(key), other.canonical_value(key)
            if mine is not None and theirs is not None:
                parents[key] = context.classify(mine, theirs).match_class.value
        if not any(MatchClass(v) in policy.parent_classes for v in parents.values()):
            return
        for this, that in ((facts, other), (other, facts)):
            yield make_finding(
                rule,
                this.student_id,
                severity,
                match_class=name_class.value,
                details={"name_match": name_class.value, "parent_match": parents},
                params={"student": student_label(that)},
                related_student_id=that.student_id,
            )


class AadhaarDetailsCheck(_Check):
    """DQ-009 per profile that needs APAAR: missing Aadhaar last 4 / as-printed fields, for
    students without a verified APAAR ID (v2, ADR-0037)."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule, keys = self.rule, context.config.apaar_attributes
        severity = _fixed(rule)
        source = rule.sources[0]
        for profile in context.profiles:
            if not profile.needs_apaar:
                continue
            for facts in context.students.values():
                if facts.has_verified(context.config.apaar_attribute):
                    continue  # ADR-0037: the APAAR ID exists; readiness no longer matters
                missing = [k for k in keys if facts.value(k, source) is None]
                if not missing:
                    continue
                yield make_finding(
                    rule,
                    facts.student_id,
                    severity,
                    details={"missing": missing},
                    profile_key=profile.key,
                )


class EnrolmentOverlapCheck(_Check):
    """DQ-012: a student with more than one active enrolment (two sections or two years)."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule = self.rule
        severity = _fixed(rule)
        for facts in context.students.values():
            if len(facts.enrolments) < 2:
                continue
            ordered = sorted(facts.enrolments, key=lambda e: str(e.enrollment_id))
            yield make_finding(
                rule,
                facts.student_id,
                severity,
                details={
                    "enrollment_ids": [str(e.enrollment_id) for e in ordered],
                    "section_ids": [str(e.section_id) for e in ordered],
                    "academic_year_ids": sorted({str(e.academic_year_id) for e in ordered}),
                },
            )


class ApaarIdCheck(_Check):
    """DQ-021 (ADR-0037): an APAAR ID that is not 12 digits from any listed source, and a
    canonical APAAR ID that another student of the school also has (one finding per checked
    student, naming the first other record by admission number; never the ID)."""

    def evaluate(self, context: CheckContext, /) -> Iterator[Finding]:
        rule = self.rule
        severity = _fixed(rule)
        key = rule.attribute_keys[0]
        for facts in context.students.values():
            for source in rule.sources:
                fact = facts.value(key, source)
                if fact is None or fact.value is None or _APAAR_RE.fullmatch(fact.value.strip()):
                    continue
                yield make_finding(
                    rule,
                    facts.student_id,
                    severity,
                    attribute_key=key,
                    sources=(source,),
                    details={"reason": "format", "values": [value_entry(key, source, fact)]},
                )
        yield from self._duplicates(context, key, severity)

    def _duplicates(self, context: CheckContext, key: str, severity: Severity) -> Iterator[Finding]:
        rule = self.rule
        index: dict[str, list[StudentFacts]] = defaultdict(list)
        for facts in {**context.population, **context.students}.values():
            value = facts.canonical_value(key)
            if value is not None and _APAAR_RE.fullmatch(value.strip()):
                index[value.strip()].append(facts)
        for facts in context.students.values():
            value = facts.canonical_value(key)
            if value is None:
                continue
            others = sorted(
                (o for o in index.get(value.strip(), ()) if o.student_id != facts.student_id),
                key=lambda o: (student_label(o), str(o.student_id)),
            )
            if not others:
                continue
            yield make_finding(
                rule,
                facts.student_id,
                severity,
                attribute_key=key,
                sources=(CANONICAL,),
                details={
                    "reason": "duplicate",
                    "others": len(others),
                    # The record named in the text: the service masks it outside the reader's
                    # scope, as for DQ-008 (only DQ-008 may use related_student_id).
                    "other_student_id": str(others[0].student_id),
                },
                params={"student": student_label(others[0])},
                explanation_code=APAAR_DUPLICATE_CODE,
            )


class ApaarDemographicsCheck(CrossSourceCheck):
    """DQ-022 (ADR-0037): UDISE+ vs Aadhaar-as-printed, compared as DQ-010/011 do, for students
    without a verified APAAR ID (generation authenticates against Aadhaar)."""

    def _students(self, context: CheckContext) -> Iterable[StudentFacts]:
        key = context.config.apaar_attribute
        return [f for f in context.students.values() if not f.has_verified(key)]


CHECKS: Final[dict[CheckKind, Callable[[Rule], RuleCheck[CheckContext]]]] = {
    CheckKind.NAME_MATCH: NameMatchCheck,
    CheckKind.VALUE_EQUAL: ValueEqualCheck,
    CheckKind.CROSS_SOURCE: CrossSourceCheck,
    CheckKind.REQUIRED_FIELDS: RequiredFieldsCheck,
    CheckKind.NAME_FORMAT: NameFormatCheck,
    CheckKind.AGE_BAND: AgeBandCheck,
    CheckKind.DUPLICATE: DuplicateCheck,
    CheckKind.AADHAAR_DETAILS: AadhaarDetailsCheck,
    CheckKind.ENROLMENT_OVERLAP: EnrolmentOverlapCheck,
    CheckKind.APAAR_ID: ApaarIdCheck,
    CheckKind.APAAR_DEMOGRAPHICS: ApaarDemographicsCheck,
}


def build_checks(rules: RuleRegistry) -> tuple[RuleCheck[CheckContext], ...]:
    """One check per catalog rule (every check kind has an implementation)."""
    return tuple(CHECKS[rule.check](rule) for rule in rules.values())


def evaluate(context: CheckContext, checks: Iterable[RuleCheck[CheckContext]]) -> list[Finding]:
    """Every finding of ``checks`` for the students of ``context`` (deduplicated by fingerprint)."""
    out: dict[str, Finding] = {}
    for check in checks:
        for finding in check.evaluate(context):
            out.setdefault(fingerprint_of(finding), finding)
    return list(out.values())


def attribute_keys_needed(config: EngineConfig, rules: RuleRegistry) -> dict[str, set[str]]:
    """Physical attribute keys per source the source-value checks read (for bulk loading)."""
    needed: dict[str, set[str]] = defaultdict(set)
    for rule in rules.values():
        if rule.check in (
            CheckKind.NAME_MATCH,
            CheckKind.VALUE_EQUAL,
            CheckKind.CROSS_SOURCE,
            CheckKind.APAAR_ID,
            CheckKind.APAAR_DEMOGRAPHICS,
        ):
            for source in rule.sources:
                for key in rule.attribute_keys:
                    needed[source].add(config.physical_key(key, source))
        if rule.check is CheckKind.AADHAAR_DETAILS:
            needed[rule.sources[0]].update(config.apaar_attributes)
    return dict(needed)
