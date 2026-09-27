"""Exports: board/portal pre-check reports and student lists (docs/05 §7.3; US-501 AC4,
US-901, FR-EXP-001..004, SEC-017).

Tables (tenant-owned in schema ``ops``: ``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS
ENABLE + FORCE with ``tenant_isolation``, composite FKs):

- ``ops.exports``: one requested export. ``student_ids`` is the list of students the requester
  could reach when they asked (frozen, IDs only; FR-EXP-003 "which students"); ``kind``,
  ``profile_key``/``profile_version`` (the DQ profile) and ``layout_version`` (the exports
  layout config) say what was produced; ``include_sensitive`` records whether restricted (C3)
  values were explicitly included. Status ``queued -> running -> ready -> expired`` (or
  ``failed``). ``expires_at`` is 7 days after completion (docs/05 §13); the daily purge deletes
  the files and marks the row ``expired``. Rows stay as the school's record of who exported
  what: the app may update only the workflow columns and may never delete one.
- ``ops.export_files``: one stored file per format (``xlsx``, ``pdf``, ``csv``) under
  ``t/<tenant>/exports/<export_id>/`` in the private bucket (SSE-KMS). Written once by the worker
  in the transaction that marks the export ready; never updated or deleted by the app.

Files contain personal data; rows contain IDs, codes and counts only.

Downgrade drops both tables (lossy: exports are working data kept 7 days; stored objects are
removed by the bucket lifecycle rule for ``exports/``).

Revision ID: 0017_exports
Revises: 0014_change_requests (relinked by the lead after 0015/0016)
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0017_exports"
down_revision = "0014_change_requests"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE ops.exports (
  id                       uuid PRIMARY KEY,
  tenant_id                uuid NOT NULL,
  kind                     text NOT NULL,
  profile_key              text,
  profile_version          int,
  layout_version           int NOT NULL,
  formats                  text[] NOT NULL,
  language                 text NOT NULL DEFAULT 'en',
  scope                    jsonb NOT NULL DEFAULT '{}'::jsonb,
  columns                  text[],
  include_sensitive        boolean NOT NULL DEFAULT false,
  student_ids              uuid[] NOT NULL,
  student_count            int NOT NULL,
  status                   text NOT NULL DEFAULT 'queued',
  error_code               text,
  job_id                   uuid,
  requested_by             uuid NOT NULL,
  requested_by_membership  uuid NOT NULL,
  created_at               timestamptz NOT NULL DEFAULT now(),
  started_at               timestamptz,
  finished_at              timestamptz,
  expires_at               timestamptz,
  files_deleted_at         timestamptz,
  updated_at               timestamptz NOT NULL DEFAULT now(),
  version                  int NOT NULL DEFAULT 1,
  CONSTRAINT exports_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT exports_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT exports_requested_by_fk FOREIGN KEY (requested_by) REFERENCES core.users (id),
  CONSTRAINT exports_membership_fk FOREIGN KEY (tenant_id, requested_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT exports_job_fk FOREIGN KEY (tenant_id, job_id)
    REFERENCES ops.job_runs (tenant_id, id) ON DELETE SET NULL (job_id),
  CONSTRAINT exports_kind_check CHECK (kind IN ('board_precheck','portal_precheck','student_list')),
  -- Pre-checks name a DQ profile; student lists name their columns.
  CONSTRAINT exports_kind_shape CHECK (
    (kind = 'student_list' AND profile_key IS NULL AND profile_version IS NULL
       AND columns IS NOT NULL AND cardinality(columns) BETWEEN 1 AND 60)
    OR (kind <> 'student_list' AND profile_key IS NOT NULL AND profile_version IS NOT NULL
       AND columns IS NULL)),
  CONSTRAINT exports_profile_key_shape CHECK (profile_key ~ '^[a-z0-9][a-z0-9-]{0,63}$'),
  CONSTRAINT exports_versions_positive CHECK (
    layout_version >= 1 AND (profile_version IS NULL OR profile_version >= 1)),
  CONSTRAINT exports_formats_check CHECK (
    cardinality(formats) BETWEEN 1 AND 3 AND formats <@ ARRAY['xlsx','pdf','csv']::text[]),
  CONSTRAINT exports_language_check CHECK (language IN ('en','te')),
  CONSTRAINT exports_scope_object CHECK (jsonb_typeof(scope) = 'object'),
  CONSTRAINT exports_students_count CHECK (
    student_count = cardinality(student_ids) AND student_count BETWEEN 1 AND 5000),
  CONSTRAINT exports_status_check
    CHECK (status IN ('queued','running','ready','failed','expired')),
  CONSTRAINT exports_error_code_shape CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
  CONSTRAINT exports_error_when_failed CHECK ((status = 'failed') = (error_code IS NOT NULL)),
  CONSTRAINT exports_ready_fields CHECK (
    status NOT IN ('ready','expired') OR (finished_at IS NOT NULL AND expires_at IS NOT NULL)),
  -- Files are purged from ready exports (-> expired) and from failed ones (orphans).
  CONSTRAINT exports_files_deleted CHECK (
    (status <> 'expired' OR files_deleted_at IS NOT NULL)
    AND (files_deleted_at IS NULL OR status IN ('expired','failed'))),
  CONSTRAINT exports_finished_after_start
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
  CONSTRAINT exports_version_positive CHECK (version >= 1)
);
CREATE INDEX exports_requester_idx
  ON ops.exports (tenant_id, requested_by_membership, created_at DESC, id DESC);
CREATE INDEX exports_expiry_idx ON ops.exports (tenant_id, expires_at)
  WHERE files_deleted_at IS NULL AND status IN ('ready','failed');

CREATE TABLE ops.export_files (
  id            uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL,
  export_id     uuid NOT NULL,
  format        text NOT NULL,
  object_key    text NOT NULL,
  content_type  text NOT NULL,
  size_bytes    bigint NOT NULL,
  sha256        bytea NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT export_files_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT export_files_one_per_format UNIQUE (tenant_id, export_id, format),
  CONSTRAINT export_files_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT export_files_export_fk FOREIGN KEY (tenant_id, export_id)
    REFERENCES ops.exports (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT export_files_format_check CHECK (format IN ('xlsx','pdf','csv')),
  -- docs/04 §8.2: t/<tenant_id>/exports/<export_id>/<file>, inside the row's own school.
  CONSTRAINT export_files_key_in_tenant CHECK (
    object_key LIKE 't/' || tenant_id::text || '/exports/' || export_id::text || '/%'
    AND object_key ~ '^t/[0-9a-f-]{36}/exports/[0-9a-f-]{36}/[a-z0-9][a-z0-9._-]{0,79}$'),
  CONSTRAINT export_files_size_positive CHECK (size_bytes > 0),
  CONSTRAINT export_files_sha256_length CHECK (octet_length(sha256) = 32)
);

CREATE TRIGGER exports_set_updated_at BEFORE UPDATE ON ops.exports
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- What was exported, for whom and from which students never changes after the request.
CREATE FUNCTION ops.tg_exports_frozen() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (NEW.id, NEW.tenant_id, NEW.kind, NEW.profile_key, NEW.profile_version,
      NEW.layout_version, NEW.formats, NEW.language, NEW.scope, NEW.columns,
      NEW.include_sensitive, NEW.student_ids, NEW.student_count, NEW.requested_by,
      NEW.requested_by_membership, NEW.created_at)
     IS DISTINCT FROM
     (OLD.id, OLD.tenant_id, OLD.kind, OLD.profile_key, OLD.profile_version,
      OLD.layout_version, OLD.formats, OLD.language, OLD.scope, OLD.columns,
      OLD.include_sensitive, OLD.student_ids, OLD.student_count, OLD.requested_by,
      OLD.requested_by_membership, OLD.created_at) THEN
    RAISE EXCEPTION 'an export keeps what was requested; request a new one instead'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'exports_frozen';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION ops.tg_exports_frozen() FROM PUBLIC;
CREATE TRIGGER exports_frozen BEFORE UPDATE ON ops.exports
  FOR EACH ROW EXECUTE FUNCTION ops.tg_exports_frozen();
"""

TENANT_TABLES = ("ops.exports", "ops.export_files")

GRANTS_SQL = r"""
-- Default privileges gave sos_app full DML. Export records are kept (FR-EXP-003): no DELETE;
-- only the workflow columns change. Files rows are written once.
REVOKE DELETE, UPDATE, TRUNCATE ON ops.exports FROM sos_app;
GRANT UPDATE (status, error_code, job_id, started_at, finished_at, expires_at, files_deleted_at,
              updated_at, version)
  ON ops.exports TO sos_app;
REVOKE DELETE, UPDATE, TRUNCATE ON ops.export_files FROM sos_app;
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
    op.execute("DROP TABLE IF EXISTS ops.export_files")
    op.execute("DROP TABLE IF EXISTS ops.exports")
    op.execute("DROP FUNCTION IF EXISTS ops.tg_exports_frozen()")
