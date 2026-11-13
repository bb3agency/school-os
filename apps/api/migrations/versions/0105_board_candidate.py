"""Board candidate choices as student attributes (FO-10; FR-STU-020..025).

``sis.attribute_definitions`` (existing table; RLS, grants and policies unchanged): six global
rows from ``app/students/attributes.yaml`` (deterministic ids, same namespace as 0008 and 0042):

- ``exam_medium`` (enum), ``first_language``, ``second_language``, ``third_language`` and
  ``board_subjects`` (text, board codes of ``app/students/board_catalogue.yaml``);
- ``photo_signature_ready`` (enum yes/no, source ``manual_entry`` only): a staff tick that the
  photograph and signature were collected for the board portal. The photograph and signature
  themselves are never stored (a photo store would be a new data class and needs an ADR).

All six are personal (C2), not identity fields: values live in ``value_text`` like any C2 value
(no ciphertext column, no DEK re-encryptor, no SEC-012 census entry) and never need a change
request. No new table, so no new RLS policy, grant, purge or export entry.

Global rows are written by the table owner with FORCE lifted inside one transaction-local block
and restored at once (as 0042). Expand-only (invariant 12): rows only.

Downgrade deletes the six global definitions. Recorded values (``sis.attribute_values`` rows with
these keys) are kept: the previous code has no definition for them and treats them as hidden;
upgrading again re-seeds the same ids and they show again.

Revision ID: 0105_board_candidate
Revises: 0103_bank_matching
Create Date: 2026-11-13
"""

from __future__ import annotations

import json
import uuid
from importlib import resources
from typing import Any

import sqlalchemy as sa
import yaml
from alembic import op

revision = "0105_board_candidate"
down_revision = "0103_bank_matching"
branch_labels = None
depends_on = None

ATTRDEF_NAMESPACE = uuid.UUID("5f0c8a52-6f0e-4d8e-9d55-2b8f6b1d0a01")  # as in 0008
KEYS = (
    "exam_medium",
    "first_language",
    "second_language",
    "third_language",
    "board_subjects",
    "photo_signature_ready",
)

# The owner lifts FORCE for these statements only; the block's own rollback restores it.
UNFORCE = "ALTER TABLE sis.attribute_definitions NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner writes global catalogue rows; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE sis.attribute_definitions FORCE ROW LEVEL SECURITY"

SEED = sa.text(
    """
    INSERT INTO sis.attribute_definitions
      (id, tenant_id, key, data_type, classification, is_identity, validation, canonical_policy,
       label_en, label_te, sort_order)
    VALUES (:id, NULL, :key, :data_type, :classification, :is_identity,
            CAST(:validation AS jsonb), CAST(:canonical_policy AS jsonb), :label_en, :label_te,
            :sort_order)
    ON CONFLICT (id) DO UPDATE SET
      data_type = EXCLUDED.data_type, classification = EXCLUDED.classification,
      is_identity = EXCLUDED.is_identity, validation = EXCLUDED.validation,
      canonical_policy = EXCLUDED.canonical_policy, label_en = EXCLUDED.label_en,
      label_te = EXCLUDED.label_te, sort_order = EXCLUDED.sort_order
    """
)


def attribute_id(key: str) -> uuid.UUID:
    return uuid.uuid5(ATTRDEF_NAMESPACE, f"sis.attribute_definitions:{key}")


def _rows() -> list[dict[str, Any]]:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    rows: list[dict[str, Any]] = []
    for key in KEYS:
        spec = raw["attributes"][key]
        rows.append(
            {
                "id": attribute_id(key),
                "key": key,
                "data_type": spec["data_type"],
                "classification": spec["classification"],
                "is_identity": bool(spec.get("is_identity", False)),
                "validation": json.dumps(spec.get("validation", {}), sort_keys=True),
                "canonical_policy": json.dumps(spec["canonical_policy"], sort_keys=True),
                "label_en": spec["label_en"],
                "label_te": spec["label_te"],
                "sort_order": int(spec.get("sort_order", 1000)),
            }
        )
    return rows


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(UNFORCE)
    for row in _rows():
        bind.execute(SEED, row)
    op.execute(REFORCE)


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(UNFORCE)
    bind.execute(
        sa.text("DELETE FROM sis.attribute_definitions WHERE tenant_id IS NULL AND id = ANY(:ids)"),
        {"ids": [attribute_id(k) for k in KEYS]},
    )
    op.execute(REFORCE)
