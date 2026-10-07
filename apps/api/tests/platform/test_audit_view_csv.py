"""The platform audit CSV neutralises formulas through ``safe_cell`` (SEC-017; audit 2026-10-04
data-layer hardening note 4). Pure: the events are built in memory."""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid

import pytest

from app.platform.audit_view import CSV_COLUMNS, to_csv
from app.platform.schemas import PlatformAuditEventOut


def _event(action: str, resource_type: str) -> PlatformAuditEventOut:
    return PlatformAuditEventOut(
        id=uuid.uuid4(),
        seq=7,
        occurred_at=dt.datetime(2026, 10, 7, 9, 30, tzinfo=dt.UTC),
        actor_type="operator",
        actor_id=uuid.uuid4(),
        action=action,
        resource_type=resource_type,
        resource_id=None,
        subject_tenant_id=None,
        summary={"note": "=1+1"},
        request_id=None,
    )


@pytest.mark.parametrize("value", ["=1+1", " =1+1", "＝1+1", "　@SUM(A1)", "\t-1+1"])
def test_DL_hardening_4_platform_audit_csv_neutralises_formulas(value: str) -> None:
    rows = list(csv.reader(io.StringIO(to_csv([_event(value, value)]))))
    assert rows[0] == list(CSV_COLUMNS)
    record = dict(zip(CSV_COLUMNS, rows[1], strict=True))
    assert record["action"].startswith("'")
    assert record["resource_type"].startswith("'")
    assert record["seq"] == "7"
    assert record["resource_id"] == ""
