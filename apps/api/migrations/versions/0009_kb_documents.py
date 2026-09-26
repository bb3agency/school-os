"""Documents: kb.documents, kb.document_versions, kb.document_acl, kb.upload_intents.

docs/05 §6 (documents, versions and ACL only; chunks, embeddings and queries arrive in M2).
Additions agreed for M1 (CONTRACT §8):
- ``kb.documents.purpose`` (evidence, register_scan, circular, policy, other, import_file) drives
  the size limit, the allowed file kinds and the S3 layout (docs/04 §8.2); ``doc_type`` gains
  ``evidence`` and ``import_file``.
- ``kb.upload_intents``: an object may only be registered if the API issued a presigned POST
  for exactly that staging key, to that user, and it has not expired or been used (SEC-016).
  Verified bytes are copied (If-Match on the verified ETag) to the final key.
- Object keys must live under the tenant's own prefix ``t/<tenant_id>/`` (CHECK constraints).
- If ``sis.attribute_values.evidence_document_id`` exists (0008_sis_students), its composite
  FK to ``kb.documents`` is added here (docs/05 §3.5: the FK comes with the referenced table).

Every table is tenant-owned: RLS ENABLE + FORCE with ``tenant_isolation``.

Requirements: FR-DOC-001..006, FR-DOC-008, SEC-016, SEC-001.

Revision ID: 0009_kb_documents
Revises: 0007_accept_invitations (the lead relinks after 0008_sis_students)
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op

revision = "0009_kb_documents"
down_revision = "0007_accept_invitations"
branch_labels = None
depends_on = None

PURPOSES = "'evidence','register_scan','circular','policy','other','import_file'"
DOC_TYPES = (
    "'circular','policy','minutes','register_scan','certificate','letter','form','report',"
    "'verified_answer','other','evidence','import_file'"
)
VERSION_STATUSES = (
    "'queued','scanning','extracting','chunking','embedding','ready','failed','quarantined'"
)
MIME_TYPES = (
    "'application/pdf','image/jpeg','image/png',"
    "'application/vnd.openxmlformats-officedocument.wordprocessingml.document',"
    "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','text/csv'"
)
# Final keys: t/<tenant_id>/docs/<document_id>/v<n>/original.<ext> or
# t/<tenant_id>/imports/<batch_id>/raw.<ext> (docs/04 §8.2).
KEY_SHAPE = (
    r"^t/[0-9a-f-]{36}/(docs/[0-9a-f-]{36}/v[1-9][0-9]{0,5}/original"
    r"|imports/[0-9a-f-]{36}/raw)\.(pdf|jpg|png|docx|xlsx|csv)$"
)
# Staging keys written by presigned POSTs: t/<tenant_id>/uploads/<intent_id>/original.<ext>.
# Verified bytes are copied to the final key, which no presigned POST can overwrite.
UPLOAD_KEY_SHAPE = r"^t/[0-9a-f-]{36}/uploads/[0-9a-f-]{36}/original\.(pdf|jpg|png|docx|xlsx|csv)$"

TABLES_SQL = rf"""
CREATE TABLE kb.documents (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL REFERENCES core.tenants (id),
  purpose             text NOT NULL CHECK (purpose IN ({PURPOSES})),
  doc_type            text NOT NULL CHECK (doc_type IN ({DOC_TYPES})),
  title               text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
  issuer              text CHECK (char_length(issuer) BETWEEN 1 AND 200),
  issued_on           date,
  academic_year_id    uuid,
  language            text CHECK (language IN ('en','te','mixed')),
  sensitivity         text NOT NULL CHECK (sensitivity IN ('C1','C2','C3')),
  current_version_id  uuid,
  status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_by          uuid NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  version             int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT documents_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT documents_academic_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id)
);
CREATE INDEX documents_tenant_list ON kb.documents (tenant_id, id DESC);
CREATE INDEX documents_tenant_type ON kb.documents (tenant_id, doc_type, issued_on);

CREATE TABLE kb.document_versions (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL REFERENCES core.tenants (id),
  document_id  uuid NOT NULL,
  version_no   int NOT NULL CHECK (version_no BETWEEN 1 AND 999999),
  object_key   text NOT NULL CHECK (object_key ~ '{KEY_SHAPE}'),
  sha256       bytea NOT NULL CHECK (octet_length(sha256) = 32),
  mime_type    text NOT NULL CHECK (mime_type IN ({MIME_TYPES})),
  size_bytes   bigint NOT NULL CHECK (size_bytes > 0),
  page_count   int CHECK (page_count >= 0),
  status       text NOT NULL CHECK (status IN ({VERSION_STATUSES})),
  error        text CHECK (error ~ '^[a-z][a-z0-9_]{{0,63}}$'),
  created_by   uuid NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT document_versions_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT document_versions_tenant_document_version_key
    UNIQUE (tenant_id, document_id, version_no),
  CONSTRAINT document_versions_tenant_object_key UNIQUE (tenant_id, object_key),
  -- Objects live under the tenant's own prefix, never another school's (FR-DOC-003).
  CONSTRAINT document_versions_key_in_tenant_prefix
    CHECK (starts_with(object_key, 't/' || tenant_id::text || '/')),
  CONSTRAINT document_versions_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX document_versions_sha ON kb.document_versions (tenant_id, sha256);
CREATE INDEX document_versions_status ON kb.document_versions (tenant_id, status)
  WHERE status IN ('queued','scanning');

ALTER TABLE kb.documents ADD CONSTRAINT documents_current_version_fk
  FOREIGN KEY (tenant_id, current_version_id)
  REFERENCES kb.document_versions (tenant_id, id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE kb.document_acl (
  tenant_id       uuid NOT NULL REFERENCES core.tenants (id),
  document_id     uuid NOT NULL,
  principal_type  text NOT NULL CHECK (principal_type IN ('role','section','class','membership')),
  -- Polymorphic (docs/05 §3.5): role key, or a section/class/membership id validated by the
  -- documents service inside the tenant session.
  principal_ref   text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, document_id, principal_type, principal_ref),
  CONSTRAINT document_acl_ref_shape CHECK (
    (principal_type = 'role' AND principal_ref ~ '^[a-z][a-z0-9_]{{1,63}}$')
    OR (principal_type <> 'role'
        AND principal_ref ~ '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$')),
  CONSTRAINT document_acl_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE kb.upload_intents (
  id                     uuid PRIMARY KEY,
  tenant_id              uuid NOT NULL REFERENCES core.tenants (id),
  purpose                text NOT NULL CHECK (purpose IN ({PURPOSES})),
  -- New document: a pre-allocated id (no row yet). New version: the existing document.
  document_id            uuid NOT NULL,
  version_no             int NOT NULL CHECK (version_no BETWEEN 1 AND 999999),
  batch_id               uuid,
  object_key             text NOT NULL CHECK (object_key ~ '{UPLOAD_KEY_SHAPE}'),
  declared_content_type  text NOT NULL CHECK (declared_content_type IN ({MIME_TYPES})),
  declared_size          bigint NOT NULL CHECK (declared_size > 0),
  max_bytes              bigint NOT NULL CHECK (max_bytes > 0),
  created_by             uuid NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  expires_at             timestamptz NOT NULL,
  consumed_at            timestamptz,
  CONSTRAINT upload_intents_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT upload_intents_tenant_object_key UNIQUE (tenant_id, object_key),
  CONSTRAINT upload_intents_key_in_tenant_prefix
    CHECK (starts_with(object_key, 't/' || tenant_id::text || '/')),
  CONSTRAINT upload_intents_size_within_max CHECK (declared_size <= max_bytes),
  CONSTRAINT upload_intents_short_lived
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '1 hour'),
  CONSTRAINT upload_intents_batch_only_for_imports
    CHECK ((batch_id IS NOT NULL) = (purpose = 'import_file'))
);
CREATE INDEX upload_intents_expiry ON kb.upload_intents (tenant_id, expires_at);
"""

TENANT_TABLES = ("kb.documents", "kb.document_versions", "kb.document_acl", "kb.upload_intents")

# docs/05 §3.5: the evidence FK arrives with the referenced table. Conditional so this revision
# works both before and after 0008_sis_students is linked in front of it.
EVIDENCE_FK_SQL = r"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'sis' AND table_name = 'attribute_values'
               AND column_name = 'evidence_document_id')
     AND NOT EXISTS (SELECT 1 FROM pg_constraint
                     WHERE conname = 'attribute_values_evidence_document_fk') THEN
    ALTER TABLE sis.attribute_values ADD CONSTRAINT attribute_values_evidence_document_fk
      FOREIGN KEY (tenant_id, evidence_document_id) REFERENCES kb.documents (tenant_id, id);
  END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in ("kb.documents", "kb.document_versions"):
        op.execute(
            f"CREATE TRIGGER {table.split('.')[1]}_set_updated_at BEFORE UPDATE ON {table} "
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
    op.execute(EVIDENCE_FK_SQL)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF to_regclass('sis.attribute_values') IS NOT NULL THEN "
        "ALTER TABLE sis.attribute_values "
        "DROP CONSTRAINT IF EXISTS attribute_values_evidence_document_fk; END IF; END $$"
    )
    op.execute("ALTER TABLE kb.documents DROP CONSTRAINT IF EXISTS documents_current_version_fk")
    for table in ("kb.upload_intents", "kb.document_acl", "kb.document_versions", "kb.documents"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
