"""The daily payments job deletes processed webhook inbox rows (encrypted provider payloads) 90
days after processing (docs/05 §13) for every school whose data is still kept, suspended and
offboarding ones included; token refreshes stay with active schools. A school whose token
refresh fails is still purged."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Sequence

import pytest

from app.fees import online, tasks_online
from app.fees.providers import ProviderError
from app.tenancy import service as tenancy


def test_FR_FEE_027_inbox_retention_runs_for_suspended_schools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active, suspended = uuid.uuid4(), uuid.uuid4()
    by_status = {"active": [active], "suspended": [suspended], "offboarding": []}

    def list_ids(_session: object, statuses: Sequence[str] | None) -> list[uuid.UUID]:
        return [t for s in statuses or by_status for t in by_status.get(s, [])]

    monkeypatch.setattr(online, "context_free_session", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(tenancy, "list_tenant_ids", list_ids)
    purged: list[uuid.UUID] = []
    refreshed: list[uuid.UUID] = []

    def purge(tenant_id: uuid.UUID) -> int:
        purged.append(tenant_id)
        return 0

    def refresh(tenant_id: uuid.UUID) -> str:
        refreshed.append(tenant_id)
        raise ProviderError("provider unreachable")

    monkeypatch.setattr(online, "purge_events", purge)
    monkeypatch.setattr(online, "refresh_tokens", refresh)

    tasks_online.payments_daily()
    assert sorted(purged, key=str) == sorted([active, suspended], key=str)
    assert refreshed == [active]
