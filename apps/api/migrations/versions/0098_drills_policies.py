"""FC-09 safety drills and periodic checks, FC-10 policy library with staff acknowledgement
(FR-COMP-046..FR-COMP-070; docs/05 §16.10).

Three tenant tables in ``ops`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs, the restrictive ``offboarding_purge`` policy with
SELECT and DELETE for ``sos_purger``; ADR-0013, ADR-0029):

- ``ops.compliance_checks`` (C1, no personal data): one row per drill or periodic check the
  school logged. ``kind`` is a key of ``app/compliance/checks.yaml`` (``other`` carries the
  school's ``label``). Counts and a short note only (``participants_count``,
  ``duration_minutes``, ``observations`` 1-500): never student names. ``led_by_membership_id``
  is the staff member who led it (composite FK to ``core.memberships``), ``document_id`` the
  report (composite FK to ``kb.documents``, ``ON DELETE SET NULL``). ``status`` ``recorded`` |
  ``removed`` (logged by mistake). ``last_reminded_lead``/``_on``: the reminder job's
  idempotency on the latest entry of a kind. ``sos_app`` never DELETEs.
- ``ops.compliance_policies`` (C1, no personal data): a school policy (a document of purpose
  ``policy``), its next review date, the role keys whose members must acknowledge it, ``active``
  or ``retired``.
- ``ops.compliance_policy_acks`` (C1, staff personal data: who read which version, when):
  **append-only** (trigger ``compliance_policy_acks_append_only``; DELETE only by the
  offboarding purge). ``document_version_id`` is the exact ``kb.document_versions`` id the
  member read, kept as recorded without a foreign key (the evidence outlives a later version
  clean-up). One acknowledgement per member, policy and version.

No new permissions: ``compliance.read`` / ``compliance.manage`` (0096) cover the office side and
the implicit ``session.authenticated`` the member's own acknowledgement.

Expand-only (invariant 12). Downgrade drops the tables (lossy: the logs and acknowledgements).

Revision ID: 0098_drills_policies
Revises: 0097_ew_followups
Create Date: 2026-11-12
"""

from __future__ import annotations

from alembic import op

revision = "0098_drills_policies"
down_revision = "0097_ew_followups"
branch_labels = None
depends_on = None

# Children before parents (the offboarding purge deletes in this order).
TENANT_TABLES = (
    "ops.compliance_policy_acks",
    "ops.compliance_policies",
    "ops.compliance_checks",
)

UPGRADE_SQL = r"""
CREATE TABLE ops.compliance_checks (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  kind                  text NOT NULL,
  label                 text,
  done_on               date NOT NULL,
  led_by_membership_id  uuid,
  participants_count    int,
  duration_minutes      int,
  result                text NOT NULL DEFAULT 'ok',
  observations          text,
  document_id           uuid,
  status                text NOT NULL DEFAULT 'recorded',
  removed_by            uuid,
  removed_at            timestamptz,
  last_reminded_lead    int,
  last_reminded_on      date,
  recorded_by           uuid NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT compliance_checks_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT compliance_checks_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT compliance_checks_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id),
  CONSTRAINT compliance_checks_led_by_fk FOREIGN KEY (tenant_id, led_by_membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT compliance_checks_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT compliance_checks_removed_by_fk FOREIGN KEY (removed_by) REFERENCES core.users (id),
  CONSTRAINT compliance_checks_kind_format CHECK (kind ~ '^[a-z][a-z0-9_]{1,62}$'),
  CONSTRAINT compliance_checks_label CHECK (
    (kind = 'other') = (label IS NOT NULL)
    AND (label IS NULL OR char_length(label) BETWEEN 3 AND 120)),
  CONSTRAINT compliance_checks_participants
    CHECK (participants_count IS NULL OR participants_count BETWEEN 0 AND 20000),
  CONSTRAINT compliance_checks_duration
    CHECK (duration_minutes IS NULL OR duration_minutes BETWEEN 0 AND 1440),
  CONSTRAINT compliance_checks_result CHECK (result IN ('ok','action_needed')),
  CONSTRAINT compliance_checks_observations_len
    CHECK (observations IS NULL OR char_length(observations) BETWEEN 1 AND 500),
  CONSTRAINT compliance_checks_status CHECK (status IN ('recorded','removed')),
  CONSTRAINT compliance_checks_removed
    CHECK ((status = 'removed') = (removed_at IS NOT NULL AND removed_by IS NOT NULL)),
  CONSTRAINT compliance_checks_reminded
    CHECK ((last_reminded_lead IS NULL) = (last_reminded_on IS NULL)
      AND (last_reminded_lead IS NULL OR last_reminded_lead BETWEEN -1 AND 365))
);
CREATE INDEX compliance_checks_kind_idx
  ON ops.compliance_checks (tenant_id, kind, done_on DESC) WHERE status = 'recorded';
CREATE TRIGGER compliance_checks_set_updated_at BEFORE UPDATE ON ops.compliance_checks
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.compliance_policies (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  title                 text NOT NULL,
  document_id           uuid,
  ack_roles             text[] NOT NULL DEFAULT '{}',
  review_due            date NOT NULL,
  last_reviewed_on      date,
  status                text NOT NULL DEFAULT 'active',
  retired_by            uuid,
  retired_at            timestamptz,
  ack_requested_at      timestamptz,
  last_reminded_lead    int,
  last_reminded_on      date,
  created_by            uuid NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT compliance_policies_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT compliance_policies_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT compliance_policies_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id),
  CONSTRAINT compliance_policies_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT compliance_policies_retired_by_fk FOREIGN KEY (retired_by) REFERENCES core.users (id),
  CONSTRAINT compliance_policies_title_len CHECK (char_length(title) BETWEEN 3 AND 120),
  CONSTRAINT compliance_policies_roles_len CHECK (cardinality(ack_roles) <= 50),
  CONSTRAINT compliance_policies_status CHECK (status IN ('active','retired')),
  CONSTRAINT compliance_policies_retired
    CHECK ((status = 'retired') = (retired_at IS NOT NULL AND retired_by IS NOT NULL)),
  CONSTRAINT compliance_policies_reminded
    CHECK ((last_reminded_lead IS NULL) = (last_reminded_on IS NULL)
      AND (last_reminded_lead IS NULL OR last_reminded_lead BETWEEN -1 AND 365))
);
CREATE INDEX compliance_policies_review_idx
  ON ops.compliance_policies (tenant_id, review_due) WHERE status = 'active';
CREATE TRIGGER compliance_policies_set_updated_at BEFORE UPDATE ON ops.compliance_policies
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.compliance_policy_acks (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  policy_id             uuid NOT NULL,
  document_version_id   uuid NOT NULL,
  membership_id         uuid NOT NULL,
  user_id               uuid NOT NULL,
  acknowledged_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT compliance_policy_acks_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT compliance_policy_acks_once
    UNIQUE (tenant_id, policy_id, document_version_id, membership_id),
  CONSTRAINT compliance_policy_acks_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT compliance_policy_acks_policy_fk FOREIGN KEY (tenant_id, policy_id)
    REFERENCES ops.compliance_policies (tenant_id, id),
  CONSTRAINT compliance_policy_acks_membership_fk FOREIGN KEY (tenant_id, membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT compliance_policy_acks_user_fk FOREIGN KEY (user_id) REFERENCES core.users (id)
);
CREATE INDEX compliance_policy_acks_member_idx
  ON ops.compliance_policy_acks (tenant_id, membership_id);

-- Append-only: UPDATE always refused; DELETE only by the offboarding purge.
CREATE FUNCTION ops.tg_compliance_policy_acks_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'policy acknowledgements are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'compliance_policy_acks_append_only';
END;
$$;
CREATE TRIGGER compliance_policy_acks_append_only BEFORE UPDATE OR DELETE
  ON ops.compliance_policy_acks
  FOR EACH ROW EXECUTE FUNCTION ops.tg_compliance_policy_acks_append_only();
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ops.compliance_checks FROM sos_app;
GRANT UPDATE (status, removed_by, removed_at, last_reminded_lead, last_reminded_on, updated_at,
  version) ON ops.compliance_checks TO sos_app;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ops.compliance_policies FROM sos_app;
GRANT UPDATE (title, document_id, ack_roles, review_due, last_reviewed_on, status, retired_by,
  retired_at, ack_requested_at, last_reminded_lead, last_reminded_on, updated_at, version)
  ON ops.compliance_policies TO sos_app;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ops.compliance_policy_acks FROM sos_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    for table in TENANT_TABLES:
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
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS ops.tg_compliance_policy_acks_append_only()")
