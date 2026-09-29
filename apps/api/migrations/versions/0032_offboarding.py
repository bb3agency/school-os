"""Offboarding: deleting a school's data, crypto-shredding, certificate (FR-PLT-005; ADR-0029).

School side (tenant schemas):

- ``sos_purger`` (created by ``infra/db/bootstrap.sql``; NOLOGIN, NOBYPASSRLS, granted to
  ``sos_app`` WITH INHERIT FALSE, SET TRUE) gets ``SELECT, DELETE`` on exactly the tables the
  offboarding purge deletes (:data:`PURGE_TABLES`) and on ``audit.events``/``audit.chain_heads``
  (deleted only after the audit retention). ``sos_app``'s own grants do not change.
- ``core.tenant_purge_allowed()``: true only when the tenant context is set, the transaction-local
  flag ``app.purge_tenant`` names it and the school's status is ``offboarding`` (set only by the
  two-person approval). ``core.tenant_audit_purge_allowed()``: the same with ``app.purge_audit``
  and status ``deleted``. Plain SQL, SECURITY INVOKER, ``search_path`` pinned (no new definer).
- Restrictive policy ``offboarding_purge ... TO sos_purger`` on every granted table (the
  standard ``tenant_isolation`` policy still applies to the role).
- ``audit.block_mutation()`` lets ``sos_purger`` delete a row of ``audit.events`` only when
  ``core.tenant_audit_purge_allowed()`` and the event is older than 365 days; UPDATE and TRUNCATE
  stay refused for everyone (invariant 7, ADR-0029 decision 2).
- ``students_delete_only_by_import_revert`` also allows the purge.
- ``attribute_values_change_request_fk`` becomes ``DEFERRABLE INITIALLY IMMEDIATE`` so change
  requests and the values that reference them can go in one transaction (normal writes unchanged).

Control plane (schema ``platform``, no student data):

- ``platform.offboarding_runs``: one row per school from approval to certificate (state, deadline,
  export confirmation, counts, lease, error codes). Backfilled for schools already offboarding.
- ``platform.deletion_certificates``: append-only; the certificate content (counts, dates,
  operator ids, retained and pending categories) and the stored PDF.

Requires ``sos_purger``: re-run ``infra/db/bootstrap.sql`` before this migration (docs/10 §9).

Revision ID: 0032_offboarding
Revises: 0031_admin
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op

revision = "0032_offboarding"
down_revision = "0031_admin"
branch_labels = None
depends_on = None

# Deleted by the purge as sos_purger (children before parents is the application's job; the
# grant list is just the set). Cascaded tables (attribute_values, student_profiles,
# promotion_items, import_rows, import_cell_edits, document_versions, document_acl, export_files)
# go with their parent through the owner's referential action and need no grant.
PURGE_TABLES = (
    "kb.document_chunks",
    "kb.verified_answers",
    "kb.queries",
    "kb.llm_calls",
    "kb.embedding_cache",
    "kb.upload_intents",
    "kb.documents",
    "sis.extraction_items",
    "sis.extraction_pages",
    "sis.extraction_batches",
    "sis.dq_findings",
    "sis.dq_runs",
    "sis.change_requests",
    "sis.promotion_runs",
    "sis.student_guardians",
    "sis.guardians",
    "sis.enrollments",
    "sis.students",
    "sis.attribute_definitions",
    "sis.import_batches",
    "sis.import_mapping_templates",
    "ops.exports",
    "ops.tenant_exports",
    "ops.retention_settings",
    "ops.notifications",
    "ops.break_glass_grants",
    "ops.job_runs",
    "ops.idempotency_keys",
    "ops.outbox",
    "core.membership_scopes",
    "core.membership_roles",
    "core.memberships",
    "core.role_permissions",
    "core.roles",
    "core.sections",
    "core.classes",
    "core.academic_years",
    "core.tenant_keys",
)
AUDIT_TABLES = ("audit.events", "audit.chain_heads")

ROLE_CHECK = r"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sos_purger') THEN
    RAISE EXCEPTION 'role sos_purger is missing: re-run infra/db/bootstrap.sql before 0032 (ADR-0029)';
  END IF;
END
$$;
"""

FUNCTIONS_SQL = r"""
CREATE FUNCTION core.tenant_purge_allowed() RETURNS boolean
  LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
  SELECT coalesce(
    core.current_tenant() IS NOT NULL
    AND current_setting('app.purge_tenant', true) = core.current_tenant()::text
    AND EXISTS (SELECT 1 FROM core.tenants AS t
                WHERE t.id = core.current_tenant() AND t.status = 'offboarding'),
    false)
$$;
COMMENT ON FUNCTION core.tenant_purge_allowed() IS
  'ADR-0029: the offboarding purge may delete the current school''s rows (status offboarding, '
  'transaction-local flag app.purge_tenant set to the school).';

CREATE FUNCTION core.tenant_audit_purge_allowed() RETURNS boolean
  LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
  SELECT coalesce(
    core.current_tenant() IS NOT NULL
    AND current_setting('app.purge_audit', true) = core.current_tenant()::text
    AND EXISTS (SELECT 1 FROM core.tenants AS t
                WHERE t.id = core.current_tenant() AND t.status = 'deleted'),
    false)
$$;
COMMENT ON FUNCTION core.tenant_audit_purge_allowed() IS
  'ADR-0029: a deleted school''s audit chain may be removed once its retention ended '
  '(transaction-local flag app.purge_audit set to the school).';

REVOKE ALL ON FUNCTION core.tenant_purge_allowed(), core.tenant_audit_purge_allowed() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.tenant_purge_allowed(), core.tenant_audit_purge_allowed()
  TO sos_app, sos_purger;
GRANT SELECT (id, status) ON core.tenants TO sos_purger;

CREATE OR REPLACE FUNCTION sis.tg_students_delete_only_by_import_revert() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $function$
BEGIN
  IF core.tenant_purge_allowed() THEN  -- offboarding purge (ADR-0029)
    RETURN OLD;
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM sis.import_rows r
    JOIN sis.import_batches b ON b.tenant_id = r.tenant_id AND b.id = r.batch_id
    WHERE r.tenant_id = OLD.tenant_id AND r.student_id = OLD.id AND r.created_student
      AND b.status = 'reverting'
  ) THEN
    RAISE EXCEPTION 'students are removed only by reverting the import that created them'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'students_delete_only_by_import_revert';
  END IF;
  RETURN OLD;
END
$function$;

CREATE OR REPLACE FUNCTION audit.block_mutation() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
  AS $$
  BEGIN
    -- ADR-0029: a deleted school's audit chain is removed after its retention, by the
    -- offboarding job only (role sos_purger, status deleted, flag set, event older than a year).
    IF TG_OP = 'DELETE' AND TG_LEVEL = 'ROW' AND TG_TABLE_SCHEMA = 'audit'
       AND current_user = 'sos_purger' THEN
      IF TG_TABLE_NAME LIKE 'events%' AND core.tenant_audit_purge_allowed()
         AND OLD.tenant_id = core.current_tenant()
         AND OLD.occurred_at < pg_catalog.now() - interval '365 days' THEN
        RETURN OLD;
      END IF;
    END IF;
    RAISE EXCEPTION 'audit.events is append-only'
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'Audit events can never be updated, deleted or truncated (FR-AUD-002).';
  END
  $$;

ALTER TABLE sis.attribute_values
  ALTER CONSTRAINT attribute_values_change_request_fk DEFERRABLE INITIALLY IMMEDIATE;
"""

PLATFORM_SQL = r"""
CREATE TABLE platform.offboarding_runs (
  id                     uuid PRIMARY KEY,
  tenant_id              uuid NOT NULL UNIQUE,
  tier                   text NOT NULL CHECK (tier IN ('shared', 'dedicated')),
  state                  text NOT NULL CHECK (state IN
                           ('awaiting_export', 'scheduled', 'deleting', 'keys_destroyed',
                            'completed')),
  approved_at            timestamptz NOT NULL,
  deadline_at            timestamptz NOT NULL,
  export_basis           text CHECK (export_basis IN ('school_confirmed', 'delivered_by_us')),
  export_reference       text CHECK (export_reference ~ '^[A-Za-z0-9][A-Za-z0-9 ._/#:-]{0,79}$'),
  export_confirmed_by    uuid REFERENCES platform.operators (id),
  export_confirmed_at    timestamptz,
  kms_deletion_reference text CHECK (kms_deletion_reference ~ '^[A-Za-z0-9][A-Za-z0-9 ._/#:-]{0,79}$'),
  host_teardown_reference text CHECK (host_teardown_reference ~ '^[A-Za-z0-9][A-Za-z0-9 ._/#:-]{0,79}$'),
  teardown_confirmed_by  uuid REFERENCES platform.operators (id),
  teardown_confirmed_at  timestamptz,
  inventory              jsonb CHECK (jsonb_typeof(inventory) = 'object'),
  objects_before         int CHECK (objects_before >= 0),
  deleted                jsonb CHECK (jsonb_typeof(deleted) = 'object'),
  objects_deleted        int CHECK (objects_deleted >= 0),
  remaining              jsonb CHECK (jsonb_typeof(remaining) = 'object'),
  profiles_cleared       int CHECK (profiles_cleared >= 0),
  keys_destroyed         int CHECK (keys_destroyed >= 0),
  deletion_started_at    timestamptz,
  data_deleted_at        timestamptz,
  keys_destroyed_at      timestamptz,
  completed_at           timestamptz,
  audit_delete_after     timestamptz,
  audit_deleted_at       timestamptz,
  audit_events_deleted   int CHECK (audit_events_deleted >= 0),
  failed_step            text CHECK (failed_step IN
                           ('inventory', 'purge', 'verify', 'keys', 'certificate', 'audit')),
  last_error             text CHECK (last_error ~ '^[a-z][a-z0-9_]{0,63}$'),
  attempts               int NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  lease_id               uuid,
  lease_expires_at       timestamptz,
  due_soon_alerted_at    timestamptz,
  overdue_alerted_at     timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT offboarding_runs_export_confirmed CHECK (
    (export_basis IS NULL) = (export_confirmed_at IS NULL)
    AND (export_basis IS NULL) = (export_confirmed_by IS NULL)
    AND (export_basis IS NULL) = (export_reference IS NULL)),
  CONSTRAINT offboarding_runs_export_before_deletion CHECK (
    state = 'awaiting_export' OR export_basis IS NOT NULL),
  CONSTRAINT offboarding_runs_teardown_dedicated CHECK (
    teardown_confirmed_at IS NULL OR tier = 'dedicated'),
  CONSTRAINT offboarding_runs_teardown_pair CHECK (
    (teardown_confirmed_at IS NULL) = (teardown_confirmed_by IS NULL)
    AND (teardown_confirmed_at IS NULL) = (kms_deletion_reference IS NULL)
    AND (teardown_confirmed_at IS NULL) = (host_teardown_reference IS NULL)),
  CONSTRAINT offboarding_runs_completed CHECK ((state = 'completed') = (completed_at IS NOT NULL)),
  CONSTRAINT offboarding_runs_lease_pair CHECK ((lease_id IS NULL) = (lease_expires_at IS NULL))
);
COMMENT ON TABLE platform.offboarding_runs IS
  'Offboarding from approval to certificate, one row per school (docs/16 5.5, ADR-0029). '
  'Codes, counts and IDs only; no student data.';
CREATE INDEX offboarding_runs_open_idx ON platform.offboarding_runs (state)
  WHERE state <> 'completed';

REVOKE ALL ON platform.offboarding_runs FROM PUBLIC, sos_app, sos_readonly;
REVOKE DELETE, TRUNCATE ON platform.offboarding_runs FROM sos_platform;
GRANT SELECT, INSERT, UPDATE ON platform.offboarding_runs TO sos_platform;

CREATE TABLE platform.deletion_certificates (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL UNIQUE REFERENCES platform.offboarding_runs (tenant_id),
  template_version  text NOT NULL CHECK (template_version ~ '^v[0-9]{1,3}$'),
  content           jsonb NOT NULL CHECK (jsonb_typeof(content) = 'object'),
  content_sha256    text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  object_key        text NOT NULL UNIQUE
                      CHECK (object_key ~ '^[a-z0-9][a-z0-9/_.-]{0,254}$'
                             AND object_key NOT LIKE 't/%'
                             AND position('..' in object_key) = 0),
  pdf_sha256        text NOT NULL CHECK (pdf_sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes        int NOT NULL CHECK (size_bytes > 0),
  issued_at         timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE platform.deletion_certificates IS
  'Certificates of deletion (FR-PLT-005, ADR-0029). Append-only. No student or staff data.';

CREATE FUNCTION platform.tg_deletion_certificates_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  RAISE EXCEPTION 'certificates of deletion are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'deletion_certificates_append_only';
END;
$$;
CREATE TRIGGER deletion_certificates_append_only
  BEFORE UPDATE OR DELETE ON platform.deletion_certificates
  FOR EACH ROW EXECUTE FUNCTION platform.tg_deletion_certificates_append_only();
CREATE TRIGGER deletion_certificates_no_truncate
  BEFORE TRUNCATE ON platform.deletion_certificates
  FOR EACH STATEMENT EXECUTE FUNCTION platform.tg_deletion_certificates_append_only();

REVOKE ALL ON platform.deletion_certificates FROM PUBLIC;
REVOKE ALL ON platform.deletion_certificates FROM sos_platform, sos_app, sos_readonly;
GRANT SELECT, INSERT ON platform.deletion_certificates TO sos_platform;

-- Schools already approved for offboarding before this migration get their run (ADR-0029).
INSERT INTO platform.offboarding_runs (id, tenant_id, tier, state, approved_at, deadline_at)
SELECT gen_random_uuid(), d.tenant_id, d.mode, 'awaiting_export',
       coalesce(d.offboard_approved_at, d.updated_at),
       coalesce(d.offboard_approved_at, d.updated_at) + interval '30 days'
FROM platform.deployments AS d
WHERE d.tenant_status = 'offboarding';
"""

DOWNGRADE_PLATFORM_SQL = """
DROP TABLE IF EXISTS platform.deletion_certificates;
DROP FUNCTION IF EXISTS platform.tg_deletion_certificates_append_only();
DROP TABLE IF EXISTS platform.offboarding_runs;
"""

DOWNGRADE_FUNCTIONS_SQL = r"""
ALTER TABLE sis.attribute_values
  ALTER CONSTRAINT attribute_values_change_request_fk NOT DEFERRABLE;

CREATE OR REPLACE FUNCTION audit.block_mutation() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
  AS $$
  BEGIN
    RAISE EXCEPTION 'audit.events is append-only'
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'Audit events can never be updated, deleted or truncated (FR-AUD-002).';
  END
  $$;

CREATE OR REPLACE FUNCTION sis.tg_students_delete_only_by_import_revert() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $function$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM sis.import_rows r
    JOIN sis.import_batches b ON b.tenant_id = r.tenant_id AND b.id = r.batch_id
    WHERE r.tenant_id = OLD.tenant_id AND r.student_id = OLD.id AND r.created_student
      AND b.status = 'reverting'
  ) THEN
    RAISE EXCEPTION 'students are removed only by reverting the import that created them'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'students_delete_only_by_import_revert';
  END IF;
  RETURN OLD;
END
$function$;

REVOKE SELECT (id, status) ON core.tenants FROM sos_purger;
DROP FUNCTION IF EXISTS core.tenant_audit_purge_allowed();
DROP FUNCTION IF EXISTS core.tenant_purge_allowed();
"""


def _policy_sql(table: str, check: str) -> str:
    return (
        f"GRANT SELECT, DELETE ON {table} TO sos_purger;\n"
        f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
        f"USING ({check});\n"
    )


def upgrade() -> None:
    op.execute(ROLE_CHECK)
    op.execute(FUNCTIONS_SQL)
    for table in PURGE_TABLES:
        op.execute(_policy_sql(table, "core.tenant_purge_allowed()"))
    for table in AUDIT_TABLES:
        op.execute(_policy_sql(table, "core.tenant_audit_purge_allowed()"))
    op.execute(PLATFORM_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_PLATFORM_SQL)
    for table in (*PURGE_TABLES, *AUDIT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS offboarding_purge ON {table}")
        op.execute(f"REVOKE ALL ON {table} FROM sos_purger")
    op.execute(DOWNGRADE_FUNCTIONS_SQL)
