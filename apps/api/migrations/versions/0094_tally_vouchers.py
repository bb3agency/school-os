"""Tally voucher export: ledger mapping, export batches and exported vouchers (FM-03;
FR-TALLY-011..030; docs/05 §7.4; ADR-0032 stays read-only: SchoolOS writes a file, never Tally).

- ``ops.tally_export_settings``: one row per school. ``party_mode`` ``student`` (one Tally party
  per student: the ledger linked to the student, else the receivable ledger) or ``receivable``
  (one receivable ledger for every student); lead decision D85, PO to confirm. Ledger names per
  receipt mode (``mode_ledgers``) and per fee head (``head_ledgers``, keyed by head id), the
  receivable ledger, the ledger for late fees and head-less adjustments, whether fee demand is
  exported, and the Tally company name. ``sos_app``: SELECT, INSERT and UPDATE of the mapping
  columns; never DELETE.
- ``ops.tally_voucher_exports``: one row per downloaded file (period, counts, total of the cash
  vouchers, the party mode used, the file's SHA-256, who and when). Insert-only for ``sos_app``.
- ``ops.tally_exported_vouchers``: which source records (receipt, cancellation, refund, ledger
  entry) went into a file first: ``UNIQUE (tenant_id, voucher_key)`` makes a re-export find them
  (idempotent export). Insert-only for ``sos_app``; composite FK to its export.

Tenant tables like 0036/0077 (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE
with ``tenant_isolation``, the restrictive ``offboarding_purge`` policy with SELECT and DELETE for
``sos_purger``; ADR-0029). Classification C2 with the Tally ledgers (ledger names may name a
family; no student names are stored: the file is generated on request and never kept).

Expand-only (invariant 12). Downgrade drops the three tables (lossy: the mapping and the export
history; a later export would then include every voucher again).

Revision ID: 0094_tally_vouchers
Revises: 0093_msg_reach_closure
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "0094_tally_vouchers"
down_revision = "0093_msg_reach_closure"
branch_labels = None
depends_on = None

TABLES = (
    "ops.tally_export_settings",
    "ops.tally_voucher_exports",
    "ops.tally_exported_vouchers",
)

UP = r"""
CREATE TABLE ops.tally_export_settings (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  party_mode           text NOT NULL,
  receivable_ledger    text NOT NULL,
  mode_ledgers         jsonb NOT NULL DEFAULT '{}'::jsonb,
  head_ledgers         jsonb NOT NULL DEFAULT '{}'::jsonb,
  other_income_ledger  text,
  include_demand       boolean NOT NULL DEFAULT false,
  company              text,
  updated_by           uuid NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1,
  CONSTRAINT tally_export_settings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_export_settings_one_per_school UNIQUE (tenant_id),
  CONSTRAINT tally_export_settings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_export_settings_updated_by_fk FOREIGN KEY (updated_by)
    REFERENCES core.users (id),
  CONSTRAINT tally_export_settings_party_mode CHECK (party_mode IN ('student', 'receivable')),
  CONSTRAINT tally_export_settings_receivable_len
    CHECK (char_length(receivable_ledger) BETWEEN 1 AND 255),
  CONSTRAINT tally_export_settings_other_len
    CHECK (other_income_ledger IS NULL OR char_length(other_income_ledger) BETWEEN 1 AND 255),
  CONSTRAINT tally_export_settings_company_len
    CHECK (company IS NULL OR char_length(company) BETWEEN 1 AND 255),
  CONSTRAINT tally_export_settings_modes_object CHECK (jsonb_typeof(mode_ledgers) = 'object'),
  CONSTRAINT tally_export_settings_heads_object CHECK (jsonb_typeof(head_ledgers) = 'object')
);

CREATE TABLE ops.tally_voucher_exports (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  period_from       date NOT NULL,
  period_to         date NOT NULL,
  include_exported  boolean NOT NULL,
  include_demand    boolean NOT NULL,
  party_mode        text NOT NULL,
  vouchers          int NOT NULL CHECK (vouchers >= 0),
  new_vouchers      int NOT NULL CHECK (new_vouchers >= 0),
  cash_total        numeric(14,2) NOT NULL,
  file_sha256       bytea NOT NULL CHECK (octet_length(file_sha256) = 32),
  created_by        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tally_voucher_exports_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_voucher_exports_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_voucher_exports_created_by_fk FOREIGN KEY (created_by)
    REFERENCES core.users (id),
  CONSTRAINT tally_voucher_exports_period CHECK (period_from <= period_to),
  CONSTRAINT tally_voucher_exports_party_mode CHECK (party_mode IN ('student', 'receivable')),
  CONSTRAINT tally_voucher_exports_new_le_all CHECK (new_vouchers <= vouchers)
);
CREATE INDEX tally_voucher_exports_created_idx
  ON ops.tally_voucher_exports (tenant_id, created_at DESC);

CREATE TABLE ops.tally_exported_vouchers (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL,
  voucher_key  text NOT NULL,
  kind         text NOT NULL,
  export_id    uuid NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tally_exported_vouchers_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_exported_vouchers_once UNIQUE (tenant_id, voucher_key),
  CONSTRAINT tally_exported_vouchers_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT tally_exported_vouchers_export_fk FOREIGN KEY (tenant_id, export_id)
    REFERENCES ops.tally_voucher_exports (tenant_id, id),
  CONSTRAINT tally_exported_vouchers_kind
    CHECK (kind IN ('receipt', 'cancellation', 'refund', 'demand')),
  CONSTRAINT tally_exported_vouchers_key_shape
    CHECK (voucher_key ~ '^(receipt|cancellation|refund|demand):[0-9a-f-]{36}$')
);
CREATE INDEX tally_exported_vouchers_export_idx
  ON ops.tally_exported_vouchers (tenant_id, export_id);

-- The mapping is edited, never deleted by the app; exports and their vouchers are a record.
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tally_export_settings FROM sos_app;
GRANT SELECT, INSERT ON ops.tally_export_settings TO sos_app;
GRANT UPDATE (party_mode, receivable_ledger, mode_ledgers, head_ledgers, other_income_ledger,
              include_demand, company, updated_by, updated_at, version)
  ON ops.tally_export_settings TO sos_app;
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tally_voucher_exports FROM sos_app;
GRANT SELECT, INSERT ON ops.tally_voucher_exports TO sos_app;
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tally_exported_vouchers FROM sos_app;
GRANT SELECT, INSERT ON ops.tally_exported_vouchers TO sos_app;
"""


def upgrade() -> None:
    op.execute(UP)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the
        # school is offboarding and the transaction-local purge flag names it.
        op.execute(f"GRANT SELECT, DELETE ON {table} TO sos_purger")
        op.execute(
            f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
            "USING (core.tenant_purge_allowed())"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
