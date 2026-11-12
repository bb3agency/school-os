"""FC-07 / FO-12 inspection readiness packs: the log of generated packs (FR-COMP-024..FR-COMP-045;
docs/05 §16.9).

Tenant table ``ops.inspection_packs`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS
ENABLE + FORCE with ``tenant_isolation``, composite FK into ``core.academic_years``, the
restrictive ``offboarding_purge`` policy with SELECT and DELETE for ``sos_purger``; ADR-0013,
ADR-0029). One row per pack the office made: which pack (``app/compliance/packs_catalogue.yaml``
key, version, label and authority as printed), for which academic year and visit date, which registers were chosen, and each
item's state at that moment (``items``: keys, sources, states, dates and record ids only) with
the counts. School-level facts: no student or staff data (registers are linked, never copied).

- ``sos_app`` may SELECT and INSERT only: the log is append-only (no UPDATE, DELETE, TRUNCATE);
  only the offboarding purge removes rows.
- No new permission: the packs use ``compliance.read`` / ``compliance.manage`` (0096).

Expand-only (invariant 12). Downgrade drops the table (lossy: the school's pack log).

Revision ID: 0098_inspection_packs
Revises: 0096_compliance_register
Create Date: 2026-11-12
"""

from __future__ import annotations

from alembic import op

revision = "0098_inspection_packs"
down_revision = "0096_compliance_register"
branch_labels = None
depends_on = None

TABLE = "ops.inspection_packs"

TABLE_SQL = r"""
CREATE TABLE ops.inspection_packs (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  pack_key          text NOT NULL,
  pack_version      int NOT NULL CHECK (pack_version >= 1),
  pack_label        text NOT NULL CHECK (char_length(pack_label) BETWEEN 3 AND 120),
  authority         text NOT NULL CHECK (char_length(authority) BETWEEN 3 AND 200),
  verified          boolean NOT NULL,
  academic_year_id  uuid,
  visit_on          date,
  registers         text[] NOT NULL DEFAULT '{}',
  items             jsonb NOT NULL,
  counts            jsonb NOT NULL,
  generated_by      uuid NOT NULL,
  generated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT inspection_packs_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT inspection_packs_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT inspection_packs_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT inspection_packs_generated_by_fk FOREIGN KEY (generated_by)
    REFERENCES core.users (id),
  CONSTRAINT inspection_packs_key_format CHECK (pack_key ~ '^[a-z][a-z0-9_]{1,62}$'),
  CONSTRAINT inspection_packs_items_array CHECK (jsonb_typeof(items) = 'array'),
  CONSTRAINT inspection_packs_counts_object CHECK (jsonb_typeof(counts) = 'object'),
  CONSTRAINT inspection_packs_registers_len CHECK (cardinality(registers) <= 10)
);
CREATE INDEX inspection_packs_recent_idx
  ON ops.inspection_packs (tenant_id, generated_at DESC, id DESC);
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ops.inspection_packs FROM sos_app;
"""


def upgrade() -> None:
    op.execute(TABLE_SQL)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the school
    # is offboarding and the transaction-local purge flag names it.
    op.execute(f"GRANT SELECT, DELETE ON {TABLE} TO sos_purger")
    op.execute(
        f"CREATE POLICY offboarding_purge ON {TABLE} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed())"
    )
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
