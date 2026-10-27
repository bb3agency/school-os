"""The daily raw-file purge treats every school on its own (FR-IMP-007): a failing school is
logged with its id and error type only and the other schools are still purged."""

from __future__ import annotations

import contextlib
import uuid

import pytest
from structlog.testing import capture_logs

from app.imports import service, tasks
from app.tenancy import service as tenancy


def test_FR_IMP_007_one_failing_school_does_not_stop_the_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, broken, last = (uuid.uuid4() for _ in range(3))
    monkeypatch.setattr(tasks, "context_free_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(
        tenancy, "list_tenant_ids", lambda _session, _statuses: [first, broken, last]
    )
    calls: list[uuid.UUID] = []

    def purge(tenant_id: uuid.UUID) -> int:
        calls.append(tenant_id)
        if tenant_id == broken:
            raise RuntimeError("storage unavailable for Synthetic School")
        return 2

    monkeypatch.setattr(service, "purge_raw_files", purge)
    with capture_logs() as logs:
        result = tasks.purge_all()
    assert calls == [first, broken, last]
    assert result == {"tenants": 3, "purged": 4, "failed": 1}
    failed = [e for e in logs if e["event"] == "imports.raw_files.purge_failed"]
    assert len(failed) == 1
    assert failed[0]["tenant_id"] == broken
    assert failed[0]["error_type"] == "RuntimeError"
    assert "Synthetic School" not in str(logs)  # ids and error types only (invariant 5)
