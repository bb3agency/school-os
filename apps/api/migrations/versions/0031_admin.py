"""School admin console data: full data exports and retention settings (docs/05 §7.4;
US-1201, FR-ADM-001, FR-ADM-002, BR-08).

Tables (tenant-owned in schema ``ops``: ``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS
ENABLE + FORCE with ``tenant_isolation``, composite FKs):

- ``ops.tenant_exports``: one full export of the school's data requested by a holder of
  ``tenant.export_all`` (the owner, with a fresh MFA sign-in). ``include_sensitive`` records
  whether restricted (C3) values were explicitly included. Status ``queued -> running -> ready ->
  expired`` (or ``failed``); at most one export per school is queued or running
  (``tenant_exports_one_live``). The archive is one object ``t/<tenant>/tenant-export/<id>.zip``
  (private bucket, SSE-KMS); the row keeps its key, size, SHA-256 and row counts per table (IDs,
  codes and counts only). ``expires_at`` is 24 hours after it was ready (FR-ADM-001); the purge
  job then deletes the archive and marks the row ``expired``. Rows stay as the school's record of
  who exported everything and when: the app updates only the workflow columns and never deletes.
- ``ops.retention_settings``: at most one row per school with its retention periods per data
  category (``rules``: category -> days, whole numbers 1..3650). Bounds per category live in
  ``app/admin/retention.yaml`` (versioned configuration) and are checked by the service; the
  database only checks the shape. No row = the defaults.

Expand-only (invariant 12). Downgrade drops both tables (lossy: exports are working data kept a
day, and retention settings fall back to the defaults; stored archives are removed by the bucket
rule ``tenant-export-2d``).

Revision ID: 0031_admin
Revises: 0030_import_cell_edits
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op

revision = "0031_admin"
down_revision = "0030_import_cell_edits"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE ops.tenant_exports (
  id                       uuid PRIMARY KEY,
  tenant_id                uuid NOT NULL,
  include_sensitive        boolean NOT NULL DEFAULT false,
  status                   text NOT NULL DEFAULT 'queued',
  error_code               text,
  job_id                   uuid,
  requested_by             uuid NOT NULL,
  requested_by_membership  uuid NOT NULL,
  object_key               text,
  size_bytes               bigint,
  sha256                   bytea,
  counts                   jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at               timestamptz NOT NULL DEFAULT now(),
  started_at               timestamptz,
  finished_at              timestamptz,
  expires_at               timestamptz,
  files_deleted_at         timestamptz,
  updated_at               timestamptz NOT NULL DEFAULT now(),
  version                  int NOT NULL DEFAULT 1,
  CONSTRAINT tenant_exports_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tenant_exports_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tenant_exports_requested_by_fk FOREIGN KEY (requested_by)
    REFERENCES core.users (id),
  CONSTRAINT tenant_exports_membership_fk FOREIGN KEY (tenant_id, requested_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tenant_exports_job_fk FOREIGN KEY (tenant_id, job_id)
    REFERENCES ops.job_runs (tenant_id, id) ON DELETE SET NULL (job_id),
  CONSTRAINT tenant_exports_status_check
    CHECK (status IN ('queued','running','ready','failed','expired')),
  CONSTRAINT tenant_exports_error_code_shape CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
  CONSTRAINT tenant_exports_error_when_failed CHECK ((status = 'failed') = (error_code IS NOT NULL)),
  -- A ready (or later expired) export names its archive: key, size and checksum.
  CONSTRAINT tenant_exports_ready_fields CHECK (
    status NOT IN ('ready','expired')
    OR (finished_at IS NOT NULL AND expires_at IS NOT NULL AND object_key IS NOT NULL
        AND size_bytes IS NOT NULL AND sha256 IS NOT NULL)),
  -- docs/04 §8.2: t/<tenant_id>/tenant-export/<export_id>.zip, inside the row's own school.
  CONSTRAINT tenant_exports_key_in_tenant CHECK (
    object_key IS NULL
    OR object_key = 't/' || tenant_id::text || '/tenant-export/' || id::text || '.zip'),
  CONSTRAINT tenant_exports_size_positive CHECK (size_bytes IS NULL OR size_bytes > 0),
  CONSTRAINT tenant_exports_sha256_length CHECK (sha256 IS NULL OR octet_length(sha256) = 32),
  CONSTRAINT tenant_exports_counts_object CHECK (jsonb_typeof(counts) = 'object'),
  -- The archive is purged from ready exports (-> expired) and from failed ones (leftovers).
  CONSTRAINT tenant_exports_files_deleted CHECK (
    (status <> 'expired' OR files_deleted_at IS NOT NULL)
    AND (files_deleted_at IS NULL OR status IN ('expired','failed'))),
  CONSTRAINT tenant_exports_finished_after_start
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
  CONSTRAINT tenant_exports_version_positive CHECK (version >= 1)
);
CREATE INDEX tenant_exports_created_idx
  ON ops.tenant_exports (tenant_id, created_at DESC, id DESC);
CREATE INDEX tenant_exports_expiry_idx ON ops.tenant_exports (tenant_id, expires_at)
  WHERE files_deleted_at IS NULL AND status IN ('ready','failed');
-- One full export at a time per school (a double click, or a second owner, waits for it).
CREATE UNIQUE INDEX tenant_exports_one_live ON ops.tenant_exports (tenant_id)
  WHERE status IN ('queued','running');

CREATE TRIGGER tenant_exports_set_updated_at BEFORE UPDATE ON ops.tenant_exports
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.retention_settings (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  rules       jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_by  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  version     int NOT NULL DEFAULT 1,
  CONSTRAINT retention_settings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT retention_settings_one_per_school UNIQUE (tenant_id),
  CONSTRAINT retention_settings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT retention_settings_updated_by_fk FOREIGN KEY (updated_by) REFERENCES core.users (id),
  CONSTRAINT retention_settings_rules_object CHECK (jsonb_typeof(rules) = 'object'),
  -- Category keys are identifiers; every value is a whole number of days, 1..3650. The bounds
  -- per category (app/admin/retention.yaml) are narrower and checked by the service.
  CONSTRAINT retention_settings_rules_shape CHECK (
    NOT jsonb_path_exists(
      rules,
      '$.keyvalue() ? (!(@.key like_regex "^[a-z][a-z0-9_]{0,62}$")
                       || @.value.type() != "number"
                       || @.value < 1 || @.value > 3650
                       || @.value.floor() != @.value)')),
  CONSTRAINT retention_settings_version_positive CHECK (version >= 1)
);

CREATE TRIGGER retention_settings_set_updated_at BEFORE UPDATE ON ops.retention_settings
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

TENANT_TABLES = ("ops.tenant_exports", "ops.retention_settings")

GRANTS_SQL = r"""
-- Default privileges gave sos_app full DML. Full export records are kept (who exported the whole
-- school, and when): no DELETE; only the workflow columns change.
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tenant_exports FROM sos_app;
GRANT UPDATE (status, error_code, job_id, object_key, size_bytes, sha256, counts, started_at,
              finished_at, expires_at, files_deleted_at, updated_at, version)
  ON ops.tenant_exports TO sos_app;
-- Retention settings are changed, never removed (a school goes back to a default by setting it).
REVOKE DELETE, UPDATE, TRUNCATE ON ops.retention_settings FROM sos_app;
GRANT UPDATE (rules, updated_by, updated_at, version) ON ops.retention_settings TO sos_app;
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
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.retention_settings")
    op.execute("DROP TABLE IF EXISTS ops.tenant_exports")
