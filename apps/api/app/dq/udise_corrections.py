"""UDISE+ correction requests to the MEO / MIS coordinator (FO-02; FR-UDI-001..FR-UDI-010;
docs/09 Data quality). Routes: :mod:`app.dq.udise_api`.

Schools cannot edit a child's name or date of birth in UDISE+ themselves; the block or district
MIS office does it on a written request. This module turns the readiness differences whose fix
owner is the school via UDISE+ (``school_udise``: the UDISE+ value differs from the right value,
``app/dq/config/readiness.yaml``) into that request:

- **Candidates** (``dq.readiness.manage``): every open difference now, grouped by field as MIS
  offices batch them (``config/udise_corrections.yaml``), with the PEN, the value in UDISE+, the
  correct value and its record, the evidence the school holds (records that agree with the
  correct value) and whether it matches the Aadhaar-as-printed details (yes/no only; never an
  Aadhaar number, invariant 4). A difference whose right value only Aadhaar-as-printed holds is
  left out: the register must be corrected first (change request, BR-01).
- **Requests** (``dq.readiness.manage``): a batch keeps which student and which field it asks
  for (ids and keys, no values) and its state ``prepared`` -> ``sent`` (date and recipient role)
  -> ``done`` | ``rejected``. A line is ``fixed`` once the readiness check no longer finds the
  difference (after the next UDISE+ list import, FO-04), so the office sees what the MEO did.
  A request never changes a record (invariant 6).
- **Letter** (``export.portal``, step-up): the A4 print view of the open lines; audited with
  counts and the request id (``dq.udise_request.printed``).

Scope: students the caller reaches with ``student.read_basic``, ``dq.readiness.read`` and
``dq.readiness.manage``; another school's request is 404 (RLS). Logs carry ids and counts only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.dq import checks
from app.dq import readiness as rd
from app.dq import readiness_service as readiness
from app.dq import repository as dq_repo
from app.dq import service as dq
from app.dq import udise_letter as letter_page
from app.dq import udise_repository as repo
from app.dq.profiles import Profile, load_engine_config
from app.dq.schemas import StudentRef
from app.dq.udise_schemas import (
    AadhaarMatch,
    LineState,
    UdiseCandidatesOut,
    UdiseCloseIn,
    UdiseGroupOut,
    UdiseLineOut,
    UdiseRecipientOut,
    UdiseRequestCreate,
    UdiseRequestDetailOut,
    UdiseRequestOut,
    UdiseSentIn,
)
from app.students import service as students
from app.tenancy import service as tenancy

log = get_logger(__name__)

MANAGE: Final = readiness.MANAGE
PRINT: Final = "export.portal"
UDISE: Final = "udise_plus"
OWNER: Final = "school_udise"
AADHAAR: Final = "aadhaar_as_printed"
RESOURCE: Final = "udise_correction_request"
LIST_LIMIT: Final = 200
DASH: Final = "—"


# --- config -------------------------------------------------------------------------------------


class _Group(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=80)
    fields: tuple[str, ...] = Field(min_length=1)


class _Letter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    intro: str
    aadhaar_note: str
    closing: str
    signature: str


class UdiseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    verified: bool
    source: tuple[str, ...] = Field(min_length=1)
    profiles: tuple[str, ...] = Field(min_length=1)
    groups: tuple[_Group, ...] = Field(min_length=1)
    recipients: dict[str, str] = Field(min_length=1)
    evidence: dict[str, str]
    max_lines: int = Field(ge=1, le=500)
    letter: _Letter

    def group_of(self, attribute: str) -> _Group | None:
        return next((g for g in self.groups if attribute in g.fields), None)


@lru_cache(maxsize=1)
def load_udise_config() -> UdiseConfig:
    text = resources.files("app.dq").joinpath("config/udise_corrections.yaml").read_text("utf-8")
    return UdiseConfig.model_validate(yaml.safe_load(text))


def _recipients(cfg: UdiseConfig) -> list[UdiseRecipientOut]:
    return [UdiseRecipientOut(key=k, label=v) for k, v in cfg.recipients.items()]


def _profile(profile_key: str) -> Profile:
    """A readiness profile this request can be made for, else 404."""
    if profile_key not in load_udise_config().profiles:
        raise NotFound("Readiness check not found")
    profile = readiness.readiness_profile(profile_key)
    spec = profile.readiness
    if spec is None or not any(UDISE in f.sources for f in spec.fields):
        raise NotFound("Readiness check not found")
    return profile


# --- differences --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Diff:
    student_id: uuid.UUID
    attribute: str
    udise_value: str | None
    correct_value: str
    correct_source: str
    evidence: tuple[str, ...]
    matches_aadhaar: AadhaarMatch


def _reach(
    session: Session, ctx: UserContext, section_ids: Collection[uuid.UUID] | None = None
) -> list[uuid.UUID]:
    return students.list_students_in_scope(
        session, ctx, section_ids=section_ids, permissions=(readiness.READ, MANAGE)
    )


def _differences(
    session: Session, profile: Profile, ids: Sequence[uuid.UUID], *, force: bool
) -> dict[tuple[uuid.UUID, str], _Diff]:
    """(student, field) -> the UDISE+ value to correct, from the live readiness assessment
    (waived differences do not count). Values stay in memory; never logged."""
    cfg, rcfg = load_udise_config(), rd.load_readiness_config()
    engine_cfg = load_engine_config()
    sensitive = frozenset(
        a.key for a in students.attribute_catalog(session) if a.classification == "C3"
    )
    assessed = readiness._assess(session, profile, ids, force=force)
    out: dict[tuple[uuid.UUID, str], _Diff] = {}
    for sid, student in assessed.items():
        for line in student.lines:
            item = line.item
            if not line.counts() or item.owner != OWNER or item.reason != "mismatch":
                continue
            if item.source != UDISE or item.against is None:
                continue
            if cfg.group_of(item.attribute) is None:
                continue
            # The correct value must be one SchoolOS may print (never Aadhaar-as-printed, C3).
            if engine_cfg.physical_key(item.attribute, item.against) in sensitive:
                continue
            held = student.assessment.facts.get(item.attribute, {})
            right, wrong = held.get(item.against), held.get(UDISE)
            if right is None or not right.value:
                continue
            kind = checks.readiness_kind(item.attribute)
            evidence = tuple(
                cfg.evidence[source]
                for source in cfg.evidence
                if (fact := held.get(source)) is not None
                and fact.value
                and rd.lenient_equal(kind, fact.value, right.value)
            )
            aadhaar = held.get(AADHAAR)
            match: AadhaarMatch = "not_recorded"
            if aadhaar is not None and aadhaar.value:
                same = rd.lenient_equal(kind, aadhaar.value, right.value)
                match = "yes" if same else "no"
            out[(sid, item.attribute)] = _Diff(
                student_id=sid,
                attribute=item.attribute,
                udise_value=wrong.value if wrong is not None else None,
                correct_value=right.value,
                correct_source=rcfg.sources[item.against].en.capitalize()
                if item.against in rcfg.sources
                else item.against,
                evidence=evidence,
                matches_aadhaar=match,
            )
    return out


def _display(attribute: str, value: str | None) -> str | None:
    if value is None:
        return None
    return rd.display_date(value) if attribute == "dob" else value


@dataclass(frozen=True, slots=True)
class _Refs:
    summaries: Mapping[uuid.UUID, Any]
    pens: Mapping[uuid.UUID, str | None]
    labels: Mapping[str, str]


def _pen(value: students.CanonicalValue | None) -> str | None:
    return value.value if value is not None else None


def _refs(session: Session, ctx: UserContext, ids: Collection[uuid.UUID]) -> _Refs:
    pens = students.canonical_values(session, list(ids), ["udise_pen"])
    return _Refs(
        summaries=students.summaries(session, ctx, ids),
        pens={sid: _pen(v.get("udise_pen")) for sid, v in pens.items()},
        labels={a.key: a.label_en for a in students.attribute_catalog(session)},
    )


def _line_out(
    key: tuple[uuid.UUID, str],
    diff: _Diff | None,
    refs: _Refs,
    *,
    state: LineState,
    requested_in: uuid.UUID | None = None,
) -> UdiseLineOut:
    sid, attribute = key
    ref = refs.summaries.get(sid)
    return UdiseLineOut(
        student=StudentRef(
            id=sid,
            display_name=ref.display_name if ref else None,
            admission_no=ref.admission_no if ref else None,
        ),
        class_section=ref.class_section if ref else None,
        pen=refs.pens.get(sid),
        attribute_key=attribute,
        field_label=refs.labels.get(attribute, attribute),
        udise_value=_display(attribute, diff.udise_value) if diff else None,
        correct_value=_display(attribute, diff.correct_value) if diff else None,
        correct_source=diff.correct_source if diff else DASH,
        evidence=list(diff.evidence) if diff else [],
        matches_aadhaar=diff.matches_aadhaar if diff else "not_recorded",
        state=state,
        requested_in=requested_in,
    )


def _groups(lines: Sequence[UdiseLineOut]) -> list[UdiseGroupOut]:
    cfg = load_udise_config()
    out: list[UdiseGroupOut] = []
    for group in cfg.groups:
        mine = [line for line in lines if line.attribute_key in group.fields]
        mine.sort(
            key=lambda line: (
                line.class_section or "",
                line.student.display_name or "",
                str(line.student.id),
                group.fields.index(line.attribute_key),
            )
        )
        out.append(UdiseGroupOut(key=group.key, label=group.label, lines=mine))
    return out


def _ordered_ids(session: Session, profile: Profile, ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    return dq.board_students(session, profile.key, ids)


# --- candidates and requests --------------------------------------------------------------------


def candidates(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    *,
    section_ids: Collection[uuid.UUID] | None = None,
) -> UdiseCandidatesOut:
    """Every open UDISE+ difference a new request would ask for (FR-UDI-001, FR-UDI-002),
    grouped by field, with lines already in an open request marked ``requested_in``."""
    profile = _profile(profile_key)
    cfg = load_udise_config()
    ids = _ordered_ids(session, profile, _reach(session, ctx, section_ids))
    diffs = _differences(session, profile, ids, force=False)
    pending = repo.open_lines(session, {sid for sid, _ in diffs})
    refs = _refs(session, ctx, {sid for sid, _ in diffs})
    lines = [
        _line_out(key, diff, refs, state="open", requested_in=pending.get(key))
        for key, diff in diffs.items()
    ]
    return UdiseCandidatesOut(
        profile_key=profile.key,
        verified=profile.verified and cfg.verified,
        groups=_groups(lines),
        lines_count=len(lines),
        students_count=len({sid for sid, _ in diffs}),
        already_requested=sum(1 for line in lines if line.requested_in is not None),
        recipients=_recipients(cfg),
    )


def _request_out(row: RowMapping) -> UdiseRequestOut:
    cfg = load_udise_config()
    return UdiseRequestOut(
        id=row["id"],
        profile_key=row["profile_key"],
        status=row["status"],
        lines_count=row["lines_count"],
        students_count=row["students_count"],
        prepared_by=row["prepared_by"],
        prepared_at=row["prepared_at"],
        sent_on=row["sent_on"],
        sent_to=row["sent_to"],
        sent_to_label=cfg.recipients.get(row["sent_to"]) if row["sent_to"] else None,
        closed_at=row["closed_at"],
        version=row["version"],
    )


def prepare(session: Session, ctx: UserContext, data: UdiseRequestCreate) -> UdiseRequestDetailOut:
    """Prepare a request from the open differences (FR-UDI-003): the chosen ``lines`` or every
    open one, without those another open request already asks for. 422 ``nothing_to_request``
    when none is left; ``lines`` outside the caller's reach or without a difference are 422
    ``line_not_open`` (no existence oracle: the same answer for both). Audit
    ``dq.udise_request.prepared`` with counts and the profile only."""
    profile = _profile(data.profile_key)
    cfg = load_udise_config()
    ids = _ordered_ids(session, profile, _reach(session, ctx, data.section_ids))
    diffs = _differences(session, profile, ids, force=False)
    pending = repo.open_lines(session, {sid for sid, _ in diffs})
    open_keys = [k for k in diffs if k not in pending]
    if data.lines is not None:
        wanted = list(dict.fromkeys((ref.student_id, ref.attribute_key) for ref in data.lines))
        missing = [k for k in wanted if k not in open_keys]
        if missing:
            raise ValidationFailed(
                [
                    {
                        "field": "lines",
                        "code": "line_not_open",
                        "message_key": "errors.dq.udise_line_not_open",
                    }
                ],
                detail="Some lines have no open UDISE+ difference or are already requested.",
            )
        open_keys = wanted
    if not open_keys:
        raise ValidationFailed(
            [
                {
                    "field": "lines",
                    "code": "nothing_to_request",
                    "message_key": "errors.dq.udise_nothing_to_request",
                }
            ],
            detail="There are no UDISE+ differences left to ask for.",
        )
    if len(open_keys) > cfg.max_lines:
        raise ValidationFailed(
            [{"field": "lines", "code": "too_many", "message_key": "errors.dq.udise_too_many"}],
            detail=f"One request can hold at most {cfg.max_lines} lines; choose a section.",
        )
    tenant_id = dq_repo.current_tenant(session)
    request_id = new_id()
    student_count = len({sid for sid, _ in open_keys})
    row = repo.insert_request(
        session,
        {
            "id": request_id,
            "tenant_id": tenant_id,
            "profile_key": profile.key,
            "status": "prepared",
            "lines_count": len(open_keys),
            "students_count": student_count,
            "prepared_by": ctx.user_id,
        },
    )
    repo.insert_lines(
        session,
        [
            {
                "id": new_id(),
                "tenant_id": tenant_id,
                "request_id": request_id,
                "student_id": sid,
                "attribute_key": attribute,
            }
            for sid, attribute in open_keys
        ],
    )
    by_group: dict[str, int] = {}
    for _, attribute in open_keys:
        group = cfg.group_of(attribute)
        if group is not None:
            by_group[group.key] = by_group.get(group.key, 0) + 1
    dq._audit(
        session,
        action="dq.udise_request.prepared",
        resource_type=RESOURCE,
        resource_id=request_id,
        summary={
            "profile_key": profile.key,
            "lines": len(open_keys),
            "students": student_count,
            # A list, not a mapping: the group key "name" is a field group, not personal data.
            "groups": [{"group": k, "lines": n} for k, n in by_group.items()],
        },
    )
    log.info("dq.udise_request.prepared", count=len(open_keys))
    return _detail(session, ctx, row, profile)


def list_requests(session: Session, ctx: UserContext) -> list[UdiseRequestOut]:
    """The school's requests, newest first (FR-UDI-006). Counts only, no student data."""
    del ctx  # the route checked dq.readiness.manage; rows are the school's own (RLS)
    return [_request_out(r) for r in repo.list_requests(session, limit=LIST_LIMIT)]


def _load(session: Session, request_id: uuid.UUID, *, lock: bool = False) -> RowMapping:
    row = repo.get_request(session, request_id, lock=lock)
    if row is None:
        raise NotFound("Request not found")
    return row


def _detail(
    session: Session, ctx: UserContext, row: RowMapping, profile: Profile | None = None
) -> UdiseRequestDetailOut:
    cfg = load_udise_config()
    profile = profile or readiness.readiness_profile(row["profile_key"])
    stored = [(r["student_id"], r["attribute_key"]) for r in repo.lines_of(session, row["id"])]
    reach = set(_reach(session, ctx))
    ids = [sid for sid in dict.fromkeys(sid for sid, _ in stored) if sid in reach]
    diffs = _differences(session, profile, ids, force=True)
    refs = _refs(session, ctx, ids)
    lines: list[UdiseLineOut] = []
    for key in stored:
        state: LineState
        if key[0] not in reach:
            state = "unavailable"
        elif key in diffs:
            state = "open"
        else:
            state = "fixed"
        lines.append(_line_out(key, diffs.get(key), refs, state=state))
    base = _request_out(row)
    return UdiseRequestDetailOut(
        **base.model_dump(),
        verified=profile.verified and cfg.verified,
        groups=_groups(lines),
        fixed_count=sum(1 for line in lines if line.state == "fixed"),
        open_count=sum(1 for line in lines if line.state == "open"),
        recipients=_recipients(cfg),
    )


def get_request(session: Session, ctx: UserContext, request_id: uuid.UUID) -> UdiseRequestDetailOut:
    """One request with each line's state now (FR-UDI-007): ``open`` (UDISE+ still differs),
    ``fixed`` (the readiness check no longer finds the difference) or ``unavailable`` (the
    student is outside your reach now)."""
    return _detail(session, ctx, _load(session, request_id))


def _transition(
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    expected_version: int,
    *,
    allowed_from: tuple[str, ...],
    values: Mapping[str, Any],
    action: str,
    summary: Mapping[str, Any],
) -> UdiseRequestDetailOut:
    row = _load(session, request_id, lock=True)
    if row["version"] != expected_version:
        raise PreconditionFailed("The request was changed meanwhile. Reload and try again.")
    if row["status"] not in allowed_from:
        raise Conflict("This request cannot change to that state now.", code="udise_request_state")
    updated = repo.update_request(session, request_id, expected_version, values)
    if updated is None:  # pragma: no cover - locked above
        raise PreconditionFailed("The request was changed meanwhile. Reload and try again.")
    dq._audit(
        session,
        action=action,
        resource_type=RESOURCE,
        resource_id=request_id,
        summary={"from": row["status"], **summary},
    )
    log.info(action, resource_id=str(request_id))
    return _detail(session, ctx, updated)


def mark_sent(
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    data: UdiseSentIn,
    *,
    expected_version: int,
) -> UdiseRequestDetailOut:
    """Record that the request went to the MEO office (FR-UDI-006): the date and the
    recipient's role (a key of ``recipients``, never a person's name). Only from
    ``prepared``. The date may not be in the future or more than a year back."""
    cfg = load_udise_config()
    if data.sent_to not in cfg.recipients:
        raise ValidationFailed(
            [{"field": "sent_to", "code": "not_allowed", "message_key": "errors.not_allowed"}]
        )
    today = dt.datetime.now(letter_page.IST).date()
    if data.sent_on > today or data.sent_on < today - dt.timedelta(days=366):
        raise ValidationFailed(
            [{"field": "sent_on", "code": "out_of_range", "message_key": "errors.out_of_range"}]
        )
    detail = _transition(
        session,
        ctx,
        request_id,
        expected_version,
        allowed_from=("prepared",),
        values={
            "status": "sent",
            "sent_on": data.sent_on,
            "sent_to": data.sent_to,
            "sent_by": ctx.user_id,
        },
        action="dq.udise_request.sent",
        summary={"sent_to": data.sent_to, "sent_on": data.sent_on.isoformat()},
    )
    _record_followups(session, ctx, detail, data.sent_on)
    return detail


def _record_followups(
    session: Session, ctx: UserContext, detail: UdiseRequestDetailOut, sent_on: dt.date
) -> None:
    """FO-01 (FR-UDI-025): the students whose lines are still open get a ``request_sent``
    follow-up step (owner ``school_udise``, channel ``letter``) in the same transaction. A
    student whose difference closed meanwhile, or who is out of the caller's reach, is left
    out: the request itself is the record (no error for the clerk)."""
    from app.dq import followups  # noqa: PLC0415 - followups imports this module's neighbours
    from app.dq.followup_schemas import FollowupIn  # noqa: PLC0415

    wanted = sorted(
        {line.student.id for g in detail.groups for line in g.lines if line.state == "open"},
        key=str,
    )
    open_ids = {
        f.student_id
        for f in readiness.open_fixes(session, ctx, detail.profile_key, student_ids=wanted)
        if "school_udise" in f.owners
    }
    reach = set(
        students.list_students_in_scope(
            session, ctx, permissions=(followups.READ, followups.MANAGE)
        )
    )
    ids = [sid for sid in wanted if sid in open_ids and sid in reach]
    for i in range(0, len(ids), 300):
        followups.record(
            session,
            ctx,
            detail.profile_key,
            FollowupIn(
                student_ids=ids[i : i + 300],
                fix_owner="school_udise",
                event="request_sent",
                channel="letter",
                occurred_on=sent_on,
            ),
        )


def close(
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    data: UdiseCloseIn,
    *,
    expected_version: int,
) -> UdiseRequestDetailOut:
    """Close a sent request (FR-UDI-008): ``done`` (the MEO office made the corrections;
    the lines' states show what the last UDISE+ import confirms) or ``rejected``."""
    return _transition(
        session,
        ctx,
        request_id,
        expected_version,
        allowed_from=("sent",),
        values={"status": data.outcome, "closed_by": ctx.user_id, "closed_at": repo.now(session)},
        action=f"dq.udise_request.{data.outcome}",
        summary={"outcome": data.outcome},
    )


def letter(
    session: Session, ctx: UserContext, request_id: uuid.UUID, *, check: bool = False
) -> str | None:
    """The A4 letter of a request's open lines (FR-UDI-004, FR-UDI-005; ``export.portal`` with
    step-up, checked by the route). ``check``: every check runs, nothing is printed or audited
    (204), so the screen can confirm the sign-in before opening the print view. 409
    ``nothing_to_print`` when no line is open any more. Audit ``dq.udise_request.printed``
    (counts and the request id)."""
    cfg = load_udise_config()
    row = _load(session, request_id)
    detail = _detail(session, ctx, row)
    groups = [
        letter_page.LetterGroup(
            label=g.label,
            lines=[
                letter_page.LetterLine(
                    pen=line.pen or DASH,
                    student=line.student.display_name or DASH,
                    admission_no=line.student.admission_no or DASH,
                    class_section=line.class_section or DASH,
                    field=line.field_label,
                    udise_value=line.udise_value or "(blank)",
                    correct_value=line.correct_value or DASH,
                    source=line.correct_source,
                    evidence=", ".join(line.evidence) or DASH,
                    matches_aadhaar={"yes": "Yes", "no": "No"}.get(
                        line.matches_aadhaar, "Not recorded"
                    ),
                )
                for line in g.lines
                if line.state == "open"
            ],
        )
        for g in detail.groups
    ]
    lines = sum(len(g.lines) for g in groups)
    if lines == 0:
        raise Conflict(
            "Every line of this request is fixed or out of reach.", code="nothing_to_print"
        )
    if check:
        return None
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    printed_students = {
        line.student.id for g in detail.groups for line in g.lines if line.state == "open"
    }
    profile = readiness.readiness_profile(row["profile_key"])
    page = letter_page.render(
        letter_page.LetterData(
            school_name=tenant.name,
            school_address=head.address_en,
            school_affiliation=head.affiliation,
            place=head.place,
            title=cfg.letter.title,
            intro=cfg.letter.intro,
            aadhaar_note=cfg.letter.aadhaar_note,
            closing=cfg.letter.closing,
            signature=cfg.letter.signature,
            recipient=cfg.recipients.get(row["sent_to"] or "", next(iter(cfg.recipients.values()))),
            reference=f"SchoolOS UDISE+ request {str(row['id'])[:8].upper()}",
            profile=profile.label_en,
            verified=detail.verified,
            groups=groups,
            students=len(printed_students),
            printed_at=dt.datetime.now(dt.UTC),
        )
    )
    dq._audit(
        session,
        action="dq.udise_request.printed",
        resource_type=RESOURCE,
        resource_id=request_id,
        summary={"lines": lines, "students": len(printed_students)},
    )
    log.info("dq.udise_request.printed", count=lines)
    return page


__all__ = [
    "MANAGE",
    "PRINT",
    "UdiseConfig",
    "candidates",
    "close",
    "get_request",
    "letter",
    "list_requests",
    "load_udise_config",
    "mark_sent",
    "prepare",
]
