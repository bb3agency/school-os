"""The daily read-notification purge treats every school on its own (FR-NOT-001): each school in
its own tenant_session; a failing school is logged with its id and error type only and the other
schools are still purged."""

from __future__ import annotations

import contextlib
import uuid

import pytest
from structlog.testing import capture_logs

from app.notifications import service, tasks
from app.tenancy import service as tenancy


def test_FR_NOT_001_one_failing_school_does_not_stop_the_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, broken, last = (uuid.uuid4() for _ in range(3))
    monkeypatch.setattr(tasks, "context_free_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(
        tenancy, "list_tenant_ids", lambda _session, _statuses: [first, broken, last]
    )
    # The fake session is the tenant id itself: one session per school.
    monkeypatch.setattr(tasks, "tenant_session", contextlib.nullcontext)
    calls: list[uuid.UUID] = []

    def purge(session: uuid.UUID) -> int:
        calls.append(session)
        if session == broken:
            raise RuntimeError("database error quoting Synthetic School")
        return 2

    monkeypatch.setattr(service, "purge_read", purge)
    with capture_logs() as logs:
        result = tasks.purge_all()
    assert calls == [first, broken, last]
    assert result == {"tenants": 3, "purged": 4, "failed": 1}
    failed = [e for e in logs if e["event"] == "notifications.purge_failed"]
    assert len(failed) == 1
    assert failed[0]["tenant_id"] == broken
    assert failed[0]["error_type"] == "RuntimeError"
    assert "Synthetic School" not in str(logs)  # ids and error types only (invariant 5)
