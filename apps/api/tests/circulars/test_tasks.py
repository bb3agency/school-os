"""The notice-drafting task (FR-NOTICE-003; docs/04 §6 task contract): IDs-only arguments,
retries with backoff, and giving up leaves the notice ``draft_failed`` (``worker_error``) so it
can be tried again or written by hand. Synthetic data only."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.circulars import service, tasks
from app.core.db import tenant_session

from .conftest import C


def _payload(tenant_id: uuid.UUID, notice_id: uuid.UUID) -> dict[str, Any]:
    return {
        "tenant_id": str(tenant_id),
        "event_id": str(uuid.uuid4()),
        "payload": {"notice_id": str(notice_id)},
    }


def test_FR_NOTICE_003_failures_retry_before_giving_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eager execution replays retries at once: every attempt runs, then the notice is given up
    exactly once with ``worker_error``."""
    attempts: list[uuid.UUID] = []
    abandoned: list[tuple[uuid.UUID, str]] = []

    def boom(tenant_id: uuid.UUID, notice_id: uuid.UUID) -> str:
        attempts.append(notice_id)
        raise RuntimeError("database unavailable")

    def give_up(tenant_id: uuid.UUID, notice_id: uuid.UUID, code: str) -> None:
        abandoned.append((notice_id, code))

    monkeypatch.setattr(service, "run_notice_draft", boom)
    monkeypatch.setattr(service, "abandon_draft", give_up)
    notice_id = uuid.uuid4()
    result = tasks.draft_notice.apply(kwargs=_payload(uuid.uuid4(), notice_id), retries=0)
    assert result.failed()
    assert len(attempts) == tasks.MAX_RETRIES + 1
    assert abandoned == [(notice_id, "worker_error")]


@pytest.mark.db
def test_FR_NOTICE_003_the_task_drafts_and_giving_up_marks_the_notice_failed(
    ai_on: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    school = ai_on.a
    actor = C.ctx(school, "office_staff")
    from app.circulars.schemas import NoticeCreate

    body = NoticeCreate(source="staff_text", text="Sports day is on 14/11/2026.")
    with tenant_session(school.tenant_id, actor.user_id) as db:
        drafted = service.create_notice(db, actor, body)
        stuck = service.create_notice(db, actor, body)
    ok = tasks.draft_notice.apply(kwargs=_payload(school.tenant_id, drafted.id))
    assert ok.get() == "draft"

    def boom(tenant_id: uuid.UUID, notice_id: uuid.UUID) -> str:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service, "run_notice_draft", boom)
    failed = tasks.draft_notice.apply(
        kwargs=_payload(school.tenant_id, stuck.id), retries=tasks.MAX_RETRIES
    )
    assert failed.failed()
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = service.get_notice(db, actor, stuck.id)
        done = service.get_notice(db, actor, drafted.id)
    assert (out.status, out.draft_error) == ("draft_failed", "worker_error")
    assert (done.status, done.ai_drafted) == ("draft", True)
    # Giving up again changes nothing (only a drafting notice is given up).
    service.abandon_draft(school.tenant_id, drafted.id, "worker_error")
    with tenant_session(school.tenant_id, actor.user_id) as db:
        assert service.get_notice(db, actor, drafted.id).status == "draft"


@pytest.mark.db
def test_FR_NOTICE_003_a_notice_whose_text_is_gone_fails_with_a_code(
    ai_on: Any, admin_engine: Engine
) -> None:
    """A staff-text notice whose text is no longer kept (dropped by a migration downgrade) is
    not drafted from nothing: ``draft_failed`` with ``source_unavailable``."""
    from sqlalchemy import text

    from app.circulars.schemas import NoticeCreate

    school = ai_on.a
    actor = C.ctx(school, "office_staff")
    body = NoticeCreate(source="staff_text", text="Sports day is on 14/11/2026.")
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = service.create_notice(db, actor, body)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE ops.parent_notices SET source_text = NULL WHERE id = :i"), {"i": out.id}
        )
    assert C.draft_now(school, out.id) == "source_unavailable"
