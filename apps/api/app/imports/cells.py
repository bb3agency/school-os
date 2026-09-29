"""Staged cell edits: encryption and key rotation (FR-IMP-008; SEC-012, docs/05 §5.2.1, §9).

``sis.import_cell_edits`` keeps the value before and after each edit only as ciphertext under the
school's key (associated data ``tenant_id|sis.import_cell_edits|<column>|<edit id>``): a cell may
hold anything a school typed into a spreadsheet, including restricted (C3) data in a column that
is not mapped yet. Plaintext leaves this module only as the values applied to the sheet, and is
never logged. :func:`reencrypt_batch` is the imports re-encryptor for DEK rotation (registered by
``app.imports.service`` as ``import_cell_edits``).
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.orm import Session

from app.core.crypto import ciphertext_key_version
from app.imports import repository as repo
from app.students import crypto

TABLE: Final = "sis.import_cell_edits"
OLD_COLUMN: Final = "old_value_ciphertext"
NEW_COLUMN: Final = "new_value_ciphertext"

CellKey = tuple[int, int]  # (row_no, column index)


def encrypt(
    session: Session, value: str | None, *, column: str, edit_id: uuid.UUID
) -> tuple[bytes | None, int | None]:
    """``(ciphertext, key_version)``; ``(None, None)`` for an empty cell."""
    if value is None:
        return None, None
    blob, version = crypto.encrypt_value(session, value, table=TABLE, column=column, row_id=edit_id)
    return blob, version


def decrypt(session: Session, blob: bytes | None, *, column: str, edit_id: uuid.UUID) -> str | None:
    if blob is None:
        return None
    return crypto.decrypt_value(session, bytes(blob), table=TABLE, column=column, row_id=edit_id)


def current_values(session: Session, batch_id: uuid.UUID) -> dict[CellKey, str | None]:
    """The newest value of every edited cell of the batch (decrypted; ``None`` = cleared)."""
    out: dict[CellKey, str | None] = {}
    for edit in repo.current_cell_edits(session, batch_id):
        out[(edit.row_no, edit.column_index)] = decrypt(
            session, edit.new_value_ciphertext, column=NEW_COLUMN, edit_id=edit.id
        )
    return out


def reencrypt_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """DEK rotation (``app.students.rotation.Reencryptor``): at most ``limit`` edits whose
    ciphertext is under another key version are re-encrypted to ``target_version`` with the
    same associated data. Rows are locked (``SKIP LOCKED``); returns how many were rewritten."""
    edits = repo.stale_cell_edits(session, target_version, limit)
    for edit in edits:
        blobs: dict[str, bytes | None] = {}
        for column, raw in (
            (OLD_COLUMN, edit.old_value_ciphertext),
            (NEW_COLUMN, edit.new_value_ciphertext),
        ):
            if raw is None or ciphertext_key_version(bytes(raw)) == target_version:
                blobs[column] = bytes(raw) if raw is not None else None
                continue
            blobs[column] = crypto.reencrypt_value(
                session,
                bytes(raw),
                table=TABLE,
                column=column,
                row_id=edit.id,
                key_version=target_version,
                keyring=keyring,
            )
        repo.set_cell_edit_ciphertext(
            session,
            edit.id,
            old=blobs[OLD_COLUMN],
            new=blobs[NEW_COLUMN],
            key_version=target_version,
        )
    return len(edits)


__all__ = ["CellKey", "current_values", "decrypt", "encrypt", "reencrypt_batch"]
