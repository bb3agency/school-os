"""Tally pending bills (bill-wise / term-wise dues) of synced party ledgers (lead decision D63;
ADR-0032; docs/05 §7.4; FR-TALLY-005, FR-TALLY-007).

- ``ops.tally_bills``: the pending bills (Tally bill-wise allocations) of a party ledger as the
  edge agent read them in the snapshot that last updated the ledger: bill reference, bill date,
  due date (Tally's due date or bill date + credit period; null when Tally has neither) and the
  pending amount (positive = the party owes the school). A ledger without bill-wise details has
  no rows and its dues fall back to the closing balance. The rows of a ledger are replaced as a
  whole by each applied snapshot, so the table only ever holds the latest snapshot's bills.

  Tenant table like 0036 (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE
  with ``tenant_isolation``, offboarding ``offboarding_purge`` and ``sos_purger`` grants;
  ADR-0029). Composite FKs to the ledger ``(tenant_id, party_id)`` and to the sync that delivered
  the bill ``(tenant_id, sync_id)``, both ``ON DELETE CASCADE``: the retention job that deletes
  old sync records (``retention.sync_days``) takes their bills with them, and nothing else keeps
  a bill alive. ``sos_app``: SELECT, INSERT, DELETE (replace per snapshot); never UPDATE or
  TRUNCATE.

Classification: C2 like the ledger itself (the bill reference may name a term; the ledger names
a person). No Aadhaar: references are cleaned by the API schema (``mask_aadhaar``) like ledger
names.

Expand-only (invariant 12). Downgrade drops the table (lossy: the bill-wise copy of Tally data;
the next sync after an upgrade sends it again).

Revision ID: 0077_tally_bills
Revises: 0075_breach_retention
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0077_tally_bills"
down_revision = "0075_breach_retention"
branch_labels = None
depends_on = None

TABLE = "ops.tally_bills"

UP = r"""
CREATE TABLE ops.tally_bills (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  party_id    uuid NOT NULL,
  sync_id     uuid NOT NULL,
  reference   text NOT NULL CHECK (char_length(reference) BETWEEN 1 AND 200),
  bill_date   date,
  due_date    date,
  pending     numeric(14,2) NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tally_bills_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_bills_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_bills_party_fk FOREIGN KEY (tenant_id, party_id)
    REFERENCES ops.tally_parties (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT tally_bills_sync_fk FOREIGN KEY (tenant_id, sync_id)
    REFERENCES ops.tally_syncs (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX tally_bills_party_idx ON ops.tally_bills (tenant_id, party_id, due_date);
CREATE INDEX tally_bills_sync_idx ON ops.tally_bills (tenant_id, sync_id);
-- Replaced as a whole per snapshot: rows are inserted and deleted, never edited.
REVOKE UPDATE, TRUNCATE ON ops.tally_bills FROM sos_app;
GRANT SELECT, INSERT, DELETE ON ops.tally_bills TO sos_app;
"""


def upgrade() -> None:
    op.execute(UP)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the school is
    # offboarding and the transaction-local purge flag names it.
    op.execute(f"GRANT SELECT, DELETE ON {TABLE} TO sos_purger")
    op.execute(
        f"CREATE POLICY offboarding_purge ON {TABLE} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed())"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
