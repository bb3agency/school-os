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

Registration (DEK rotation, ``app/students/rotation.py``): :mod:`app.knowledge.service` registers
:func:`reencrypt_queries_batch` as ``kb_queries`` when it is imported (the API and the worker both
import it), so rotation batches cover the query log and old key versions can be retired.
"""

from __future__ import annotations

import unicodedata
import uuid
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import ColumnElement, Integer, func, or_, select, update

from app.core.crypto import ciphertext_key_version
from app.core.logging import get_logger
from app.knowledge import repository as repo
from app.knowledge.models import Conversation, Query, UserMemory
from app.students import crypto

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

TABLE: Final = "kb.queries"
QUESTION_COLUMN: Final = "question_ciphertext"
ANSWER_COLUMN: Final = "answer_ciphertext"
CITATIONS_COLUMN: Final = "citations_ciphertext"
FOLLOWUPS_COLUMN: Final = "followups_ciphertext"
CONVERSATIONS_TABLE: Final = "kb.conversations"
MEMORIES_TABLE: Final = "kb.user_memories"
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
    max_rows: int | None = None,
) -> int:
    """Bring ``kb.queries`` rows of the current school to the active key version.

    Every row by default; at most ``max_rows`` when given (one DEK-rotation batch).
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if max_rows is not None and max_rows < 1:
        return 0
    tenant_id = repo.current_tenant_id(session)
    active = (keyring or crypto.get_keyring()).active_version(session)
    done = 0
    while max_rows is None or done < max_rows:
        limit = batch_size if max_rows is None else min(batch_size, max_rows - done)
        rows = session.execute(
            select(
                Query.id,
                Query.question_ciphertext,
                Query.answer_ciphertext,
                Query.citations_ciphertext,
                Query.followups_ciphertext,
            )
            .where(Query.key_version != active)
            .order_by(Query.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        if not rows:
            break
        for row_id, question_blob, answer_blob, citations_blob, followups_blob in rows:
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
            extra = {
                column: _again(session, blob, TABLE, column, row_id, keyring)
                for column, blob in (
                    (CITATIONS_COLUMN, citations_blob),
                    (FOLLOWUPS_COLUMN, followups_blob),
                )
            }
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
                    **extra,
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


__all__ = [
    "DEFAULT_BATCH",
    "QUESTION_PURPOSE",
    "question_key",
    "reencrypt_conversations_batch",
    "reencrypt_memories_batch",
    "reencrypt_queries",
]


def reencrypt_queries_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """DEK-rotation re-encryptor (``app.students.rotation.Reencryptor``): at most ``limit`` rows.

    ``target_version`` is the school's current key version, which is the keyring's active one.
    """
    del target_version  # the keyring's active version is the rotation target
    return reencrypt_queries(session, batch_size=limit, keyring=keyring, max_rows=limit)


def _again(  # noqa: PLR0917 - one cell: table, column and row name its associated data
    session: Session,
    blob: bytes | None,
    table: str,
    column: str,
    row_id: uuid.UUID,
    keyring: crypto.TenantKeyring | None,
) -> bytes | None:
    """``blob`` under the active version (same associated data); None stays None."""
    if blob is None:
        return None
    plain = crypto.decrypt_value(
        session, bytes(blob), table=table, column=column, row_id=row_id, keyring=keyring
    )
    value, _ = crypto.encrypt_value(
        session, plain, table=table, column=column, row_id=row_id, keyring=keyring
    )
    return value


def _header_version(column: Any) -> ColumnElement[int]:
    # Header: version(1) | key_version(2, big-endian) | ... (app.core.crypto).
    return func.get_byte(column, 1, type_=Integer) * 256 + func.get_byte(column, 2, type_=Integer)


def _stale(column: Any, version: int) -> ColumnElement[bool]:
    stale: ColumnElement[bool] = column.is_not(None) & (_header_version(column) != version)
    return stale


def reencrypt_conversations_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """DEK-rotation re-encryptor for ``kb.conversations`` (title and rolling summary): rows
    whose ciphertext header names another version, at most ``limit`` (SEC-012, ADR-0033)."""
    del target_version
    active = keyring.active_version(session)
    rows = session.execute(
        select(Conversation.id, Conversation.title_ciphertext, Conversation.summary_ciphertext)
        .where(
            or_(
                _stale(Conversation.title_ciphertext, active),
                _stale(Conversation.summary_ciphertext, active),
            )
        )
        .order_by(Conversation.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for row_id, title, summary in rows:
        values: dict[str, object] = {"key_version": active}
        for column, blob in (("title_ciphertext", title), ("summary_ciphertext", summary)):
            if blob is not None and ciphertext_key_version(bytes(blob)) != active:
                values[column] = _again(session, blob, CONVERSATIONS_TABLE, column, row_id, keyring)
        # Re-encryption is not activity: updated_at and the ETag version stay as they are.
        session.execute(update(Conversation).where(Conversation.id == row_id).values(**values))
    return len(rows)


def reencrypt_memories_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """DEK-rotation re-encryptor for ``kb.user_memories`` (SEC-012, ADR-0033)."""
    del target_version
    active = keyring.active_version(session)
    rows = session.execute(
        select(UserMemory.id, UserMemory.text_ciphertext)
        .where(_stale(UserMemory.text_ciphertext, active))
        .order_by(UserMemory.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for row_id, blob in rows:
        session.execute(
            update(UserMemory)
            .where(UserMemory.id == row_id)
            .values(
                text_ciphertext=_again(
                    session, blob, MEMORIES_TABLE, "text_ciphertext", row_id, keyring
                ),
                key_version=active,
            )
        )
    return len(rows)
