"""Export tasks: IDs-only arguments, retries, and giving up marks the export failed
(FR-EXP-002; docs/04 §6 task contract)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.exports import service, tasks

EX = sys.modules["sos_test_exports_objects"]


def _payload(export_id: uuid.UUID) -> dict[str, Any]:
    return {
        "tenant_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "payload": {"export_id": str(export_id)},
    }


def test_last_retry_abandons_the_export(monkeypatch: pytest.MonkeyPatch) -> None:
    abandoned: list[tuple[uuid.UUID, str]] = []

    def boom(tenant_id: uuid.UUID, export_id: uuid.UUID) -> str:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(service, "run_export", boom)

    def give_up(tenant_id: uuid.UUID, export_id: uuid.UUID, code: str = "") -> bool:
        abandoned.append((export_id, code))
        return True

    monkeypatch.setattr(service, "abandon", give_up)
    export_id = uuid.uuid4()
    result = tasks.generate.apply(kwargs=_payload(export_id), retries=tasks.MAX_RETRIES)
    assert result.failed()
    assert abandoned == [(export_id, "worker_error")]


def test_failures_retry_before_giving_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eager execution replays retries at once: every attempt runs, then the export is
    abandoned exactly once."""
    attempts: list[uuid.UUID] = []
    abandoned: list[uuid.UUID] = []

    def boom(tenant_id: uuid.UUID, export_id: uuid.UUID) -> str:
        attempts.append(export_id)
        raise RuntimeError("renderer down")

    monkeypatch.setattr(service, "run_export", boom)

    def give_up(tenant_id: uuid.UUID, export_id: uuid.UUID, code: str = "") -> bool:
        abandoned.append(export_id)
        return True

    monkeypatch.setattr(service, "abandon", give_up)
    export_id = uuid.uuid4()
    result = tasks.render.apply(kwargs=_payload(export_id), retries=0)
    assert result.failed()
    assert len(attempts) == tasks.MAX_RETRIES + 1
    assert abandoned == [export_id]


@pytest.mark.db
def test_task_runs_the_export(school: Any, admin_engine: Engine) -> None:
    EX.student(school)
    export_id = EX.queued_export(school, "principal")
    payload = {
        "tenant_id": str(school.tenant_id),
        "event_id": str(uuid.uuid4()),
        "payload": {"export_id": str(export_id)},
    }
    assert tasks.generate.apply(kwargs=payload).get() == "ready"
    assert EX.export_row(admin_engine, export_id)["status"] == "ready"
    # Delivered twice (at-least-once): nothing happens the second time.
    assert tasks.generate.apply(kwargs=payload).get() == "ready"
    assert len(EX.files(admin_engine, export_id)) == 1
