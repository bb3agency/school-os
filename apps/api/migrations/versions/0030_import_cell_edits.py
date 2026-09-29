"""Staged cell edits of spreadsheet imports (docs/05 §5.2.1; US-401 AC5, FR-IMP-008).

``sis.import_cell_edits`` is the append-only edit history of an import batch's staged sheet:
one row per edited cell (``row_no`` as the spreadsheet shows it, 0-based ``column_index``).
The uploaded raw file is never changed; the current value of a cell is the newest edit of that
cell (highest ``batch_version``: the batch ETag version the edit produced, so edits of one batch
are totally ordered by the batch row lock). Parsing, validation and commit apply the edits on
top of the raw file.

Cell values may be anything a school puts in a spreadsheet, including restricted (C3) data in
columns that are not mapped yet, so both the value before and after the edit are stored only as
AES-256-GCM ciphertext under the school's key (``old_value_ciphertext`` /
``new_value_ciphertext``, associated data ``tenant_id|sis.import_cell_edits|<column>|<id>``,
``key_version`` of both; NULL = the cell was / became empty). Full Aadhaar numbers are refused
before anything is stored (invariant 4).

Tenant-owned: ``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FK to ``sis.import_batches`` (``ON DELETE CASCADE``). The app
may only insert rows and rewrite the ciphertext columns (DEK re-encryption, SEC-012); history is
never updated or deleted by ``sos_app``.

Expand-only (invariant 12). Downgrade drops the table (the edit history is lost; batches keep
working from their raw files).

Revision ID: 0030_import_cell_edits
Revises: 0029_kb_v2
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op

revision = "0030_import_cell_edits"
down_revision = "0029_kb_v2"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE sis.import_cell_edits (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL REFERENCES core.tenants (id),
  batch_id              uuid NOT NULL,
  batch_version         int NOT NULL,
  row_no                int NOT NULL,
  column_index          int NOT NULL,
  old_value_ciphertext  bytea,
  new_value_ciphertext  bytea,
  key_version           int,
  edited_by             uuid NOT NULL REFERENCES core.users (id),
  edited_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT import_cell_edits_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT import_cell_edits_cell_version_key
    UNIQUE (tenant_id, batch_id, row_no, column_index, batch_version),
  CONSTRAINT import_cell_edits_row_no_positive CHECK (row_no >= 1),
  CONSTRAINT import_cell_edits_column_range CHECK (column_index BETWEEN 0 AND 255),
  CONSTRAINT import_cell_edits_batch_version_positive CHECK (batch_version >= 1),
  CONSTRAINT import_cell_edits_key_version CHECK (
    (old_value_ciphertext IS NULL AND new_value_ciphertext IS NULL) OR key_version >= 1),
  CONSTRAINT import_cell_edits_batch_fk FOREIGN KEY (tenant_id, batch_id)
    REFERENCES sis.import_batches (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX import_cell_edits_cell_idx
  ON sis.import_cell_edits (tenant_id, batch_id, row_no, column_index, batch_version DESC);
"""

GRANTS_SQL = r"""
-- Append-only history: the app inserts edits and may only rewrite their ciphertext (re-encryption
-- to a newer key version, SEC-012); it never updates the cell, author or time, nor deletes.
REVOKE UPDATE, DELETE ON sis.import_cell_edits FROM sos_app;
GRANT UPDATE (old_value_ciphertext, new_value_ciphertext, key_version)
  ON sis.import_cell_edits TO sos_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    op.execute("ALTER TABLE sis.import_cell_edits ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sis.import_cell_edits FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON sis.import_cell_edits "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sis.import_cell_edits")
