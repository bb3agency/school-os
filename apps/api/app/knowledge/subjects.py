"""Ask history in a data principal's access report and erasure (R3-1A; FR-DPDP-009,
FR-DPDP-013, PRV-031, PRV-032; ADR-0048 decisions 6 and 8).

The rows are keyed by the user (account) id; the subject is a staff member
(``core.memberships.id``), mapped through ``identity.service.user_id_for_membership``.

Report: how many questions the member asked, their conversations (with the titles, decrypted
with this module's own helper: titles are the member's own words about their own chats) and
memory items, and the memory switch. The questions, answers and memory texts are not
repeated: answers quote school records the requester may not be entitled to, and the question
texts are counted, not printed (the member can read them in Ask while they are kept).

Erasure: every question (the encrypted query log, so the answer cache that reuses them goes
too), conversation (title and summary), memory item and memory setting of the member in this
school, in the caller's transaction, audited ``kb.ask_history.erased`` with counts only. The
metering ledger ``kb.llm_calls`` keeps its rows (ids, counts and costs only).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.logging import get_context
from app.core.report_format import cap_rows, fmt_bool, fmt_count, fmt_date, fmt_text
from app.core.subjects import MAX_SECTION_ROWS, ErasureOutcome, ReportSection, Subject
from app.identity import service as identity
from app.knowledge import repository as repo
from app.knowledge.sealed import title_of

CLASS_KEY = "ask_history"
ERASED_EVENT = "kb.ask_history.erased"


def _user_of(session: Session, subject: Subject) -> uuid.UUID | None:
    if subject.kind != "member":
        return None
    return identity.user_id_for_membership(session, subject.id)


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The member's Ask history in counts plus their conversation titles ([] for other kinds,
    an unknown member or a member who never used Ask)."""
    user_id = _user_of(session, subject)
    if user_id is None:
        return []
    counts = repo.ask_history_counts(session, user_id)
    if counts.total == 0:
        return []
    switch = repo.memory_enabled(session, user_id)
    summary = ReportSection(
        class_key=CLASS_KEY,
        title='Questions to "Ask the school"',
        columns=("Detail", "Value"),
        rows=(
            ("Questions asked (kept for 180 days)", fmt_count(counts.queries)),
            ("Conversations", fmt_count(counts.conversations)),
            (
                "Conversations removed from the history (questions kept until deleted)",
                fmt_count(counts.removed_conversations),
            ),
            ("Memory items", fmt_count(counts.memories)),
            ("Memory switched on", fmt_bool(switch is not False)),
        ),
        note="Question and answer texts are not repeated here; they are shown in Ask.",
    )
    sections = [summary]
    if counts.conversations:
        rows = [
            (fmt_text(title_of(session, c)), fmt_date(c.created_at), fmt_date(c.updated_at))
            for c in repo.conversations_of(session, user_id, MAX_SECTION_ROWS)
        ]
        kept, note = cap_rows(rows, counts.conversations)
        sections.append(
            ReportSection(
                class_key=CLASS_KEY,
                title="Conversations",
                columns=("Title", "Started on", "Last used on"),
                rows=tuple(kept),
                note=note,
            )
        )
    return sections


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def erase_for_subject(
    session: Session, subject: Subject, class_keys: Collection[str], *, dry_run: bool
) -> list[ErasureOutcome]:
    """Delete the member's questions, conversations, memory items and memory setting (a dry run
    only counts them). ``count`` is the number of rows."""
    if CLASS_KEY not in class_keys:
        return []
    user_id = _user_of(session, subject)
    if user_id is None:
        return []
    if dry_run:
        return [ErasureOutcome(CLASS_KEY, repo.ask_history_counts(session, user_id).total)]
    erased = repo.delete_ask_history_of(session, user_id)
    audit.record(
        session,
        action=ERASED_EVENT,
        resource_type="membership",
        resource_id=subject.id,
        summary={
            "queries": erased.queries,
            "conversations": erased.conversations + erased.removed_conversations,
            "memories": erased.memories,
            "memory_settings": erased.memory_settings,
        },
        request_id=_request_id(),
    )
    return [ErasureOutcome(CLASS_KEY, erased.total)]


__all__ = ["ERASED_EVENT", "erase_for_subject", "subject_report"]
