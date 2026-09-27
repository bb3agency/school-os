"""Register-photo extraction with human verification (US-402; FR-IMP-020..024; PRV-015/016).

docs/05 §5 ``sis.extraction_items`` plus the batch and page tables it needs (the spreadsheet
``sis.import_batches`` belongs to the imports module; photo batches have their own table so the
two modules stay independent):

- ``sis.extraction_batches``: one run over register-page photos. ``source`` is always
  ``admission_register`` (BR-01). Progress counters are kept on the row (pages done/failed,
  items pending/confirmed/rejected, low-confidence items).
- ``sis.extraction_pages``: one row per page image (M1: one JPG/PNG per document; PDFs are
  refused until a rasteriser is approved). ``aadhaar_detected`` marks pages whose text held a
  Verhoeff-valid 12-digit number; ``image_withheld`` keeps such an image from every reviewer
  until image redaction exists (PRV-016). No OCR text is stored.
- ``sis.extraction_items``: one candidate register row. ``fields`` = ``{field: {value,
  confidence, bbox, masked}}`` with Aadhaar-like numbers already masked (FR-IMP-021/022).
  Nothing here is a student record: values reach ``sis.attribute_values`` only when a person
  confirms the item (FR-IMP-020, FR-IMP-023).

Every table is tenant-owned (RLS ENABLE + FORCE, ``tenant_isolation``), carries
``UNIQUE (tenant_id, id)`` and references other tenant tables with composite FKs. Rows are the
provenance of confirmed values, so the app may not delete them.

Revision ID: 0016_extraction
Revises: 0015_platform_decisions
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0016_extraction"
down_revision = "0015_platform_decisions"
branch_labels = None
depends_on = None

TABLES_SQL = r"""
CREATE TABLE sis.extraction_batches (
  id                     uuid PRIMARY KEY,
  tenant_id              uuid NOT NULL REFERENCES core.tenants (id),
  source                 text NOT NULL DEFAULT 'admission_register'
                           CHECK (source = 'admission_register'),
  status                 text NOT NULL DEFAULT 'queued'
                           CHECK (status IN ('queued','processing','review','completed','failed')),
  provider               text NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_-]{0,31}$'),
  error_code             text CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
  created_by             uuid NOT NULL,
  created_by_membership  uuid NOT NULL,
  page_count             int NOT NULL CHECK (page_count BETWEEN 1 AND 500),
  pages_done             int NOT NULL DEFAULT 0 CHECK (pages_done >= 0),
  pages_failed           int NOT NULL DEFAULT 0 CHECK (pages_failed >= 0),
  pages_withheld         int NOT NULL DEFAULT 0 CHECK (pages_withheld >= 0),
  items_total            int NOT NULL DEFAULT 0 CHECK (items_total >= 0),
  items_pending          int NOT NULL DEFAULT 0 CHECK (items_pending >= 0),
  items_confirmed        int NOT NULL DEFAULT 0 CHECK (items_confirmed >= 0),
  items_rejected         int NOT NULL DEFAULT 0 CHECK (items_rejected >= 0),
  items_low_confidence   int NOT NULL DEFAULT 0 CHECK (items_low_confidence >= 0),
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  processed_at           timestamptz,
  completed_at           timestamptz,
  version                int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT extraction_batches_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT extraction_batches_membership_fk FOREIGN KEY (tenant_id, created_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT extraction_batches_pages_add_up
    CHECK (pages_done + pages_failed <= page_count AND pages_withheld <= pages_done),
  CONSTRAINT extraction_batches_items_add_up
    CHECK (items_pending + items_confirmed + items_rejected = items_total
           AND items_low_confidence <= items_total),
  CONSTRAINT extraction_batches_failed_has_code
    CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE INDEX extraction_batches_tenant_list ON sis.extraction_batches (tenant_id, id DESC);

CREATE TABLE sis.extraction_pages (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL REFERENCES core.tenants (id),
  batch_id              uuid NOT NULL,
  document_id           uuid NOT NULL,
  document_version_no   int NOT NULL CHECK (document_version_no BETWEEN 1 AND 999999),
  page_no               int NOT NULL CHECK (page_no BETWEEN 1 AND 9999),
  seq                   int NOT NULL CHECK (seq BETWEEN 1 AND 500),
  status                text NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued','done','failed')),
  error_code            text CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
  aadhaar_detected      boolean NOT NULL DEFAULT false,
  image_withheld        boolean NOT NULL DEFAULT false,
  row_count             int NOT NULL DEFAULT 0 CHECK (row_count >= 0),
  low_confidence_count  int NOT NULL DEFAULT 0 CHECK (low_confidence_count >= 0),
  dropped_field_count   int NOT NULL DEFAULT 0 CHECK (dropped_field_count >= 0),
  processed_at          timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT extraction_pages_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT extraction_pages_page_key UNIQUE (tenant_id, batch_id, document_id, page_no),
  CONSTRAINT extraction_pages_seq_key UNIQUE (tenant_id, batch_id, seq),
  CONSTRAINT extraction_pages_batch_fk FOREIGN KEY (tenant_id, batch_id)
    REFERENCES sis.extraction_batches (tenant_id, id),
  CONSTRAINT extraction_pages_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT extraction_pages_failed_has_code
    CHECK ((status = 'failed') = (error_code IS NOT NULL)),
  -- PRV-016: a page whose text held a full Aadhaar number is never shown until redacted.
  CONSTRAINT extraction_pages_withhold_detected CHECK (NOT aadhaar_detected OR image_withheld),
  CONSTRAINT extraction_pages_low_confidence_le_rows CHECK (low_confidence_count <= row_count)
);
CREATE INDEX extraction_pages_document ON sis.extraction_pages (tenant_id, document_id);

CREATE TABLE sis.extraction_items (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL REFERENCES core.tenants (id),
  batch_id          uuid NOT NULL,
  page_id           uuid NOT NULL,
  document_id       uuid NOT NULL,
  page_no           int NOT NULL CHECK (page_no BETWEEN 1 AND 9999),
  row_index         int NOT NULL CHECK (row_index BETWEEN 0 AND 999),
  fields            jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'object'),
  low_confidence    boolean NOT NULL DEFAULT false,
  masked            boolean NOT NULL DEFAULT false,
  status            text NOT NULL DEFAULT 'pending_review'
                      CHECK (status IN ('pending_review','confirmed','rejected')),
  reviewed_by       uuid,
  reviewed_at       timestamptz,
  reject_reason     text CHECK (reject_reason IN
                      ('not_a_student_row','duplicate','unreadable','other')),
  student_id        uuid,
  value_ids         uuid[] NOT NULL DEFAULT '{}',
  corrected_fields  text[] NOT NULL DEFAULT '{}',
  created_student   boolean NOT NULL DEFAULT false,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT extraction_items_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT extraction_items_row_key UNIQUE (tenant_id, page_id, row_index),
  CONSTRAINT extraction_items_batch_fk FOREIGN KEY (tenant_id, batch_id)
    REFERENCES sis.extraction_batches (tenant_id, id),
  CONSTRAINT extraction_items_page_fk FOREIGN KEY (tenant_id, page_id)
    REFERENCES sis.extraction_pages (tenant_id, id),
  CONSTRAINT extraction_items_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT extraction_items_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  -- A reviewed item names who reviewed it and when; a pending one has no outcome yet.
  CONSTRAINT extraction_items_review_shape CHECK (
    (status = 'pending_review' AND reviewed_by IS NULL AND reviewed_at IS NULL
       AND student_id IS NULL AND reject_reason IS NULL AND cardinality(value_ids) = 0)
    OR (status = 'confirmed' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
       AND student_id IS NOT NULL AND reject_reason IS NULL)
    OR (status = 'rejected' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
       AND student_id IS NULL AND reject_reason IS NOT NULL AND cardinality(value_ids) = 0)
  )
);
CREATE INDEX extraction_items_queue ON sis.extraction_items (tenant_id, batch_id, status, id);
CREATE INDEX extraction_items_status ON sis.extraction_items (tenant_id, status, id);
CREATE INDEX extraction_items_student ON sis.extraction_items (tenant_id, student_id)
  WHERE student_id IS NOT NULL;

-- Extracted values are the evidence trail of what the machine read: once written they never
-- change; only the review outcome may be set (once).
CREATE FUNCTION sis.tg_extraction_items_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.batch_id <> OLD.batch_id
     OR NEW.page_id <> OLD.page_id OR NEW.document_id <> OLD.document_id
     OR NEW.page_no <> OLD.page_no OR NEW.row_index <> OLD.row_index
     OR NEW.fields IS DISTINCT FROM OLD.fields OR NEW.low_confidence <> OLD.low_confidence
     OR NEW.masked <> OLD.masked OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'extracted values are immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'pending_review' THEN
    RAISE EXCEPTION 'extraction item already reviewed' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION sis.tg_extraction_items_immutable() FROM PUBLIC;
CREATE TRIGGER extraction_items_immutable BEFORE UPDATE ON sis.extraction_items
  FOR EACH ROW EXECUTE FUNCTION sis.tg_extraction_items_immutable();
"""

TENANT_TABLES = ("sis.extraction_batches", "sis.extraction_pages", "sis.extraction_items")


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in TENANT_TABLES:
        name = table.split(".")[1]
        op.execute(
            f"CREATE TRIGGER {name}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at()"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        # Provenance of confirmed register values: never deleted by the app.
        op.execute(f"REVOKE DELETE, TRUNCATE ON {table} FROM sos_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sis.extraction_items")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_extraction_items_immutable()")
    op.execute("DROP TABLE IF EXISTS sis.extraction_pages")
    op.execute("DROP TABLE IF EXISTS sis.extraction_batches")
