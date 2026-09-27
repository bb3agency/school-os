"""Re-encrypt the query log to the school's current key version (SEC-012, FR-KB-009).

``kb.queries`` keeps each question and answer as AES-256-GCM ciphertext under the school's data
encryption key (``question_ciphertext``, ``answer_ciphertext``; associated data
``tenant_id|kb.queries|<column>|<row id>``) and a keyed HMAC of the normalised question
(``question_hmac``) for repeat detection; ``key_version`` names the key version of all three.

:func:`reencrypt_queries` is the knowledge module's re-encryptor for DEK rotation: in the
caller's ``tenant_session`` it takes rows whose ``key_version`` is not the active one, in
batches (``FOR UPDATE SKIP LOCKED``, so two runs never fight over a row), decrypts both columns
with the key named in each ciphertext header, encrypts them again under the active version with
the SAME associated data, recomputes ``question_hmac`` with the active version's HMAC key (so a
rotation that also replaces the HMAC key, ``--new-hmac-key``, is covered), and sets
``key_version``. Idempotent: rows already at the active version are never touched, and a rerun
after an interruption continues where it stopped. Returns the number of rows re-encrypted. No
plaintext, ciphertext or key material is logged (ids and counts only).

Registration (DEK rotation, ``app/students/rotation.py``)::

    register_reencryptor("kb_queries", app.knowledge.service.reencrypt_queries)
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Final

from sqlalchemy import select, update

from app.core.logging import get_logger
from app.knowledge import repository as repo
from app.knowledge.models import Query
from app.students import crypto

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

TABLE: Final = "kb.queries"
QUESTION_COLUMN: Final = "question_ciphertext"
ANSWER_COLUMN: Final = "answer_ciphertext"
QUESTION_PURPOSE: Final = "kb_question"
DEFAULT_BATCH: Final = 200


def question_key(question: str) -> str:
    """The HMAC input for repeat detection: NFC, casefolded, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFC", question).casefold().split())


def reencrypt_queries(
    session: Session,
    *,
    batch_size: int = DEFAULT_BATCH,
    keyring: crypto.TenantKeyring | None = None,
) -> int:
    """Bring every ``kb.queries`` row of the current school to the active key version."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    tenant_id = repo.current_tenant_id(session)
    active = (keyring or crypto.get_keyring()).active_version(session)
    done = 0
    while True:
        rows = session.execute(
            select(Query.id, Query.question_ciphertext, Query.answer_ciphertext)
            .where(Query.key_version != active)
            .order_by(Query.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        if not rows:
            break
        for row_id, question_blob, answer_blob in rows:
            question = crypto.decrypt_value(
                session,
                bytes(question_blob),
                table=TABLE,
                column=QUESTION_COLUMN,
                row_id=row_id,
                keyring=keyring,
            )
            new_question, version = crypto.encrypt_value(
                session,
                question,
                table=TABLE,
                column=QUESTION_COLUMN,
                row_id=row_id,
                keyring=keyring,
            )
            new_answer = None
            if answer_blob is not None:
                answer = crypto.decrypt_value(
                    session,
                    bytes(answer_blob),
                    table=TABLE,
                    column=ANSWER_COLUMN,
                    row_id=row_id,
                    keyring=keyring,
                )
                new_answer, _ = crypto.encrypt_value(
                    session,
                    answer,
                    table=TABLE,
                    column=ANSWER_COLUMN,
                    row_id=row_id,
                    keyring=keyring,
                )
            digest, _ = crypto.blind_index(
                session,
                question_key(question),
                purpose=QUESTION_PURPOSE,
                key_version=version,
                keyring=keyring,
            )
            session.execute(
                update(Query)
                .where(Query.id == row_id)
                .values(
                    question_ciphertext=new_question,
                    answer_ciphertext=new_answer,
                    question_hmac=digest,
                    key_version=version,
                )
            )
        done += len(rows)
    log.info(
        "knowledge.queries.reencrypted",
        tenant_id=tenant_id,
        resource_type="kb_query",
        count=done,
        outcome=f"key_version_{active}",
    )
    return done


__all__ = ["DEFAULT_BATCH", "QUESTION_PURPOSE", "question_key", "reencrypt_queries"]
