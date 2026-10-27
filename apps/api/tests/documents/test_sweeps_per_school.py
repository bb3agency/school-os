"""The daily document sweeps treat every school on its own (FR-DOC-007, PRV-016): one school
whose store or database fails is logged with its id and error type only and the other schools
are still swept; the failed school is due again on the next run."""

from __future__ import annotations

import contextlib
import uuid

import pytest
from structlog.testing import capture_logs

from app.documents import service, tasks
from app.tenancy import service as tenancy


def _three_schools(monkeypatch: pytest.MonkeyPatch) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    first, broken, last = (uuid.uuid4() for _ in range(3))
    monkeypatch.setattr(tasks, "context_free_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(
        tenancy, "list_tenant_ids", lambda _session, _statuses: [first, broken, last]
    )
    return first, broken, last


@pytest.mark.parametrize(
    ("job", "key", "event"),
    [
        ("purge_expired_uploads", "purged", "documents.purge_uploads_failed"),
        ("sweep_discarded_objects", "discarded", "documents.sweep_discarded_failed"),
    ],
)
def test_FR_DOC_007_PRV_016_one_failing_school_does_not_stop_the_sweep(
    monkeypatch: pytest.MonkeyPatch, job: str, key: str, event: str
) -> None:
    first, broken, last = _three_schools(monkeypatch)
    calls: list[uuid.UUID] = []

    def run(tenant_id: uuid.UUID) -> int:
        calls.append(tenant_id)
        if tenant_id == broken:
            raise RuntimeError("storage unavailable for Synthetic School")
        return 2

    monkeypatch.setattr(service, job, run)
    with capture_logs() as logs:
        result = getattr(tasks, job)()
    assert calls == [first, broken, last]
    assert result == {key: 4, "failed": 1}
    failed = [e for e in logs if e["event"] == event]
    assert len(failed) == 1
    assert failed[0]["tenant_id"] == broken
    assert failed[0]["error_type"] == "RuntimeError"
    assert "Synthetic School" not in str(logs)  # ids and error types only (invariant 5)
