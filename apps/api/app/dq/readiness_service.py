"""Board and portal readiness: summary, student detail, runs and parent slips (US-503..US-505,
FR-DQ-030..FR-DQ-036, ADR-0040). Routes: ``app.dq.api`` (``/api/v1/dq/readiness/...``).

- **Live, overlaid.** Readiness is computed from the current values on every read (the same
  pure assessment the DQ-030 check runs: :func:`app.dq.checks.assess_readiness`), then the
  stored DQ-030 findings of the profile are laid over it by fingerprint: a difference whose
  finding is **waived** (same conflict) no longer counts, and a finding that waits for a
  confirmation (A-01, ``needs_confirmation``) holds the student as ``blocked`` even though the
  values now agree. Runs (``POST .../runs``) persist the findings so the resolve/waive workflow
  and change requests apply (FR-DQ-033); they reuse ``dq.request_run`` (audit
  ``dq.run.completed``).
- **Scope (SEC-015).** Everything is limited to the students the caller reaches with both
  ``student.read_basic`` and ``dq.readiness.read`` (class teachers: their sections this year);
  a student or section outside it, or of another school, is 404.
- **Values (invariants 4, 5).** C2 values are shown; C3 values (Aadhaar as printed) only to
  holders of ``student.read_sensitive`` for that student, and the view is audited
  (``dq.readiness.student_viewed``). Character-level diffs need every value involved to be
  visible. No Aadhaar number ever appears (only as-printed demographic fields); slips are
  audited (``dq.readiness.slips_printed``). Logs carry ids and counts only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import NotFound, ValidationFailed
from app.core.languages import telugu_text
from app.core.logging import get_logger
from app.dq import checks, engine
from app.dq import readiness as rd
from app.dq import repository as repo
from app.dq import service as dq
from app.dq import slip as slip_page
from app.dq.masking import mask_value
from app.dq.matching import MatchClass, classify, load_match_policy, load_variant_dictionary
from app.dq.profiles import Profile, ReadinessSpec, load_engine_config, load_profiles
from app.dq.rules import load_rules
from app.dq.schemas import (
    Bilingual,
    DiffSegment,
    ReadinessCounts,
    ReadinessFieldOut,
    ReadinessItemOut,
    ReadinessProfileOut,
    ReadinessRunIn,
    ReadinessSectionOut,
    ReadinessStudentDetailOut,
    ReadinessStudentOut,
    ReadinessSummaryOut,
    ReadinessValueOut,
    RunCreate,
    RunOut,
    RunScopeIn,
    StudentRef,
)
from app.students import service as students
from app.tenancy import service as tenancy

log = get_logger(__name__)

READ: Final = "dq.readiness.read"
MANAGE: Final = "dq.readiness.manage"
STUDENT_READ: Final = "student.read_basic"
SENSITIVE: Final = "student.read_sensitive"
RULE_ID: Final = "DQ-030"
MAX_SLIPS: Final = 300
NOT_RECORDED: Final = "—"


# --- profile ----------------------------------------------------------------------------------


def readiness_profile(profile_key: str) -> Profile:
    """A profile with a readiness check, else 404."""
    profile = load_profiles().get(profile_key)
    if profile is None or profile.readiness is None:
        raise NotFound("Readiness check not found")
    return profile


def _spec(profile: Profile) -> ReadinessSpec:
    if profile.readiness is None:
        raise NotFound("Readiness check not found")
    return profile.readiness


def _profile_out(profile: Profile) -> ReadinessProfileOut:
    spec = _spec(profile)
    return ReadinessProfileOut(
        key=profile.key,
        version=profile.version,
        label_en=profile.label_en,
        label_te=telugu_text(profile.label_te) or "",  # empty while Telugu is hidden (ADR-0036)
        source=list(profile.source),
        verified=profile.verified,
        classes=list(spec.classes),
        fields=[f.attribute for f in spec.fields],
    )


def _bilingual(code: str, en: str, te: str) -> Bilingual:
    return Bilingual(code=code, en=en, te=telugu_text(te) or "")


# --- assessment and overlay -------------------------------------------------------------------


def _classifier() -> rd.Classifier:
    policy, variants = load_match_policy(), load_variant_dictionary()

    def run(a: str, b: str) -> MatchClass:
        return classify(a, b, variants=variants, thresholds=policy.thresholds).match_class

    return run


def _keys(profile: Profile) -> tuple[set[str], set[str]]:
    """(physical source attribute keys, canonical keys) the assessment reads."""
    spec = _spec(profile)
    cfg, rcfg = load_engine_config(), rd.load_readiness_config()
    source_keys = {
        cfg.physical_key(f.attribute, s)
        for f in spec.fields
        for s in (*f.sources, rcfg.referee, *rcfg.corroborating_sources)
    }
    canonical = {"full_name"}
    if spec.skip_when_verified:
        canonical.add(spec.skip_when_verified)
    return source_keys, canonical


@dataclass
class _Line:
    """One readiness item with its finding (if a run stored one)."""

    item: rd.Item
    finding: Any
    row: RowMapping | None
    waived: bool

    def counts(self) -> bool:
        return self.item.counts() and not self.waived


@dataclass
class _Student:
    student_id: uuid.UUID
    assessment: checks.StudentReadiness
    applies: bool
    lines: list[_Line] = field(default_factory=list)
    # Stored findings that wait for a confirmation although the values now agree (A-01).
    confirmations: list[RowMapping] = field(default_factory=list)
    status: rd.Status = "ready"


def _assess(
    session: Session,
    profile: Profile,
    student_ids: Sequence[uuid.UUID],
    *,
    force: bool = False,
) -> dict[uuid.UUID, _Student]:
    """Live readiness of ``student_ids`` with the stored findings laid over it.

    ``force``: assess even students the profile does not apply to (another class, or a
    verified APAAR ID), for the detail view; their status stays ``ready``."""
    if not student_ids:
        return {}
    cfg, rcfg = load_engine_config(), rd.load_readiness_config()
    source_keys, canonical_keys = _keys(profile)
    facts = engine.load_facts(session, list(student_ids), source_keys, canonical_keys)
    classifier = _classifier()
    rule = load_rules()[RULE_ID]
    rows = repo.profile_findings(session, list(facts), profile.key, RULE_ID)
    by_fp = {r["fingerprint"]: r for r in rows}
    out: dict[uuid.UUID, _Student] = {}
    for sid, fact in facts.items():
        applies = checks.readiness_applies(fact, profile)
        if applies or force:
            unfiltered = profile.model_copy(
                update={
                    "readiness": _spec(profile).model_copy(
                        update={"classes": (), "skip_when_verified": None}
                    )
                }
            )
            assessment = checks.assess_readiness(fact, unfiltered, cfg, classifier, rcfg)
        else:
            assessment = checks.StudentReadiness(sid, profile.key, applies=False)
        student = _Student(sid, assessment, applies)
        live: set[str] = set()
        for item in assessment.items:
            finding = checks.readiness_finding(rule, assessment, item, cfg, rcfg)
            fp = checks.fingerprint_of(finding)
            live.add(fp)
            row = by_fp.get(fp)
            waived = False
            if row is not None and row["status"] == "waived":
                details, params, _, _ = engine.split_details(finding)
                waived = row["conflict_hash"] == engine.conflict_hash(finding, details, params)
            student.lines.append(_Line(item, finding, row, waived))
        student.confirmations = [
            r
            for r in rows
            if r["student_id"] == sid
            and r["status"] == "needs_confirmation"
            and r["fingerprint"] not in live
        ]
        if applies:
            counted = [line.item for line in student.lines if line.counts()]
            status = rd.student_status(counted, rcfg)
            if student.confirmations:
                status = "blocked"
            student.status = status
        out[sid] = student
    return out


# --- scope ------------------------------------------------------------------------------------


def _section_in_reach(ctx: UserContext, section: Any) -> bool:
    for permission in (READ, STUDENT_READ):
        grant = ctx.scope_for(permission)
        if grant.school_wide:
            continue
        if section.id not in grant.section_ids and section.class_id not in grant.class_ids:
            return False
    return True


def _section(session: Session, ctx: UserContext, section_id: uuid.UUID) -> Any:
    """A section of this school in the caller's reach, else 404 (no existence oracle)."""
    section = tenancy.get_section(session, section_id)
    if not _section_in_reach(ctx, section):
        raise NotFound("Section not found")
    return section


def _profile_sections(session: Session, profile: Profile) -> dict[uuid.UUID, Any]:
    """Current-year sections of the profile's classes (every class when it names none)."""
    spec = _spec(profile)
    years = [y for y in tenancy.list_academic_years(session) if y.is_current]
    if not years:
        return {}
    classes = {c.id: c for c in tenancy.list_classes(session)}
    return {
        s.id: s
        for s in tenancy.list_sections(session, academic_year_id=years[0].id)
        if not spec.classes or (s.class_id in classes and classes[s.class_id].code in spec.classes)
    }


def _in_reach(
    session: Session, ctx: UserContext, section_ids: Collection[uuid.UUID] | None
) -> list[uuid.UUID]:
    return students.list_students_in_scope(
        session, ctx, section_ids=section_ids, permissions=(READ,)
    )


def _can_reveal(session: Session, ctx: UserContext, student_id: uuid.UUID) -> bool:
    if not ctx.has(SENSITIVE):
        return False
    try:
        students.ensure_in_scope(session, ctx, student_id, SENSITIVE)
    except NotFound:
        return False
    return True


# --- summary and lists ------------------------------------------------------------------------


def _counts(statuses: Sequence[rd.Status]) -> dict[str, int]:
    return {
        "students": len(statuses),
        "ready": sum(1 for s in statuses if s == "ready"),
        "needs_parent": sum(1 for s in statuses if s == "needs_parent"),
        "needs_school": sum(1 for s in statuses if s == "needs_school"),
        "blocked": sum(1 for s in statuses if s == "blocked"),
    }


def summary(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    *,
    section_ids: Collection[uuid.UUID] | None = None,
) -> ReadinessSummaryOut:
    """Readiness by section of the profile's classes in the caller's scope (US-503 AC3)."""
    profile = readiness_profile(profile_key)
    sections = _profile_sections(session, profile)
    wanted = [s for s in (section_ids or sections) if s in sections]
    ids = _in_reach(session, ctx, wanted) if wanted else []
    placements = students.current_placements(session, ids)
    ids = [sid for sid in ids if placements.get(sid) in sections]
    assessed = _assess(session, profile, ids)
    by_section: dict[uuid.UUID, list[rd.Status]] = {}
    for sid in ids:
        by_section.setdefault(placements[sid], []).append(assessed[sid].status)
    classes = {c.id: c for c in tenancy.list_classes(session)}

    def order(section_id: uuid.UUID) -> tuple[int, str]:
        section = sections[section_id]
        klass = classes.get(section.class_id)
        return (klass.sort_order if klass is not None else 0, section.name)

    rows = [
        ReadinessSectionOut(
            section_id=section_id,
            class_id=sections[section_id].class_id,
            **_counts(by_section.get(section_id, [])),
        )
        for section_id in sorted(by_section, key=order)
    ]
    last = repo.last_profile_run(session, profile.key)
    visible = last is not None and (
        ctx.scope_for(READ).school_wide or last["requested_by_membership"] == ctx.membership_id
    )
    all_statuses = [assessed[sid].status for sid in ids]
    log.info("dq.readiness.summary", count=len(ids))
    return ReadinessSummaryOut(
        profile=_profile_out(profile),
        totals=ReadinessCounts(**_counts(all_statuses)),
        sections=rows,
        last_run=dq._run_out(last) if visible and last is not None else None,
    )


def _owners(student: _Student) -> list[str]:
    owners = [line.item.owner for line in student.lines if line.counts()]
    if student.confirmations:
        owners.append("unknown")
    return [o for o in rd.OWNERS if o in owners]


def section_students(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    section_id: uuid.UUID,
    *,
    status: Collection[str] | None = None,
) -> list[ReadinessStudentOut]:
    """The students of one section with their readiness, worst first (no values)."""
    profile = readiness_profile(profile_key)
    _section(session, ctx, section_id)
    ids = _in_reach(session, ctx, [section_id])
    assessed = _assess(session, profile, ids)
    names = students.summaries(session, ctx, ids)
    rcfg = rd.load_readiness_config()
    out: list[ReadinessStudentOut] = []
    for sid in ids:
        student = assessed[sid]
        if status and student.status not in status:
            continue
        ref = names.get(sid)
        counted = [line for line in student.lines if line.counts()]
        out.append(
            ReadinessStudentOut(
                student=StudentRef(
                    id=sid,
                    display_name=ref.display_name if ref else None,
                    admission_no=ref.admission_no if ref else None,
                ),
                section_id=section_id,
                status=student.status,
                owners=_owners(student),
                attribute_keys=sorted({line.item.attribute for line in counted}),
                open_items=len(counted) + len(student.confirmations),
            )
        )
    out.sort(key=lambda s: (-rcfg.rank(s.status), s.student.display_name or "", str(s.student.id)))
    return out


# --- detail -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Visible:
    labels: Mapping[str, tuple[str, str]]
    sensitive: frozenset[str]
    reveal: bool

    def shows(self, physical_key: str) -> bool:
        return self.reveal or physical_key not in self.sensitive


def _visible(session: Session, reveal: bool) -> _Visible:
    catalog = students.attribute_catalog(session)
    return _Visible(
        labels={a.key: (a.label_en, a.label_te) for a in catalog},
        sensitive=frozenset(a.key for a in catalog if a.classification == "C3"),
        reveal=reveal,
    )


def _value_out(
    attribute: str, source: str, fact: checks.SourceFact | None, vis: _Visible
) -> ReadinessValueOut:
    cfg = load_engine_config()
    physical = cfg.physical_key(attribute, source)
    sensitive = physical in vis.sensitive
    if fact is None or fact.value is None:
        return ReadinessValueOut(source=source, value=None, masked=None, sensitive=sensitive)
    kind = checks.value_kind(attribute)
    return ReadinessValueOut(
        source=source,
        value=fact.value if vis.shows(physical) else None,
        masked=mask_value(fact.value, kind=kind),
        sensitive=sensitive,
    )


def _item_out(
    line: _Line,
    student: _Student,
    vis: _Visible,
    labels: Any,
    rcfg: rd.ReadinessConfig,
) -> ReadinessItemOut:
    item = line.item
    cfg = load_engine_config()
    held = student.assessment.facts.get(item.attribute, {})
    kind = checks.readiness_kind(item.attribute)
    segments: list[DiffSegment] | None = None
    changes: list[Bilingual] | None = None
    if item.reason == "mismatch" and item.source is not None and item.against is not None:
        a, b = held.get(item.against), held.get(item.source)
        visible = all(
            vis.shows(cfg.physical_key(item.attribute, s)) for s in (item.source, item.against)
        )
        if visible and a is not None and b is not None and a.value and b.value:
            segments = [
                DiffSegment(op=s.op, reference=s.reference, other=s.other)
                for s in rd.segments(kind, a.value, b.value)
            ]
            changes = [
                _bilingual(c.code, c.text(rcfg, "en"), c.text(rcfg, "te"))
                for c in rd.describe(kind, a.value, b.value, item.kinds)
            ]
    finding = line.finding
    _details, params, _, _ = engine.split_details(finding)
    owner_label = rcfg.owner_labels[item.owner]
    diff = rd.kinds_param(item.kinds)
    return ReadinessItemOut(
        reason=item.reason,
        owner=item.owner,
        status=rd.item_status(item, rcfg),
        source=item.source,
        against=item.against,
        sources=list(item.sources),
        kinds=list(item.kinds),
        advisory=item.advisory,
        waived=line.waived,
        severity=finding.severity.value,
        explanation=dq._explanation(finding.explanation_code, params, labels, True),
        owner_label=_bilingual(item.owner, owner_label.en, owner_label.te),
        kinds_text=_bilingual(
            "kinds", rd.kinds_text(diff, rcfg, "en"), rd.kinds_text(diff, rcfg, "te")
        ),
        segments=segments,
        changes=changes,
        finding_id=line.row["id"] if line.row is not None else None,
        finding_status=line.row["status"] if line.row is not None else None,
    )


def _confirmation_out(row: RowMapping, labels: Any, rcfg: rd.ReadinessConfig) -> ReadinessItemOut:
    owner_label = rcfg.owner_labels["unknown"]
    return ReadinessItemOut(
        reason="needs_confirmation",
        owner="unknown",
        status="blocked",
        source=None,
        against=None,
        sources=list(row["sources"] or []),
        kinds=list((row["details"] or {}).get("kinds", [])),
        advisory=False,
        waived=False,
        severity=row["severity"],
        explanation=dq._explanation(
            row["explanation_code"], row["explanation_params"] or {}, labels, True
        ),
        owner_label=_bilingual("unknown", owner_label.en, owner_label.te),
        kinds_text=_bilingual("kinds", "", ""),
        segments=None,
        changes=None,
        finding_id=row["id"],
        finding_status=row["status"],
    )


def student_detail(
    session: Session, ctx: UserContext, profile_key: str, student_id: uuid.UUID
) -> ReadinessStudentDetailOut:
    """One student's readiness with the values of every record and the exact differences
    (US-504 AC1). C3 values only with ``student.read_sensitive`` for the student (audited)."""
    profile = readiness_profile(profile_key)
    students.ensure_in_scope(session, ctx, student_id, READ)
    reveal = _can_reveal(session, ctx, student_id)
    student = _assess(session, profile, [student_id], force=True)[student_id]
    rcfg = rd.load_readiness_config()
    vis = _visible(session, reveal)
    labels = dq._Labels.load(session)
    spec = _spec(profile)
    fields: list[ReadinessFieldOut] = []
    shown_sensitive: set[str] = set()
    cfg = load_engine_config()
    for assessed in student.assessment.fields:
        compared = next(f for f in spec.fields if f.attribute == assessed.attribute)
        held = student.assessment.facts.get(assessed.attribute, {})
        sources = list(compared.sources)
        if assessed.reference is not None and assessed.reference not in sources:
            sources.append(assessed.reference)
        values = [_value_out(assessed.attribute, s, held.get(s), vis) for s in sources]
        for s, v in zip(sources, values, strict=True):
            if v.sensitive and v.value is not None:
                shown_sensitive.add(cfg.physical_key(assessed.attribute, s))
        lines = [line for line in student.lines if line.item.attribute == assessed.attribute]
        items = [_item_out(line, student, vis, labels, rcfg) for line in lines]
        items += [
            _confirmation_out(r, labels, rcfg)
            for r in student.confirmations
            if r["attribute_key"] == assessed.attribute
        ]
        fields.append(
            ReadinessFieldOut(
                attribute_key=assessed.attribute,
                reference=assessed.reference,
                values=values,
                items=items,
            )
        )
    if shown_sensitive:
        dq._audit(
            session,
            action="dq.readiness.student_viewed",
            resource_type="student",
            resource_id=student_id,
            summary={"profile_key": profile.key, "attribute_keys": sorted(shown_sensitive)},
        )
    ref = students.summaries(session, ctx, [student_id]).get(student_id)
    return ReadinessStudentDetailOut(
        profile=_profile_out(profile),
        student=StudentRef(
            id=student_id,
            display_name=ref.display_name if ref else None,
            admission_no=ref.admission_no if ref else None,
        ),
        section_id=ref.section_id if ref else None,
        applies=student.applies,
        status=student.status,
        values_shown=reveal,
        fields=fields,
    )


# --- runs -------------------------------------------------------------------------------------


def request_run(
    session: Session, ctx: UserContext, profile_key: str, data: ReadinessRunIn
) -> RunOut:
    """Check the scope for the profile and store the differences as DQ-030 findings
    (``dq.request_run``: small scopes now, bigger ones queued; audit ``dq.run.completed``).
    An empty scope means the profile's classes."""
    profile = readiness_profile(profile_key)
    scope = data.scope
    spec = _spec(profile)
    empty = not (scope.section_ids or scope.class_ids or scope.student_ids or scope.batch_id)
    if empty and spec.classes:
        class_ids = [c.id for c in tenancy.list_classes(session) if c.code in spec.classes]
        if not class_ids:
            raise ValidationFailed(
                [
                    {
                        "field": "scope",
                        "code": "no_profile_classes",
                        "message_key": "errors.dq.no_profile_classes",
                    }
                ],
                detail="This school has none of the classes this check is for.",
            )
        scope = RunScopeIn(class_ids=class_ids)
    return dq.request_run(session, ctx, RunCreate(scope=scope, profile_key=profile.key))


# --- parent verification slips ----------------------------------------------------------------


def _slip_student(
    student: _Student,
    *,
    profile: Profile,
    vis: _Visible,
    labels: Any,
    rcfg: rd.ReadinessConfig,
    ref: Any,
    columns: Sequence[str],
) -> slip_page.SlipStudent:
    spec = _spec(profile)
    cfg = load_engine_config()
    rows: list[slip_page.SlipRow] = []
    for compared in spec.fields:
        held = student.assessment.facts.get(compared.attribute, {})
        wrong = {
            line.item.source
            for line in student.lines
            if line.item.attribute == compared.attribute and line.counts()
        }
        undecided = any(
            line.item.attribute == compared.attribute and line.item.reason == "undecided"
            for line in student.lines
        )
        cells: list[str] = []
        differs: list[bool] = []
        for source in columns:
            if source not in compared.sources:
                cells.append("")
                differs.append(False)
                continue
            fact = held.get(source)
            physical = cfg.physical_key(compared.attribute, source)
            if fact is None or not fact.value:
                cells.append(NOT_RECORDED)
            elif vis.shows(physical):
                value = fact.value
                cells.append(rd.display_date(value) if compared.attribute == "dob" else value)
            else:
                cells.append(
                    mask_value(fact.value, kind=checks.value_kind(compared.attribute)) or ""
                )
            differs.append(source in wrong or (undecided and fact is not None))
        label = labels.attributes.get(compared.attribute, (compared.attribute, ""))
        rows.append(slip_page.SlipRow(slip_page.SlipText(label[0], label[1]), cells, differs))
    issues: list[slip_page.SlipIssue] = []
    for line in student.lines:
        if not line.counts():
            continue
        finding = line.finding
        _, params, _, _ = engine.split_details(finding)
        text = dq._explanation(finding.explanation_code, params, labels, True)
        changes = []
        item = line.item
        held = student.assessment.facts.get(item.attribute, {})
        if item.source and item.against:
            a, b = held.get(item.against), held.get(item.source)
            if (
                a is not None
                and b is not None
                and a.value
                and b.value
                and all(
                    vis.shows(cfg.physical_key(item.attribute, s))
                    for s in (item.source, item.against)
                )
            ):
                changes = list(
                    rd.describe(checks.readiness_kind(item.attribute), a.value, b.value, item.kinds)
                )
        en = text.en + "".join(f" ({c.text(rcfg, 'en')})" for c in changes)
        te = (text.te or "") + "".join(f" ({c.text(rcfg, 'te')})" for c in changes)
        label = labels.attributes.get(item.attribute, (item.attribute, ""))
        owner = rcfg.owner_labels[item.owner]
        issues.append(
            slip_page.SlipIssue(
                field=slip_page.SlipText(label[0], label[1]),
                text=slip_page.SlipText(en, te),
                owner=slip_page.SlipText(owner.en, owner.te),
            )
        )
    return slip_page.SlipStudent(
        name=(ref.display_name if ref else None) or NOT_RECORDED,
        admission_no=(ref.admission_no if ref else None) or NOT_RECORDED,
        class_section=(ref.class_section if ref else None) or NOT_RECORDED,
        status=student.status,
        rows=rows,
        issues=issues,
    )


def slips(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    *,
    student_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    include_ready: bool = True,
) -> str:
    """Printable parent verification slips (A4 HTML), for one student or one section
    (US-504 AC2). Audit ``dq.readiness.slips_printed`` (counts and ids only)."""
    profile = readiness_profile(profile_key)
    if (student_id is None) == (section_id is None):
        raise ValidationFailed(
            [{"field": "student_id", "code": "one_of", "message_key": "errors.dq.slip_target"}],
            detail="Choose one student or one section.",
        )
    ids: list[uuid.UUID] = []
    target = uuid.UUID(int=0)
    if student_id is not None:
        students.ensure_in_scope(session, ctx, student_id, READ)
        ids, target = [student_id], student_id
    elif section_id is not None:
        _section(session, ctx, section_id)
        ids, target = _in_reach(session, ctx, [section_id])[:MAX_SLIPS], section_id
    assessed = _assess(session, profile, ids, force=student_id is not None)
    refs = students.summaries(session, ctx, ids)
    ordered = sorted(ids, key=lambda s: ((refs[s].display_name or "") if s in refs else "", str(s)))
    if not include_ready:
        ordered = [s for s in ordered if assessed[s].status != "ready"]
    spec = _spec(profile)
    columns = list(dict.fromkeys(s for f in spec.fields for s in f.sources))
    rcfg = rd.load_readiness_config()
    labels = dq._Labels.load(session)
    # One scope read for the whole slip set (student.read_sensitive), not one per student.
    sensitive_reach = (
        frozenset(students.list_students_in_scope(session, ctx, permissions=(SENSITIVE,)))
        if ctx.has(SENSITIVE)
        else frozenset()
    )
    shown, hidden = _visible(session, True), _visible(session, False)
    revealed = 0
    printed: list[slip_page.SlipStudent] = []
    for sid in ordered:
        reveal = sid in sensitive_reach
        revealed += 1 if reveal else 0
        vis = shown if reveal else hidden
        printed.append(
            _slip_student(
                assessed[sid],
                profile=profile,
                vis=vis,
                labels=labels,
                rcfg=rcfg,
                ref=refs.get(sid),
                columns=columns,
            )
        )
    data = slip_page.SlipData(
        school_name=tenancy.get_tenant(session).name,
        profile=slip_page.SlipText(profile.label_en, profile.label_te),
        verified=profile.verified,
        columns=[
            slip_page.SlipText(rcfg.sources[s].en.capitalize(), rcfg.sources[s].te) for s in columns
        ],
        students=printed,
        printed_at=dt.datetime.now(dt.UTC),
    )
    page = slip_page.render(data)
    summary: dict[str, Any] = {
        "profile_key": profile.key,
        "students": len(printed),
        "sensitive_shown": revealed,
    }
    if section_id is not None:
        summary["section_id"] = str(section_id)
    dq._audit(
        session,
        action="dq.readiness.slips_printed",
        resource_type="student" if student_id is not None else "section",
        resource_id=target,
        summary=summary,
    )
    log.info("dq.readiness.slips_printed", count=len(printed))
    return page


__all__ = [
    "MANAGE",
    "READ",
    "readiness_profile",
    "request_run",
    "section_students",
    "slips",
    "student_detail",
    "summary",
]
