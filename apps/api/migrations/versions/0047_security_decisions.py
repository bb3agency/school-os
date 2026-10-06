"""Owner decisions on the 2026-10-06 API route audit (R-17, R-18, R-19).

Data migration on the global catalog ``core.permissions`` (no RLS; ``0004_authz_seed``'s
docstring: later catalog changes get their own revision that re-runs the upsert):

- R-17 adds ``support.manage`` (read and reply on every support ticket of the school). Without
  it a member reads and answers only the tickets they opened themselves; ticket subjects and
  messages may name students (OWASP API3, DPDP purpose limit). ``support.ticket.create`` gets
  the matching description (open tickets; read and reply on the ones you opened).

The rows are written out here rather than read from ``permissions.yaml`` so this revision means
the same thing whatever the YAML says later.

- R-18 adds ``platform.deployments.security_hold`` (control-plane schema: no RLS, written only by
  ``sos_platform``, no student data). An operator security hold is tracked apart from a billing
  suspension (which lives on the subscription and shows as ``tenant_status_reason = 'billing'``),
  so a hold can be placed on a school suspended for billing and paying never lifts a hold. The
  school is active only when neither is set; a CHECK refuses a held school that is active.
  Backfill: a suspended school whose reason is not ``billing`` was suspended by an operator
  (``tenants.suspend``), so it is on hold.

- R-19 adds ``audit.chain_verifications``: one row per school with the latest stored result of
  the chain verification (daily job or on-demand run) and the checkpoint (seq and hash of the
  last verified event) that on-demand runs verify from. ``GET /audit/verify`` serves it instead
  of re-hashing the whole chain on every call (OWASP API4, CWE-400). A tenant table: RLS
  ENABLE + FORCE with the standard policy; ``sos_app`` SELECT/INSERT/UPDATE (no DELETE or
  TRUNCATE; like ``audit.chain_heads``), ``sos_readonly`` SELECT, no ``sos_platform`` access.
  It is evidence about the chain, so it is retained with ``audit.events`` at offboarding and
  deleted with the chain when the audit retention ends (``sos_purger`` under
  ``core.tenant_audit_purge_allowed()``, as ``0032_offboarding`` does for the chain).

**Role grants of existing schools are NOT changed here** (system roles are tenant rows under
FORCE RLS; see ``0019_export_access``). New schools get ``support.manage`` for owner, principal
and office admin from ``roles.yaml`` at provisioning; existing schools get it from
``python -m app.identity.sync_system_roles --apply`` (docs/10 runbook). Until then their owner,
principal and office admin see only the tickets they opened themselves: the change fails safe.

Downgrade drops the verification table (stored results only; the next daily run re-creates
them on re-upgrade), the hold column (a school held and suspended for billing at once keeps its hold
reason; the previous code then refuses reactivation while the subscription is suspended, A-08),
then deletes the key in a savepoint and keeps it while a role still holds it (the
foreign-key check sees rows RLS hides from the migrator), exactly like ``0019_export_access``.

Revision ID: 0047_security_decisions
Revises: 0046_audit_append_guard
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_security_decisions"
down_revision = "0046_audit_append_guard"
branch_labels = None
depends_on = None

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "support.manage",
        "description": "Read and reply on every support ticket of the school",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
)

TICKET_CREATE_DESCRIPTION = {
    "new": "Open support tickets and read and reply on the ones you opened",
    "old": "Open and read the school's own support tickets",
}
SET_DESCRIPTION = sa.text("UPDATE core.permissions SET description = :d WHERE key = :k")

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


SECURITY_HOLD_UP = (
    "ALTER TABLE platform.deployments ADD COLUMN security_hold boolean NOT NULL DEFAULT false",
    "UPDATE platform.deployments SET security_hold = true "
    "WHERE tenant_status = 'suspended' AND tenant_status_reason IS DISTINCT FROM 'billing'",
    "ALTER TABLE platform.deployments ADD CONSTRAINT deployments_security_hold_not_active "
    "CHECK (NOT security_hold OR tenant_status <> 'active')",
)
SECURITY_HOLD_DOWN = (
    "ALTER TABLE platform.deployments DROP CONSTRAINT IF EXISTS "
    "deployments_security_hold_not_active",
    "ALTER TABLE platform.deployments DROP COLUMN IF EXISTS security_hold",
)


VERIFICATIONS_UP = (
    """
    CREATE TABLE audit.chain_verifications (
      tenant_id        uuid        PRIMARY KEY,
      verified_at      timestamptz,
      mode             text        CHECK (mode IN ('full','incremental')),
      source           text        CHECK (source IN ('daily','on_demand')),
      ok               boolean,
      checked          bigint      NOT NULL DEFAULT 0 CHECK (checked >= 0),
      first_bad_seq    bigint      CHECK (first_bad_seq >= 0),
      reason           text        CHECK (reason ~ '^[a-z_]{1,64}$'),
      checkpoint_seq   bigint      NOT NULL DEFAULT 0 CHECK (checkpoint_seq >= 0),
      checkpoint_hash  bytea       CHECK (octet_length(checkpoint_hash) = 32),
      checkpoint_at    timestamptz,
      last_full_at     timestamptz,
      requested_at     timestamptz,
      requested_by     uuid,
      requested_full   boolean     NOT NULL DEFAULT false,
      updated_at       timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT chain_verifications_checkpoint
        CHECK ((checkpoint_seq = 0) = (checkpoint_hash IS NULL)),
      CONSTRAINT chain_verifications_result
        CHECK ((verified_at IS NULL) = (ok IS NULL))
    )
    """,
    "ALTER TABLE audit.chain_verifications ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit.chain_verifications FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON audit.chain_verifications
      USING (tenant_id = core.current_tenant())
      WITH CHECK (tenant_id = core.current_tenant())
    """,
    "REVOKE ALL ON audit.chain_verifications "
    "FROM PUBLIC, sos_app, sos_readonly, sos_definer, sos_platform",
    "GRANT SELECT, INSERT, UPDATE ON audit.chain_verifications TO sos_app",
    "GRANT SELECT ON audit.chain_verifications TO sos_readonly",
    "GRANT SELECT, DELETE ON audit.chain_verifications TO sos_purger",
    "CREATE POLICY offboarding_purge ON audit.chain_verifications AS RESTRICTIVE FOR ALL "
    "TO sos_purger USING (core.tenant_audit_purge_allowed())",
)
VERIFICATIONS_DOWN = ("DROP TABLE IF EXISTS audit.chain_verifications",)


def upgrade() -> None:
    for statement in (*SECURITY_HOLD_UP, *VERIFICATIONS_UP):
        op.execute(statement)
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)
    bind.execute(
        SET_DESCRIPTION, {"k": "support.ticket.create", "d": TICKET_CREATE_DESCRIPTION["new"]}
    )


def downgrade() -> None:
    for statement in (*VERIFICATIONS_DOWN, *SECURITY_HOLD_DOWN):
        op.execute(statement)
    bind = op.get_bind()
    bind.execute(
        SET_DESCRIPTION, {"k": "support.ticket.create", "d": TICKET_CREATE_DESCRIPTION["old"]}
    )
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
