"""The daily export-file purge treats every school on its own (docs/05 §13; FR-EXP-004): a
school whose purge fails is logged with its id and error type only, the other schools are still
purged, and the counts say how many failed (their files are due again on the next run)."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.exports import service, tasks


def test_FR_EXP_004_one_failing_school_does_not_stop_the_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, broken, last = (uuid.uuid4() for _ in range(3))
    monkeypatch.setattr(tasks, "context_free_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(
        tasks.tenancy, "list_tenant_ids", lambda _session, _statuses: [first, broken, last]
    )
    calls: list[uuid.UUID] = []

    def purge(tenant_id: uuid.UUID, **_kw: Any) -> int:
        calls.append(tenant_id)
        if tenant_id == broken:
            raise RuntimeError("storage unavailable for Synthetic School")
        return 2

    monkeypatch.setattr(service, "purge_expired", purge)
    with capture_logs() as logs:
        result = tasks.purge_all()
    assert calls == [first, broken, last]
    assert result == {"tenants": 3, "purged": 4, "failed": 1}
    failed = [e for e in logs if e["event"] == "exports.purge_failed"]
    assert len(failed) == 1
    assert failed[0]["tenant_id"] == broken
    assert failed[0]["error_type"] == "RuntimeError"
    assert "Synthetic School" not in str(logs)  # ids and error types only (invariant 5)
    done = next(e for e in logs if e["event"] == "exports.purge_done")
    assert (done["count"], done["failed"]) == (4, 1)
