"""The daily query-log purge treats every school on its own (docs/05 §13; FR-KB-009): a school
whose purge fails (its transaction rolls back) is logged with its id and error type only, the
other schools are still purged, and the counts say how many failed (retried on the next run)."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.knowledge import service, tasks


def test_FR_KB_009_one_failing_school_does_not_stop_the_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, broken, last = (uuid.uuid4() for _ in range(3))
    monkeypatch.setattr(tasks, "context_free_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(
        tasks.tenancy, "list_tenant_ids", lambda _session, _statuses: [first, broken, last]
    )
    sessions: list[uuid.UUID] = []

    @contextlib.contextmanager
    def session_of(tenant_id: uuid.UUID, *_a: Any, **_kw: Any) -> Iterator[uuid.UUID]:
        sessions.append(tenant_id)
        yield tenant_id

    def purge(session: Any, **_kw: Any) -> int:
        if session == broken:
            raise RuntimeError("database unavailable for Synthetic School")
        return 3

    monkeypatch.setattr(tasks, "tenant_session", session_of)
    monkeypatch.setattr(service, "purge_old_queries", purge)
    with capture_logs() as logs:
        result = tasks.purge_queries_all()
    assert sessions == [first, broken, last]
    assert result == {"tenants": 3, "purged": 6, "failed": 1}
    failed = [e for e in logs if e["event"] == "knowledge.queries.purge_failed"]
    assert len(failed) == 1
    assert failed[0]["tenant_id"] == broken
    assert failed[0]["error_type"] == "RuntimeError"
    assert "Synthetic School" not in str(logs)  # ids and error types only (invariant 5)
    done = next(e for e in logs if e["event"] == "knowledge.queries.purged")
    assert (done["count"], done["failed"]) == (6, 1)
