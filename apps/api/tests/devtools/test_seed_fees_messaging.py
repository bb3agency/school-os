"""``seed-synthetic`` with students also fills the ``fin`` (fees) and ``msg`` (messaging) schemas
through the module services, so the restore drill restores those tables with rows in them
(SEC-024, NFR-AVL-005, SEC-008; docs/12 §3; FR-FEE-001..013, FR-MSG-008). Synthetic data only
(invariant 11): no phone numbers, nothing sent to Meta.

One school per module with a unique code prefix (the session database is shared). Messaging is
turned on for this process while seeding (``app.messaging.runtime.patch_for_tests``), like the
restore drill does; the second run checks the seeder's idempotency contract.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.authz.kv import InMemoryKV, set_kv_store
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import tenant_session
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.devtools.plan import build_plan
from app.messaging import runtime as messaging_runtime
from app.students import crypto

pytestmark = pytest.mark.db

SEED = 23
STUDENTS = 150
CI_SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
# Tables the finance seeder writes, with the least rows each must hold for one school.
EXPECTED_MIN = {
    "fin.fee_heads": 2,
    "fin.fee_plans": 1,
    "fin.fee_plan_lines": 2,
    "fin.fee_plan_instalments": 2,
    "fin.student_fee_accounts": 4,
    "fin.ledger_entries": 16,
    "fin.receipts": 4,
    "fin.receipt_allocations": 4,
    "fin.receipt_counters": 1,
    "fin.receipt_cancellations": 1,
    "fin.refunds": 1,
    "msg.settings": 1,
    "msg.optins": 4,
    "msg.messages": 3,
}


@dataclass
class Seeded:
    tenant_id: uuid.UUID
    first: dict[str, Any]
    second: dict[str, Any]
    printed: str


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        argv,
        settings=CI_SETTINGS,
        stdout=out,
        stderr=err,
        wrapper=LocalDevKeyWrapper(CI_SETTINGS),
        owner_bootstrap=seeder.PlatformOwnerBootstrap(),
    )
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def seeded(
    admin_engine: Engine,
    app_engine: Engine,
    platform_engine: Engine,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Seeded]:
    set_kv_store(InMemoryKV())
    crypto.set_key_wrapper(LocalDevKeyWrapper(CI_SETTINGS))
    previous = messaging_runtime.current_patch()
    messaging_runtime.patch_for_tests({**(previous or {}), "messaging_enabled": True})
    prefix = f"f{uuid.uuid4().hex[:8]}"
    argv = ["--code-prefix", prefix, "--tenants", "1", "--seed", str(SEED)]
    argv += ["--profile", "small", "--students", str(STUDENTS), "--no-documents"]
    argv += ["--out-dir", str(tmp_path_factory.mktemp("synthetic-fees"))]
    try:
        code, out, err = _run(argv)
        assert code == 0, err
        code2, out2, err2 = _run(argv)
        assert code2 == 0, err2
        plan = build_plan(seed=SEED, tenants=1, code_prefix=prefix)
        yield Seeded(plan.tenants[0].tenant_id, json.loads(out), json.loads(out2), out + out2)
    finally:
        messaging_runtime.patch_for_tests(previous)
        set_kv_store(None)


def _counts(admin: Engine, tenant_id: uuid.UUID) -> dict[str, int]:
    with admin.connect() as c:
        return {
            table: int(
                c.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),
                    {"t": tenant_id},
                ).scalar_one()
            )
            for table in EXPECTED_MIN
        }


def test_sec024_seeder_fills_the_fin_and_msg_tables(seeded: Seeded, admin_engine: Engine) -> None:
    counts = _counts(admin_engine, seeded.tenant_id)
    assert all(counts[t] >= n for t, n in EXPECTED_MIN.items()), counts
    created = seeded.first["tenants"][0]["created"]
    assert created["fee_heads"] == 2
    assert created["fee_plans"] == 1
    assert created["fee_receipts"] == 4
    assert created["fee_cancellations"] == 1
    assert created["fee_refunds"] == 1
    assert created["msg_settings"] == 1
    assert created["msg_optins"] == 4
    assert created["msg_messages"] == 3  # one per receipt that was not cancelled
    summary_counts = seeded.first["tenants"][0]["counts"]
    assert summary_counts["fin.receipts"] == counts["fin.receipts"]
    assert summary_counts["msg.messages"] == counts["msg.messages"]


def test_sec024_fee_and_message_states(seeded: Seeded, admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        receipts = c.execute(
            text("SELECT status, count(*) FROM fin.receipts WHERE tenant_id = :t GROUP BY 1"),
            {"t": seeded.tenant_id},
        ).all()
        refunds = c.execute(
            text("SELECT status, voucher_number FROM fin.refunds WHERE tenant_id = :t"),
            {"t": seeded.tenant_id},
        ).all()
        messages = c.execute(
            text(
                "SELECT status, skip_reason, phone_bidx IS NULL AS no_phone FROM msg.messages "
                "WHERE tenant_id = :t"
            ),
            {"t": seeded.tenant_id},
        ).all()
        optins = c.execute(
            text("SELECT status, method, category FROM msg.optins WHERE tenant_id = :t"),
            {"t": seeded.tenant_id},
        ).all()
        accounts: int = c.execute(
            text("SELECT count(*) FROM msg.accounts WHERE tenant_id = :t"),
            {"t": seeded.tenant_id},
        ).scalar_one()
    assert {r[0]: r[1] for r in receipts} == {"issued": 3, "cancelled": 1}
    assert [(r.status, (r.voucher_number or "")[:4]) for r in refunds] == [("processed", "RFD/")]
    # Never connected to WhatsApp: every queued message is skipped without a provider call.
    assert accounts == 0
    assert {(m.status, m.skip_reason, m.no_phone) for m in messages} == {
        ("skipped", "messaging_not_connected", True)
    }
    assert {(o.status, o.method, o.category) for o in optins} == {
        ("opted_out", "staff_recorded_stop", "fees")
    }


def test_sec024_rerun_is_idempotent(seeded: Seeded, admin_engine: Engine) -> None:
    a, b = seeded.first["tenants"][0], seeded.second["tenants"][0]
    assert b["outcome"] == "unchanged"
    assert b["created"] == {}
    assert a["counts"] == b["counts"]


def test_sec024_rls_scopes_fin_and_msg_to_the_school(seeded: Seeded, admin_engine: Engine) -> None:
    expected = _counts(admin_engine, seeded.tenant_id)
    other = uuid.uuid4()
    for table in ("fin.receipts", "fin.ledger_entries", "msg.optins", "msg.messages"):
        with tenant_session(seeded.tenant_id) as s:
            own: int = s.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        with tenant_session(other) as s:
            foreign: int = s.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        assert own == expected[table] > 0
        assert foreign == 0


def test_sec008_summary_has_no_names_or_numbers(seeded: Seeded) -> None:
    # Codes, IDs and counts only (the seeder's stdout contract, invariant 5).
    assert "Synthetic Class X fees" not in seeded.printed
    assert "@" not in seeded.printed
