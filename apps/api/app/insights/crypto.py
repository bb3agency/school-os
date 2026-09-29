"""Behaviour-note and action-note text under the school's key (C3; FR-EW-010; docs/05 §8, §9).

Notes are restricted (disciplinary notes and early-warning data are C3, docs/05 §8): the text is
stored only as AES-256-GCM ciphertext under the school's DEK with associated data
``tenant_id|<table>|<column>|<row id>`` (a ciphertext moved to another row or school fails to
decrypt). Plaintext leaves this module only to the service that shows it to someone allowed to
see it, and is never logged or sent to an AI provider. :func:`reencrypt_batch` is the insights
re-encryptor for DEK rotation (registered by ``app.insights.service`` as ``insights_notes``).
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.orm import Session

from app.core.crypto import ciphertext_key_version
from app.insights import repository as repo
from app.students import crypto

NOTES_TABLE: Final = "sis.behaviour_notes"
NOTE_COLUMN: Final = "body_ciphertext"
ACTIONS_TABLE: Final = "sis.flag_actions"
ACTION_COLUMN: Final = "note_ciphertext"
COLUMNS: Final = ((NOTES_TABLE, NOTE_COLUMN), (ACTIONS_TABLE, ACTION_COLUMN))


def encrypt_note(session: Session, text: str, note_id: uuid.UUID) -> tuple[bytes, int]:
    return crypto.encrypt_value(
        session, text, table=NOTES_TABLE, column=NOTE_COLUMN, row_id=note_id
    )


def decrypt_note(session: Session, blob: bytes, note_id: uuid.UUID) -> str:
    return crypto.decrypt_value(
        session, bytes(blob), table=NOTES_TABLE, column=NOTE_COLUMN, row_id=note_id
    )


def encrypt_action(
    session: Session, text: str | None, action_id: uuid.UUID
) -> tuple[bytes | None, int | None]:
    if text is None:
        return None, None
    blob, version = crypto.encrypt_value(
        session, text, table=ACTIONS_TABLE, column=ACTION_COLUMN, row_id=action_id
    )
    return blob, version


def decrypt_action(session: Session, blob: bytes | None, action_id: uuid.UUID) -> str | None:
    if blob is None:
        return None
    return crypto.decrypt_value(
        session, bytes(blob), table=ACTIONS_TABLE, column=ACTION_COLUMN, row_id=action_id
    )


def reencrypt_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """DEK rotation (``app.students.rotation.Reencryptor``): at most ``limit`` notes and action
    notes whose ciphertext is under another key version are re-encrypted to ``target_version``
    with the same associated data (rows locked, ``SKIP LOCKED``); returns how many."""
    done = 0
    for note in repo.stale_notes(session, target_version, limit):
        blob = bytes(note.body_ciphertext)
        if ciphertext_key_version(blob) != target_version:
            blob = crypto.reencrypt_value(
                session,
                blob,
                table=NOTES_TABLE,
                column=NOTE_COLUMN,
                row_id=note.id,
                key_version=target_version,
                keyring=keyring,
            )
        repo.set_note_ciphertext(session, note.id, blob, target_version)
        done += 1
    for action in repo.stale_actions(session, target_version, limit - done):
        raw = action.note_ciphertext
        if raw is None:  # pragma: no cover - the query selects rows with a note
            continue
        blob = bytes(raw)
        if ciphertext_key_version(blob) != target_version:
            blob = crypto.reencrypt_value(
                session,
                blob,
                table=ACTIONS_TABLE,
                column=ACTION_COLUMN,
                row_id=action.id,
                key_version=target_version,
                keyring=keyring,
            )
        repo.set_action_ciphertext(session, action.id, blob, target_version)
        done += 1
    return done


__all__ = [
    "ACTIONS_TABLE",
    "ACTION_COLUMN",
    "COLUMNS",
    "NOTES_TABLE",
    "NOTE_COLUMN",
    "decrypt_action",
    "decrypt_note",
    "encrypt_action",
    "encrypt_note",
    "reencrypt_batch",
]
