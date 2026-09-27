"""Spreadsheet onboarding: import batches, rows and mapping templates (docs/05 §5.2; US-401,
FR-IMP-001..007).

Tables (every one tenant-owned: ``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs):

- ``sis.import_batches``: one uploaded file (``kb.documents``, purpose ``import_file``) imported
  from one source. Status machine ``uploaded -> parsing -> parsed -> validating -> validated ->
  committing -> committed -> reverting -> reverted`` (+ ``failed``). ``revert_deadline`` is the
  24-hour revert window (FR-IMP-005). The raw file reference is set NULL when the retention job
  deletes the document (FR-IMP-007).
- ``sis.import_rows``: one data row per spreadsheet row with row-level ``errors``/``warnings``.
  ``parsed`` holds only the NON-C3 mapped values (normalised) and the list of C3 attribute keys
  present; C3 cells are never stored here (they are re-read from the SSE-KMS raw file when the
  batch is committed, then encrypted by the student service).
- ``sis.import_mapping_templates``: saved column mappings per school, matched by a header
  signature and reused automatically (FR-IMP-002).

Changes to the student-record tables of 0008 (for FR-IMP-004/005):

- ``sis.attribute_values.import_batch_id`` gets its composite FK to ``sis.import_batches``.
- Reverting a batch removes the students it created. ``sos_app`` still has no DELETE on
  ``sis.attribute_values`` (FR-STU-005); instead ``attribute_values_student_fk`` becomes
  ``ON DELETE CASCADE`` (the referential action runs as the table owner) and a trigger on
  ``sis.students`` allows deleting a student ONLY while an import batch that created it is being
  reverted (status ``reverting`` in the same transaction). Everything else keeps failing, so
  value history can still never be deleted by the app.

Requirements: FR-IMP-001..007, SEC-001, SEC-017 (formulas are never evaluated; data only).

Revision ID: 0012_imports
Revises: 0011_breakglass
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0012_imports"
down_revision = "0011_breakglass"
branch_labels = None
depends_on = None

SOURCES = (
    "'admission_register','aadhaar_as_printed','udise_plus','board_registration',"
    "'birth_certificate','parent_form','tc_incoming','manual_entry'"
)
BATCH_STATUSES = (
    "'uploaded','parsing','parsed','validating','validated','committing','committed',"
    "'reverting','reverted','failed'"
)
ROW_STATUSES = "'valid','error','committed','skipped','reverted'"
CODE_RE = "^[a-z][a-z0-9_]{0,63}$"

TABLES_SQL = rf"""
CREATE TABLE sis.import_mapping_templates (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL REFERENCES core.tenants (id),
  name              text NOT NULL,
  source            text NOT NULL,
  header_signature  text NOT NULL,
  headers           jsonb NOT NULL,
  mapping           jsonb NOT NULL,
  created_by        uuid NOT NULL REFERENCES core.users (id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  last_used_at      timestamptz,
  version           int NOT NULL DEFAULT 1,
  CONSTRAINT import_mapping_templates_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT import_mapping_templates_name_key UNIQUE (tenant_id, name),
  CONSTRAINT import_mapping_templates_name_length CHECK (char_length(name) BETWEEN 1 AND 100),
  CONSTRAINT import_mapping_templates_source_check CHECK (source IN ({SOURCES})),
  CONSTRAINT import_mapping_templates_signature_shape
    CHECK (header_signature ~ '^[0-9a-f]{{64}}$'),
  CONSTRAINT import_mapping_templates_headers_array CHECK (jsonb_typeof(headers) = 'array'),
  CONSTRAINT import_mapping_templates_mapping_object CHECK (jsonb_typeof(mapping) = 'object'),
  CONSTRAINT import_mapping_templates_version_positive CHECK (version >= 1)
);
CREATE INDEX import_mapping_templates_signature_idx
  ON sis.import_mapping_templates (tenant_id, header_signature);

CREATE TABLE sis.import_batches (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL REFERENCES core.tenants (id),
  kind                 text NOT NULL DEFAULT 'spreadsheet',
  source               text NOT NULL,
  status               text NOT NULL DEFAULT 'uploaded',
  document_id          uuid,
  file_kind            text,
  header_row           int,
  columns              jsonb NOT NULL DEFAULT '[]'::jsonb,
  mapping              jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  mapping_template_id  uuid,
  stats                jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  row_count            int NOT NULL DEFAULT 0,
  error_count          int NOT NULL DEFAULT 0,
  error_code           text,
  job_id               uuid,
  created_by           uuid NOT NULL REFERENCES core.users (id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  committed_at         timestamptz,
  committed_by         uuid REFERENCES core.users (id),
  revert_deadline      timestamptz,
  reverted_at          timestamptz,
  reverted_by          uuid REFERENCES core.users (id),
  raw_file_deleted_at  timestamptz,
  version              int NOT NULL DEFAULT 1,
  CONSTRAINT import_batches_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT import_batches_kind_check CHECK (kind IN ('spreadsheet','register_photos')),
  CONSTRAINT import_batches_source_check CHECK (source IN ({SOURCES})),
  CONSTRAINT import_batches_status_check CHECK (status IN ({BATCH_STATUSES})),
  CONSTRAINT import_batches_file_kind_check CHECK (file_kind IN ('xlsx','csv')),
  CONSTRAINT import_batches_header_row_positive CHECK (header_row >= 1),
  CONSTRAINT import_batches_columns_array CHECK (jsonb_typeof(columns) = 'array'),
  CONSTRAINT import_batches_mapping_object CHECK (jsonb_typeof(mapping) = 'object'),
  CONSTRAINT import_batches_stats_object CHECK (jsonb_typeof(stats) = 'object'),
  CONSTRAINT import_batches_counts CHECK (row_count >= 0 AND error_count BETWEEN 0 AND row_count),
  CONSTRAINT import_batches_error_code_shape CHECK (error_code ~ '{CODE_RE}'),
  CONSTRAINT import_batches_committed_fields CHECK (
    (committed_at IS NULL) = (committed_by IS NULL)
    AND (committed_at IS NULL) = (revert_deadline IS NULL)),
  CONSTRAINT import_batches_committed_status CHECK (
    status NOT IN ('committed','reverting','reverted') OR committed_at IS NOT NULL),
  CONSTRAINT import_batches_reverted_fields CHECK (
    (reverted_at IS NULL) = (reverted_by IS NULL)
    AND (status = 'reverted') = (reverted_at IS NOT NULL)),
  CONSTRAINT import_batches_version_positive CHECK (version >= 1),
  -- The raw file is referenced until the retention job deletes it (FR-IMP-007).
  CONSTRAINT import_batches_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id),
  CONSTRAINT import_batches_template_fk FOREIGN KEY (tenant_id, mapping_template_id)
    REFERENCES sis.import_mapping_templates (tenant_id, id) ON DELETE SET NULL (mapping_template_id),
  CONSTRAINT import_batches_job_fk FOREIGN KEY (tenant_id, job_id)
    REFERENCES ops.job_runs (tenant_id, id) ON DELETE SET NULL (job_id)
);
CREATE INDEX import_batches_list_idx ON sis.import_batches (tenant_id, id DESC);
-- One live batch per uploaded file (a reverted or failed batch frees the file again).
CREATE UNIQUE INDEX import_batches_one_live_per_document ON sis.import_batches (tenant_id, document_id)
  WHERE document_id IS NOT NULL AND status NOT IN ('reverted','failed');
CREATE INDEX import_batches_retention_idx ON sis.import_batches (tenant_id, committed_at)
  WHERE document_id IS NOT NULL;

CREATE TABLE sis.import_rows (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL REFERENCES core.tenants (id),
  batch_id          uuid NOT NULL,
  row_no            int NOT NULL,
  parsed            jsonb NOT NULL,
  errors            jsonb NOT NULL DEFAULT '[]'::jsonb,
  warnings          jsonb NOT NULL DEFAULT '[]'::jsonb,
  status            text NOT NULL,
  action            text,
  student_id        uuid,
  created_student   boolean NOT NULL DEFAULT false,
  student_version   int,
  CONSTRAINT import_rows_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT import_rows_row_key UNIQUE (tenant_id, batch_id, row_no),
  CONSTRAINT import_rows_row_no_positive CHECK (row_no >= 1),
  CONSTRAINT import_rows_status_check CHECK (status IN ({ROW_STATUSES})),
  CONSTRAINT import_rows_action_check CHECK (action IN ('create','update')),
  CONSTRAINT import_rows_parsed_object CHECK (jsonb_typeof(parsed) = 'object'),
  CONSTRAINT import_rows_errors_array CHECK (jsonb_typeof(errors) = 'array'),
  CONSTRAINT import_rows_warnings_array CHECK (jsonb_typeof(warnings) = 'array'),
  CONSTRAINT import_rows_error_status CHECK ((status = 'error') = (jsonb_array_length(errors) > 0)
    OR status IN ('skipped','reverted')),
  CONSTRAINT import_rows_created_student CHECK (NOT created_student OR action = 'create'),
  CONSTRAINT import_rows_batch_fk FOREIGN KEY (tenant_id, batch_id)
    REFERENCES sis.import_batches (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT import_rows_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id) ON DELETE SET NULL (student_id)
);
CREATE INDEX import_rows_status_idx ON sis.import_rows (tenant_id, batch_id, status, row_no);
CREATE INDEX import_rows_student_idx ON sis.import_rows (tenant_id, student_id)
  WHERE student_id IS NOT NULL;
"""

TENANT_TABLES = ("sis.import_mapping_templates", "sis.import_batches", "sis.import_rows")
UPDATED_AT_TABLES = ("sis.import_mapping_templates", "sis.import_batches")

STUDENT_LINKS_SQL = r"""
ALTER TABLE sis.attribute_values ADD CONSTRAINT attribute_values_import_batch_fk
  FOREIGN KEY (tenant_id, import_batch_id) REFERENCES sis.import_batches (tenant_id, id);
CREATE INDEX attribute_values_import_batch_idx ON sis.attribute_values (tenant_id, import_batch_id)
  WHERE import_batch_id IS NOT NULL;

-- FR-IMP-005: a reverted batch removes the students it created. sos_app has no DELETE on
-- attribute_values (FR-STU-005); the cascade runs as the table owner, and the trigger below makes
-- sure a student can only be deleted while the batch that created it is being reverted.
ALTER TABLE sis.attribute_values DROP CONSTRAINT attribute_values_student_fk;
ALTER TABLE sis.attribute_values ADD CONSTRAINT attribute_values_student_fk
  FOREIGN KEY (tenant_id, student_id) REFERENCES sis.students (tenant_id, id) ON DELETE CASCADE;

CREATE FUNCTION sis.tg_students_delete_only_by_import_revert() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
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
$$;
REVOKE ALL ON FUNCTION sis.tg_students_delete_only_by_import_revert() FROM PUBLIC;
CREATE TRIGGER students_delete_only_by_import_revert BEFORE DELETE ON sis.students
  FOR EACH ROW EXECUTE FUNCTION sis.tg_students_delete_only_by_import_revert();
"""

DOWNGRADE_STUDENT_LINKS_SQL = r"""
DROP TRIGGER IF EXISTS students_delete_only_by_import_revert ON sis.students;
DROP FUNCTION IF EXISTS sis.tg_students_delete_only_by_import_revert();
ALTER TABLE sis.attribute_values DROP CONSTRAINT IF EXISTS attribute_values_student_fk;
ALTER TABLE sis.attribute_values ADD CONSTRAINT attribute_values_student_fk
  FOREIGN KEY (tenant_id, student_id) REFERENCES sis.students (tenant_id, id);
DROP INDEX IF EXISTS sis.attribute_values_import_batch_idx;
ALTER TABLE sis.attribute_values DROP CONSTRAINT IF EXISTS attribute_values_import_batch_fk;
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in UPDATED_AT_TABLES:
        name = table.split(".", 1)[1]
        op.execute(
            f"CREATE TRIGGER {name}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at()"
        )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
    op.execute(STUDENT_LINKS_SQL)


def downgrade() -> None:
    # attribute_values.import_batch_id stays (0008 column); only the link to the batch goes.
    op.execute(DOWNGRADE_STUDENT_LINKS_SQL)
    for table in ("sis.import_rows", "sis.import_batches", "sis.import_mapping_templates"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
