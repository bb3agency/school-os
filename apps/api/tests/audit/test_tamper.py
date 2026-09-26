"""SEC-007 / threat T4: chain verification detects tampering (docs/12 §4.7).

Tampering is simulated with the superuser fixture and ``session_replication_role = replica``
(user triggers off), i.e. an attacker with full database control. Each test uses a fresh
synthetic tenant so a broken chain never affects other tests.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
from app.audit.hashing import chain_hash, tenant_event_dict
from app.audit.schemas import ZERO_HASH, VerifyResult
from app.audit.service import verify_chain
from app.core.db import tenant_session
from sqlalchemy import Connection, text

pytestmark = pytest.mark.db

Tamper = Callable[[], AbstractContextManager[Connection]]
N = 5


def _verify(tenant: uuid.UUID) -> VerifyResult:
    with tenant_session(tenant) as s:
        return verify_chain(s, tenant)


@pytest.fixture
def chain(tenant: uuid.UUID, record_events: object) -> uuid.UUID:
    record_events(tenant, N)  # type: ignore[operator]
    assert _verify(tenant) == VerifyResult(True, N)
    return tenant


def test_SEC_007_intact_chain_verifies(chain: uuid.UUID) -> None:
    assert _verify(chain).ok


def test_SEC_007_empty_chain_verifies(tenant: uuid.UUID, app_engine: object) -> None:
    assert _verify(tenant) == VerifyResult(True, 0)


def test_SEC_007_tamper_detected_modified_summary(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(
            text("UPDATE audit.events SET summary = :s WHERE tenant_id = :t AND seq = 3"),
            {"s": json.dumps({"field": "gender"}), "t": chain},
        )
    assert _verify(chain) == VerifyResult(False, 2, 3, "hash_mismatch")


def test_SEC_007_tamper_detected_modified_actor(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(
            text("UPDATE audit.events SET actor_id = :a WHERE tenant_id = :t AND seq = 1"),
            {"a": uuid.uuid4(), "t": chain},
        )
    result = _verify(chain)
    assert (result.first_bad_seq, result.reason) == (1, "hash_mismatch")


def test_SEC_007_tamper_detected_deleted_event_gap(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(text("DELETE FROM audit.events WHERE tenant_id = :t AND seq = 3"), {"t": chain})
    assert _verify(chain) == VerifyResult(False, 2, 3, "seq_gap")


def test_SEC_007_tamper_detected_deleted_last_event(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(
            text("DELETE FROM audit.events WHERE tenant_id = :t AND seq = :n"), {"t": chain, "n": N}
        )
    assert _verify(chain) == VerifyResult(False, N - 1, N, "head_mismatch")


def test_SEC_007_tamper_detected_reordered_events(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(
            text(
                "UPDATE audit.events SET seq = CASE seq WHEN 2 THEN 3 ELSE 2 END "
                "WHERE tenant_id = :t AND seq IN (2, 3)"
            ),
            {"t": chain},
        )
    result = _verify(chain)
    assert (result.ok, result.first_bad_seq) == (False, 2)
    assert result.reason == "prev_hash_mismatch"


def test_SEC_007_tamper_detected_duplicated_seq(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(
            text("UPDATE audit.events SET seq = 2 WHERE tenant_id = :t AND seq = 3"), {"t": chain}
        )
    assert _verify(chain) == VerifyResult(False, 2, 2, "duplicate_seq")


def test_SEC_007_tamper_detected_forged_hash(chain: uuid.UUID, tamper: Tamper) -> None:
    """Rewriting one event AND its hash consistently still breaks the next link."""
    with tamper() as c:
        row = dict(
            c.execute(
                text("SELECT * FROM audit.events WHERE tenant_id = :t AND seq = 3"), {"t": chain}
            )
            .mappings()
            .one()
        )
        row["summary"] = {"field": "gender"}
        forged = chain_hash(bytes(row["prev_hash"]), tenant_event_dict(row))
        c.execute(
            text(
                "UPDATE audit.events SET summary = :s, hash = :h WHERE tenant_id = :t AND seq = 3"
            ),
            {"s": json.dumps(row["summary"]), "h": forged, "t": chain},
        )
    assert _verify(chain) == VerifyResult(False, 3, 4, "prev_hash_mismatch")


def test_SEC_007_tamper_detected_random_hash(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(
            text("UPDATE audit.events SET hash = :h WHERE tenant_id = :t AND seq = 2"),
            {"h": b"\x01" * 32, "t": chain},
        )
    assert _verify(chain) == VerifyResult(False, 1, 2, "hash_mismatch")


def test_SEC_007_tamper_detected_head_hash_mismatch(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(
            text("UPDATE audit.chain_heads SET last_hash = :h WHERE tenant_id = :t"),
            {"h": ZERO_HASH, "t": chain},
        )
    assert _verify(chain) == VerifyResult(False, N, N, "head_mismatch")


def test_SEC_007_tamper_detected_head_rolled_back(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(
            text("UPDATE audit.chain_heads SET last_seq = 2 WHERE tenant_id = :t"), {"t": chain}
        )
    assert _verify(chain) == VerifyResult(False, N, 3, "head_mismatch")


def test_SEC_007_tamper_detected_appended_forgery(chain: uuid.UUID, tamper: Tamper) -> None:
    """An event appended behind the app's back (head not advanced) is detected."""
    with tamper() as c:
        last = dict(
            c.execute(
                text("SELECT * FROM audit.events WHERE tenant_id = :t AND seq = :n"),
                {"t": chain, "n": N},
            )
            .mappings()
            .one()
        )
        forged = {**last, "id": uuid.uuid4(), "seq": N + 1, "prev_hash": bytes(last["hash"])}
        forged["hash"] = chain_hash(forged["prev_hash"], tenant_event_dict(forged))
        c.execute(
            text(
                "INSERT INTO audit.events (id, tenant_id, seq, occurred_at, actor_type, "
                "actor_id, action, resource_type, resource_id, summary, request_id, ip_hash, "
                "prev_hash, hash) "
                "VALUES (:id, :tenant_id, :seq, :occurred_at, :actor_type, :actor_id, :action, "
                ":resource_type, :resource_id, :summary, :request_id, :ip_hash, :prev_hash, :hash)"
            ),
            {**forged, "summary": json.dumps(forged["summary"])},
        )
    assert _verify(chain) == VerifyResult(False, N + 1, N + 1, "head_mismatch")


def test_SEC_007_tamper_detected_head_deleted(chain: uuid.UUID, tamper: Tamper) -> None:
    with tamper() as c:
        c.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id = :t"), {"t": chain})
    assert _verify(chain) == VerifyResult(False, N, N, "head_missing")
