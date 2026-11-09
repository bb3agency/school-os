"""FC-12 legal holds (ADR-0059; FR-DPDP-039..FR-DPDP-052; docs/05 §16.4).

Tenant table ``ops.legal_holds`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FK into ``sis.students``, the restrictive
``offboarding_purge`` policy with SELECT and DELETE for ``sos_purger``; ADR-0013, ADR-0029):

- ``scope`` ``student`` (``student_id`` set) or ``record_class`` (``record_class`` set: a key of
  ``app/privacy/record_classes.yaml``, whole school).
- ``reason_code`` from ``app/privacy/holds.yaml``; ``reference_ciphertext``: the case or letter
  reference, C3, encrypted with the school's DEK (AAD ``ops.legal_holds|reference_ciphertext|<id>``,
  re-encrypted on key rotation), never logged, audited or shown in access reports.
- ``review_on``: required (the service caps it at 12 months ahead, config).
- Maker-checker (lead decision D84): ``placed_by`` asks, ``approved_by`` (another person,
  CHECK) makes it ``active``; ``lift_requested_by`` asks to lift, ``lifted_by`` (another person,
  CHECK) makes it ``lifted``. ``status`` ``pending`` | ``active`` | ``lift_pending`` | ``lifted``
  | ``rejected``; holds that are ``active`` or ``lift_pending`` stop retention and erasure.
- ``sos_app`` may SELECT, INSERT and UPDATE only the workflow columns; never DELETE (a hold is a
  record of its own; only the offboarding purge removes it).

Also: the permission catalog gets ``privacy.hold.manage`` (critical, step-up). Role grants of
existing schools change only with the post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the table (lossy: the school's holds) and removes the
catalog key when no role holds it.

Revision ID: 0083_legal_holds
Revises: 0082_audit_ym_fixes
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0083_legal_holds"
down_revision = "0082_audit_ym_fixes"
branch_labels = None
depends_on = None

TABLE = "ops.legal_holds"

TABLE_SQL = r"""
CREATE TABLE ops.legal_holds (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  scope                 text NOT NULL,
  student_id            uuid,
  record_class          text,
  reason_code           text NOT NULL,
  reference_ciphertext  bytea,
  review_on             date NOT NULL,
  status                text NOT NULL DEFAULT 'pending',
  placed_by             uuid NOT NULL,
  placed_at             timestamptz NOT NULL DEFAULT now(),
  approved_by           uuid,
  approved_at           timestamptz,
  rejected_by           uuid,
  rejected_at           timestamptz,
  lift_requested_by     uuid,
  lift_requested_at     timestamptz,
  lifted_by             uuid,
  lifted_at             timestamptz,
  last_reminded_on      date,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT legal_holds_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT legal_holds_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT legal_holds_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT legal_holds_placed_by_fk FOREIGN KEY (placed_by) REFERENCES core.users (id),
  CONSTRAINT legal_holds_approved_by_fk FOREIGN KEY (approved_by) REFERENCES core.users (id),
  CONSTRAINT legal_holds_rejected_by_fk FOREIGN KEY (rejected_by) REFERENCES core.users (id),
  CONSTRAINT legal_holds_lift_requested_by_fk
    FOREIGN KEY (lift_requested_by) REFERENCES core.users (id),
  CONSTRAINT legal_holds_lifted_by_fk FOREIGN KEY (lifted_by) REFERENCES core.users (id),
  CONSTRAINT legal_holds_scope_check CHECK (
    (scope = 'student' AND student_id IS NOT NULL AND record_class IS NULL)
    OR (scope = 'record_class' AND student_id IS NULL AND record_class IS NOT NULL)),
  CONSTRAINT legal_holds_record_class_format
    CHECK (record_class IS NULL OR record_class ~ '^[a-z][a-z0-9_]{1,62}$'),
  CONSTRAINT legal_holds_reason_code_format CHECK (reason_code ~ '^[a-z][a-z0-9_]{1,62}$'),
  CONSTRAINT legal_holds_status_check
    CHECK (status IN ('pending','active','lift_pending','lifted','rejected')),
  CONSTRAINT legal_holds_approved CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
  CONSTRAINT legal_holds_two_people_place
    CHECK (approved_by IS NULL OR approved_by <> placed_by),
  CONSTRAINT legal_holds_two_people_lift
    CHECK (lifted_by IS NULL OR (lift_requested_by IS NOT NULL AND lifted_by <> lift_requested_by)),
  CONSTRAINT legal_holds_active_approved
    CHECK (status NOT IN ('active','lift_pending','lifted') OR approved_by IS NOT NULL),
  CONSTRAINT legal_holds_lifted CHECK ((status = 'lifted') = (lifted_at IS NOT NULL)),
  CONSTRAINT legal_holds_rejected CHECK ((status = 'rejected') = (rejected_at IS NOT NULL))
);
CREATE INDEX legal_holds_status_idx ON ops.legal_holds (tenant_id, status, review_on);
CREATE INDEX legal_holds_student_idx ON ops.legal_holds (tenant_id, student_id)
  WHERE student_id IS NOT NULL;
CREATE TRIGGER legal_holds_set_updated_at BEFORE UPDATE ON ops.legal_holds
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE ON ops.legal_holds FROM sos_app;
GRANT UPDATE (status, approved_by, approved_at, rejected_by, rejected_at, lift_requested_by,
  lift_requested_at, lifted_by, lifted_at, last_reminded_on, reference_ciphertext, updated_at,
  version) ON ops.legal_holds TO sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "privacy.hold.manage",
        "description": (
            "See the school's legal holds, and place or lift one (two different people: one "
            "asks, another approves)"
        ),
        "sensitivity": "critical",
        "step_up": True,
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
