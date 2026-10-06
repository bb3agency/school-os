"""Stored chain verification with a checkpoint (audit 2026-10-06 R-19; SEC-007, FR-AUD-004).

``GET /audit/verify`` used to re-hash the whole chain on every call (OWASP API4, CWE-400). Now a
verification run stores its result and, when the chain is intact, a checkpoint (the seq and hash
of the last verified event). An on-demand run verifies only the events after the checkpoint;
the daily job and ``full=True`` still re-hash everything. The security property is kept:

- a break after the checkpoint is detected by the incremental run;
- a changed checkpoint event (its content or its hash) is detected by the incremental run;
- a break before the checkpoint is detected by the next full run (the daily job).

Tampering is simulated as in test_tamper.py (superuser, user triggers off).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from sqlalchemy import Connection, text

from app.audit import verification
from app.audit.verify_all import verify_all
from app.core.db import tenant_session

pytestmark = pytest.mark.db

Tamper = Callable[[], AbstractContextManager[Connection]]


def _run(
    tenant: uuid.UUID, *, full: bool = False, source: verification.Source = "on_demand"
) -> Any:
    with tenant_session(tenant) as s:
        return verification.run(s, tenant, full=full, source=source)


def _latest(tenant: uuid.UUID) -> Any:
    with tenant_session(tenant) as s:
        return verification.latest(s, tenant)


def test_R_19_nothing_stored_before_the_first_run(tenant: uuid.UUID, app_engine: object) -> None:
    out = _latest(tenant)
    assert (out.ok, out.verified_at, out.checkpoint_seq, out.pending) == (None, None, 0, False)


def test_R_19_full_run_stores_result_and_checkpoint(tenant: uuid.UUID, record_events: Any) -> None:
    record_events(tenant, 4)
    out = _run(tenant, full=True, source="daily")
    assert (out.ok, out.checked, out.mode, out.source) == (True, 4, "full", "daily")
    assert out.checkpoint_seq == 4
    assert out.verified_at is not None
    assert out.last_full_at == out.verified_at
    stored = _latest(tenant)
    assert (stored.ok, stored.checked, stored.checkpoint_seq) == (True, 4, 4)


def test_R_19_incremental_run_checks_only_events_after_the_checkpoint(
    tenant: uuid.UUID, record_events: Any
) -> None:
    record_events(tenant, 3)
    assert _run(tenant, full=True).checkpoint_seq == 3
    record_events(tenant, 2)
    out = _run(tenant)
    assert (out.ok, out.checked, out.mode, out.checkpoint_seq) == (True, 2, "incremental", 5)
    # Nothing new: nothing to re-hash, the checkpoint stays.
    again = _run(tenant)
    assert (again.ok, again.checked, again.checkpoint_seq) == (True, 0, 5)


def test_R_19_first_run_without_a_checkpoint_is_full(tenant: uuid.UUID, record_events: Any) -> None:
    record_events(tenant, 3)
    out = _run(tenant)
    assert (out.ok, out.checked, out.mode, out.checkpoint_seq) == (True, 3, "full", 3)


def test_R_19_break_after_the_checkpoint_is_detected_and_keeps_the_checkpoint(
    tenant: uuid.UUID, record_events: Any, tamper: Tamper
) -> None:
    record_events(tenant, 3)
    _run(tenant, full=True)
    record_events(tenant, 3)
    with tamper() as c:
        c.execute(
            text("UPDATE audit.events SET summary = :s WHERE tenant_id = :t AND seq = 5"),
            {"s": json.dumps({"field": "gender"}), "t": tenant},
        )
    out = _run(tenant)
    assert (out.ok, out.first_bad_seq, out.reason, out.mode) == (
        False,
        5,
        "hash_mismatch",
        "incremental",
    )
    assert out.checkpoint_seq == 3, "a broken run never advances the checkpoint"
    assert _latest(tenant).ok is False


def test_R_19_deleted_event_after_the_checkpoint_is_a_gap(
    tenant: uuid.UUID, record_events: Any, tamper: Tamper
) -> None:
    record_events(tenant, 2)
    _run(tenant, full=True)
    record_events(tenant, 3)
    with tamper() as c:
        c.execute(text("DELETE FROM audit.events WHERE tenant_id = :t AND seq = 4"), {"t": tenant})
    out = _run(tenant)
    assert (out.ok, out.first_bad_seq, out.reason) == (False, 4, "seq_gap")


def test_R_19_changed_checkpoint_event_is_detected_by_the_incremental_run(
    tenant: uuid.UUID, record_events: Any, tamper: Tamper
) -> None:
    record_events(tenant, 3)
    _run(tenant, full=True)
    with tamper() as c:
        c.execute(
            text("UPDATE audit.events SET summary = :s WHERE tenant_id = :t AND seq = 3"),
            {"s": json.dumps({"field": "gender"}), "t": tenant},
        )
    out = _run(tenant)
    assert (out.ok, out.first_bad_seq, out.reason) == (False, 3, "checkpoint_mismatch")


def test_R_19_rehashed_checkpoint_event_is_detected_by_the_incremental_run(
    tenant: uuid.UUID, record_events: Any, tamper: Tamper
) -> None:
    """An attacker who rewrites the checkpoint event AND its hash (and the head) still fails:
    the stored checkpoint hash no longer matches."""
    record_events(tenant, 3)
    _run(tenant, full=True)
    with tamper() as c:
        c.execute(
            text(
                "UPDATE audit.events SET hash = decode(repeat('ab', 32), 'hex') "
                "WHERE tenant_id = :t AND seq = 3"
            ),
            {"t": tenant},
        )
        c.execute(
            text(
                "UPDATE audit.chain_heads SET last_hash = decode(repeat('ab', 32), 'hex') "
                "WHERE tenant_id = :t"
            ),
            {"t": tenant},
        )
    out = _run(tenant)
    assert (out.ok, out.reason) == (False, "checkpoint_mismatch")


def test_R_19_break_before_the_checkpoint_is_caught_by_the_full_run(
    tenant: uuid.UUID, record_events: Any, tamper: Tamper
) -> None:
    record_events(tenant, 4)
    _run(tenant, full=True)
    with tamper() as c:
        c.execute(
            text("UPDATE audit.events SET summary = :s WHERE tenant_id = :t AND seq = 2"),
            {"s": json.dumps({"field": "gender"}), "t": tenant},
        )
    assert _run(tenant, full=True).first_bad_seq == 2
    assert _latest(tenant).ok is False


def test_R_19_daily_job_runs_full_and_stores_each_school(
    tenant_ids: tuple[uuid.UUID, uuid.UUID], record_events: Any, tamper: Tamper
) -> None:
    good, bad = tenant_ids
    record_events(good, 2)
    record_events(bad, 3)
    _run(bad, full=True)  # checkpoint at 3
    with tamper() as c:
        c.execute(text("DELETE FROM audit.events WHERE tenant_id = :t AND seq = 2"), {"t": bad})
    results = verify_all([good, bad])
    assert results[good].ok
    assert not results[bad].ok
    stored_good, stored_bad = _latest(good), _latest(bad)
    assert (stored_good.ok, stored_good.source, stored_good.mode) == (True, "daily", "full")
    assert (stored_bad.ok, stored_bad.first_bad_seq, stored_bad.reason) == (False, 2, "seq_gap")
