"""Data-principal access report and erasure for the insights module (R3-1A; FR-DPDP-009,
FR-DPDP-012..014, ADR-0048 decisions 6 and 8, PRV-031, PRV-032).

**Report.** A student's behaviour notes (date, category and the note text, decrypted: it is the
child's own record; the author is left out, being another person's identity) and early-warning
flags (indicator, rule, status, raised and closed dates, closing reason and how many actions
were recorded; action notes are staff free text and are never shown).

**Erasure.** ``behaviour_notes`` deletes the student's notes, ``early_warning`` deletes their
flags (their action logs go with them: ``ON DELETE CASCADE``), each audited like the
module's own erase paths (``insights.note_erased`` / ``insights.flag_erased`` with reason
``privacy_request``; ids, codes and counts only). ``sis.insight_settings`` is school
configuration and is never touched.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping
from typing import Any, Final

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.logging import get_context
from app.core.report_format import cap_rows, fmt_count, fmt_date, fmt_text, humanise
from app.core.subjects import ErasureOutcome, ReportSection, Subject
from app.insights import crypto
from app.insights import repository as repo

NOTES_CLASS: Final = "behaviour_notes"
FLAGS_CLASS: Final = "early_warning"
REASON: Final = "privacy_request"
NOTE_COLUMNS: Final = ("Date", "Category", "Note")
FLAG_COLUMNS: Final = (
    "Indicator",
    "Rule",
    "Status",
    "Raised on",
    "Closed on",
    "Closing reason",
    "Actions recorded",
)


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    summary: Mapping[str, Any],
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        request_id=_request_id(),
    )


# --- report ---------------------------------------------------------------------------------------


def _notes(session: Session, student_id: uuid.UUID) -> ReportSection | None:
    notes = repo.notes_of(session, student_id)  # newest first
    if not notes:
        return None
    kept, note = cap_rows(notes)
    rows = tuple(
        (
            fmt_date(n.noted_on),
            humanise(n.category),
            fmt_text(crypto.decrypt_note(session, n.body_ciphertext, n.id)),
        )
        for n in kept
    )
    return ReportSection(
        class_key=NOTES_CLASS, title="Behaviour notes", columns=NOTE_COLUMNS, rows=rows, note=note
    )


def _flags(session: Session, student_id: uuid.UUID) -> ReportSection | None:
    flags = repo.flags_of(session, student_id)  # newest first
    if not flags:
        return None
    kept, note = cap_rows(flags)
    actions = repo.actions_of(session, [f.id for f in kept])
    rows = tuple(
        (
            humanise(f.indicator),
            humanise(f.rule),
            humanise(f.status),
            fmt_date(f.raised_on),
            fmt_date(f.closed_at),
            humanise(f.close_reason),
            fmt_count(len(actions.get(f.id, []))),
        )
        for f in kept
    )
    return ReportSection(
        class_key=FLAGS_CLASS,
        title="Early-warning flags",
        columns=FLAG_COLUMNS,
        rows=rows,
        note=note,
    )


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The student's behaviour notes and early-warning flags; [] for other subject kinds or
    when nothing is held."""
    if subject.kind != "student":
        return []
    sections = [_notes(session, subject.id), _flags(session, subject.id)]
    return [s for s in sections if s is not None]


# --- erasure --------------------------------------------------------------------------------------


def _erase_notes(session: Session, student_id: uuid.UUID, *, dry_run: bool) -> ErasureOutcome:
    notes = repo.notes_of(session, student_id)
    if not dry_run:
        for n in notes:
            repo.delete_note(session, n.id)
            _audit(
                session,
                "insights.note_erased",
                "student",
                student_id,
                {"note_id": n.id, "reason": REASON, "category": n.category},
            )
    return ErasureOutcome(class_key=NOTES_CLASS, count=len(notes))


def _erase_flags(session: Session, student_id: uuid.UUID, *, dry_run: bool) -> ErasureOutcome:
    flags = repo.flags_of(session, student_id)
    actions = repo.actions_of(session, [f.id for f in flags])
    count = len(flags) + sum(len(a) for a in actions.values())
    if not dry_run:
        for f in flags:
            repo.delete_flag(session, f.id)  # the action log goes with it (ON DELETE CASCADE)
            _audit(
                session,
                "insights.flag_erased",
                "insight_flag",
                f.id,
                {
                    "reason": REASON,
                    "rule": f.rule,
                    "student_id": student_id,
                    "actions": len(actions.get(f.id, [])),
                },
            )
    return ErasureOutcome(class_key=FLAGS_CLASS, count=count)


def erase_for_subject(
    session: Session, subject: Subject, class_keys: Collection[str], *, dry_run: bool
) -> list[ErasureOutcome]:
    """Erase (or with ``dry_run`` count) the student's behaviour notes and early-warning flags
    with their action logs, in the caller's transaction. Counts are rows: notes; flags plus
    actions."""
    if subject.kind != "student":
        return []
    keys = set(class_keys)
    out: list[ErasureOutcome] = []
    if NOTES_CLASS in keys:
        out.append(_erase_notes(session, subject.id, dry_run=dry_run))
    if FLAGS_CLASS in keys:
        out.append(_erase_flags(session, subject.id, dry_run=dry_run))
    return out


__all__ = ["erase_for_subject", "subject_report"]
