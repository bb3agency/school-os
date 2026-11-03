"""The daily messaging retention job (docs/05 §13: provider events 30 days, inbound kinds 90
days, message variables 180 days, messages 13 months) runs for every school whose data is
still kept, suspended and offboarding ones included: retention is data minimisation, not a
service to the school. Sending, syncing and the inbox stay with active schools only."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Sequence

import pytest

from app.messaging import service, tasks
from app.messaging.sends import service as sends
from app.tenancy import service as tenancy


def test_FR_MSG_023_retention_runs_for_suspended_schools(monkeypatch: pytest.MonkeyPatch) -> None:
    active, suspended = uuid.uuid4(), uuid.uuid4()
    by_status = {"active": [active], "suspended": [suspended], "offboarding": []}

    def list_ids(_session: object, statuses: Sequence[str] | None) -> list[uuid.UUID]:
        return [t for s in statuses or by_status for t in by_status.get(s, [])]

    monkeypatch.setattr(service, "available", lambda *a, **k: True)
    monkeypatch.setattr(service, "context_free_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(tenancy, "list_tenant_ids", list_ids)
    purged: list[uuid.UUID] = []

    def purge(tenant_id: uuid.UUID) -> dict[str, int]:
        purged.append(tenant_id)
        return {}

    monkeypatch.setattr(service, "purge_expired", purge)
    monkeypatch.setattr(sends, "purge_expired", lambda t: {})
    synced: list[uuid.UUID] = []
    monkeypatch.setattr(service, "sync_account", synced.append)
    monkeypatch.setattr(service, "sync_templates", lambda t: {})

    assert tasks.purge_expired() == {"schools": 2, "failed": 0}
    assert sorted(purged, key=str) == sorted([active, suspended], key=str)
    tasks.sync_accounts()
    assert synced == [active]
