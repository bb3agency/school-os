"""Per-tenant C3 field encryption (SEC-012, FR-STU-007, docs/05 §9).

Tamper and row-swap tests edit ciphertext with the admin engine (simulating a database-level
attacker); decryption must then fail authentication.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.crypto import CryptoError
from app.core.db import tenant_session
from app.students import crypto
from app.students import service as students
from app.students.schemas import RevealIn

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]


def wrapper() -> Any:
    return SW.W.wrapper()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_SEC_012_roundtrip_and_ciphertext_format(world: Any, app_engine: Engine) -> None:
    ring = crypto.TenantKeyring(wrapper())
    row_id = uuid.uuid4()
    with tenant_session(world.a.tenant_id) as db:
        blob, version = crypto.encrypt_value(
            db,
            "Synthetic note",
            table="sis.attribute_values",
            column="value_ciphertext",
            row_id=row_id,
            keyring=ring,
        )
        assert version == 1
        assert blob[0] == 1
        assert int.from_bytes(blob[1:3], "big") == 1
        assert b"Synthetic" not in blob
        assert (
            crypto.decrypt_value(
                db,
                blob,
                table="sis.attribute_values",
                column="value_ciphertext",
                row_id=row_id,
                keyring=ring,
            )
            == "Synthetic note"
        )
        # AAD binds the row, column and table.
        for table, column, rid in (
            ("sis.attribute_values", "value_ciphertext", uuid.uuid4()),
            ("sis.attribute_values", "other_column", row_id),
            ("sis.guardians", "value_ciphertext", row_id),
        ):
            with pytest.raises(CryptoError):
                crypto.decrypt_value(db, blob, table=table, column=column, row_id=rid, keyring=ring)
    # Another school's DEK (and tenant id in the AAD) cannot open it.
    with tenant_session(world.b.tenant_id) as db, pytest.raises(CryptoError):
        crypto.decrypt_value(
            db,
            blob,
            table="sis.attribute_values",
            column="value_ciphertext",
            row_id=row_id,
            keyring=ring,
        )


def test_SEC_012_key_cache_expires_after_ttl(world: Any, app_engine: Engine) -> None:
    clock = Clock()
    ring = crypto.TenantKeyring(wrapper(), ttl_s=600, clock=clock)
    with tenant_session(world.a.tenant_id) as db:
        ring.dek(db, 1)
        first = ring.unwrap_count
        assert first == 1
        clock.now += 599
        ring.dek(db, 1)
        ring.active_version(db)
        assert ring.unwrap_count == first, "served from the cache within the TTL"
        clock.now += 2
        ring.dek(db, 1)
        assert ring.unwrap_count == first + 1, "reloaded (KMS unwrap) after the TTL"
        ring.clear()
        ring.hmac_key(db, 1)
        assert ring.unwrap_count == first + 2


def test_SEC_012_cache_ttl_is_capped_at_15_minutes() -> None:
    with pytest.raises(ValueError, match="15 minutes"):
        crypto.TenantKeyring(wrapper(), ttl_s=15 * 60 + 1)
    assert crypto.CACHE_TTL_S == 900


def test_SEC_012_missing_key_version_fails_closed(world: Any, app_engine: Engine) -> None:
    ring = crypto.TenantKeyring(wrapper())
    with tenant_session(world.a.tenant_id) as db, pytest.raises(crypto.KeyMaterialMissing):
        ring.dek(db, 999)


def test_SEC_012_blind_index_is_per_tenant_and_per_purpose(world: Any, app_engine: Engine) -> None:
    ring = crypto.TenantKeyring(wrapper())
    with tenant_session(world.a.tenant_id) as db:
        a1, _ = crypto.blind_index(db, "9876543210", purpose="guardian_phone", keyring=ring)
        a2, _ = crypto.blind_index(db, "9876543210", purpose="guardian_phone", keyring=ring)
        other, _ = crypto.blind_index(db, "9876543210", purpose="other_field", keyring=ring)
    with tenant_session(world.b.tenant_id) as db:
        b1, _ = crypto.blind_index(db, "9876543210", purpose="guardian_phone", keyring=ring)
    assert a1 == a2
    assert len({a1, other, b1}) == 3


def _c3_rows(admin: Engine, student_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT id, attribute_key, value_text, value_norm, value_date, "
                    "value_ciphertext, "
                    "key_version FROM sis.attribute_values WHERE student_id = :s "
                    "AND attribute_key IN ('health_notes', 'aadhaar_last4') ORDER BY attribute_key"
                ),
                {"s": student_id},
            )
        )


def test_FR_STU_007_c3_values_are_stored_only_as_ciphertext(
    world: Any, shared: dict[str, Any], admin_engine: Engine
) -> None:
    rows = _c3_rows(admin_engine, shared["s9a"])
    assert [r.attribute_key for r in rows] == ["aadhaar_last4", "health_notes"]
    for r in rows:
        assert r.value_text is None
        assert r.value_norm is None
        assert r.value_date is None
        assert r.value_ciphertext is not None
        assert r.key_version == 1
        assert b"4821" not in bytes(r.value_ciphertext)
    with admin_engine.connect() as c:
        profile = c.execute(
            text("SELECT * FROM sis.student_profiles WHERE student_id = :s"), {"s": shared["s9a"]}
        ).one()
    assert "asthma" not in repr(profile).lower()
    assert "4821" not in repr(profile)


def test_SEC_012_tampered_or_swapped_ciphertext_fails(
    world: Any, admin_engine: Engine, app_engine: Engine
) -> None:
    sw = SW
    sid = sw.create(
        world.a,
        name="Synthetica Tamper Case",
        section_key="section_9a",
        extra=[
            sw.ValueIn(attribute_key="health_notes", source="parent_form", value="Synthetic A"),
            sw.ValueIn(attribute_key="caste", source="parent_form", value="Synthetic B"),
        ],
    )
    ctx = sw.admin_ctx(world.a)
    rows = {r.attribute_key: r for r in _all_c3(admin_engine, sid)}
    health, caste = rows["health_notes"], rows["caste"]
    with tenant_session(world.a.tenant_id, ctx.user_id) as db:
        assert (
            students.reveal_sensitive(db, ctx, sid, RevealIn(attribute_key="caste")).value
            == "Synthetic B"
        )
    # Row swap: copy the caste ciphertext into the health_notes row (same school, same key).
    with admin_engine.begin() as c:
        c.execute(
            text("ALTER TABLE sis.attribute_values DISABLE TRIGGER attribute_values_immutable")
        )
        c.execute(
            text("UPDATE sis.attribute_values SET value_ciphertext = :b WHERE id = :i"),
            {"b": bytes(caste.value_ciphertext), "i": health.id},
        )
        c.execute(
            text("ALTER TABLE sis.attribute_values ENABLE TRIGGER attribute_values_immutable")
        )
    with tenant_session(world.a.tenant_id, ctx.user_id) as db, pytest.raises(CryptoError):
        students.reveal_sensitive(db, ctx, sid, RevealIn(attribute_key="health_notes"))
    # Bit flip in the ciphertext body.
    flipped = bytearray(caste.value_ciphertext)
    flipped[-1] ^= 0x01
    with admin_engine.begin() as c:
        c.execute(
            text("ALTER TABLE sis.attribute_values DISABLE TRIGGER attribute_values_immutable")
        )
        c.execute(
            text("UPDATE sis.attribute_values SET value_ciphertext = :b WHERE id = :i"),
            {"b": bytes(flipped), "i": caste.id},
        )
        c.execute(
            text("ALTER TABLE sis.attribute_values ENABLE TRIGGER attribute_values_immutable")
        )
    with tenant_session(world.a.tenant_id, ctx.user_id) as db, pytest.raises(CryptoError):
        students.reveal_sensitive(db, ctx, sid, RevealIn(attribute_key="caste"))


def _all_c3(admin: Engine, student_id: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT id, attribute_key, value_ciphertext FROM sis.attribute_values "
                    "WHERE student_id = :s AND value_ciphertext IS NOT NULL"
                ),
                {"s": student_id},
            )
        )
