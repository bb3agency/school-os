"""Student timeline and early warning: public API (M5; US-1704..US-1709; FR-EW-*).

**Purpose limit (08 §4 PRV-003..005, invariant 14).** Everything here is restricted (C3) and
exists only for the school's educational activities and the safety of its enrolled children:

- Visible only to callers holding ``insights.read`` AND ``student.read_sensitive`` whose scopes
  both reach the student (the student's class teacher, the principal; PRV-004). Any other
  student, flag or note is ``NotFound`` (404), exactly like an id of another school.
- Rules, never AI: indicators and flags come from :mod:`app.insights.engine` with versioned
  thresholds (``rules.yaml`` + the school's overrides within bounds). No note, flag or indicator
  is ever sent to an AI provider, exported other than in the school's full data export, or
  aggregated across schools.
- Every flag needs a person (PRV-005): a flag only asks its owner to act; actions and closing
  are recorded by people; nothing changes a student's record.

**Flags.** Raised by the rules job (daily, and after attendance/marks writes through the outbox)
for students actively enrolled in the current academic year, at most one open flag per student
and rule and never twice for the same basis (FR-EW-003). Owner: the class teacher of the
student's current section when that member may act for the student, else unassigned (the
principal is notified). Due date: raised + 7 days. Actions (with an optional encrypted note)
move ``open`` to ``in_progress``; closing needs a reason.

**Audit (invariant 7).** Writes and every read of restricted insights (flag lists and details,
notes, timelines) are audited in the same transaction with ids, codes and counts only; logs carry
ids and counts only (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.academics import service as academics
from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.certificates import service as certificates
from app.core import purge as purging
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.identity import service as identity
from app.insights import crypto, engine
from app.insights import repository as repo
from app.insights.config import (
    RULE_KEYS,
    InsightsConfig,
    RuleKey,
    RuleSetting,
    effective,
    load_config,
)
from app.insights.models import BehaviourNote, FlagAction, InsightFlag
from app.insights.schemas import (
    ActionIn,
    ActionOut,
    AssignIn,
    AttendanceIndicatorOut,
    AttendanceMonthEvent,
    BehaviourIndicatorOut,
    CertificateEvent,
    CloseIn,
    CourseIndicatorOut,
    DueFilter,
    EnrolmentEvent,
    ErasedOut,
    EraseIn,
    ExamEvent,
    FlagStatus,
    FlagView,
    IndicatorName,
    IndicatorsOut,
    InsightFlagDetail,
    InsightFlagOut,
    InsightStudentRef,
    InsightSummaryOut,
    ManualFlagIn,
    NoteIn,
    NoteOut,
    OwnerOut,
    RuleSettingIn,
    RuleSettingOut,
    SettingsIn,
    SettingsOut,
    StaffRef,
    TimelineItem,
    TimelineOut,
)
from app.notifications import service as notifications
from app.ops import service as ops
from app.students import rotation as key_rotation
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import SectionOut as TenancySection

log = get_logger(__name__)

READ: Final = "insights.read"
NOTE: Final = "insights.note"
ACT: Final = "insights.act"
MANAGE: Final = "insights.manage"
SENSITIVE: Final = students.SENSITIVE
STUDENT_READ: Final = students.READ
CERTIFICATE_READ: Final = "certificate.read"
OWNER_NEEDS: Final = frozenset({STUDENT_READ, SENSITIVE, READ, ACT})

EVALUATE_EVENT: Final = "insights.evaluate.requested"
EVALUATE_TASK: Final = "insights.evaluate_students"
OPEN: Final = ("open", "in_progress")
IST: Final = ZoneInfo("Asia/Kolkata")
SYSTEM_ID: Final = uuid.UUID(int=0)
AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
MASK: Final = "••••"
_USER_PAGE: Final = 200

ops.register_outbox_route(academics.RECORDS_CHANGED_EVENT, EVALUATE_TASK)
ops.register_outbox_route(EVALUATE_EVENT, EVALUATE_TASK)


# --- helpers --------------------------------------------------------------------------------------


def today_ist(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.UTC)).astimezone(IST).date()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _error(field_name: str, code: str) -> dict[str, str]:
    return {"field": field_name, "code": code, "message_key": f"errors.{code}"}


def _refuse_aadhaar(values: Mapping[str, str | None]) -> None:
    """Typed text is never stored with a full Aadhaar number (invariant 4)."""
    fields = sorted(k for k, v in values.items() if v and contains_full_aadhaar(v))
    if fields:
        raise ValidationFailed(
            [
                {"field": f, "code": AADHAAR_CODE, "message_key": "errors.aadhaar_last4_only"}
                for f in fields
            ],
            detail="Don't enter Aadhaar numbers. Enter only the last 4 digits.",
        )


def _audit(
    session: Session,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    summary: Mapping[str, Any],
    *,
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=None if system else _request_id(),
    )


def _check_version(current: int, expected: int) -> None:
    if current != expected:
        raise PreconditionFailed(
            "Someone else changed this in the meantime. Reload it and try again.",
            code="version_mismatch",
        )


# --- scope (FR-EW-011) ----------------------------------------------------------------------------


def _scope_ids(session: Session, ctx: UserContext) -> set[uuid.UUID] | None:
    """Students whose insights the caller may see (None = the whole school)."""
    needed = (STUDENT_READ, READ, SENSITIVE)
    if all(ctx.has(p) and ctx.scope_for(p).school_wide for p in needed):
        return None
    return set(students.list_students_in_scope(session, ctx, permissions=(READ, SENSITIVE)))


def _ensure_student(session: Session, ctx: UserContext, student_id: uuid.UUID, *extra: str) -> None:
    """404 unless the caller reaches the student with every insights permission needed."""
    if not all(ctx.has(p) for p in (READ, SENSITIVE, *extra)):
        raise NotFound("Student not found")
    students.ensure_in_scope(session, ctx, student_id, READ, SENSITIVE, *extra)


def _visible_flag(
    session: Session, ctx: UserContext, flag_id: uuid.UUID, *extra: str, lock: bool = False
) -> InsightFlag:
    flag = repo.get_flag(session, flag_id, lock=lock)
    if flag is None:
        raise NotFound("Flag not found")
    try:
        _ensure_student(session, ctx, flag.student_id, *extra)
    except NotFound:
        raise NotFound("Flag not found") from None
    return flag


def _current_section(session: Session, student_id: uuid.UUID) -> TenancySection | None:
    placement = students.current_placements(session, [student_id]).get(student_id)
    return tenancy.get_section(session, placement) if placement is not None else None


# --- people ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Staff:
    membership_id: uuid.UUID
    display_name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    scoped: bool
    school: bool
    section_ids: frozenset[uuid.UUID]
    class_ids: frozenset[uuid.UUID]

    def may_act_for(self, section: TenancySection) -> bool:
        if not self.permissions >= OWNER_NEEDS:
            return False
        if not self.scoped or self.school:
            return True
        return section.id in self.section_ids or section.class_id in self.class_ids


@dataclass
class _Owners:
    """Active staff and who may own flags of which section (built once per request/run)."""

    session: Session
    _staff: dict[uuid.UUID, _Staff] | None = field(default=None, init=False)

    def staff(self) -> dict[uuid.UUID, _Staff]:
        if self._staff is None:
            self._staff = _load_staff(self.session)
        return self._staff

    def default_owner(self, section: TenancySection) -> uuid.UUID | None:
        """FR-EW-004: the section's class teacher, when active and able to act for it."""
        teacher = section.class_teacher_membership_id
        member = self.staff().get(teacher) if teacher is not None else None
        return teacher if member is not None and member.may_act_for(section) else None

    def eligible(self, section: TenancySection) -> list[_Staff]:
        return [s for s in self.staff().values() if s.may_act_for(section)]


def _load_staff(session: Session) -> dict[uuid.UUID, _Staff]:
    roles = {r.key: r for r in identity.list_roles(session)}
    now = _now()
    out: dict[uuid.UUID, _Staff] = {}
    after: uuid.UUID | None = None
    while True:
        users, after = identity.list_users(session, limit=_USER_PAGE, after=after)
        for user in users:
            if user.status != "active" or (user.expires_at is not None and user.expires_at <= now):
                continue
            held = [roles[k] for k in user.roles if k in roles]
            permissions = frozenset(p for r in held for p in r.permissions)
            out[user.membership_id] = _Staff(
                membership_id=user.membership_id,
                display_name=user.display_name,
                roles=tuple(sorted(user.roles)),
                permissions=permissions,
                scoped=any(r.scoped for r in held if ACT in r.permissions),
                school=any(s.type == "school" for s in user.scopes),
                section_ids=frozenset(
                    s.ref for s in user.scopes if s.type == "section" and s.ref is not None
                ),
                class_ids=frozenset(
                    s.ref for s in user.scopes if s.type == "class" and s.ref is not None
                ),
            )
        if after is None:
            return out


def _members(session: Session, membership_ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {m for m in membership_ids if m is not None}
    return identity.member_display_names(session, wanted) if wanted else {}


def _member(names: Mapping[uuid.UUID, str], membership_id: uuid.UUID | None) -> StaffRef | None:
    if membership_id is None:
        return None
    return StaffRef(membership_id=membership_id, display_name=names.get(membership_id))


def _user_members(
    session: Session, user_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, StaffRef]:
    wanted = {u for u in user_ids if u is not None}
    found = identity.members_for_users(session, wanted) if wanted else {}
    return {
        user: StaffRef(membership_id=membership, display_name=name)
        for user, (membership, name) in found.items()
    }


# --- students and sections ------------------------------------------------------------------------


def _section_labels(session: Session) -> dict[uuid.UUID, str]:
    classes = {c.id: c.code for c in tenancy.list_classes(session)}
    return {
        s.id: f"{classes.get(s.class_id, '?')}-{s.name}" for s in tenancy.list_sections(session)
    }


def _student_refs(
    session: Session, student_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, InsightStudentRef]:
    """Name, admission number and current section of students the caller already reached."""
    if not student_ids:
        return {}
    ids = list(student_ids)
    values = students.canonical_values(session, ids, ["full_name", "admission_no"])
    placements = students.current_placements(session, ids)
    labels = _section_labels(session)
    out: dict[uuid.UUID, InsightStudentRef] = {}
    for sid in ids:
        vals = values.get(sid, {})
        name, adm = vals.get("full_name"), vals.get("admission_no")
        section = placements.get(sid)
        out[sid] = InsightStudentRef(
            id=sid,
            full_name=name.value if name else None,
            admission_no=adm.value if adm else None,
            section_label=labels.get(section) if section is not None else None,
        )
    return out


# --- flag output ----------------------------------------------------------------------------------


def _overdue(flag: InsightFlag, today: dt.date) -> bool:
    return flag.status != "closed" and flag.first_action_at is None and flag.due_on < today


def _flag_out(
    flag: InsightFlag,
    refs: Mapping[uuid.UUID, InsightStudentRef],
    names: Mapping[uuid.UUID, str],
    users: Mapping[uuid.UUID, StaffRef],
    today: dt.date,
) -> InsightFlagOut:
    return InsightFlagOut(
        id=flag.id,
        student=refs[flag.student_id],
        indicator=flag.indicator,
        rule=flag.rule,
        evidence=dict(flag.evidence),
        status=flag.status,
        owner=_member(names, flag.owner_membership_id),
        raised_on=flag.raised_on,
        due_on=flag.due_on,
        overdue=_overdue(flag, today),
        actioned=flag.first_action_at is not None,
        first_action_at=flag.first_action_at,
        closed_at=flag.closed_at,
        close_reason=flag.close_reason,
        raised_by=users.get(flag.raised_by) if flag.raised_by is not None else None,
        version=flag.version,
    )


def _action_out(session: Session, action: FlagAction, names: Mapping[uuid.UUID, str]) -> ActionOut:
    return ActionOut(
        id=action.id,
        kind=action.kind,
        acted_on=action.acted_on,
        note=crypto.decrypt_action(session, action.note_ciphertext, action.id),
        by=_member(names, action.created_by_membership),
        created_at=action.created_at,
    )


def _details(session: Session, flags: Sequence[InsightFlag]) -> list[InsightFlagDetail]:
    if not flags:
        return []
    today = today_ist()
    actions = repo.actions_of(session, [f.id for f in flags])
    refs = _student_refs(session, {f.student_id for f in flags})
    names = _members(
        session,
        [f.owner_membership_id for f in flags]
        + [a.created_by_membership for acts in actions.values() for a in acts],
    )
    users = _user_members(session, [f.raised_by for f in flags])
    return [
        InsightFlagDetail(
            **_flag_out(f, refs, names, users, today).model_dump(),
            actions=[_action_out(session, a, names) for a in actions.get(f.id, [])],
        )
        for f in flags
    ]


def _viewed(
    session: Session, view: str, resource_type: str, resource_id: uuid.UUID, **counts: Any
) -> None:
    """FR-EW-012: reading restricted insights is audited (ids and counts only)."""
    _audit(session, "insights.viewed", resource_type, resource_id, {"view": view, **counts})


# --- rule settings --------------------------------------------------------------------------------


def _settings(session: Session) -> dict[RuleKey, RuleSetting]:
    row = repo.get_settings(session)
    return effective(row.rules if row is not None else {})


def get_settings(session: Session, ctx: UserContext) -> SettingsOut:
    """The school's rules with their thresholds and bounds (``insights.read``): people who get
    flags can see exactly why a flag is raised (explainable, FR-EW-001)."""
    cfg = load_config()
    row = repo.get_settings(session)
    current = effective(row.rules if row is not None else {}, cfg)
    return SettingsOut(
        rules=[
            RuleSettingOut(
                key=key,
                indicator=s.indicator,
                enabled=s.enabled,
                can_disable=s.spec.can_disable,
                threshold=s.threshold,
                default=s.spec.threshold.default,
                min=s.spec.threshold.min,
                max=s.spec.threshold.max,
                window=s.spec.window,
                min_days=s.spec.min_days,
            )
            for key, s in current.items()
        ],
        rules_version=cfg.version,
        due_days=cfg.flags.due_days,
        version=row.version if row is not None else 0,
        updated_at=row.updated_at if row is not None else None,
    )


def _apply_change(
    stored: dict[str, dict[str, Any]], key: str, change: RuleSettingIn, cfg: InsightsConfig
) -> list[dict[str, str]]:
    """Apply one rule's change to the stored overrides (only differences from the defaults are
    kept); returns the problems found."""
    if key not in RULE_KEYS:
        return [_error(f"rules.{key}", "unknown_rule")]
    spec = cfg.rules[key]
    own = stored.setdefault(key, {})
    errors: list[dict[str, str]] = []
    if change.threshold is not None:
        if not spec.threshold.min <= change.threshold <= spec.threshold.max:
            errors.append(_error(f"rules.{key}.threshold", "threshold_out_of_bounds"))
        elif change.threshold == spec.threshold.default:
            own.pop("threshold", None)
        else:
            own["threshold"] = change.threshold
    if change.enabled is False and not spec.can_disable:
        errors.append(_error(f"rules.{key}.enabled", "rule_required"))
    elif change.enabled is False:
        own["enabled"] = False
    elif change.enabled:
        own.pop("enabled", None)
    return errors


def update_settings(
    session: Session, ctx: UserContext, data: SettingsIn, version: int
) -> SettingsOut:
    """Change thresholds and switches within the bounds of ``rules.yaml`` (``insights.manage``,
    step-up; ``If-Match``; FR-EW-014). 422 ``unknown_rule``, ``threshold_out_of_bounds`` or
    ``rule_required``. Only differences from the defaults are stored. Audit
    ``insights.settings_updated`` with each change (rule, field, from, to)."""
    if not (ctx.has(MANAGE) and ctx.scope_for(MANAGE).school_wide):
        raise Forbidden()
    cfg = load_config()
    row = repo.get_settings(session, lock=True)
    _check_version(row.version if row is not None else 0, version)
    stored: dict[str, dict[str, Any]] = {
        k: dict(v) for k, v in (row.rules if row is not None else {}).items() if isinstance(v, dict)
    }
    before = effective(stored, cfg)
    errors: list[dict[str, str]] = []
    for key, change in data.rules.items():
        errors.extend(_apply_change(stored, key, change, cfg))
    if errors:
        raise ValidationFailed(errors)
    clean = {k: v for k, v in stored.items() if v}
    after = effective(clean, cfg)
    changes: list[dict[str, Any]] = []
    for key in RULE_KEYS:
        for attr in ("threshold", "enabled"):
            old, new = getattr(before[key], attr), getattr(after[key], attr)
            if old != new:
                changes.append({"rule": key, "field": attr, "from": old, "to": new})
    if not changes:
        return get_settings(session, ctx)
    saved = repo.save_settings(session, clean, ctx.user_id, new_id=new_id())
    _audit(session, "insights.settings_updated", "insight_settings", saved.id, {"changes": changes})
    return get_settings(session, ctx)


# --- rule evaluation (FR-EW-001..005) -------------------------------------------------------------


def _notify_raised(
    session: Session, tenant_id: uuid.UUID, flag_id: uuid.UUID, rule: str, owner: uuid.UUID | None
) -> None:
    notifications.notify(
        session,
        tenant_id=tenant_id,
        recipients=[owner] if owner is not None else notifications.PermissionSelector(MANAGE),
        template_key="insights.flag_raised",
        params={"flag_id": str(flag_id), "rule": rule},
        resource_id=flag_id,
        dedupe_key=f"insights.flag_raised:{flag_id}",
    )


def evaluate(
    session: Session,
    student_ids: Collection[uuid.UUID] | None = None,
    *,
    today: dt.date | None = None,
) -> int:
    """Raise the flags the rules find for students actively enrolled in the current academic
    year (all of them when ``student_ids`` is None); system job in the school's
    ``tenant_session``. Idempotent (FR-EW-003). Returns how many flags were raised."""
    day = today or today_ist()
    placements = students.current_placements(session, student_ids)
    year = tenancy.get_current_academic_year(session)
    if not placements or year is None:
        return 0
    cfg = load_config()
    settings = _settings(session)
    ids = list(placements)
    history = academics.attendance_history(
        session, ids, since=day - dt.timedelta(days=cfg.evaluation.history_days)
    )
    results = academics.exam_results(session, ids, academic_year_id=year.id)
    window = max(cfg.rules["behaviour_concerns"].window or 30, 1)
    concerns = repo.concern_dates(session, ids, day - dt.timedelta(days=window))
    opened, raised = repo.open_keys(session, ids)
    sections = {s.id: s for s in tenancy.list_sections(session, academic_year_id=year.id)}
    owners = _Owners(session)
    tenant_id = repo.current_tenant_id(session)
    count = 0
    for sid in ids:
        section = sections.get(placements[sid])
        if section is None:  # pragma: no cover - placements come from current-year sections
            continue
        facts = engine.Facts(
            marks=history.get(sid, []),
            results=results.get(sid, []),
            concern_dates=concerns.get(sid, []),
        )
        for finding in engine.findings(facts, settings, day):
            if (sid, finding.rule) in opened or (sid, finding.rule, finding.basis) in raised:
                continue
            owner = owners.default_owner(section)
            flag_id = repo.insert_flag(
                session,
                {
                    "id": new_id(),
                    "student_id": sid,
                    "section_id": section.id,
                    "indicator": finding.indicator,
                    "rule": finding.rule,
                    "rules_version": cfg.version,
                    "basis": finding.basis,
                    "evidence": finding.evidence,
                    "owner_membership_id": owner,
                    "raised_on": day,
                    "due_on": day + dt.timedelta(days=cfg.flags.due_days),
                },
            )
            if flag_id is None:
                continue
            opened.add((sid, finding.rule))
            raised.add((sid, finding.rule, finding.basis))
            count += 1
            _audit(
                session,
                "insights.flag_raised",
                "insight_flag",
                flag_id,
                {
                    "student_id": sid,
                    "rule": finding.rule,
                    "indicator": finding.indicator,
                    "rules_version": cfg.version,
                    "owner_membership_id": owner,
                    "assigned": owner is not None,
                },
                system=True,
            )
            _notify_raised(session, tenant_id, flag_id, finding.rule, owner)
    if count:
        log.info("insights.flags_raised", count=count)
    return count


def queue_evaluation(session: Session, student_ids: Iterable[uuid.UUID]) -> None:
    ids = sorted(set(student_ids), key=str)
    chunk = academics.EVENT_CHUNK
    for start in range(0, len(ids), chunk):
        ops.enqueue_event(session, EVALUATE_EVENT, {"student_ids": ids[start : start + chunk]})


# --- flags: reads ---------------------------------------------------------------------------------


def list_flags(
    session: Session,
    ctx: UserContext,
    *,
    view: FlagView,
    status: FlagStatus | None,
    indicator: IndicatorName | None,
    section_id: uuid.UUID | None,
    student_id: uuid.UUID | None,
    due: DueFilter | None,
    limit: int,
    cursor: str | None,
) -> Page[InsightFlagOut]:
    """``mine``: flags I own; ``all``: every flag in my scope (class teachers: their sections'
    students; the principal: the school). Without ``status``: open and in progress. Soonest due
    first. Audit ``insights.viewed`` (view ``flags``, count)."""
    scope = _scope_ids(session, ctx)
    after = decode_cursor(cursor)
    position: tuple[dt.date, uuid.UUID] | None = None
    if after is not None:
        try:
            position = (dt.date.fromisoformat(str(after["d"])), uuid.UUID(str(after["i"])))
        except (KeyError, ValueError):
            raise ValidationFailed([_error("cursor", "invalid")]) from None
    today = today_ist()
    rows = repo.list_flags(
        session,
        student_ids=scope,
        owner=ctx.membership_id if view == "mine" else None,
        statuses=[status] if status else list(OPEN),
        indicator=indicator,
        section_id=section_id,
        student_id=student_id,
        overdue_before=today if due == "overdue" else None,
        due_until=today + dt.timedelta(days=2) if due == "due_soon" else None,
        after=position,
        limit=limit + 1,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    refs = _student_refs(session, {f.student_id for f in rows})
    names = _members(session, [f.owner_membership_id for f in rows])
    users = _user_members(session, [f.raised_by for f in rows])
    data = [_flag_out(f, refs, names, users, today) for f in rows]
    _viewed(session, "flags", "membership", ctx.membership_id, count=len(data), list=view)
    next_cursor = (
        encode_cursor({"d": rows[-1].due_on.isoformat(), "i": str(rows[-1].id)}) if more else None
    )
    return Page[InsightFlagOut](data=data, next_cursor=next_cursor)


def get_flag(session: Session, ctx: UserContext, flag_id: uuid.UUID) -> InsightFlagDetail:
    """One flag with its action log (404 outside my scope). Audit ``insights.viewed``."""
    flag = _visible_flag(session, ctx, flag_id)
    detail = _details(session, [flag])[0]
    _viewed(
        session,
        "flag",
        "insight_flag",
        flag.id,
        student_id=flag.student_id,
        actions=len(detail.actions),
    )
    return detail


def summary(session: Session, ctx: UserContext, since: dt.date | None = None) -> InsightSummaryOut:
    """Counts only for the caller's scope in this school (FR-EW-015; the M5 exit metric:
    flags first acted on by their due date)."""
    today = today_ist()
    start = since or today - dt.timedelta(days=30)
    rows = repo.summary_rows(session, _scope_ids(session, ctx), start)
    raised = [r for r in rows if r[1] >= start]
    on_time = late = not_actioned = 0
    for _status, _raised_on, due_on, first_action_at, _created in raised:
        if first_action_at is None:
            not_actioned += 1
        elif first_action_at.astimezone(IST).date() <= due_on:
            on_time += 1
        else:
            late += 1
    return InsightSummaryOut(
        since=start,
        raised=len(raised),
        actioned_on_time=on_time,
        actioned_late=late,
        not_actioned=not_actioned,
        overdue=sum(1 for r in rows if r[0] == "open" and r[3] is None and r[2] < today),
        open=sum(1 for r in rows if r[0] in OPEN),
    )


# --- flags: writes --------------------------------------------------------------------------------


def _insert_action(
    session: Session,
    ctx: UserContext,
    flag_id: uuid.UUID,
    *,
    kind: str,
    day: dt.date,
    note: str | None,
) -> FlagAction:
    action_id = new_id()
    blob, key_version = crypto.encrypt_action(session, note, action_id)
    return repo.insert_action(
        session,
        {
            "id": action_id,
            "flag_id": flag_id,
            "kind": kind,
            "acted_on": day,
            "note_ciphertext": blob,
            "key_version": key_version,
            "created_by": ctx.user_id,
            "created_by_membership": ctx.membership_id,
        },
    )


def raise_flag(
    session: Session, ctx: UserContext, student_id: uuid.UUID, data: ManualFlagIn
) -> InsightFlagDetail:
    """A person raises a concern for a student in their scope (``insights.act``; FR-EW-008).
    Owner: the section's class teacher when they may act, else the person raising it. 422
    ``not_enrolled`` for students without a current-year section. Audit
    ``insights.flag_raised`` (rule ``manual``)."""
    _ensure_student(session, ctx, student_id, ACT)
    _refuse_aadhaar({"note": data.note})
    section = _current_section(session, student_id)
    if section is None:
        raise ValidationFailed([_error("student_id", "not_enrolled")])
    cfg = load_config()
    day = today_ist()
    owner = _Owners(session).default_owner(section) or ctx.membership_id
    flag_id = new_id()
    inserted = repo.insert_flag(
        session,
        {
            "id": flag_id,
            "student_id": student_id,
            "section_id": section.id,
            "indicator": data.indicator,
            "rule": "manual",
            "rules_version": cfg.version,
            "basis": f"manual:{flag_id}",
            "evidence": {},
            "owner_membership_id": owner,
            "raised_on": day,
            "due_on": day + dt.timedelta(days=cfg.flags.due_days),
            "raised_by": ctx.user_id,
        },
    )
    if inserted is None:  # pragma: no cover - a fresh id and basis
        raise Conflict("The flag could not be raised. Try again.", code="flag_conflict")
    _insert_action(session, ctx, flag_id, kind="raised", day=day, note=data.note)
    _audit(
        session,
        "insights.flag_raised",
        "insight_flag",
        flag_id,
        {
            "student_id": student_id,
            "rule": "manual",
            "indicator": data.indicator,
            "owner_membership_id": owner,
            "has_note": data.note is not None,
        },
    )
    if owner != ctx.membership_id:
        _notify_raised(session, ctx.tenant_id, flag_id, "manual", owner)
    flag = repo.get_flag(session, flag_id)
    if flag is None:  # pragma: no cover - inserted above in this transaction
        raise NotFound("Flag not found")
    return _details(session, [flag])[0]


def add_action(
    session: Session, ctx: UserContext, flag_id: uuid.UUID, data: ActionIn
) -> InsightFlagDetail:
    """Record what was done (``insights.act``; FR-EW-007): kind, date (not in the future, not
    before the flag), optional note (encrypted). The first action marks the flag actioned and
    in progress. 409 ``flag_closed``. Audit ``insights.flag_action_added``."""
    flag = _visible_flag(session, ctx, flag_id, ACT, lock=True)
    if flag.status == "closed":
        raise Conflict("This flag is closed.", code="flag_closed")
    today = today_ist()
    day = data.acted_on or today
    errors: list[dict[str, str]] = []
    if day > today:
        errors.append(_error("acted_on", "future_date"))
    elif day < flag.raised_on:
        errors.append(_error("acted_on", "before_flag"))
    if errors:
        raise ValidationFailed(errors)
    _refuse_aadhaar({"note": data.note})
    _insert_action(session, ctx, flag.id, kind=data.kind, day=day, note=data.note)
    first = flag.first_action_at is None
    values: dict[str, Any] = (
        {"first_action_at": _now(), "status": "in_progress"} if first else {"status": flag.status}
    )
    flag = repo.update_flag(session, flag.id, values)
    _audit(
        session,
        "insights.flag_action_added",
        "insight_flag",
        flag.id,
        {"kind": data.kind, "has_note": data.note is not None, "first_action": first},
    )
    return _details(session, [flag])[0]


def close_flag(
    session: Session, ctx: UserContext, flag_id: uuid.UUID, data: CloseIn, version: int
) -> InsightFlagDetail:
    """Close a flag with a reason (``insights.act``; ``If-Match``). Closing counts as acting on
    it. 409 ``flag_closed``. Audit ``insights.flag_closed``."""
    flag = _visible_flag(session, ctx, flag_id, ACT, lock=True)
    _check_version(flag.version, version)
    if flag.status == "closed":
        raise Conflict("This flag is already closed.", code="flag_closed")
    _refuse_aadhaar({"note": data.note})
    now = _now()
    _insert_action(session, ctx, flag.id, kind="closed", day=today_ist(), note=data.note)
    first = flag.first_action_at is None
    flag = repo.update_flag(
        session,
        flag.id,
        {
            "status": "closed",
            "closed_at": now,
            "closed_by": ctx.user_id,
            "close_reason": data.reason,
            "first_action_at": flag.first_action_at or now,
        },
    )
    _audit(
        session,
        "insights.flag_closed",
        "insight_flag",
        flag.id,
        {"reason": data.reason, "rule": flag.rule, "first_action": first},
    )
    return _details(session, [flag])[0]


def owners(session: Session, ctx: UserContext, flag_id: uuid.UUID) -> list[OwnerOut]:
    """Staff who may own this flag: active, and allowed to see and act on the student's
    insights in the student's current section (``insights.manage``)."""
    flag = _visible_flag(session, ctx, flag_id, MANAGE)
    section = _current_section(session, flag.student_id) or tenancy.get_section(
        session, flag.section_id
    )
    return sorted(
        (
            OwnerOut(
                membership_id=s.membership_id, display_name=s.display_name, roles=list(s.roles)
            )
            for s in _Owners(session).eligible(section)
        ),
        key=lambda o: (o.display_name.casefold(), str(o.membership_id)),
    )


def assign_flag(
    session: Session, ctx: UserContext, flag_id: uuid.UUID, data: AssignIn, version: int
) -> InsightFlagDetail:
    """Give a flag another owner (``insights.manage``, step-up; ``If-Match``; FR-EW-014). 422
    ``owner_not_eligible``; 409 ``flag_closed``. The new owner is told in the app. Audit
    ``insights.flag_assigned``."""
    flag = _visible_flag(session, ctx, flag_id, MANAGE, lock=True)
    _check_version(flag.version, version)
    if flag.status == "closed":
        raise Conflict("This flag is closed.", code="flag_closed")
    section = _current_section(session, flag.student_id) or tenancy.get_section(
        session, flag.section_id
    )
    member = _Owners(session).staff().get(data.owner_membership_id)
    if member is None or not member.may_act_for(section):
        raise ValidationFailed([_error("owner_membership_id", "owner_not_eligible")])
    if data.owner_membership_id == flag.owner_membership_id:
        return _details(session, [flag])[0]
    flag = repo.update_flag(session, flag.id, {"owner_membership_id": data.owner_membership_id})
    _audit(
        session,
        "insights.flag_assigned",
        "insight_flag",
        flag.id,
        {"owner_membership_id": data.owner_membership_id},
    )
    if data.owner_membership_id != ctx.membership_id:
        notifications.notify(
            session,
            tenant_id=ctx.tenant_id,
            recipients=[data.owner_membership_id],
            template_key="insights.flag_assigned",
            params={"flag_id": str(flag.id)},
            resource_id=flag.id,
            dedupe_key=f"insights.flag_assigned:{flag.id}:{flag.version}",
        )
    return _details(session, [flag])[0]


def erase_flag(session: Session, ctx: UserContext, flag_id: uuid.UUID, data: EraseIn) -> ErasedOut:
    """Erase a flag and its action log on a parent's request or when raised in error
    (``insights.manage``, step-up; FR-EW-014, 08 §3 erasure). Audit ``insights.flag_erased``
    (reason and rule; never text)."""
    flag = _visible_flag(session, ctx, flag_id, MANAGE, lock=True)
    repo.delete_flag(session, flag.id)
    _audit(
        session,
        "insights.flag_erased",
        "insight_flag",
        flag.id,
        {"reason": data.reason, "rule": flag.rule, "student_id": flag.student_id},
    )
    return ErasedOut(id=flag.id)


def send_reminders(session: Session, *, today: dt.date | None = None) -> int:
    """Daily (FR-EW-006): one ``insights.flag_overdue`` per open flag past its due date that
    nobody acted on, to its owner (or the principal when unassigned); reruns never repeat."""
    day = today or today_ist()
    tenant_id = repo.current_tenant_id(session)
    sent = 0
    for flag in repo.overdue_flags(session, day):
        sent += notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=(
                [flag.owner_membership_id]
                if flag.owner_membership_id is not None
                else notifications.PermissionSelector(MANAGE)
            ),
            template_key="insights.flag_overdue",
            params={"flag_id": str(flag.id)},
            resource_id=flag.id,
            dedupe_key=f"insights.flag_overdue:{flag.id}",
        )
    return sent


# --- behaviour notes (FR-EW-010) ------------------------------------------------------------------


def _note_out(session: Session, note: BehaviourNote, names: Mapping[uuid.UUID, str]) -> NoteOut:
    return NoteOut(
        id=note.id,
        student_id=note.student_id,
        category=note.category,
        noted_on=note.noted_on,
        text=crypto.decrypt_note(session, note.body_ciphertext, note.id),
        by=_member(names, note.created_by_membership),
        created_at=note.created_at,
    )


def list_notes(session: Session, ctx: UserContext, student_id: uuid.UUID) -> list[NoteOut]:
    """A student's behaviour notes, newest first (``insights.read``; 404 outside scope).
    Audit ``insights.viewed`` (view ``notes``)."""
    _ensure_student(session, ctx, student_id)
    rows = repo.notes_of(session, student_id)
    names = _members(session, [n.created_by_membership for n in rows])
    out = [_note_out(session, n, names) for n in rows]
    _viewed(session, "notes", "student", student_id, count=len(out))
    return out


def add_note(session: Session, ctx: UserContext, student_id: uuid.UUID, data: NoteIn) -> NoteOut:
    """Write a behaviour note (``insights.note``): category, date (today by default; not in the
    future, at most the configured days back), up to 500 characters, stored encrypted. 422
    ``aadhaar_full_number_rejected``, ``future_date``, ``too_old`` or ``not_enrolled``. A
    concern note asks the rules to look at the student again. Audit ``insights.note_added``."""
    _ensure_student(session, ctx, student_id, NOTE)
    cfg = load_config().notes
    today = today_ist()
    day = data.noted_on or today
    errors: list[dict[str, str]] = []
    if day > today:
        errors.append(_error("noted_on", "future_date"))
    elif day < today - dt.timedelta(days=cfg.max_backdate_days):
        errors.append(_error("noted_on", "too_old"))
    if len(data.text) > cfg.max_chars:
        errors.append(_error("text", "too_long"))
    if errors:
        raise ValidationFailed(errors)
    _refuse_aadhaar({"text": data.text})
    section = _current_section(session, student_id)
    if section is None:
        raise ValidationFailed([_error("student_id", "not_enrolled")])
    note_id = new_id()
    blob, key_version = crypto.encrypt_note(session, data.text, note_id)
    note = repo.insert_note(
        session,
        {
            "id": note_id,
            "student_id": student_id,
            "section_id": section.id,
            "category": data.category,
            "noted_on": day,
            "body_ciphertext": blob,
            "key_version": key_version,
            "created_by": ctx.user_id,
            "created_by_membership": ctx.membership_id,
        },
    )
    _audit(
        session,
        "insights.note_added",
        "student",
        student_id,
        {"note_id": note.id, "category": note.category},
    )
    if note.category == "concern":
        queue_evaluation(session, [student_id])
    return _note_out(session, note, _members(session, [ctx.membership_id]))


def erase_note(session: Session, ctx: UserContext, note_id: uuid.UUID, data: EraseIn) -> ErasedOut:
    """Erase a note on a parent's request or when entered in error (``insights.manage``,
    step-up; FR-EW-014). Audit ``insights.note_erased`` (reason and category; never text)."""
    note = repo.get_note(session, note_id, lock=True)
    if note is None:
        raise NotFound("Note not found")
    try:
        _ensure_student(session, ctx, note.student_id, MANAGE)
    except NotFound:
        raise NotFound("Note not found") from None
    repo.delete_note(session, note.id)
    _audit(
        session,
        "insights.note_erased",
        "student",
        note.student_id,
        {"note_id": note.id, "reason": data.reason, "category": note.category},
    )
    return ErasedOut(id=note.id)


# --- timeline (FR-EW-013) -------------------------------------------------------------------------


def _indicators_out(ind: engine.Indicators) -> IndicatorsOut:
    a, b, c = ind.attendance, ind.behaviour, ind.course
    return IndicatorsOut(
        attendance=AttendanceIndicatorOut(
            days=a.days,
            present=a.present,
            late=a.late,
            absent=a.absent,
            leave=a.leave,
            rate=a.rate,
            streak=a.streak,
            concern=a.concern,
        ),
        behaviour=BehaviourIndicatorOut(
            concerns=b.concerns, window_days=b.window_days, concern=b.concern
        ),
        course=CourseIndicatorOut(
            exam_id=c.exam_id,
            percent=c.percent,
            previous_percent=c.previous_percent,
            change=c.change,
            concern=c.concern,
        ),
    )


def _attendance_months(marks: Sequence[academics.DayMark], months: int) -> list[TimelineItem]:
    grouped: dict[str, list[academics.DayMark]] = {}
    for mark in marks:
        grouped.setdefault(mark.on_date.strftime("%Y-%m"), []).append(mark)
    out: list[TimelineItem] = []
    for month in sorted(grouped)[-months:]:
        days = grouped[month]
        found = Counter(m.status for m in days)
        out.append(
            TimelineItem(
                kind="attendance_month",
                on=max(m.on_date for m in days),
                attendance=AttendanceMonthEvent(
                    month=month,
                    days=len(days),
                    present=found.get("present", 0),
                    late=found.get("late", 0),
                    absent=found.get("absent", 0),
                    leave=found.get("leave", 0),
                ),
            )
        )
    return out


def _certificate_items(
    session: Session, ctx: UserContext, student_id: uuid.UUID
) -> list[TimelineItem]:
    """Certificates only for viewers who may see them (``certificate.read``)."""
    if not ctx.has(CERTIFICATE_READ):
        return []
    page = certificates.list_certificates(session, ctx, student_id=student_id, limit=50)
    return [
        TimelineItem(
            kind="certificate",
            on=(c.issued_at or c.requested_at).astimezone(IST).date(),
            certificate=CertificateEvent(
                certificate_id=c.id,
                certificate_type=c.certificate_type,
                status=c.status,
                serial=c.serial,
            ),
        )
        for c in page.data
    ]


def timeline(session: Session, ctx: UserContext, student_id: uuid.UUID) -> TimelineOut:
    """Everything the school holds for the student's educational follow-up, newest first,
    with the ABC indicators (``insights.read``; 404 outside scope). Audit ``insights.viewed``
    (view ``timeline``)."""
    _ensure_student(session, ctx, student_id)
    cfg = load_config()
    today = today_ist()
    ref = _student_refs(session, [student_id])[student_id]
    year = tenancy.get_current_academic_year(session)
    marks = academics.attendance_history(session, [student_id]).get(student_id, [])
    results = academics.exam_results(session, [student_id]).get(student_id, [])
    notes = repo.notes_of(session, student_id)
    concern_dates = [n.noted_on for n in notes if n.category == "concern"]
    current = [r for r in results if year is not None and r.academic_year_id == year.id]
    ind = engine.indicators(
        engine.Facts(
            marks=marks[-max(cfg.evaluation.history_days, 1) :],
            results=current,
            concern_dates=concern_dates,
        ),
        _settings(session),
        today,
    )
    labels = _section_labels(session)
    years = {y.id: y for y in tenancy.list_academic_years(session)}
    items: list[TimelineItem] = []
    for enrolment in students.enrolment_histories(session, [student_id]).get(student_id, []):
        start = enrolment.started_on or (
            years[enrolment.academic_year_id].starts_on
            if enrolment.academic_year_id in years
            else None
        )
        if start is None:  # pragma: no cover - every enrolment has a year
            continue
        items.append(
            TimelineItem(
                kind="enrolment",
                on=start,
                enrolment=EnrolmentEvent(
                    section_label=labels.get(enrolment.section_id),
                    status=enrolment.status,
                    started_on=enrolment.started_on,
                    ended_on=enrolment.ended_on,
                ),
            )
        )
    items.extend(_attendance_months(marks, cfg.timeline.attendance_months))
    items.extend(
        TimelineItem(
            kind="exam",
            on=r.held_on,
            exam=ExamEvent(
                exam_id=r.exam_id,
                name=r.name,
                percent=r.percent,
                papers=r.papers,
                absent_papers=r.absent_papers,
            ),
        )
        for r in results
    )
    names = _members(session, [n.created_by_membership for n in notes])
    items.extend(
        TimelineItem(kind="note", on=n.noted_on, note=_note_out(session, n, names)) for n in notes
    )
    items.extend(
        TimelineItem(kind="flag", on=f.raised_on, flag=f)
        for f in _details(session, repo.flags_of(session, student_id))
    )
    items.extend(_certificate_items(session, ctx, student_id))
    order = {
        "flag": 0,
        "note": 1,
        "exam": 2,
        "attendance_month": 3,
        "certificate": 4,
        "enrolment": 5,
    }
    items.sort(key=lambda i: (i.on, -order[i.kind]), reverse=True)
    _viewed(session, "timeline", "student", student_id, items=len(items))
    return TimelineOut(student=ref, indicators=_indicators_out(ind), items=items)


# --- retention (FR-EW-017) ------------------------------------------------------------------------


def purge_expired(session: Session, *, today: dt.date | None = None) -> dict[str, int]:
    """Daily: behaviour notes older than their retention and flags closed longer ago than
    theirs are deleted (with their actions). Audit ``insights.retention_purged`` (counts)."""
    cfg = load_config().retention
    day = today or today_ist()
    notes = repo.purge_notes(session, day - dt.timedelta(days=cfg.notes_days))
    cutoff = dt.datetime.combine(day, dt.time(), tzinfo=IST) - dt.timedelta(
        days=cfg.closed_flags_days
    )
    flags = repo.purge_closed(session, cutoff)
    if notes or flags:
        _audit(
            session,
            "insights.retention_purged",
            "tenant",
            repo.current_tenant_id(session),
            {"notes": notes, "flags": flags},
            system=True,
        )
    return {"notes": notes, "flags": flags}


# --- full export and offboarding ------------------------------------------------------------------


def export_records(session: Session, *, include_sensitive: bool) -> list[RecordTable]:
    """Worker only: notes, flags, actions and settings of the current school for its full data
    export (``app.admin``; the caller checked ``tenant.export_all`` and audits the export).
    Restricted text (notes, action notes) and flag evidence are ``••••`` unless the owner
    explicitly included restricted values (``include_sensitive``); ciphertext never leaves."""
    masked: tuple[str, ...] = () if include_sensitive else ("c3_masked",)
    notes: list[tuple[object, ...]] = [
        (
            n.id,
            n.student_id,
            n.section_id,
            n.category,
            n.noted_on,
            crypto.decrypt_note(session, n.body_ciphertext, n.id) if include_sensitive else MASK,
            n.created_by,
            n.created_by_membership,
            n.created_at,
        )
        for n in repo.all_notes(session)
    ]
    flags: list[tuple[object, ...]] = [
        (
            f.id,
            f.student_id,
            f.section_id,
            f.indicator,
            f.rule,
            f.rules_version,
            f.basis,
            json.dumps(f.evidence, sort_keys=True) if include_sensitive else MASK,
            f.status,
            f.owner_membership_id,
            f.raised_on,
            f.due_on,
            f.raised_by,
            f.first_action_at,
            f.closed_at,
            f.closed_by,
            f.close_reason,
            f.created_at,
        )
        for f in repo.all_flags(session)
    ]
    actions: list[tuple[object, ...]] = [
        (
            a.id,
            a.flag_id,
            a.kind,
            a.acted_on,
            (
                crypto.decrypt_action(session, a.note_ciphertext, a.id)
                if include_sensitive
                else (MASK if a.note_ciphertext is not None else None)
            ),
            a.created_by,
            a.created_by_membership,
            a.created_at,
        )
        for a in repo.all_actions(session)
    ]
    row = repo.get_settings(session)
    return [
        RecordTable(
            name="behaviour_notes",
            columns=(
                "id",
                "student_id",
                "section_id",
                "category",
                "noted_on",
                "text",
                "created_by",
                "created_by_membership",
                "created_at",
            ),
            rows=notes,
            notes=masked,
        ),
        RecordTable(
            name="insight_flags",
            columns=(
                "id",
                "student_id",
                "section_id",
                "indicator",
                "rule",
                "rules_version",
                "basis",
                "evidence",
                "status",
                "owner_membership_id",
                "raised_on",
                "due_on",
                "raised_by",
                "first_action_at",
                "closed_at",
                "closed_by",
                "close_reason",
                "created_at",
            ),
            rows=flags,
            notes=masked,
        ),
        RecordTable(
            name="flag_actions",
            columns=(
                "id",
                "flag_id",
                "kind",
                "acted_on",
                "note",
                "created_by",
                "created_by_membership",
                "created_at",
            ),
            rows=actions,
            notes=masked,
        ),
        RecordTable(
            name="insight_settings",
            columns=("rules", "updated_by", "updated_at", "version"),
            rows=(
                [
                    (
                        json.dumps(row.rules, sort_keys=True),
                        row.updated_by,
                        row.updated_at,
                        row.version,
                    )
                ]
                if row is not None
                else []
            ),
        ),
    ]


_PURGE = purging.PurgeTables(
    deleted=(
        "sis.flag_actions",
        "sis.insight_flags",
        "sis.behaviour_notes",
        "sis.insight_settings",
    ),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only; ADR-0029)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="insights", count=tenant_data_counts, purge=purge_tenant_data)
)
key_rotation.register_reencryptor("insights_notes", crypto.reencrypt_batch)


__all__ = [
    "ACT",
    "EVALUATE_EVENT",
    "EVALUATE_TASK",
    "MANAGE",
    "NOTE",
    "READ",
    "ErasedOut",
    "add_action",
    "add_note",
    "assign_flag",
    "close_flag",
    "erase_flag",
    "erase_note",
    "evaluate",
    "export_records",
    "get_flag",
    "get_settings",
    "list_flags",
    "list_notes",
    "owners",
    "purge_expired",
    "purge_tenant_data",
    "queue_evaluation",
    "raise_flag",
    "send_reminders",
    "summary",
    "tenant_data_counts",
    "timeline",
    "today_ist",
    "update_settings",
]
