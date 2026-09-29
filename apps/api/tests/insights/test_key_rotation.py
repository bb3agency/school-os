"""DEK rotation for behaviour notes and action notes (SEC-012, FR-EW-010, FR-EW-018).

A fresh synthetic school writes a note and an action note under key version 1; a second key
version is added; the insights re-encryptor moves both ciphertexts (same associated data) to
version 2, the text still reads back, and a second run changes nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.crypto import generate_tenant_keys
from app.core.db import tenant_session
from app.insights import crypto as insights_crypto
from app.insights import service as insights
from app.insights.schemas import ActionIn
from app.students import crypto, rotation

pytestmark = pytest.mark.db


def _support() -> Any:
    import sys

    return sys.modules["sos_test_insights_support"]


S = _support()


def _versions(admin: Engine, note_id: uuid.UUID, flag_id: uuid.UUID) -> tuple[int, int]:
    with admin.connect() as c:
        note: Any = c.execute(
            text("SELECT key_version FROM sis.behaviour_notes WHERE id = :i"), {"i": note_id}
        ).scalar_one()
        action: Any = c.execute(
            text(
                "SELECT key_version FROM sis.flag_actions WHERE flag_id = :f "
                "AND note_ciphertext IS NOT NULL"
            ),
            {"f": flag_id},
        ).scalar_one()
    return int(note), int(action)


def test_SEC_012_notes_are_reencrypted_to_the_new_key(world: Any, admin_engine: Engine) -> None:
    assert "insights_notes" in rotation.reencryptor_names()
    assert ("sis.behaviour_notes", "body_ciphertext") in rotation.CIPHERTEXT_COLUMNS
    assert ("sis.flag_actions", "note_ciphertext") in rotation.CIPHERTEXT_COLUMNS
    school = S.insights_school(admin_engine)
    note = S.note(school, "a1", "Synthetic rotation note.")
    flag = S.manual_flag(school, "a1")
    ct = S.ct_ctx(school)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        insights.add_action(db, ct, flag.id, ActionIn(kind="other", note="Synthetic action."))
    assert _versions(admin_engine, note.id, flag.id) == (1, 1)

    dek, mac = generate_tenant_keys(school.tenant_id, S.W.wrapper())
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenant_keys (tenant_id, key_version, wrapped_dek, wrapped_hmac, "
                "kms_key_arn) VALUES (:t, 2, :d, :h, 'local-dev:test')"
            ),
            {"t": school.tenant_id, "d": dek, "h": mac},
        )
    crypto.get_keyring().clear()
    with tenant_session(school.tenant_id) as db:
        moved = insights_crypto.reencrypt_batch(db, crypto.get_keyring(), 2, 100)
    assert moved == 2
    assert _versions(admin_engine, note.id, flag.id) == (2, 2)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        assert [n.text for n in insights.list_notes(db, ct, school.ids["a1"])] == [
            "Synthetic rotation note."
        ]
        detail = insights.get_flag(db, ct, flag.id)
        assert detail.actions[-1].note == "Synthetic action."
        assert insights_crypto.reencrypt_batch(db, crypto.get_keyring(), 2, 100) == 0
