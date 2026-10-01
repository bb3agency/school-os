"""APAAR ID and UDISE+ PEN as student attributes (ADR-0037; FR-STU-013..015, PRV-020).

``sis.attribute_definitions`` (existing table; RLS, grants and policies unchanged):

- ``attribute_definitions_data_type_check`` also allows ``digits12`` (exactly 12 ASCII digits,
  the typed APAAR ID), and the new CHECK ``attribute_definitions_digits12_global`` keeps that
  type to global rows: a school's own attribute can never claim the typed-field exemption from
  the full-Aadhaar refusal (FR-STU-015).
- Two global rows from ``app/students/attributes.yaml`` (deterministic ids, same namespace as
  0008): ``udise_pen`` (text, C2) and ``apaar_id`` (digits12, C2). Both are personal (C2), not
  identity fields, so their values live in ``value_text`` like any C2 value (no ciphertext
  column, no DEK re-encryptor, no SEC-012 census entry).

Global rows are written by the table owner with FORCE lifted inside one transaction-local block
and restored at once (the attrdef policies admit only the school's own rows, and FORCE applies to
the owner too; same pattern as 0014). Expand-only (invariant 12): rows and a widened CHECK.

Downgrade deletes the two global definitions and restores the original CHECK. Recorded values
(``sis.attribute_values`` rows with these keys) are kept: the previous code has no definition for
them and treats them as hidden; upgrading again re-seeds the same ids and they show again.

Revision ID: 0042_apaar_id
Revises: 0041_billing_catalogue
Create Date: 2026-10-01
"""

from __future__ import annotations

import json
import uuid
from importlib import resources
from typing import Any

import sqlalchemy as sa
import yaml
from alembic import op

revision = "0042_apaar_id"
down_revision = "0041_billing_catalogue"
branch_labels = None
depends_on = None

ATTRDEF_NAMESPACE = uuid.UUID("5f0c8a52-6f0e-4d8e-9d55-2b8f6b1d0a01")  # as in 0008
KEYS = ("udise_pen", "apaar_id")

UPGRADE_SQL = """
ALTER TABLE sis.attribute_definitions DROP CONSTRAINT attribute_definitions_data_type_check;
ALTER TABLE sis.attribute_definitions ADD CONSTRAINT attribute_definitions_data_type_check
  CHECK (data_type IN ('text','date','enum','digits4','digits12'));
ALTER TABLE sis.attribute_definitions ADD CONSTRAINT attribute_definitions_digits12_global
  CHECK (data_type <> 'digits12' OR tenant_id IS NULL);
"""

DOWNGRADE_SQL = """
ALTER TABLE sis.attribute_definitions DROP CONSTRAINT IF EXISTS
  attribute_definitions_digits12_global;
ALTER TABLE sis.attribute_definitions DROP CONSTRAINT attribute_definitions_data_type_check;
ALTER TABLE sis.attribute_definitions ADD CONSTRAINT attribute_definitions_data_type_check
  CHECK (data_type IN ('text','date','enum','digits4'));
"""

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
    op.execute(UPGRADE_SQL)
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
    op.execute(DOWNGRADE_SQL)
