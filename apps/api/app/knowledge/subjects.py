"""Ask history in a data principal's access report and erasure (R3-1A; FR-DPDP-009,
FR-DPDP-013, PRV-031, PRV-032; ADR-0048 decisions 6 and 8).

The rows are keyed by the user (account) id; the subject is a staff member
(``core.memberships.id``), mapped through ``identity.service.user_id_for_membership``.

Report: how many questions the member asked and their conversations (with the titles, decrypted
with this module's own helper: titles are the member's own words about their own chats) under
``ask_history``; their memory items and the memory switch under ``ask_memory`` (kept until the
person deletes them or leaves, not 180 days; docs/05 §13). The questions, answers and memory
texts are not repeated: answers quote school records the requester may not be entitled to,
and the question texts are counted, not printed (the member can read them in Ask while they
are kept).

Erasure, per class asked for: ``ask_history`` deletes every question (the encrypted query log,
so the answer cache that reuses them goes too) and conversation (title and summary);
``ask_memory`` deletes every memory item and the memory setting. Both of the member in this
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
MEMORY_CLASS_KEY = "ask_memory"
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
    sections: list[ReportSection] = []
    if counts.queries or counts.conversations or counts.removed_conversations:
        sections.append(
            ReportSection(
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
                ),
                note="Question and answer texts are not repeated here; they are shown in Ask.",
            )
        )
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
    if counts.memories or counts.memory_settings:
        switch = repo.memory_enabled(session, user_id)
        sections.append(
            ReportSection(
                class_key=MEMORY_CLASS_KEY,
                title='What "Ask the school" remembers',
                columns=("Detail", "Value"),
                rows=(
                    ("Memory items (kept until deleted)", fmt_count(counts.memories)),
                    ("Memory switched on", fmt_bool(switch is not False)),
                ),
                note="Memory texts are not repeated here; they are shown in Ask.",
            )
        )
    return sections


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def erase_for_subject(
    session: Session, subject: Subject, class_keys: Collection[str], *, dry_run: bool
) -> list[ErasureOutcome]:
    """Delete the member's questions and conversations (``ask_history``) and/or memory items and
    memory setting (``ask_memory``); a dry run only counts them. ``count`` is the number of
    rows."""
    history = CLASS_KEY in class_keys
    memory = MEMORY_CLASS_KEY in class_keys
    if not (history or memory):
        return []
    user_id = _user_of(session, subject)
    if user_id is None:
        return []
    before = repo.ask_history_counts(session, user_id)
    history_rows = before.queries + before.conversations + before.removed_conversations
    memory_rows = before.memories + before.memory_settings
    outcomes: list[ErasureOutcome] = []
    if dry_run:
        if history:
            outcomes.append(ErasureOutcome(CLASS_KEY, history_rows))
        if memory:
            outcomes.append(ErasureOutcome(MEMORY_CLASS_KEY, memory_rows))
        return outcomes
    queries = conversations = memories = memory_settings = 0
    if memory:
        # Memory first: its links to questions and conversations would be set NULL anyway.
        memories = repo.delete_memory_data_of(session, [user_id])
        memory_settings = before.memory_settings
        outcomes.append(ErasureOutcome(MEMORY_CLASS_KEY, memories + memory_settings))
    if history:
        erased = repo.delete_questions_of(session, user_id)
        queries = erased.queries
        conversations = erased.conversations + erased.removed_conversations
        outcomes.append(ErasureOutcome(CLASS_KEY, queries + conversations))
    audit.record(
        session,
        action=ERASED_EVENT,
        resource_type="membership",
        resource_id=subject.id,
        summary={
            "queries": queries,
            "conversations": conversations,
            "memories": memories,
            "memory_settings": memory_settings,
        },
        request_id=_request_id(),
    )
    return outcomes


__all__ = ["ERASED_EVENT", "erase_for_subject", "subject_report"]
