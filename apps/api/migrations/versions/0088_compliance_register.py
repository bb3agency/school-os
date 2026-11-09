"""FC-01 school compliance certificate register (FR-COMP-001..FR-COMP-030; docs/05 §16.9).

Tenant table ``ops.compliance_certificates`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``,
RLS ENABLE + FORCE with ``tenant_isolation``, composite FKs into ``kb.documents`` and
``core.memberships``, the restrictive ``offboarding_purge`` policy with SELECT and DELETE for
``sos_purger``; ADR-0013, ADR-0029). One row per certificate the school holds or held: the
SCHOOL's certificates (fire NOC, building safety, recognition, affiliation, water and
sanitation), no personal data.

- ``kind``: a key of ``app/compliance/catalogue.yaml``; ``other`` carries the school's own
  ``label`` (CHECK: a label exactly for ``other``).
- ``valid_until`` NULL means "no expiry"; ``valid_from`` <= ``valid_until`` when both are set.
- ``document_id``: the evidence copy in ``kb.documents`` (``ON DELETE SET NULL``).
- ``owner_membership_id``: who renews it (reminders go to them).
- ``status`` ``current`` | ``superseded`` (a renewal replaced it; ``superseded_by`` names the
  renewal) | ``removed`` (recorded by mistake; ``removed_at``/``removed_by``). At most one
  ``current`` row per catalogue kind (partial unique index; ``other`` may repeat).
- ``last_reminded_lead`` / ``last_reminded_on``: the reminder job's idempotency (FR-COMP-012).
- ``sos_app`` may SELECT, INSERT and UPDATE only the editable and workflow columns; never
  DELETE (history is kept; only the offboarding purge removes rows).

Also: the permission catalog gets ``compliance.read`` and ``compliance.manage``. Role grants of
existing schools change only with the post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the table (lossy: the school's register) and removes
the catalog keys when no role holds them.

Revision ID: 0088_compliance_register
Revises: 0087_legal_holds
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0088_compliance_register"
down_revision = "0087_legal_holds"
branch_labels = None
depends_on = None

TABLE = "ops.compliance_certificates"

TABLE_SQL = r"""
CREATE TABLE ops.compliance_certificates (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  kind                  text NOT NULL,
  label                 text,
  reference_no          text,
  issued_by             text,
  valid_from            date,
  valid_until           date,
  document_id           uuid,
  owner_membership_id   uuid,
  notes                 text,
  status                text NOT NULL DEFAULT 'current',
  superseded_by         uuid,
  superseded_at         timestamptz,
  removed_by            uuid,
  removed_at            timestamptz,
  last_reminded_lead    int,
  last_reminded_on      date,
  recorded_by           uuid NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT compliance_certificates_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT compliance_certificates_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT compliance_certificates_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id),
  CONSTRAINT compliance_certificates_owner_fk FOREIGN KEY (tenant_id, owner_membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT compliance_certificates_superseded_fk FOREIGN KEY (tenant_id, superseded_by)
    REFERENCES ops.compliance_certificates (tenant_id, id) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT compliance_certificates_recorded_by_fk FOREIGN KEY (recorded_by)
    REFERENCES core.users (id),
  CONSTRAINT compliance_certificates_removed_by_fk FOREIGN KEY (removed_by)
    REFERENCES core.users (id),
  CONSTRAINT compliance_certificates_kind_format CHECK (kind ~ '^[a-z][a-z0-9_]{1,62}$'),
  CONSTRAINT compliance_certificates_label CHECK (
    (kind = 'other') = (label IS NOT NULL)
    AND (label IS NULL OR char_length(label) BETWEEN 3 AND 120)),
  CONSTRAINT compliance_certificates_reference_len
    CHECK (reference_no IS NULL OR char_length(reference_no) BETWEEN 1 AND 80),
  CONSTRAINT compliance_certificates_issued_by_len
    CHECK (issued_by IS NULL OR char_length(issued_by) BETWEEN 1 AND 200),
  CONSTRAINT compliance_certificates_notes_len
    CHECK (notes IS NULL OR char_length(notes) BETWEEN 1 AND 500),
  CONSTRAINT compliance_certificates_dates
    CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT compliance_certificates_status_check
    CHECK (status IN ('current','superseded','removed')),
  CONSTRAINT compliance_certificates_superseded
    CHECK ((status = 'superseded') = (superseded_by IS NOT NULL AND superseded_at IS NOT NULL)),
  CONSTRAINT compliance_certificates_not_self CHECK (superseded_by IS NULL OR superseded_by <> id),
  CONSTRAINT compliance_certificates_removed
    CHECK ((status = 'removed') = (removed_at IS NOT NULL AND removed_by IS NOT NULL)),
  CONSTRAINT compliance_certificates_reminded
    CHECK ((last_reminded_lead IS NULL) = (last_reminded_on IS NULL)
      AND (last_reminded_lead IS NULL OR last_reminded_lead BETWEEN -1 AND 365))
);
CREATE UNIQUE INDEX compliance_certificates_one_current
  ON ops.compliance_certificates (tenant_id, kind)
  WHERE status = 'current' AND kind <> 'other';
CREATE INDEX compliance_certificates_due_idx
  ON ops.compliance_certificates (tenant_id, valid_until) WHERE status = 'current';
CREATE TRIGGER compliance_certificates_set_updated_at BEFORE UPDATE
  ON ops.compliance_certificates
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ops.compliance_certificates FROM sos_app;
GRANT UPDATE (label, reference_no, issued_by, valid_from, valid_until, document_id,
  owner_membership_id, notes, status, superseded_by, superseded_at, removed_by, removed_at,
  last_reminded_lead, last_reminded_on, updated_at, version)
  ON ops.compliance_certificates TO sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "compliance.read",
        "description": (
            "See the school's compliance certificates (fire NOC, building safety, recognition, "
            "affiliation, water and sanitation), their validity and renewal owners"
        ),
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "compliance.manage",
        "description": (
            "Record, renew, correct and remove the school's compliance certificates and choose "
            "who renews them"
        ),
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
)

UPSERT = sa.text(
    """
    INSERT INTO core.permissions (key, description, sensitivity, step_up, is_platform)
    VALUES (:key, :description, :sensitivity, :step_up, :is_platform)
    ON CONFLICT (key) DO UPDATE SET
      description = EXCLUDED.description,
      sensitivity = EXCLUDED.sensitivity,
      step_up = EXCLUDED.step_up,
      is_platform = EXCLUDED.is_platform
    """
)


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
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)


def downgrade() -> None:
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
