"""Fix-owner letters from readiness differences (FA-08; FR-DQ-049..FR-DQ-060; docs/09 Data
quality). Routes: :mod:`app.dq.fix_letter_api`. Templates, not AI.

Each readiness difference names who must fix it (``app/dq/config/readiness.yaml``). For the
fixes outside the school this module prints the letter that asks for the fix, filled from the
school's own records with only the fields the letter needs:

- **Parent letter** (``parent_aadhaar``): Aadhaar-as-printed differs from the right value; the
  parent updates Aadhaar at an Aadhaar centre. One sheet per student, for one student, one
  stored finding or a whole section. It names the field, the value in the school's record and
  what differs, never the Aadhaar value (C3, the parent holds the card) and never an Aadhaar
  number (invariant 4). Permission ``dq.readiness.manage``.
- **Board letter** (``board_registration``): the board registration differs from the right
  value; the school writes to the board office (owner ``school_udise``). One letter listing
  every correction of the profile (or a section). It leaves the office, so it needs
  ``export.portal`` with a recent MFA sign-in, like the UDISE+ letter to the MEO.

The UDISE+ letter to the MEO is the UDISE+ correction request (``app.dq.udise_corrections``):
not repeated here. Words, recipients and limits live in ``config/fix_letters.yaml`` (versioned,
``verified: false`` until a school confirms the format, D3).

- **Audited** (invariant 7): every print ``dq.fix_letter.printed`` with the letter, its version,
  counts and student ids (no values), in the same transaction.
- **Follow-up**: "letters given" / "letter sent" records the letter's follow-up step
  (``parent_contacted`` by ``letter`` for parents, ``request_sent`` by ``letter`` for the board)
  through :func:`app.dq.followups.record` (audited ``dq.followup.recorded``). Printing alone
  records nothing: a printed letter is not yet a letter given.
- **Never a correction** (invariant 6): a letter asks; nothing changes on any record.

Scope: students the caller reaches with ``student.read_basic``, ``dq.readiness.read`` and
``dq.readiness.manage``; a student, section or finding outside it, or of another school, is 404.
Logs carry counts only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import get_context, get_logger
from app.dq import calendar_service, checks, followups
from app.dq import fix_letter_page as page
from app.dq import followup_config as fc
from app.dq import followup_repository as followup_repo
from app.dq import readiness as rd
from app.dq import readiness_service as readiness
from app.dq import service as dq
from app.dq.fix_letter_schemas import (
    FixLetterKey,
    FixLetterOut,
    FixLetterRecipientOut,
    FixLettersOut,
    FixLetterStudentOut,
)
from app.dq.followup_config import FollowupChannel, FollowupEvent, FollowupOwner
from app.dq.followup_schemas import FollowupIn
from app.dq.profiles import Profile, load_engine_config
from app.dq.schemas import StudentRef
from app.dq.udise_corrections import load_udise_config
from app.students import service as students
from app.tenancy import service as tenancy

log = get_logger(__name__)

READ: Final = readiness.READ
MANAGE: Final = readiness.MANAGE
PRINT_BOARD: Final = "export.portal"
PARENT: Final = "parent_aadhaar"
BOARD: Final = "board_registration"
DASH: Final = "—"


# --- config -------------------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Followup(_Frozen):
    event: FollowupEvent
    channel: FollowupChannel


class _LetterBase(_Frozen):
    label: str = Field(min_length=1, max_length=80)
    audience: Literal["parent", "office"]
    owner: FollowupOwner
    source: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    profiles: tuple[str, ...] = Field(min_length=1)
    followup: _Followup
    title: str = Field(min_length=1, max_length=120)
    signature: str


class ParentLetterConfig(_LetterBase):
    reply_within_days: int = Field(ge=1, le=90)
    salutation: str
    intro: str
    steps: tuple[str, ...] = Field(min_length=1, max_length=8)
    reply_by: str
    privacy_note: str
    wrong_record_note: str
    acknowledgement: str


class BoardLetterConfig(_LetterBase):
    recipients: dict[str, str] = Field(min_length=1)
    intro: str
    udise_note: str
    closing: str


class _Letters(_Frozen):
    parent_aadhaar: ParentLetterConfig
    board_registration: BoardLetterConfig


class FixLetterConfig(_Frozen):
    version: int = Field(ge=1)
    verified: bool
    source: tuple[str, ...] = Field(min_length=1)
    max_students: int = Field(ge=1, le=300)
    letters: _Letters

    @model_validator(mode="after")
    def _followups_exist(self) -> FixLetterConfig:
        """Each letter's follow-up step must be one its fix owner records (udise_followup.yaml)."""
        spec = fc.load_followup_config()
        for letter in (self.letters.parent_aadhaar, self.letters.board_registration):
            event, channel = letter.followup.event, letter.followup.channel
            if event not in spec.followups.owners[letter.owner].events:
                raise ValueError(f"{event} is not a follow-up step of {letter.owner}")
            if channel not in spec.channels_for(event):
                raise ValueError(f"{channel} is not a channel of {event}")
        if self.letters.board_registration.audience != "office":
            raise ValueError("the board letter goes to an office")
        return self

    def letter(self, key: str) -> ParentLetterConfig | BoardLetterConfig:
        if key == PARENT:
            return self.letters.parent_aadhaar
        if key == BOARD:
            return self.letters.board_registration
        raise NotFound("Letter not found")


@lru_cache(maxsize=1)
def load_fix_letter_config() -> FixLetterConfig:
    text = resources.files("app.dq").joinpath("config/fix_letters.yaml").read_text("utf-8")
    return FixLetterConfig.model_validate(yaml.safe_load(text))


# Validated when the API or a worker imports this module (a broken file stops the start-up).
load_fix_letter_config()


def _letter_profile(
    key: str, profile_key: str
) -> tuple[Profile, ParentLetterConfig | BoardLetterConfig]:
    """The letter and a readiness profile it is offered for (one comparing its source), else 404."""
    letter = load_fix_letter_config().letter(key)
    if profile_key not in letter.profiles:
        raise NotFound("Letter not found")
    profile = readiness.readiness_profile(profile_key)
    spec = profile.readiness
    if spec is None or not any(letter.source in f.sources for f in spec.fields):
        raise NotFound("Letter not found")
    return profile, letter


def letters_for(profile_key: str) -> list[str]:
    """The letter keys offered for a readiness profile (404 for a profile without a check)."""
    readiness.readiness_profile(profile_key)
    out: list[str] = []
    for key in (PARENT, BOARD):
        try:
            _letter_profile(key, profile_key)
        except NotFound:
            continue
        out.append(key)
    return out


# --- lines --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Line:
    """One difference a letter asks to fix. Values stay in memory; never logged."""

    student_id: uuid.UUID
    attribute: str
    wrong_value: str | None
    correct_value: str
    correct_source: str
    differs: str
    evidence: tuple[str, ...]
    finding_id: uuid.UUID | None


def _display(attribute: str, value: str | None) -> str | None:
    if value is None:
        return None
    return rd.display_date(value) if attribute == "dob" else value


def _lines(
    session: Session,
    profile: Profile,
    letter: ParentLetterConfig | BoardLetterConfig,
    ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, list[_Line]]:
    """student -> the live differences this letter asks to fix (waived and advisory ones do not
    count), in the profile's field order. The right value is always one SchoolOS may print
    (never Aadhaar-as-printed, C3)."""
    rcfg, engine_cfg = rd.load_readiness_config(), load_engine_config()
    evidence_words = load_udise_config().evidence
    sensitive = frozenset(
        a.key for a in students.attribute_catalog(session) if a.classification == "C3"
    )
    spec = profile.readiness
    order = [f.attribute for f in spec.fields] if spec is not None else []
    out: dict[uuid.UUID, list[_Line]] = {}
    for sid, student in readiness._assess(session, profile, ids).items():
        found: list[_Line] = []
        for line in student.lines:
            item = line.item
            if not line.counts() or item.reason != "mismatch":
                continue
            if item.owner != letter.owner or item.source != letter.source:
                continue
            if item.against is None or item.attribute not in order:
                continue
            if engine_cfg.physical_key(item.attribute, item.against) in sensitive:
                continue
            held = student.assessment.facts.get(item.attribute, {})
            right, wrong = held.get(item.against), held.get(letter.source)
            if right is None or not right.value:
                continue
            kind = checks.readiness_kind(item.attribute)
            differs = [rcfg.kinds[k].en for k in item.kinds if k in rcfg.kinds]
            found.append(
                _Line(
                    student_id=sid,
                    attribute=item.attribute,
                    wrong_value=wrong.value if wrong is not None else None,
                    correct_value=right.value,
                    correct_source=rcfg.sources[item.against].en.capitalize()
                    if item.against in rcfg.sources
                    else item.against,
                    differs="; ".join(differs) or rcfg.kinds["different"].en,
                    evidence=tuple(
                        evidence_words[source]
                        for source in evidence_words
                        if (fact := held.get(source)) is not None
                        and fact.value
                        and rd.lenient_equal(kind, fact.value, right.value)
                    ),
                    finding_id=line.row["id"] if line.row is not None else None,
                )
            )
        if found:
            found.sort(key=lambda x: order.index(x.attribute))
            out[sid] = found
    return out


def _reach(
    session: Session, ctx: UserContext, section_ids: Collection[uuid.UUID] | None = None
) -> list[uuid.UUID]:
    return students.list_students_in_scope(
        session, ctx, section_ids=section_ids, permissions=(READ, MANAGE)
    )


def _profile_students(
    session: Session,
    ctx: UserContext,
    profile: Profile,
    section_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """The profile's students in the caller's reach (one section of it, or all of them)."""
    if section_id is not None:
        readiness._section(session, ctx, section_id)  # 404 outside the reach
        ids = _reach(session, ctx, [section_id])
    else:
        # Only the profile's current-year sections (e.g. Classes IX and X), not the whole school.
        sections = list(readiness._profile_sections(session, profile))
        ids = _reach(session, ctx, sections) if sections else []
    return dq.board_students(session, profile.key, ids)


def _order(ids: Collection[uuid.UUID], refs: Mapping[uuid.UUID, Any]) -> list[uuid.UUID]:
    def key(sid: uuid.UUID) -> tuple[str, str, str]:
        ref = refs.get(sid)
        return (
            (ref.class_section or "") if ref else "",
            (ref.display_name or "") if ref else "",
            str(sid),
        )

    return sorted(ids, key=key)


# --- overview -----------------------------------------------------------------------------------


def _last_sent(
    session: Session,
    profile_key: str,
    letter: ParentLetterConfig | BoardLetterConfig,
    ids: Collection[uuid.UUID],
) -> dict[uuid.UUID, dt.date]:
    out: dict[uuid.UUID, dt.date] = {}
    for row in followup_repo.followups_of(session, profile_key, ids):
        if (
            row["fix_owner"] == letter.owner
            and row["event"] == letter.followup.event
            and row["channel"] == letter.followup.channel
        ):
            out[row["student_id"]] = row["occurred_on"]  # oldest first: the last one wins
    return out


def overview(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    *,
    section_id: uuid.UUID | None = None,
) -> FixLettersOut:
    """The letters offered for the profile and, for each, the students it covers now with the
    fields it lists (labels only, no values) and when the letter was last recorded as given or
    sent (FR-DQ-049, FR-DQ-050)."""
    cfg = load_fix_letter_config()
    keys = letters_for(profile_key)
    profile = readiness.readiness_profile(profile_key)
    ids = _profile_students(session, ctx, profile, section_id) if keys else []
    labels = {a.key: a.label_en for a in students.attribute_catalog(session)}
    letters: list[FixLetterOut] = []
    for key in keys:
        _, letter = _letter_profile(key, profile_key)
        lines = _lines(session, profile, letter, ids)
        refs = students.summaries(session, ctx, lines)
        sent = _last_sent(session, profile.key, letter, lines)
        rows = []
        for sid in _order(lines, refs):
            ref = refs.get(sid)
            rows.append(
                FixLetterStudentOut(
                    student=StudentRef(
                        id=sid,
                        display_name=ref.display_name if ref else None,
                        admission_no=ref.admission_no if ref else None,
                    ),
                    section_id=ref.section_id if ref else None,
                    class_section=ref.class_section if ref else None,
                    fields=[labels.get(x.attribute, x.attribute) for x in lines[sid]],
                    last_sent_on=sent.get(sid),
                )
            )
        recipients = letter.recipients if isinstance(letter, BoardLetterConfig) else {}
        letters.append(
            FixLetterOut(
                key=cast(FixLetterKey, key),
                label=letter.label,
                title=letter.title,
                audience=letter.audience,
                fix_owner=letter.owner,
                version=cfg.version,
                verified=cfg.verified and profile.verified,
                followup_event=letter.followup.event,
                followup_channel=letter.followup.channel,
                print_permission=PRINT_BOARD if key == BOARD else MANAGE,
                step_up=key == BOARD,
                recipients=[FixLetterRecipientOut(key=k, label=v) for k, v in recipients.items()],
                students_count=len(rows),
                lines_count=sum(len(v) for v in lines.values()),
                students=rows,
            )
        )
    log.info("dq.fix_letter.listed", count=sum(x.students_count for x in letters))
    return FixLettersOut(
        profile_key=profile.key, verified=cfg.verified and profile.verified, letters=letters
    )


# --- printing -----------------------------------------------------------------------------------


def _letterhead(session: Session) -> page.Letterhead:
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    return page.Letterhead(
        school_name=tenant.name,
        address=head.address_en,
        affiliation=head.affiliation,
        place=head.place,
    )


def _nothing() -> Conflict:
    return Conflict(
        "No open difference for this letter: nothing to print.", code="nothing_to_print"
    )


def _too_many(limit: int) -> ValidationFailed:
    return ValidationFailed(
        [{"field": "section_id", "code": "too_many", "message_key": "errors.too_many"}],
        detail=f"One print holds at most {limit} students; choose a section.",
    )


def _reference(prefix: str, sid: uuid.UUID | None = None) -> str:
    today = calendar_service.today_ist()
    tail = f"-{str(sid)[:8].upper()}" if sid is not None else ""
    return f"SchoolOS {prefix} {today.strftime('%Y%m%d')}{tail}"


def _audit_print(
    session: Session,
    *,
    key: str,
    profile: Profile,
    target_type: str,
    target_id: uuid.UUID | None,
    lines: Mapping[uuid.UUID, list[_Line]],
    extra: Mapping[str, Any] | None = None,
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action="dq.fix_letter.printed",
        resource_type=target_type,
        resource_id=target_id,
        request_id=request_id if isinstance(request_id, str) else None,
        summary={
            "letter": key,
            "template_version": load_fix_letter_config().version,
            "profile_key": profile.key,
            "students": len(lines),
            "lines": sum(len(v) for v in lines.values()),
            "fields": sorted({x.attribute for v in lines.values() for x in v}),
            "student_ids": [str(s) for s in lines],
            **(extra or {}),
        },
    )
    log.info("dq.fix_letter.printed", count=len(lines))


def parent_letters(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    *,
    student_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    finding_id: uuid.UUID | None = None,
    check: bool = False,
) -> str | None:
    """The parent letters (A4 HTML, one per student) for one student, one stored DQ-031 finding
    or one section (FR-DQ-051..FR-DQ-053). 409 ``nothing_to_print`` when none of them has an
    open Aadhaar difference; ``check``: every check runs, nothing is printed or audited. Audit
    ``dq.fix_letter.printed`` (FR-DQ-056)."""
    profile, _ = _letter_profile(PARENT, profile_key)
    cfg = load_fix_letter_config()
    letter = cfg.letters.parent_aadhaar
    if sum(x is not None for x in (student_id, section_id, finding_id)) != 1:
        raise ValidationFailed(
            [{"field": "student_id", "code": "one_of", "message_key": "errors.dq.slip_target"}],
            detail="Choose one student, one finding or one section.",
        )
    target_type, target_id = "section", section_id
    if finding_id is not None:
        finding = dq.get_finding(session, ctx, finding_id)  # 404 outside the reach
        if finding.profile_key != profile.key or finding.rule_id != readiness.RULE_ID:
            raise NotFound("Finding not found")
        student_id = finding.student.id
        target_type, target_id = "dq_finding", finding_id
    if student_id is not None:
        students.ensure_in_scope(session, ctx, student_id, READ, MANAGE)
        ids = dq.board_students(session, profile.key, [student_id])
        if target_type != "dq_finding":
            target_type, target_id = "student", student_id
    else:
        ids = _profile_students(session, ctx, profile, section_id)
    lines = _lines(session, profile, letter, ids)
    if finding_id is not None:
        lines = {
            sid: kept
            for sid, found in lines.items()
            if (kept := [x for x in found if x.finding_id == finding_id])
        }
    if not lines:
        raise _nothing()
    if len(lines) > cfg.max_students:
        raise _too_many(cfg.max_students)
    if check:
        return None
    refs = students.summaries(session, ctx, lines)
    labels = {a.key: a.label_en for a in students.attribute_catalog(session)}
    printed_at = dt.datetime.now(dt.UTC)
    letters = []
    for sid in _order(lines, refs):
        ref = refs.get(sid)
        letters.append(
            page.ParentLetter(
                student=(ref.display_name if ref else None) or DASH,
                admission_no=(ref.admission_no if ref else None) or DASH,
                class_section=(ref.class_section if ref else None) or DASH,
                reference=_reference("letter", sid),
                lines=[
                    page.ParentLine(
                        field=labels.get(x.attribute, x.attribute),
                        correct_value=_display(x.attribute, x.correct_value) or DASH,
                        source=x.correct_source,
                        differs=x.differs.capitalize(),
                    )
                    for x in lines[sid]
                ],
            )
        )
    html_page = page.render_parent(
        page.ParentData(
            head=_letterhead(session),
            words=page.ParentWords(
                title=letter.title,
                salutation=letter.salutation,
                intro=letter.intro,
                steps=letter.steps,
                reply_by=letter.reply_by,
                privacy_note=letter.privacy_note,
                wrong_record_note=letter.wrong_record_note,
                signature=letter.signature,
                acknowledgement=letter.acknowledgement,
            ),
            profile=profile.label_en,
            verified=cfg.verified and profile.verified,
            reply_on=calendar_service.today_ist() + dt.timedelta(days=letter.reply_within_days),
            letters=letters,
            printed_at=printed_at,
        )
    )
    _audit_print(
        session,
        key=PARENT,
        profile=profile,
        target_type=target_type,
        target_id=target_id,
        lines=lines,
    )
    return html_page


def board_letter(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    *,
    section_id: uuid.UUID | None = None,
    recipient: str | None = None,
    check: bool = False,
) -> str | None:
    """The letter to the board office listing every open registration correction of the
    profile, or of one section (FR-DQ-054, FR-DQ-055; ``export.portal`` with step-up, checked by
    the route). ``recipient``: a key of the letter's recipients (a role; default the first).
    409 ``nothing_to_print``; ``check`` as for parent letters. Audit ``dq.fix_letter.printed``."""
    profile, _ = _letter_profile(BOARD, profile_key)
    cfg = load_fix_letter_config()
    letter = cfg.letters.board_registration
    if recipient is not None and recipient not in letter.recipients:
        raise ValidationFailed(
            [{"field": "recipient", "code": "not_allowed", "message_key": "errors.not_allowed"}]
        )
    lines = _lines(session, profile, letter, _profile_students(session, ctx, profile, section_id))
    if not lines:
        raise _nothing()
    if len(lines) > cfg.max_students:
        raise _too_many(cfg.max_students)
    if check:
        return None
    refs = students.summaries(session, ctx, lines)
    labels = {a.key: a.label_en for a in students.attribute_catalog(session)}
    pens = students.canonical_values(session, list(lines), ["udise_pen"])
    rows: list[page.BoardLine] = []
    for sid in _order(lines, refs):
        ref = refs.get(sid)
        pen = pens.get(sid, {}).get("udise_pen")
        for x in lines[sid]:
            rows.append(
                page.BoardLine(
                    pen=(pen.value if pen is not None else None) or DASH,
                    student=(ref.display_name if ref else None) or DASH,
                    admission_no=(ref.admission_no if ref else None) or DASH,
                    class_section=(ref.class_section if ref else None) or DASH,
                    field=labels.get(x.attribute, x.attribute),
                    board_value=_display(x.attribute, x.wrong_value) or "(blank)",
                    correct_value=_display(x.attribute, x.correct_value) or DASH,
                    source=x.correct_source,
                    evidence=", ".join(x.evidence) or DASH,
                )
            )
    chosen = recipient or next(iter(letter.recipients))
    html_page = page.render_board(
        page.BoardData(
            head=_letterhead(session),
            words=page.BoardWords(
                title=letter.title,
                intro=letter.intro,
                udise_note=letter.udise_note,
                closing=letter.closing,
                signature=letter.signature,
            ),
            recipient=letter.recipients[chosen],
            reference=_reference("board request"),
            profile=profile.label_en,
            verified=cfg.verified and profile.verified,
            lines=rows,
            students=len(lines),
            printed_at=dt.datetime.now(dt.UTC),
        )
    )
    _audit_print(
        session,
        key=BOARD,
        profile=profile,
        target_type="section" if section_id is not None else "readiness_profile",
        target_id=section_id,
        lines=lines,
        extra={"recipient": chosen},
    )
    return html_page


# --- follow-up ----------------------------------------------------------------------------------


def record_sent(
    session: Session,
    ctx: UserContext,
    profile_key: str,
    key: str,
    *,
    student_ids: Sequence[uuid.UUID],
    occurred_on: dt.date | None,
) -> tuple[int, ParentLetterConfig | BoardLetterConfig]:
    """Record that the letter was given to the parents (or sent to the board office) for these
    students (FR-DQ-057): the letter's follow-up step through :func:`app.dq.followups.record`
    (audited ``dq.followup.recorded``, same transaction). A student outside the caller's reach:
    404; one without an open difference for this letter: 422 ``no_open_fix``."""
    profile, letter = _letter_profile(key, profile_key)
    wanted = list(dict.fromkeys(student_ids))
    reach = set(_reach(session, ctx))
    if any(sid not in reach for sid in wanted):
        raise NotFound("Student not found")
    ids = dq.board_students(session, profile.key, wanted)
    open_ids = set(_lines(session, profile, letter, ids))
    if any(sid not in open_ids for sid in wanted):
        raise ValidationFailed(
            [{"field": "student_ids", "code": "no_open_fix", "message_key": "errors.no_open_fix"}]
        )
    recorded = followups.record(
        session,
        ctx,
        profile.key,
        FollowupIn(
            student_ids=wanted,
            fix_owner=letter.owner,
            event=letter.followup.event,
            channel=letter.followup.channel,
            occurred_on=occurred_on,
        ),
    )
    return recorded.recorded, letter


__all__ = [
    "BOARD",
    "MANAGE",
    "PARENT",
    "PRINT_BOARD",
    "FixLetterConfig",
    "board_letter",
    "letters_for",
    "load_fix_letter_config",
    "overview",
    "parent_letters",
    "record_sent",
]
