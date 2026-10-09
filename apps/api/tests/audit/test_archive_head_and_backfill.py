"""The signed archive as the reference for the database chain, and archive backfill
(data-protection audit 2026-10-05 H-04 / data-layer hardening note 3, and H-05; SEC-007,
FR-AUD-004). Synthetic events only; S3 is the in-memory fake.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.audit import tasks as audit_tasks
from app.audit.archive import ArchiveCheck, archived_days, export_backfill, export_day
from app.audit.hashing import chain_hash, tenant_event_dict
from app.audit.signing import LocalDevSigner
from app.audit.verify_all import verify_all
from app.core.config import Environment, Settings

pytestmark = pytest.mark.db

BUCKET = "sos-test-audit-archive"
SIGNER = LocalDevSigner(Settings(env=Environment.CI, local_dev_master_key=SecretStr("h04-key")))


def _day(event: Any) -> date:
    occurred: datetime = event.occurred_at
    return occurred.astimezone(UTC).date()


def _rewind_to(tamper: Any, tenant: uuid.UUID, seq: int) -> None:
    """A DBA deletes the newest events and rewinds the head (triggers disabled)."""
    with tamper() as c:
        c.execute(
            text("DELETE FROM audit.events WHERE tenant_id = :t AND seq > :s"),
            {"t": tenant, "s": seq},
        )
        c.execute(
            text(
                "UPDATE audit.chain_heads SET last_seq = :s, last_hash = "
                "(SELECT hash FROM audit.events WHERE tenant_id = :t AND seq = :s) "
                "WHERE tenant_id = :t"
            ),
            {"t": tenant, "s": seq},
        )


def test_H_04_truncated_tail_with_rewound_head_is_caught_by_the_archive(
    tenant: uuid.UUID,
    record_events: Any,
    fake_s3: Any,
    tamper: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events = record_events(tenant, 3)
    export_day(tenant, _day(events[0]), s3=fake_s3, signer=SIGNER, bucket=BUCKET)
    _rewind_to(tamper, tenant, 2)
    # The database alone cannot tell: the shortened chain is internally consistent.
    assert verify_all([tenant])[tenant].ok
    check = ArchiveCheck(s3=fake_s3, bucket=BUCKET, signer=SIGNER)
    with caplog.at_level(logging.INFO, logger="app.audit"):
        result = verify_all([tenant], archive=check)[tenant]
    assert (result.ok, result.first_bad_seq, result.reason) == (False, 3, "head_behind_archive")
    broken = [r for r in caplog.records if r.getMessage() == "audit.chain.broken"]
    assert [r.reason for r in broken] == ["head_behind_archive"]  # type: ignore[attr-defined]
    assert broken[0].severity == "P1"  # type: ignore[attr-defined]


def test_H_04_a_rewritten_tail_is_caught_by_the_archived_hash(
    tenant: uuid.UUID, record_events: Any, fake_s3: Any, tamper: Any
) -> None:
    events = record_events(tenant, 3)
    export_day(tenant, _day(events[0]), s3=fake_s3, signer=SIGNER, bucket=BUCKET)
    _rewind_to(tamper, tenant, 2)
    record_events(tenant, 1)  # a new seq 3 with another hash
    check = ArchiveCheck(s3=fake_s3, bucket=BUCKET, signer=SIGNER)
    result = verify_all([tenant], archive=check)[tenant]
    assert (result.ok, result.reason) == (False, "archive_hash_mismatch")


def test_H_04_an_intact_chain_matches_its_archive_and_one_without_archive_passes(
    tenant_ids: tuple[uuid.UUID, uuid.UUID], record_events: Any, fake_s3: Any
) -> None:
    archived, fresh = tenant_ids
    events = record_events(archived, 2)
    record_events(fresh, 1)
    export_day(archived, _day(events[0]), s3=fake_s3, signer=SIGNER, bucket=BUCKET)
    record_events(archived, 1)  # newer than the archive: fine
    check = ArchiveCheck(s3=fake_s3, bucket=BUCKET, signer=SIGNER)
    results = verify_all([archived, fresh], archive=check)
    assert results[archived].ok
    assert results[fresh].ok


def test_H_04_a_manifest_with_a_bad_signature_is_reported(
    tenant: uuid.UUID, record_events: Any, fake_s3: Any
) -> None:
    events = record_events(tenant, 1)
    out = export_day(tenant, _day(events[0]), s3=fake_s3, signer=SIGNER, bucket=BUCKET)
    other = LocalDevSigner(Settings(env=Environment.CI, local_dev_master_key=SecretStr("other")))
    check = ArchiveCheck(s3=fake_s3, bucket=BUCKET, signer=other)
    result = verify_all([tenant], archive=check)[tenant]
    assert (result.ok, result.reason) == (False, "archive_signature_invalid")
    assert out.sig_key is not None


def test_H_04_the_daily_task_compares_with_the_archive(  # noqa: PLR0917 - pytest fixtures
    tenant: uuid.UUID,
    record_events: Any,
    fake_s3: Any,
    tamper: Any,
    platform_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = record_events(tenant, 2)
    export_day(tenant, _day(events[0]), s3=fake_s3, signer=SIGNER, bucket=BUCKET)
    _rewind_to(tamper, tenant, 1)
    monkeypatch.setattr(audit_tasks, "_tenant_ids", lambda: [tenant])
    monkeypatch.setattr(audit_tasks, "build_s3_client", lambda _s: fake_s3)
    monkeypatch.setattr(audit_tasks, "build_signer", lambda _s: SIGNER)
    monkeypatch.setattr(audit_tasks, "_audit_bucket", lambda _s: BUCKET)
    out = audit_tasks.verify_all_chains.apply().get()
    assert out["broken"] == [str(tenant)]


# --- H-05: missed days are backfilled from what the bucket holds ---------------------------------


def _redate(tamper: Any, tenant: uuid.UUID, days: list[date]) -> None:
    """Spread the tenant's events over ``days`` (event i on days[i]) and re-hash the chain, as
    if they had been written on those days (the append trigger only accepts "now")."""
    with tamper() as c:
        rows = [
            dict(r)
            for r in c.execute(
                text("SELECT * FROM audit.events WHERE tenant_id = :t ORDER BY seq"), {"t": tenant}
            ).mappings()
        ]
        prev = bytes(32)
        for row, day in zip(rows, days, strict=True):
            row["occurred_at"] = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
                hours=10, seconds=int(row["seq"])
            )
            new_hash = chain_hash(prev, tenant_event_dict(row))
            c.execute(
                text(
                    "UPDATE audit.events SET occurred_at = :o, prev_hash = :p, hash = :h "
                    "WHERE tenant_id = :t AND seq = :s"
                ),
                {"o": row["occurred_at"], "p": prev, "h": new_hash, "t": tenant, "s": row["seq"]},
            )
            prev = new_hash
        c.execute(
            text("UPDATE audit.chain_heads SET last_hash = :h WHERE tenant_id = :t"),
            {"h": prev, "t": tenant},
        )


def test_H_05_missed_days_are_backfilled_oldest_first_and_bounded(
    tenant: uuid.UUID, record_events: Any, fake_s3: Any, tamper: Any
) -> None:
    base = datetime.now(UTC).date() + timedelta(days=5)  # inside the partitions
    d1, d2, d3 = base, base + timedelta(days=1), base + timedelta(days=2)
    record_events(tenant, 5)
    _redate(tamper, tenant, [d1, d1, d2, d3, d3])
    # The job archived d2 only; d1 failed every retry and d3 is the day being archived now.
    export_day(tenant, d2, s3=fake_s3, signer=SIGNER, bucket=BUCKET)
    fake_s3.puts.clear()
    results = export_backfill([tenant], d3, s3=fake_s3, signer=SIGNER, bucket=BUCKET)
    written = [p["Key"] for p in fake_s3.puts if not p["Key"].endswith(".sig")]
    assert [k.rsplit("audit-", 1)[1][:10] for k in written] == [str(d1), str(d3)]
    assert {r.day for r in results} == {d1, d3}
    assert archived_days(fake_s3, BUCKET, tenant, since=d1) == {d1, d2, d3}
    # Nothing left to do on the next run.
    fake_s3.puts.clear()
    assert export_backfill([tenant], d3, s3=fake_s3, signer=SIGNER, bucket=BUCKET) == []
    assert fake_s3.puts == []
    # At most 31 days back: a day older than that is not looked at.
    late = d3 + timedelta(days=31)
    fake_s3.objects.clear()
    assert export_backfill([tenant], late, s3=fake_s3, signer=SIGNER, bucket=BUCKET) == []


def test_H_05_the_daily_task_backfills_up_to_yesterday(
    tenant: uuid.UUID, fake_s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    calls: list[tuple[list[uuid.UUID], date]] = []

    def backfill(tenant_ids: Any, until: date, **_kw: Any) -> list[Any]:
        calls.append((list(tenant_ids), until))
        return []

    monkeypatch.setattr(audit_tasks, "_tenant_ids", lambda: [tenant])
    monkeypatch.setattr(audit_tasks, "build_s3_client", lambda _s: fake_s3)
    monkeypatch.setattr(audit_tasks, "build_signer", lambda _s: SIGNER)
    monkeypatch.setattr(audit_tasks, "export_backfill", backfill)
    out = audit_tasks.archive_daily.apply().get()
    assert calls == [([tenant], yesterday)]
    assert out["idempotency_key"] == f"audit.archive_daily:{yesterday.isoformat()}"
    assert out["days"] == 0
