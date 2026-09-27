"""DEK rotation for the query log (SEC-012, FR-KB-009): ``reencrypt_queries``.

A fresh synthetic school logs a question under key version 1; a second key version is added;
the re-encryptor moves question and answer ciphertext (same associated data) and the question
HMAC to version 2, and a second run changes nothing (idempotent).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.crypto import generate_tenant_keys
from app.core.db import tenant_session
from app.knowledge import repository as repo
from app.knowledge import service
from app.knowledge.keys import QUESTION_PURPOSE, question_key
from app.students import crypto

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


K = _load("sos_test_ask_support", Path(__file__).with_name("ask_support.py"))
W = K.W
world = W.world

QUESTION = "When is the  Parent-Teacher meeting?"
ANSWER = "On 18/10/2026 at 10:00. [1]"


def _row(admin: Engine, query_id: uuid.UUID) -> Any:
    with admin.connect() as c:
        return c.execute(
            text(
                "SELECT key_version, question_ciphertext, answer_ciphertext, question_hmac "
                "FROM kb.queries WHERE id = :i"
            ),
            {"i": query_id},
        ).one()


def test_SEC_012_query_log_is_reencrypted_to_the_active_key(
    world: Any, admin_engine: Engine
) -> None:
    K.SW.configure_keyring()
    tenant = W.provision_school()
    query_id = uuid.uuid4()
    with tenant_session(tenant) as s:
        q, version = crypto.encrypt_value(
            s, QUESTION, table="kb.queries", column="question_ciphertext", row_id=query_id
        )
        a, _ = crypto.encrypt_value(
            s, ANSWER, table="kb.queries", column="answer_ciphertext", row_id=query_id
        )
        h, _ = crypto.blind_index(
            s, question_key(QUESTION), purpose=QUESTION_PURPOSE, key_version=version
        )
        repo.insert_query(
            s,
            {
                "id": query_id,
                "session_id": uuid.uuid4(),
                "user_id": uuid.uuid4(),
                "question_ciphertext": q,
                "question_hmac": h,
                "answer_ciphertext": a,
                "key_version": version,
                "mode": "full",
                "status": "answered",
            },
        )
    assert version == 1
    before = _row(admin_engine, query_id)

    dek, mac = generate_tenant_keys(tenant, W.wrapper())
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenant_keys (tenant_id, key_version, wrapped_dek, wrapped_hmac, "
                "kms_key_arn) VALUES (:t, 2, :d, :h, 'local-dev:test')"
            ),
            {"t": tenant, "d": dek, "h": mac},
        )
    crypto.get_keyring().clear()

    with tenant_session(tenant) as s:
        assert service.reencrypt_queries(s, batch_size=1) == 1
    after = _row(admin_engine, query_id)
    assert after.key_version == 2
    assert bytes(after.question_ciphertext) != bytes(before.question_ciphertext)
    assert bytes(after.question_hmac) != bytes(before.question_hmac)  # new HMAC key, recomputed
    with tenant_session(tenant) as s:
        assert (
            crypto.decrypt_value(
                s,
                bytes(after.question_ciphertext),
                table="kb.queries",
                column="question_ciphertext",
                row_id=query_id,
            )
            == QUESTION
        )
        assert (
            crypto.decrypt_value(
                s,
                bytes(after.answer_ciphertext),
                table="kb.queries",
                column="answer_ciphertext",
                row_id=query_id,
            )
            == ANSWER
        )
        expected, _ = crypto.blind_index(
            s, question_key(QUESTION), purpose=QUESTION_PURPOSE, key_version=2
        )
        assert bytes(after.question_hmac) == expected
        assert service.reencrypt_queries(s) == 0  # idempotent
    # Another school's rows are out of reach (RLS): nothing to do there.
    with tenant_session(world.b.tenant_id) as s:
        assert service.reencrypt_queries(s) == 0
