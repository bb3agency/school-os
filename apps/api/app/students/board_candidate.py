"""Board candidate grid: medium, languages, subjects and the photo tick per section (FO-10;
FR-STU-026..029).

- ``section_grid``: the section's current-year students the caller reaches with
  ``student.read_basic`` (404 for a section outside the caller's scope or school), each with the
  current (canonical) choices and the issues of
  :func:`app.students.board_catalogue.candidate_issues` for the section's board scheme (the
  class's board, FR-TEN-021, or the school's only board).
- ``save_section``: records the choices through :func:`app.students.service.record_value`
  (``student.update_nonidentity`` with its scope on every student; one ``student.value.recorded``
  audit event per changed value) plus one ``student.board_candidate.saved`` summary event, all in
  the request's transaction. Codes outside the scheme are refused (422 ``not_in_scheme``); a
  value already recorded as sent is left alone. The photo tick is always recorded as
  ``manual_entry`` (a staff observation). Nothing is corrected automatically (invariant 6):
  incomplete or invalid combinations stay visible as issues here and as DQ-032 findings.

No new table: the choices are attribute values (C2, not identity fields).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import NotFound, ValidationFailed
from app.students import repository as repo
from app.students import service as svc
from app.students.board_candidate_schemas import (
    BoardCandidateEntryIn,
    BoardCandidateGridOut,
    BoardCandidateIssueOut,
    BoardCandidateRowOut,
    BoardCandidateSaveIn,
    BoardCandidateSaveOut,
    BoardCodeOut,
    BoardLanguageSlotOut,
    BoardSchemeOut,
    BoardStudentCandidateOut,
    BoardSubjectGroupOut,
)
from app.students.board_catalogue import (
    CANDIDATE_KEYS,
    LANGUAGE_SLOTS,
    MEDIUM_KEY,
    PHOTO_KEY,
    SUBJECTS_KEY,
    BoardScheme,
    candidate_issues,
    load_board_catalogue,
    normalise_codes,
)
from app.students.definitions import error
from app.tenancy import service as tenancy

SAVED_EVENT: Final = "student.board_candidate.saved"
PHOTO_SOURCE: Final = "manual_entry"


def scheme_out(scheme: BoardScheme) -> BoardSchemeOut:
    def codes(options: Sequence[str]) -> list[BoardCodeOut]:
        return [BoardCodeOut(code=c, label=scheme.codes.get(c, c)) for c in options]

    return BoardSchemeOut(
        key=scheme.key,
        version=scheme.version,
        label_en=scheme.label_en,
        board=scheme.board,
        classes=list(scheme.classes),
        verified=scheme.verified,
        source=list(scheme.source),
        source_note=scheme.source_note,
        medium_required=scheme.medium.required,
        medium_options=list(scheme.medium.options),
        languages=[
            BoardLanguageSlotOut(slot=s.slot, required=s.required, options=codes(s.options))
            for s in scheme.languages
        ],
        subjects=[
            BoardSubjectGroupOut(
                key=g.key,
                label_en=g.label_en,
                required=g.required,
                min=g.min,
                max=g.max,
                options=codes(g.options),
            )
            for g in scheme.subjects
        ],
        max_subjects=scheme.max_subjects,
        photo_required=scheme.photo_required,
        keys=list(scheme.keys),
    )


def list_schemes() -> list[BoardSchemeOut]:
    """Every scheme of the packaged catalogue (reference data, no student data)."""
    return [scheme_out(s) for s in load_board_catalogue().schemes.values()]


def schemes_for_class(session: Session, class_code: str | None) -> list[BoardScheme]:
    """Schemes of the board the class follows: the class -> board map (FR-TEN-021), else the
    school's declared boards (all schemes when the school declared none, FR-TEN-020)."""
    if class_code is None:
        return []
    boards, class_boards = tenancy.school_boards(session)
    mapped = class_boards.get(class_code)
    wanted = {mapped} if mapped else set(boards)
    return [
        s
        for s in load_board_catalogue().schemes.values()
        if s.applies_to(class_code) and (not wanted or s.board in wanted)
    ]


def _section(
    session: Session, ctx: UserContext, section_id: uuid.UUID, permission: str
) -> tuple[svc._Structure, str | None]:
    """The current structure and the section's class code (404 outside scope or school)."""
    structure = svc._structure(session)
    section = structure.sections.get(section_id)
    allowed = svc._allowed_sections(ctx, permission, structure)
    if section is None or (allowed is not None and section_id not in allowed):
        raise NotFound("Section not found")
    klass = structure.classes.get(section.class_id)
    return structure, (klass.code if klass is not None else None)


def _choose(schemes: Sequence[BoardScheme], wanted: str | None) -> BoardScheme | None:
    if wanted is None:
        return schemes[0] if schemes else None
    for scheme in schemes:
        if scheme.key == wanted:
            return scheme
    raise ValidationFailed([error("scheme", "not_for_this_class")])


def _grid(
    session: Session,
    ctx: UserContext,
    section_id: uuid.UUID,
    *,
    structure: svc._Structure,
    class_code: str | None,
    scheme_key: str | None,
) -> BoardCandidateGridOut:
    schemes = schemes_for_class(session, class_code)
    scheme = _choose(schemes, scheme_key)
    ids = svc.list_students_in_scope(session, ctx, section_ids=[section_id])
    summaries = svc.summaries(session, ctx, ids)
    keys = scheme.keys if scheme is not None else CANDIDATE_KEYS
    canonical = svc.canonical_values(session, ids, keys)
    rolls = {
        sid: next((e.roll_no for e in enrols if e.section_id == section_id), None)
        for sid, enrols in svc.active_enrolments(session, ids).items()
    }
    rows: list[BoardCandidateRowOut] = []
    for sid in ids:
        summary = summaries.get(sid)
        if summary is None or summary.status not in repo.LIVE_STATUSES:
            continue
        values = {k: canonical.get(sid, {}).get(k, _EMPTY).value for k in keys}
        issues = issues_for(scheme, values) if scheme is not None else []
        rows.append(
            BoardCandidateRowOut(
                student_id=sid,
                admission_no=summary.admission_no,
                display_name=summary.display_name,
                roll_no=rolls.get(sid),
                values=values,
                issues=issues,
                ready=scheme is not None and not issues,
            )
        )
    rows.sort(key=lambda r: (_roll_key(r.roll_no), r.display_name or "", str(r.student_id)))
    return BoardCandidateGridOut(
        section_id=section_id,
        class_section=structure.label(section_id),
        class_code=class_code,
        scheme=scheme_out(scheme) if scheme is not None else None,
        available_schemes=[s.key for s in schemes],
        rows=rows,
        ready_count=sum(1 for r in rows if r.ready),
    )


class _Empty:
    value: str | None = None


_EMPTY: Final = _Empty()


def _roll_key(roll: str | None) -> tuple[int, int, str]:
    if roll is None:
        return (1, 0, "")
    return (0, int(roll), "") if roll.isdigit() else (0, 1 << 30, roll)


def section_grid(
    session: Session, ctx: UserContext, section_id: uuid.UUID, scheme_key: str | None = None
) -> BoardCandidateGridOut:
    """``GET /sections/{id}/board-candidates`` (``student.read_basic``)."""
    structure, class_code = _section(session, ctx, section_id, svc.READ)
    return _grid(
        session, ctx, section_id, structure=structure, class_code=class_code, scheme_key=scheme_key
    )


def _entry_values(
    scheme: BoardScheme, entry: BoardCandidateEntryIn, index: int
) -> list[tuple[str, str]]:
    """(attribute key, stored value) pairs of one entry, checked against ``scheme`` (422)."""
    errors: list[dict[str, str]] = []
    out: list[tuple[str, str]] = []
    prefix = f"rows.{index}"
    if entry.exam_medium is not None:
        medium = entry.exam_medium.strip().lower()
        if medium not in scheme.medium.options:
            errors.append(error(f"{prefix}.{MEDIUM_KEY}", "not_in_scheme"))
        out.append((MEDIUM_KEY, medium))
    for slot_key in LANGUAGE_SLOTS:
        raw = getattr(entry, slot_key)
        if raw is None:
            continue
        slot = scheme.slot(slot_key)
        code = raw.strip().upper()
        if slot is None or code not in slot.options:
            errors.append(error(f"{prefix}.{slot_key}", "not_in_scheme"))
        out.append((slot_key, code))
    if entry.board_subjects is not None:
        known = {c for g in scheme.subjects for c in g.options}
        stored = normalise_codes(entry.board_subjects)
        if not scheme.subjects or not stored or any(c not in known for c in stored.split(",")):
            errors.append(error(f"{prefix}.{SUBJECTS_KEY}", "not_in_scheme"))
        out.append((SUBJECTS_KEY, stored))
    if entry.photo_signature_ready is not None:
        out.append((PHOTO_KEY, "yes" if entry.photo_signature_ready else "no"))
    if errors:
        raise ValidationFailed(errors)
    return out


def save_section(
    session: Session, ctx: UserContext, section_id: uuid.UUID, data: BoardCandidateSaveIn
) -> BoardCandidateSaveOut:
    """``PUT /sections/{id}/board-candidates`` (``student.update_nonidentity``)."""
    structure, class_code = _section(session, ctx, section_id, svc.UPDATE)
    scheme = _choose(schemes_for_class(session, class_code), data.scheme)
    if scheme is None:  # pragma: no cover - _choose raises for an explicit key
        raise ValidationFailed([error("scheme", "not_for_this_class")])
    in_section = set(svc.list_students_in_scope(session, ctx, section_ids=[section_id]))
    seen: set[uuid.UUID] = set()
    planned: list[tuple[uuid.UUID, list[tuple[str, str]]]] = []
    errors: list[dict[str, str]] = []
    for index, entry in enumerate(data.rows):
        if entry.student_id not in in_section or entry.student_id in seen:
            errors.append(error(f"rows.{index}.student_id", "not_in_section"))
            continue
        seen.add(entry.student_id)
        try:
            planned.append((entry.student_id, _entry_values(scheme, entry, index)))
        except ValidationFailed as exc:
            errors.extend(exc.errors)
    if errors:
        raise ValidationFailed(errors)
    recorded = unchanged = 0
    for student_id, values in planned:
        for key, value in values:
            source = PHOTO_SOURCE if key == PHOTO_KEY else data.source
            before = repo.current_value(session, student_id, key, source)
            out = svc.record_value(
                session, ctx, student_id, key, source, value, permission=svc.UPDATE
            )
            if before is not None and out.id == before.id:
                unchanged += 1
            else:
                recorded += 1
    svc._audit(
        session,
        action=SAVED_EVENT,
        resource_type="section",
        resource_id=section_id,
        summary={
            "scheme": scheme.key,
            "scheme_version": scheme.version,
            "source": data.source,
            "students": len(planned),
            "recorded": recorded,
            "unchanged": unchanged,
        },
    )
    grid = _grid(
        session, ctx, section_id, structure=structure, class_code=class_code, scheme_key=scheme.key
    )
    return BoardCandidateSaveOut(recorded=recorded, unchanged=unchanged, grid=grid)


def issues_for(
    scheme: BoardScheme, values: Mapping[str, str | None]
) -> list[BoardCandidateIssueOut]:
    """The grid's issue list for one set of values (profile page; same rules as DQ-032)."""
    return [
        BoardCandidateIssueOut(field=i.field, reason=i.reason, code=i.code, group=i.group)
        for i in candidate_issues(scheme, values)
    ]


def student_candidate(
    session: Session, ctx: UserContext, student_id: uuid.UUID
) -> BoardStudentCandidateOut:
    """``GET /students/{id}/board-candidate`` (``student.read_basic`` with its scope; 404
    outside it): the student's scheme (from the current class), choices and issues. ``scheme``
    is null when the current class is not a board class of the school."""
    svc.ensure_in_scope(session, ctx, student_id)
    structure = svc._structure(session)
    section_id = svc.current_placements(session, [student_id]).get(student_id)
    section = structure.sections.get(section_id) if section_id is not None else None
    klass = structure.classes.get(section.class_id) if section is not None else None
    schemes = schemes_for_class(session, klass.code if klass is not None else None)
    scheme = schemes[0] if schemes else None
    keys = scheme.keys if scheme is not None else CANDIDATE_KEYS
    canonical = svc.canonical_values(session, [student_id], keys).get(student_id, {})
    values = {k: canonical.get(k, _EMPTY).value for k in keys}
    issues = issues_for(scheme, values) if scheme is not None else []
    return BoardStudentCandidateOut(
        student_id=student_id,
        section_id=section_id,
        class_section=structure.label(section_id),
        scheme=scheme_out(scheme) if scheme is not None else None,
        values=values,
        issues=issues,
        ready=scheme is not None and not issues,
    )
