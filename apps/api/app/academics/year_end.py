"""Year-end progression pack (FT-08; lead decision D85; FR-YRE-001..FR-YRE-020).

Results → promotion → UDISE+ progression → TC, typed once:

- **Annual result per student** (FR-YRE-001..FR-YRE-007). SchoolOS *proposes* a result from the
  year's marks (one overall percentage, FR-MRK-005, against ``year_end.pass_percent`` in
  ``config.yaml``; unverified, D3) and shows the days attended; the principal *confirms* each one
  (``year_result.confirm``; PRV-005: a person decides). ``detained`` needs a reason. Who, when,
  what was proposed and the figures shown are stored on ``sis.year_results`` and every write is
  audited (``year_result.confirmed``) in the same transaction. Nothing is changed by itself
  (invariant 6): a proposal is only a suggestion.
- **RTE no-detention** (FR-YRE-006). In the classes of ``year_end.no_detention`` (AP: I-VIII,
  ``verified: false``) "detained" is never proposed, and a confirmed "detained" is accepted with
  the warning ``no_detention_class``. It never refuses.
- **Promotion** (FR-YRE-008, FR-YRE-009). :func:`held_back` lists the students confirmed as
  detained for the promotion screen to pre-fill its held-back list; the promotion itself is
  unchanged (``students.preview_promotion`` / ``commit_promotion``, ``tenant.structure.manage``).
- **UDISE+ progression check sheet** (FR-YRE-010..FR-YRE-013): class, confirmed result, % marks
  and days attended per student of the year, with the blanks to fill first; CSV download is a
  portal export (``export.portal``, step-up) audited ``year_end.progression_exported``.
- **TC suggestions** (FR-YRE-014..FR-YRE-016): "qualified for promotion", working days and days
  attended as "From SchoolOS" values the clerk may change (``app.certificates`` shows them; they
  are never filled in silently).

Working days are the days the student's section was marked while the student was in it, until
the school calendar (FT-06) gives real working days; that is the one integration point to swap.

Reads are school-wide only (``year_result.read`` has no scoped grant), so past-year sections are
listed through :func:`app.students.service.students_finishing_year`. Logs carry ids and counts.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.academics import repository as repo
from app.academics import year_end_repository as yrepo
from app.academics.config import YearEndRules, load_config
from app.academics.models import YearResultRow
from app.academics.schemas import RecordSectionOut, RosterStudentOut
from app.academics.year_end_schemas import (
    HeldBackOut,
    ProgressionBlank,
    ProgressionOut,
    ProgressionRowOut,
    TcSuggestion,
    YearConfirmedOut,
    YearEndRulesOut,
    YearEndWarningCode,
    YearEndWarningOut,
    YearProposalOut,
    YearResultCountsOut,
    YearResultsOut,
    YearResultStudentOut,
    YearResultsWrite,
    YearResultsWriteOut,
)
from app.audit import service as audit
from app.authz.context import UserContext
from app.core.errors import Conflict, Forbidden, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.redaction import contains_full_aadhaar
from app.core.spreadsheet import write_csv
from app.identity import service as identity
from app.students import service as students
from app.students.schemas import EnrollmentOut
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut

log = get_logger(__name__)

YEAR_RESULT_READ: Final = "year_result.read"
YEAR_RESULT_CONFIRM: Final = "year_result.confirm"
EXPORT_PORTAL: Final = "export.portal"
NO_DETENTION: Final[YearEndWarningCode] = "no_detention_class"
CHANGED_CODE: Final = "year_result_changed"
_RESULTS: Final = ("passed", "promoted", "detained", "not_appeared")
_ERRORS_SHOWN: Final = 50
CSV_MEDIA_TYPE: Final = "text/csv; charset=utf-8"
CSV_HEADER: Final = (
    "Admission number",
    "Name",
    "PEN",
    "Class",
    "Section",
    "Result",
    "Marks %",
    "Days attended",
    "Working days",
    "Blanks",
)
RESULT_LABELS: Final = {
    "passed": "Passed",
    "promoted": "Promoted",
    "detained": "Detained",
    "not_appeared": "Not appeared",
}


def rules() -> YearEndRules:
    return load_config().year_end


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _rules_out() -> YearEndRulesOut:
    cfg = rules()
    return YearEndRulesOut(
        rule_version=cfg.rule_version,
        pass_percent=cfg.pass_percent,
        pass_percent_verified=cfg.pass_percent_verified,
        no_detention_class_codes=list(cfg.no_detention.class_codes),
        no_detention_verified=cfg.no_detention.verified,
    )


def no_detention_class(class_code: str | None) -> bool:
    """Whether the class is in the configured no-detention list (FR-YRE-006; unverified)."""
    return class_code is not None and class_code in rules().no_detention.class_codes


# --- the proposal (pure) --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Figures:
    """What the records say about one student's year."""

    percent: float | None
    papers: int
    absent_papers: int
    exams: int
    days_attended: int | None
    working_days: int | None


def propose(figures: Figures, *, class_code: str | None) -> str | None:
    """FR-YRE-003: the proposed result. None without papers; ``not_appeared`` when absent from
    every paper; else ``passed`` at or above the pass mark, below it ``detained``, or
    ``promoted`` in a no-detention class (never ``detained`` there)."""
    if figures.papers == 0:
        return None
    if figures.percent is None:
        return "not_appeared" if figures.absent_papers == figures.papers else None
    if figures.percent >= rules().pass_percent:
        return "passed"
    return "promoted" if no_detention_class(class_code) else "detained"


# --- figures from the records ---------------------------------------------------------------------


def _window(
    enrolment: EnrollmentOut, year: AcademicYearOut, last: dt.date
) -> tuple[dt.date, dt.date]:
    start = year.starts_on
    if enrolment.started_on_explicit and enrolment.started_on is not None:
        start = max(start, enrolment.started_on)
    end = min(enrolment.ended_on or last, last)
    return start, end


def figures_for(
    session: Session,
    year: AcademicYearOut,
    student_ids: Collection[uuid.UUID],
    *,
    until: dt.date | None = None,
) -> dict[uuid.UUID, Figures]:
    """Marks and attendance figures of ``student_ids`` in ``year`` up to ``until`` (default the
    year's last day). System read: the caller reached the students."""
    ids = list(student_ids)
    if not ids:
        return {}
    last = min(until or year.ends_on, year.ends_on)
    papers: dict[uuid.UUID, list[tuple[Decimal | None, Decimal, bool]]] = {}
    exams: dict[uuid.UUID, set[uuid.UUID]] = {}
    for mark, exam in repo.results_of(session, ids, year.id):
        if exam.held_on > last:
            continue
        papers.setdefault(mark.student_id, []).append((mark.marks, mark.max_marks, mark.absent))
        exams.setdefault(mark.student_id, set()).add(exam.id)
    # FT-06 (FR-CAL-012): with a school calendar, days attended and working days come from
    # ``attendance_summary`` (working days only, from the explicit enrolment start); without one
    # its working days are null and the register-based count below is the fallback.
    attended = yrepo.attended_days(session, ids, year.starts_on, last, rules().attended_statuses)
    histories = students.enrolment_histories(session, ids)
    in_year = {
        sid: [e for e in rows if e.academic_year_id == year.id] for sid, rows in histories.items()
    }
    section_ids = {e.section_id for rows in in_year.values() for e in rows}
    dates = yrepo.section_dates(session, section_ids, year.starts_on, last)
    # Imported late: the percentage helper is the marks rule FR-MRK-005 (service.percent_of).
    from app.academics.calendar_service import attendance_summary  # noqa: PLC0415
    from app.academics.service import percent_of  # noqa: PLC0415 - avoid an import cycle

    summaries = attendance_summary(session, ids, year.id, until=last)
    statuses = set(rules().attended_statuses)

    out: dict[uuid.UUID, Figures] = {}
    for sid in ids:
        rows = papers.get(sid, [])
        worked: set[dt.date] = set()
        for enrolment in in_year.get(sid, []):
            start, end = _window(enrolment, year, last)
            worked.update(d for d in dates.get(enrolment.section_id, []) if start <= d <= end)
        has_register = bool(worked)
        days_attended = attended.get(sid, 0) if has_register else None
        working_days = len(worked) if has_register else None
        summary = summaries.get(sid)
        if summary is not None and summary.working_days is not None:
            working_days = summary.working_days
            days_attended = sum(
                count
                for status, count in (
                    ("present", summary.present),
                    ("late", summary.late),
                    ("leave", summary.leave),
                    ("absent", summary.absent),
                )
                if status in statuses
            )
        out[sid] = Figures(
            percent=percent_of(rows),
            papers=len(rows),
            absent_papers=sum(1 for _, _, absent in rows if absent),
            exams=len(exams.get(sid, set())),
            days_attended=days_attended,
            working_days=working_days,
        )
    return out


# --- structure helpers ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _YearStructure:
    year: AcademicYearOut
    section_label: dict[uuid.UUID, str]
    section_class_code: dict[uuid.UUID, str]


def _year_structure(session: Session, year: AcademicYearOut) -> _YearStructure:
    codes = {c.id: c.code for c in tenancy.list_classes(session)}
    sections = tenancy.list_sections(session, academic_year_id=year.id)
    return _YearStructure(
        year=year,
        section_label={s.id: f"{codes.get(s.class_id, '?')}-{s.name}" for s in sections},
        section_class_code={s.id: codes.get(s.class_id, "?") for s in sections},
    )


def _section(session: Session, section_id: uuid.UUID) -> tuple[Any, AcademicYearOut, str]:
    """The section (404 for unknown or another school's), its year and class code."""
    try:
        section = tenancy.get_section(session, section_id)
    except NotFound:
        raise NotFound("Section not found") from None
    year = tenancy.get_academic_year(session, section.academic_year_id)
    klass = tenancy.get_class(session, section.class_id)
    return section, year, klass.code


def _final_section(rows: Sequence[EnrollmentOut], year_id: uuid.UUID) -> EnrollmentOut | None:
    """The enrolment a student finishes the year in (active, else completed)."""
    in_year = [e for e in rows if e.academic_year_id == year_id and e.status != "transferred"]
    return in_year[-1] if in_year else None


def _names(session: Session, rows: Collection[YearResultRow]) -> dict[uuid.UUID, str]:
    users = {r.confirmed_by for r in rows}
    return {u: ref[1] for u, ref in identity.members_for_users(session, users).items()}


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _confirmed_out(row: YearResultRow, names: Mapping[uuid.UUID, str]) -> YearConfirmedOut:
    return YearConfirmedOut(
        result=row.result,
        proposed_result=row.proposed_result,
        reason=row.reason,
        percent=_float(row.percent),
        days_attended=row.days_attended,
        working_days=row.working_days,
        rule_version=row.rule_version,
        confirmed_by_name=names.get(row.confirmed_by),
        confirmed_at=row.confirmed_at,
        version=row.version,
    )


def _roll_key(roll: str | None, name: str | None) -> tuple[int, int, str, str]:
    if roll is None:
        return (2, 0, "", (name or "").casefold())
    digits = roll.strip()
    if digits.isdigit():
        return (0, int(digits), digits, (name or "").casefold())
    return (1, 0, digits.casefold(), (name or "").casefold())


# --- reads ----------------------------------------------------------------------------------------


def year_results(session: Session, ctx: UserContext, section_id: uuid.UUID) -> YearResultsOut:
    """FR-YRE-002: every student finishing the year in the section, with SchoolOS's proposal and
    the confirmed result (``year_result.read``, school-wide; 404 for another school's section)."""
    section, year, class_code = _section(session, section_id)
    ids = students.students_finishing_year(
        session, academic_year_id=year.id, section_ids=[section.id]
    )
    histories = students.enrolment_histories(session, ids)
    values = students.canonical_values(session, ids, ["full_name", "admission_no"])
    figures = figures_for(session, year, ids)
    confirmed = yrepo.results_of_year(session, year.id, ids)
    names = _names(session, list(confirmed.values()))
    no_detention = no_detention_class(class_code)
    rows: list[YearResultStudentOut] = []
    for sid in ids:
        roll = next(
            (e.roll_no for e in reversed(histories.get(sid, [])) if e.section_id == section.id),
            None,
        )
        vals = values.get(sid, {})
        name = vals.get("full_name")
        adm = vals.get("admission_no")
        fig = figures[sid]
        row = confirmed.get(sid)
        warnings: list[YearEndWarningCode] = []
        if row is not None and row.result == "detained" and no_detention:
            warnings.append(NO_DETENTION)
        rows.append(
            YearResultStudentOut(
                student=RosterStudentOut(
                    student_id=sid,
                    full_name=name.value if name else None,
                    admission_no=adm.value if adm else None,
                    roll_no=roll,
                ),
                proposal=YearProposalOut(
                    result=propose(fig, class_code=class_code),
                    percent=fig.percent,
                    exams_counted=fig.exams,
                    days_attended=fig.days_attended,
                    working_days=fig.working_days,
                ),
                confirmed=_confirmed_out(row, names) if row is not None else None,
                warnings=warnings,
            )
        )
    rows.sort(key=lambda r: _roll_key(r.student.roll_no, r.student.full_name))
    tally = Counter(r.confirmed.result for r in rows if r.confirmed is not None)
    done = sum(tally.values())
    return YearResultsOut(
        section=RecordSectionOut(
            id=section.id,
            class_id=section.class_id,
            academic_year_id=year.id,
            label=f"{class_code}-{section.name}",
        ),
        academic_year_label=year.label,
        no_detention_class=no_detention,
        rules=_rules_out(),
        counts=YearResultCountsOut(
            students=len(rows),
            confirmed=done,
            unconfirmed=len(rows) - done,
            **{k: tally.get(k, 0) for k in _RESULTS},
        ),
        students=rows,
    )


def held_back(session: Session, ctx: UserContext, year_id: uuid.UUID) -> HeldBackOut:
    """FR-YRE-008: students of ``year_id`` confirmed as ``detained`` (the promotion screen
    pre-fills its held-back list with them), and how many have no confirmed result yet."""
    year = tenancy.get_academic_year(session, year_id)  # 404 for another school's year
    ids = students.students_finishing_year(session, academic_year_id=year.id, section_ids=None)
    confirmed = yrepo.results_of_year(session, year.id, ids)
    detained = sorted((sid for sid, row in confirmed.items() if row.result == "detained"), key=str)
    structure = _year_structure(session, year)
    histories = students.enrolment_histories(session, detained)
    warned: list[uuid.UUID] = []
    for sid in detained:
        final = _final_section(histories.get(sid, []), year.id)
        code = structure.section_class_code.get(final.section_id) if final else None
        if no_detention_class(code):
            warned.append(sid)
    return HeldBackOut(
        academic_year_id=year.id,
        held_back_student_ids=detained,
        no_detention_student_ids=warned,
        confirmed=len(confirmed),
        unconfirmed=len(ids) - len(confirmed),
        no_detention_verified=rules().no_detention.verified,
    )


def progression(session: Session, ctx: UserContext, year_id: uuid.UUID) -> ProgressionOut:
    """FR-YRE-010..FR-YRE-012: the UDISE+ progression check sheet of ``year_id``: per student
    finishing the year, class and section, the confirmed result, % marks and days attended (the
    figures stored when the result was confirmed, else today's), the PEN, and the blanks."""
    year = tenancy.get_academic_year(session, year_id)
    ids = students.students_finishing_year(session, academic_year_id=year.id, section_ids=None)
    if len(ids) > rules().max_sheet_students:
        raise ValidationFailed(
            [_error("year_id", "too_many_students")],
            detail="This year has too many students for one sheet.",
        )
    structure = _year_structure(session, year)
    histories = students.enrolment_histories(session, ids)
    values = students.canonical_values(session, ids, ["full_name", "admission_no", "udise_pen"])
    confirmed = yrepo.results_of_year(session, year.id, ids)
    figures = figures_for(session, year, [sid for sid in ids if sid not in confirmed])
    rows: list[ProgressionRowOut] = []
    blank_counts: Counter[str] = Counter()
    for sid in ids:
        final = _final_section(histories.get(sid, []), year.id)
        section_id = final.section_id if final else None
        vals = values.get(sid, {})
        row = confirmed.get(sid)
        if row is not None:
            percent, days, worked = _float(row.percent), row.days_attended, row.working_days
        else:
            fig = figures[sid]
            percent, days, worked = fig.percent, fig.days_attended, fig.working_days
        pen = vals.get("udise_pen")
        blanks: list[ProgressionBlank] = []
        if row is None:
            blanks.append("result")
        if percent is None:
            blanks.append("percent")
        if days is None:
            blanks.append("days_attended")
        if pen is None or pen.value is None:
            blanks.append("udise_pen")
        blank_counts.update(blanks)
        name = vals.get("full_name")
        adm = vals.get("admission_no")
        rows.append(
            ProgressionRowOut(
                student_id=sid,
                admission_no=adm.value if adm else None,
                full_name=name.value if name else None,
                udise_pen=pen.value if pen else None,
                class_code=structure.section_class_code.get(section_id, "?") if section_id else "?",
                section_label=structure.section_label.get(section_id, "?") if section_id else "?",
                result=row.result if row is not None else None,
                percent=percent,
                days_attended=days,
                working_days=worked,
                blanks=blanks,
            )
        )
    rows.sort(key=lambda r: (r.section_label, (r.admission_no or ""), str(r.student_id)))
    return ProgressionOut(
        academic_year_id=year.id,
        academic_year_label=year.label,
        students=len(rows),
        blank_counts={
            k: blank_counts.get(k, 0) for k in ("result", "percent", "days_attended", "udise_pen")
        },
        rows=rows,
    )


@dataclass(frozen=True, slots=True)
class CsvFile:
    filename: str
    body: bytes
    rows: int


def progression_csv(session: Session, ctx: UserContext, year_id: uuid.UUID) -> CsvFile:
    """FR-YRE-013: the progression sheet as CSV (every cell through ``safe_cell``), audited
    ``year_end.progression_exported`` (year, row and blank counts) in this transaction. The
    route checked ``export.portal``; the caller also needs ``year_result.read`` school-wide."""
    if not (ctx.has(YEAR_RESULT_READ) and ctx.scope_for(YEAR_RESULT_READ).school_wide):
        raise Forbidden("You need permission to see annual results to download this sheet.")
    sheet = progression(session, ctx, year_id)
    body = write_csv(
        CSV_HEADER,
        [
            (
                r.admission_no,
                r.full_name,
                r.udise_pen,
                r.class_code,
                r.section_label,
                RESULT_LABELS.get(r.result or "", ""),
                "" if r.percent is None else f"{r.percent:.1f}",
                r.days_attended,
                r.working_days,
                " ".join(r.blanks),
            )
            for r in sheet.rows
        ],
    )
    audit.record(
        session,
        action="year_end.progression_exported",
        resource_type="academic_year",
        resource_id=sheet.academic_year_id,
        summary={"rows": sheet.students, "blank_counts": sheet.blank_counts},
        actor_type="user",
        request_id=_request_id(),
    )
    log.info(
        "year_end.progression_exported",
        resource_type="academic_year",
        resource_id=sheet.academic_year_id,
        rows=sheet.students,
    )
    safe_label = "".join(ch if ch.isalnum() else "-" for ch in sheet.academic_year_label)
    return CsvFile(filename=f"udise-progression-{safe_label}.csv", body=body, rows=sheet.students)


# --- confirm --------------------------------------------------------------------------------------


def _check_entries(data: YearResultsWrite, members: Collection[uuid.UUID]) -> None:
    cfg = rules()
    errors: list[dict[str, str]] = []
    seen: set[uuid.UUID] = set()
    allowed = set(members)
    for i, entry in enumerate(data.entries):
        field = f"entries.{i}"
        if entry.student_id in seen:
            errors.append(_error(f"{field}.student_id", "duplicate"))
        seen.add(entry.student_id)
        if entry.student_id not in allowed:
            errors.append(_error(f"{field}.student_id", "not_in_section"))
        if entry.result == "detained" and not entry.reason:
            errors.append(_error(f"{field}.reason", "reason_required"))
        if entry.reason is not None:
            if len(entry.reason) > cfg.max_reason_chars:
                errors.append(_error(f"{field}.reason", "too_long"))
            elif contains_full_aadhaar(entry.reason):
                errors.append(_error(f"{field}.reason", "aadhaar_full_number_rejected"))
    if len(data.entries) > cfg.max_entries:
        errors.append(_error("entries", "too_many"))
    if errors:
        raise ValidationFailed(
            errors[:_ERRORS_SHOWN], detail="Some results cannot be saved. Fix them and try again."
        )


def _changed() -> PreconditionFailed:
    return PreconditionFailed(
        "Someone else changed these results. Reload them and check before saving again.",
        code=CHANGED_CODE,
    )


def confirm_results(
    session: Session, ctx: UserContext, section_id: uuid.UUID, data: YearResultsWrite
) -> YearResultsWriteOut:
    """FR-YRE-004..FR-YRE-007: confirm (or change) the annual results of students finishing the
    year in the section, all or nothing (``year_result.confirm``, school-wide). Each entry keeps
    what SchoolOS proposed and the figures shown, who and when; ``detained`` needs a reason (422
    ``reason_required``); a student not finishing the year in the section is 422
    ``not_in_section``; a stale ``expected_version`` is 412 ``year_result_changed``. In a
    no-detention class ``detained`` is saved with the warning ``no_detention_class``.
    Audit ``year_result.confirmed`` (section, year, counts, the detained ids) in the same
    transaction."""
    section, year, class_code = _section(session, section_id)
    members = students.students_finishing_year(
        session, academic_year_id=year.id, section_ids=[section.id]
    )
    _check_entries(data, members)
    wanted = [e.student_id for e in data.entries]
    existing = yrepo.results_of_year(session, year.id, wanted, lock=True)
    for entry in data.entries:
        current = existing.get(entry.student_id)
        if entry.expected_version is not None and entry.expected_version != (
            current.version if current is not None else 0
        ):
            raise _changed()
    figures = figures_for(session, year, wanted)
    cfg = rules()
    tenant_id = repo.current_tenant_id(session)
    no_detention = no_detention_class(class_code)
    written = unchanged = changed_results = 0
    detained_ids: list[uuid.UUID] = []
    tally: Counter[str] = Counter()
    warnings: list[YearEndWarningOut] = []
    for entry in data.entries:
        fig = figures[entry.student_id]
        if entry.result == "detained":
            detained_ids.append(entry.student_id)
            if no_detention:
                warnings.append(YearEndWarningOut(code=NO_DETENTION, student_id=entry.student_id))
        current = existing.get(entry.student_id)
        if (
            current is not None
            and current.result == entry.result
            and (current.reason == entry.reason)
        ):
            unchanged += 1
            continue
        values: dict[str, Any] = {
            "section_id": section.id,
            "result": entry.result,
            "proposed_result": propose(fig, class_code=class_code),
            "percent": Decimal(str(fig.percent)) if fig.percent is not None else None,
            "days_attended": fig.days_attended,
            "working_days": fig.working_days,
            "reason": entry.reason,
            "rule_version": cfg.rule_version,
            "confirmed_by": ctx.user_id,
        }
        try:
            if current is None:
                row = yrepo.insert_result(
                    session,
                    {
                        **values,
                        "id": new_id(),
                        "tenant_id": tenant_id,
                        "academic_year_id": year.id,
                        "student_id": entry.student_id,
                    },
                )
            else:
                changed_results += int(current.result != entry.result)
                row = yrepo.update_result(
                    session,
                    current.id,
                    expected_version=current.version,
                    values={**values, "confirmed_at": func.now()},
                )
        except IntegrityError as exc:  # pragma: no cover - CHECKs mirror the validation above
            raise Conflict("The result could not be saved.", code="year_result_invalid") from exc
        if row is None:
            raise _changed()
        written += 1
        tally[entry.result] += 1
    if written:
        audit.record(
            session,
            action="year_result.confirmed",
            resource_type="section",
            resource_id=section.id,
            summary={
                "academic_year_id": year.id,
                "written": written,
                "unchanged": unchanged,
                "changed_results": changed_results,
                "counts": {k: tally.get(k, 0) for k in _RESULTS},
                "detained_student_ids": sorted(str(s) for s in detained_ids),
                "no_detention_warnings": len(warnings),
                "rule_version": cfg.rule_version,
            },
            actor_type="user",
            request_id=_request_id(),
        )
    log.info(
        "year_result.confirmed",
        resource_type="section",
        resource_id=section.id,
        written=written,
        unchanged=unchanged,
    )
    return YearResultsWriteOut(written=written, unchanged=unchanged, warnings=warnings)


# --- TC suggestions -------------------------------------------------------------------------------

_TC_PROMOTION: Final = {"passed": "promoted", "promoted": "promoted", "detained": "not_promoted"}


def tc_suggestions(
    session: Session, student_id: uuid.UUID, *, today: dt.date
) -> list[TcSuggestion]:
    """FR-YRE-014..FR-YRE-016: TC inputs SchoolOS can suggest for ``student_id`` (a student the
    caller already reached with ``certificate.issue``): "qualified for promotion" from the
    confirmed result of the student's current (else last) year, and working days and days
    attended in that year up to ``today``. Empty when the records say nothing."""
    rows = students.enrolment_histories(session, [student_id]).get(student_id, [])
    active = [e for e in rows if e.status == "active"]
    chosen = active[-1] if active else (rows[-1] if rows else None)
    if chosen is None:
        return []
    year = tenancy.get_academic_year(session, chosen.academic_year_id)
    out: list[TcSuggestion] = []
    confirmed = yrepo.results_of_year(session, year.id, [student_id]).get(student_id)
    if confirmed is not None and confirmed.result in _TC_PROMOTION:
        out.append(
            TcSuggestion(
                key="promotion", value=_TC_PROMOTION[confirmed.result], source="year_result"
            )
        )
    fig = figures_for(session, year, [student_id], until=today).get(student_id)
    if fig is not None and fig.working_days is not None:
        out.append(
            TcSuggestion(
                key="working_days", value=str(fig.working_days), source="attendance_register"
            )
        )
        out.append(
            TcSuggestion(
                key="days_attended",
                value=str(fig.days_attended or 0),
                source="attendance_register",
            )
        )
    return out


# --- access report (FR-DPDP-009) ------------------------------------------------------------------


def results_for_report(session: Session, student_id: uuid.UUID) -> list[YearResultRow]:
    return yrepo.results_of_student(session, student_id)


__all__ = [
    "CSV_MEDIA_TYPE",
    "EXPORT_PORTAL",
    "YEAR_RESULT_CONFIRM",
    "YEAR_RESULT_READ",
    "CsvFile",
    "Figures",
    "confirm_results",
    "figures_for",
    "held_back",
    "no_detention_class",
    "progression",
    "progression_csv",
    "propose",
    "results_for_report",
    "rules",
    "tc_suggestions",
    "year_results",
]
