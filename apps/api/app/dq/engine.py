"""Data-quality engine: bulk loading, checks, idempotent findings (FR-DQ-002, FR-DQ-004,
FR-DQ-005, NFR-PERF-005).

One run = one transaction in the school's ``tenant_session``:

1. **Load** the facts of every student in scope with a fixed number of bulk reads through
   ``app.students.service`` (current values per source incl. C3 Aadhaar-as-printed, decrypted in
   memory only; canonical values; active enrolments) plus the school's structure (years, classes,
   sections) and, for DQ-008, the canonical names and dates of birth of the whole school.
2. **Check**: :mod:`app.dq.checks` (pure) returns :class:`~app.dq.rules.Finding` values with
   masked values only.
3. **Reconcile** by fingerprint against the stored findings of those students (locked):

   ============================  ========================================================
   found, no row                  insert ``open``
   found, ``open``/``reopened``   refresh (severity, values, details) when changed
   found, ``resolved``            ``reopened`` (US-502 AC2: the conflict came back)
   found, ``waived``              stays waived while the conflict is the same
                                  (``conflict_hash``); a changed conflict is ``reopened``
   not found, unresolved          ``resolved``: ``change_request`` when the finding is linked
                                  to a change request, else ``auto_cleared``
   ============================  ========================================================

   "Not found" applies only to what the run evaluated: the students in scope (either side of a
   DQ-008 pair) and, for profile rules, the profiles the run checked. Nothing is ever deleted.

No per-student queries: 2,000 students are checked well inside the 2-minute budget
(``tests/dq/test_performance.py``).
"""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import time
import uuid
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.authz.context import Scopes, UserContext
from app.core.ids import new_id
from app.dq import repository as repo
from app.dq.checks import (
    CANONICAL,
    RESERVED_DETAILS,
    CanonicalFact,
    CheckContext,
    EnrolmentFact,
    SourceFact,
    StudentFacts,
    attribute_keys_needed,
    build_checks,
    evaluate,
    fingerprint_of,
)
from app.dq.matching import load_match_policy, load_variant_dictionary
from app.dq.models import ACTIVE_STATUSES
from app.dq.profiles import Profile, load_engine_config, load_profiles
from app.dq.rules import Finding, RuleCheck, Severity, load_rules
from app.students import service as students
from app.tenancy import service as tenancy

READ: Final = "student.read_basic"
SENSITIVE: Final = "student.read_sensitive"
DQ_READ: Final = "dq.findings.read"
SYSTEM_USER_ID: Final = uuid.UUID(int=0)
DUPLICATE_KEYS: Final = ("full_name", "dob", "father_name", "mother_name", "admission_no")
BASE_CANONICAL_KEYS: Final = frozenset(DUPLICATE_KEYS)


def system_context(tenant_id: uuid.UUID) -> UserContext:
    """Whole-school read context for runs started by events and queued runs (worker)."""
    return UserContext(
        user_id=SYSTEM_USER_ID,
        tenant_id=tenant_id,
        membership_id=SYSTEM_USER_ID,
        roles=frozenset({"system"}),
        permissions=frozenset({READ, SENSITIVE, DQ_READ}),
        scopes=Scopes(school=True),
        mfa=False,
        auth_time=None,
    )


# --- scope ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scope:
    """At most one of the fields is set; none = every student the caller can read."""

    section_ids: tuple[uuid.UUID, ...] | None = None
    class_ids: tuple[uuid.UUID, ...] | None = None
    student_ids: tuple[uuid.UUID, ...] | None = None
    batch_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        chosen = [
            f for f in (self.section_ids, self.class_ids, self.student_ids, self.batch_id) if f
        ]
        if len(chosen) > 1:
            raise ValueError("a run scope names sections, classes, students or one import batch")

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("section_ids", "class_ids", "student_ids"):
            value = getattr(self, name)
            if value is not None:
                out[name] = [str(v) for v in value]
        if self.batch_id is not None:
            out["batch_id"] = str(self.batch_id)
        return out

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Scope:
        def ids(name: str) -> tuple[uuid.UUID, ...] | None:
            value = raw.get(name)
            return None if value is None else tuple(uuid.UUID(str(v)) for v in value)

        batch = raw.get("batch_id")
        return cls(
            section_ids=ids("section_ids"),
            class_ids=ids("class_ids"),
            student_ids=ids("student_ids"),
            batch_id=uuid.UUID(str(batch)) if batch else None,
        )


def resolve_students(session: Session, ctx: UserContext, scope: Scope) -> list[uuid.UUID]:
    """Student ids of ``scope`` limited to what ``ctx`` may read (``student.read_basic``) and,
    when it holds ``dq.findings.read``, to that grant's scope too (SEC-015)."""
    reach = (DQ_READ,) if ctx.has(DQ_READ) else ()
    if scope.section_ids is not None:
        return students.list_students_in_scope(
            session, ctx, section_ids=scope.section_ids, permissions=reach
        )
    if scope.class_ids is not None:
        return students.list_students_in_scope(
            session, ctx, class_ids=scope.class_ids, permissions=reach
        )
    reachable = students.list_students_in_scope(session, ctx, permissions=reach)
    if scope.student_ids is not None:
        wanted = set(scope.student_ids)
        return [s for s in reachable if s in wanted]
    if scope.batch_id is not None:
        in_batch = set(students.student_ids_for_import_batch(session, scope.batch_id))
        return [s for s in reachable if s in in_batch]
    return reachable


# --- loading --------------------------------------------------------------------------------------


@functools.cache
def _checks() -> tuple[RuleCheck[CheckContext], ...]:
    return build_checks(load_rules())


def profiles_for(keys: Iterable[str]) -> tuple[Profile, ...]:
    known = load_profiles()
    return tuple(known[k] for k in dict.fromkeys(keys) if k in known)


def _structure(
    session: Session,
) -> tuple[dict[uuid.UUID, Any], dict[uuid.UUID, Any], dict[uuid.UUID, str]]:
    years = {y.id: y for y in tenancy.list_academic_years(session)}
    sections = {s.id: s for s in tenancy.list_sections(session)}
    classes = {c.id: c.code for c in tenancy.list_classes(session)}
    return years, sections, classes


def _canonical_facts(
    canon: Mapping[str, students.CanonicalValue],
) -> dict[str, CanonicalFact]:
    return {
        key: CanonicalFact(
            value=v.value, source=v.source, provisional=v.provisional, verified=v.verified
        )
        for key, v in canon.items()
    }


def load_context(
    session: Session, student_ids: Sequence[uuid.UUID], profiles: Sequence[Profile]
) -> CheckContext:
    """Every fact the checks need, in a fixed number of bulk reads."""
    cfg, rules = load_engine_config(), load_rules()
    catalog = students.attribute_catalog(session)
    identity_keys = frozenset(a.key for a in catalog if a.is_identity)
    needed = attribute_keys_needed(cfg, rules)
    source_keys = set().union(*needed.values()) if needed else set()
    # The APAAR ID gates DQ-009/DQ-022 and is compared for DQ-021 (ADR-0037).
    canonical_keys = {*BASE_CANONICAL_KEYS, cfg.apaar_attribute}
    for profile in profiles:
        canonical_keys.update(profile.required_fields)
        if profile.name_format is not None:
            canonical_keys.update(profile.name_format.fields)
    source_keys.update(canonical_keys & {"full_name", "father_name", "mother_name", "dob"})
    # C3 values (Aadhaar-as-printed, category) are decrypted in memory only; findings keep the
    # masked form (FR-DQ-006).
    by_source = students.source_values(session, student_ids, source_keys, include_sensitive=True)
    canonical = students.canonical_values(
        session, student_ids, canonical_keys, include_sensitive=True
    )
    enrolments = students.active_enrolments(session, student_ids)
    years, sections, classes = _structure(session)

    def enrolment_fact(e: students.ActiveEnrolment) -> EnrolmentFact:
        section = sections.get(e.section_id)
        year = years.get(e.academic_year_id)
        return EnrolmentFact(
            enrollment_id=e.enrollment_id,
            section_id=e.section_id,
            academic_year_id=e.academic_year_id,
            class_code=classes.get(section.class_id) if section is not None else None,
            year_starts_on=year.starts_on if year is not None else None,
            current=bool(year is not None and year.is_current),
        )

    facts: dict[uuid.UUID, StudentFacts] = {}
    for sid in student_ids:
        values = {
            key: {src: SourceFact(v.value_id, v.value) for src, v in per_source.items()}
            for key, per_source in by_source.get(sid, {}).items()
        }
        canon = _canonical_facts(canonical.get(sid, {}))
        facts[sid] = StudentFacts(
            student_id=sid,
            admission_no=canon["admission_no"].value if "admission_no" in canon else None,
            values=values,
            canonical=canon,
            enrolments=tuple(enrolment_fact(e) for e in enrolments.get(sid, ())),
        )
    return CheckContext(
        students=facts,
        config=cfg,
        policy=load_match_policy(),
        variants=load_variant_dictionary(),
        population=_population(session, facts, cfg.apaar_attribute),
        profiles=tuple(profiles),
        identity_keys=identity_keys,
    )


def _population(
    session: Session, in_scope: Mapping[uuid.UUID, StudentFacts], apaar_key: str
) -> dict[uuid.UUID, StudentFacts]:
    """Canonical name, date of birth and parents (DQ-008) and APAAR ID (DQ-021) of every
    student of the school."""
    tenant_id = repo.current_tenant(session)
    everyone = students.list_students_in_scope(session, system_context(tenant_id))
    others = [s for s in everyone if s not in in_scope]
    canonical = students.canonical_values(session, others, (*DUPLICATE_KEYS, apaar_key))
    out: dict[uuid.UUID, StudentFacts] = {}
    for sid in others:
        canon = _canonical_facts(canonical.get(sid, {}))
        compared = [canon.get(k) for k in ("dob", apaar_key)]
        if all(fact is None or fact.value is None for fact in compared):
            continue
        out[sid] = StudentFacts(
            student_id=sid,
            admission_no=canon["admission_no"].value if "admission_no" in canon else None,
            canonical=canon,
        )
    return out


# --- reconcile ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeRequestLink:
    """An approved change request whose student the run re-checks (FR-CR, US-601 AC3)."""

    change_request_id: uuid.UUID
    student_id: uuid.UUID
    attribute_key: str | None = None


@dataclass
class RunStats:
    students: int = 0
    findings: int = 0
    new: int = 0
    reopened: int = 0
    updated: int = 0
    unchanged: int = 0
    cleared: int = 0
    blockers: int = 0
    warnings: int = 0
    by_severity: Counter[str] = field(default_factory=Counter)
    duration_ms: int = 0

    def as_json(self, profiles: Sequence[str]) -> dict[str, Any]:
        return {
            "students": self.students,
            "findings": self.findings,
            "new": self.new,
            "reopened": self.reopened,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "cleared": self.cleared,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "by_severity": {s.value: self.by_severity.get(s.value, 0) for s in Severity},
            "profiles": list(profiles),
            "duration_ms": self.duration_ms,
        }


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def split_details(
    finding: Finding,
) -> tuple[dict[str, Any], dict[str, Any], str | None, str | None]:
    """(details without reserved keys, params, profile key, related student id)."""
    details: dict[str, Any] = dict(finding.details)
    raw_params = details.pop("params", None)
    params: dict[str, Any] = dict(raw_params) if isinstance(raw_params, Mapping) else {}
    profile = details.pop("profile_key", None)
    related = details.pop("related_student_id", None)
    for key in RESERVED_DETAILS:
        details.pop(key, None)
    return (
        _json(details),
        _json(params),
        str(profile) if profile else None,
        str(related) if related else None,
    )


def conflict_hash(finding: Finding, details: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    """Identifies *this* conflict (values, severity, match class): a waiver covers exactly it."""
    body = {
        "severity": finding.severity.value,
        "match_class": finding.match_class,
        "explanation_code": finding.explanation_code,
        "details": details,
        "params": params,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _columns(finding: Finding) -> dict[str, Any]:
    details, params, profile, related = split_details(finding)
    return {
        "rule_id": finding.rule_id,
        "rule_version": finding.rule_version,
        "student_id": finding.student_id,
        "related_student_id": uuid.UUID(related) if related else None,
        "profile_key": profile,
        "attribute_key": finding.attribute_key,
        "sources": list(finding.sources),
        "match_class": finding.match_class,
        "severity": finding.severity.value,
        "explanation_code": finding.explanation_code,
        "explanation_params": params,
        "route_codes": list(finding.route_codes),
        "details": details,
        "conflict_hash": conflict_hash(finding, details, params),
    }


_DATA_COLUMNS: Final = (
    "rule_version",
    "severity",
    "match_class",
    "explanation_code",
    "explanation_params",
    "route_codes",
    "details",
    "conflict_hash",
    "sources",
    "related_student_id",
)


def _evaluated(row: RowMapping, scope: Collection[uuid.UUID], profiles: Collection[str]) -> bool:
    in_scope = row["student_id"] in scope or (
        row["related_student_id"] is not None and row["related_student_id"] in scope
    )
    return in_scope and (row["profile_key"] is None or row["profile_key"] in profiles)


def _clear(
    row: RowMapping, change_request: ChangeRequestLink | None, at: dt.datetime
) -> dict[str, Any]:
    """A finding the run no longer finds: resolved by the linked change request, or cleared."""
    linked = row["change_request_id"]
    if (
        change_request is not None
        and row["student_id"] == change_request.student_id
        and (
            change_request.attribute_key is None
            or row["attribute_key"] == change_request.attribute_key
            or linked == change_request.change_request_id
        )
    ):
        linked = change_request.change_request_id
    return {
        "id": row["id"],
        "status": "resolved",
        "resolution": "change_request" if linked is not None else "auto_cleared",
        "change_request_id": linked,
        "resolved_at": at,
        "version": row["version"] + 1,
    }


def reconcile(
    session: Session,
    *,
    run_id: uuid.UUID,
    findings: Sequence[Finding],
    scope: Collection[uuid.UUID],
    profiles: Collection[str],
    change_request: ChangeRequestLink | None = None,
) -> RunStats:
    tenant_id = repo.current_tenant(session)
    at = repo.now(session)
    by_fp = {fingerprint_of(f): f for f in findings}
    existing = {
        r["fingerprint"]: r for r in repo.findings_to_reconcile(session, scope, list(by_fp))
    }
    stats = RunStats(findings=len(by_fp))
    inserts: list[dict[str, Any]] = []
    refresh: list[dict[str, Any]] = []
    unchanged: list[uuid.UUID] = []
    for fp, finding in by_fp.items():
        cols = _columns(finding)
        row = existing.get(fp)
        if row is None:
            inserts.append(
                {
                    **cols,
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "fingerprint": fp,
                    "status": "open",
                    "first_seen_run_id": run_id,
                    "last_seen_run_id": run_id,
                    "first_seen_at": at,
                    "last_seen_at": at,
                    "reopened_count": 0,
                    "version": 1,
                }
            )
            stats.new += 1
            status = "open"
        else:
            status = row["status"]
            reopened = status == "resolved" or (
                status == "waived" and row["conflict_hash"] != cols["conflict_hash"]
            )
            changed = any(_differs(row[c], cols[c]) for c in _DATA_COLUMNS)
            if reopened:
                status = "reopened"
                stats.reopened += 1
            elif changed:
                stats.updated += 1
            if reopened or changed:
                refresh.append(
                    {
                        **{c: cols[c] for c in _DATA_COLUMNS},
                        "id": row["id"],
                        "status": status,
                        "reopened_count": row["reopened_count"] + (1 if reopened else 0),
                        "last_seen_run_id": run_id,
                        "last_seen_at": at,
                        "version": row["version"] + 1,
                    }
                )
            else:
                unchanged.append(row["id"])
                stats.unchanged += 1
        # Counts cover the run's own students (DQ-008 mirrors of other students are not theirs).
        if status in ACTIVE_STATUSES and finding.student_id in scope:
            stats.by_severity[finding.severity.value] += 1
            if finding.severity is Severity.BLOCKER:
                stats.blockers += 1
            else:
                stats.warnings += 1
    clears = [
        _clear(row, change_request, at)
        for fp, row in existing.items()
        if fp not in by_fp and row["status"] != "resolved" and _evaluated(row, scope, profiles)
    ]
    stats.cleared = len(clears)
    repo.insert_findings(session, inserts)
    repo.refresh_findings(session, refresh)
    repo.clear_findings(session, clears)
    repo.touch_findings(session, unchanged, run_id, at)
    return stats


def _differs(stored: Any, new: Any) -> bool:
    if isinstance(stored, uuid.UUID) or isinstance(new, uuid.UUID):
        return str(stored) != str(new) if (stored is not None or new is not None) else False
    if isinstance(stored, (list, tuple)) and isinstance(new, (list, tuple)):
        return list(stored) != list(new)
    return bool(_json(stored) != _json(new))


# --- execute --------------------------------------------------------------------------------------


def execute(
    session: Session,
    *,
    run_id: uuid.UUID,
    student_ids: Sequence[uuid.UUID],
    profile_keys: Sequence[str],
    change_request: ChangeRequestLink | None = None,
) -> RunStats:
    """Check ``student_ids`` for the base rules and ``profile_keys``; reconcile findings."""
    started = time.monotonic()
    profiles = profiles_for(profile_keys)
    context = load_context(session, list(student_ids), profiles)
    findings = evaluate(context, _checks())
    stats = reconcile(
        session,
        run_id=run_id,
        findings=findings,
        scope=set(student_ids),
        profiles={p.key for p in profiles},
        change_request=change_request,
    )
    stats.students = len(student_ids)
    stats.duration_ms = int((time.monotonic() - started) * 1000)
    return stats


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


__all__ = [
    "CANONICAL",
    "ChangeRequestLink",
    "RunStats",
    "Scope",
    "execute",
    "load_context",
    "profiles_for",
    "reconcile",
    "resolve_students",
    "system_context",
]
