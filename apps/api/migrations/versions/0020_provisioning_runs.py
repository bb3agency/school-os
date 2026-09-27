"""Resumable school provisioning: ``platform.provisioning_runs`` (FR-PLT-002, docs/16 §5.4).

Shared-tier provisioning spans the control plane's own transaction (tenant row via the definer
``core.provision_tenant``, deployment, billing account, subscription) and steps that run in the
new school's ``tenant_session`` (keys, system roles) or need their own platform transaction
(owner invite, school-chain ``tenant.provisioned``). They cannot share one transaction, so each
provisioning is a persisted state machine, one row per school:

``registered`` (first transaction committed) -> ``initialised`` (keys and roles) ->
``completed`` (owner invite and school-chain event, same transaction); any step may end in
``failed`` (``failed_step``, ``last_error`` code) and an operator resumes it.

- ``request_sha256`` fingerprints the provisioning request: a retry with the same code and the
  same request resumes; the same code with a different request is a conflict.
- ``lease_id`` / ``lease_expires_at``: one runner at a time. A crashed runner's lease expires and
  the next retry takes over; every state change is fenced on the runner's own lease.
- ``owner_*``: the owner invite parameters (display name, optional email, sign-in subject,
  language), held only until the invite exists (docs/16 §5.4 "stored only for the invite") so a
  failed provisioning can be resumed without the form. A CHECK clears them on completion.

Not tenant-owned (schema ``platform``, no RLS, no student data). Grants: ``sos_platform``
SELECT, INSERT, UPDATE (no DELETE); ``sos_app`` and ``sos_readonly`` nothing.

Backfill: every existing deployment gets a row. Dedicated schools, and shared schools whose
platform audit log has ``tenant.owner_invite_created``, are ``completed``; any other shared
school is ``registered`` with no fingerprint (resumable by submitting the same code, tier and
school name again, as before this revision).

Downgrade drops the table (the running code of 0019 does not use it).

Revision ID: 0020_provisioning_runs
Revises: 0019_export_access
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0020_provisioning_runs"
down_revision = "0019_export_access"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE platform.provisioning_runs (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL UNIQUE
                        REFERENCES platform.deployments (tenant_id) ON DELETE CASCADE,
  tenant_code         text NOT NULL UNIQUE CHECK (tenant_code ~ '^[a-z][a-z0-9-]{1,31}$'),
  tier                text NOT NULL CHECK (tier IN ('shared','dedicated')),
  request_sha256      text CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  state               text NOT NULL
                        CHECK (state IN ('registered','initialised','completed','failed')),
  failed_step         text CHECK (failed_step IN ('initialise','owner_invite')),
  last_error          text CHECK (last_error ~ '^[a-z][a-z0-9_]{0,63}$'),
  attempts            int NOT NULL DEFAULT 1 CHECK (attempts >= 0),
  lease_id            uuid,
  lease_expires_at    timestamptz,
  owner_subject       text CHECK (char_length(owner_subject) BETWEEN 1 AND 255),
  owner_display_name  text CHECK (char_length(owner_display_name) BETWEEN 1 AND 200),
  owner_email         public.citext CHECK (char_length(owner_email) <= 254),
  owner_language      text CHECK (owner_language IN ('en','te')),
  created_by          uuid REFERENCES platform.operators (id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  completed_at        timestamptz,
  CONSTRAINT provisioning_runs_failed_has_step
    CHECK ((state = 'failed') = (failed_step IS NOT NULL AND last_error IS NOT NULL)),
  CONSTRAINT provisioning_runs_lease_pair
    CHECK ((lease_id IS NULL) = (lease_expires_at IS NULL)),
  CONSTRAINT provisioning_runs_completed_clean
    CHECK (state <> 'completed' OR (completed_at IS NOT NULL AND lease_id IS NULL
      AND owner_subject IS NULL AND owner_display_name IS NULL AND owner_email IS NULL
      AND owner_language IS NULL)),
  CONSTRAINT provisioning_runs_dedicated_no_owner
    CHECK (tier = 'shared' OR owner_subject IS NULL)
);
COMMENT ON TABLE platform.provisioning_runs IS
  'Resumable provisioning state per school (FR-PLT-002). Owner invite parameters are held only '
  'until the invite exists. No student data.';
COMMENT ON COLUMN platform.provisioning_runs.owner_email IS
  'Owner invite parameter; cleared when the run completes (docs/16 5.4).';
CREATE INDEX provisioning_runs_open
  ON platform.provisioning_runs (updated_at) WHERE state <> 'completed';

CREATE TRIGGER provisioning_runs_set_updated_at BEFORE UPDATE ON platform.provisioning_runs
  FOR EACH ROW EXECUTE FUNCTION platform.tg_set_updated_at();

REVOKE ALL ON platform.provisioning_runs FROM PUBLIC;
REVOKE ALL ON platform.provisioning_runs FROM sos_platform, sos_app, sos_readonly;
GRANT SELECT, INSERT, UPDATE ON platform.provisioning_runs TO sos_platform;

-- Backfill: one row per existing deployment (UUIDv7-shaped ids are not needed here).
INSERT INTO platform.provisioning_runs
  (id, tenant_id, tenant_code, tier, request_sha256, state, attempts, created_at,
   completed_at)
SELECT gen_random_uuid(), d.tenant_id, d.tenant_code, d.mode, NULL,
       CASE WHEN d.mode = 'dedicated' OR EXISTS (
              SELECT 1 FROM platform.audit_events e
               WHERE e.subject_tenant_id = d.tenant_id
                 AND e.action = 'tenant.owner_invite_created')
            THEN 'completed' ELSE 'registered' END,
       1, COALESCE(d.created_at, now()),
       CASE WHEN d.mode = 'dedicated' OR EXISTS (
              SELECT 1 FROM platform.audit_events e
               WHERE e.subject_tenant_id = d.tenant_id
                 AND e.action = 'tenant.owner_invite_created')
            THEN COALESCE(d.created_at, now()) END
  FROM platform.deployments d;
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS platform.provisioning_runs;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    # Open runs lose their resume state; their schools stay "provisioning" and 0019's code
    # resumes them by code and school name.
    op.execute(DOWNGRADE_SQL)
