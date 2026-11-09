"""FM-06 annual tuition fee certificate for income tax (FR-FEE-046..060; ADR-0042 follow-up;
lead decision D84).

- ``fin.fee_heads.tax_tuition``: the head setting "counts as tuition for tax". Existing heads of
  kind ``tuition`` are switched on (the default for new tuition heads, FR-FEE-047); every other
  head stays off. ``sos_app`` may update the column (``fee.manage``, head PATCH).
- ``fin.receipt_counters`` accepts the fees certificate series ``FTC`` next to ``RCT`` and ``RFD``
  (gap-free per school and financial year, FR-FEE-051).
- ``fin.tax_certificates``: one issued certificate per student and financial year (partial
  unique index); a re-issue with different amounts supersedes the earlier one, which keeps its
  serial. The content JSON is frozen with its SHA-256 (as receipts, FR-FEE-011); trigger
  ``tax_certificates_frozen`` allows only ``issued -> superseded`` (with ``superseded_by``), the
  PDF state and the document link. Tenant-table rules: ``tenant_id`` first, ``UNIQUE (tenant_id,
  id)``, RLS ENABLE + FORCE with ``tenant_isolation``, composite FKs, the restrictive
  ``offboarding_purge`` policy for ``sos_purger`` (ADR-0029), no ``definer_access``, nothing for
  ``sos_platform``; ``sos_app`` has SELECT, INSERT and UPDATE of the state columns only, no
  DELETE.

The certificate PDF is stored as a generated ``fee_receipt`` document (same ACL roles and 8-year
retention as receipts), so ``kb.documents.purpose`` is unchanged.

Expand-only (invariant 12). The owner lifts FORCE on ``fin.fee_heads`` for the one backfill
UPDATE and restores it in the same transaction (0064 pattern). Downgrade drops the table and
the column (lossy: issued certificates are removed; their PDFs stay as documents) and re-adds
the old series CHECK as ``NOT VALID`` (``FTC`` counter rows may remain; counters are
append-only).

Revision ID: 0083_fee_tax_cert
Revises: 0082_audit_ym_fixes
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "0083_fee_tax_cert"
down_revision = "0082_audit_ym_fixes"
branch_labels = None
depends_on = None

TABLE = "fin.tax_certificates"

UNFORCE = "ALTER TABLE fin.fee_heads NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner switches on the tax flag of every school's tuition heads; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE fin.fee_heads FORCE ROW LEVEL SECURITY"

UPGRADE_SQL = r"""
ALTER TABLE fin.fee_heads ADD COLUMN tax_tuition boolean NOT NULL DEFAULT false;
GRANT UPDATE (tax_tuition) ON fin.fee_heads TO sos_app;

ALTER TABLE fin.receipt_counters DROP CONSTRAINT receipt_counters_series_check;
ALTER TABLE fin.receipt_counters ADD CONSTRAINT receipt_counters_series_check
  CHECK (series IN ('RCT','RFD','FTC'));

CREATE TABLE fin.tax_certificates (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  student_id       uuid NOT NULL,
  financial_year   text NOT NULL,
  serial_no        int NOT NULL,
  serial_number    text NOT NULL,
  as_of            date NOT NULL,
  amount           numeric(14,2) NOT NULL,
  receipt_count    int NOT NULL,
  content          jsonb NOT NULL,
  content_sha256   bytea NOT NULL,
  basis_sha256     bytea NOT NULL,
  status           text NOT NULL DEFAULT 'issued',
  superseded_by    uuid,
  issued_by        uuid NOT NULL,
  issued_at        timestamptz NOT NULL DEFAULT now(),
  document_id      uuid,
  pdf_status       text NOT NULL DEFAULT 'queued',
  pdf_error        text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          int NOT NULL DEFAULT 1,
  CONSTRAINT tax_certificates_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tax_certificates_serial_unique UNIQUE (tenant_id, financial_year, serial_no),
  CONSTRAINT tax_certificates_number_unique UNIQUE (tenant_id, serial_number),
  CONSTRAINT tax_certificates_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tax_certificates_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  -- Deferred: a re-issue marks the earlier certificate superseded by the new id first (the
  -- partial unique index allows one issued row), then inserts the new one.
  CONSTRAINT tax_certificates_superseded_by_fk FOREIGN KEY (tenant_id, superseded_by)
    REFERENCES fin.tax_certificates (tenant_id, id) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT tax_certificates_issued_by_fk FOREIGN KEY (tenant_id, issued_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tax_certificates_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT tax_certificates_year_shape CHECK (financial_year ~ '^[0-9]{4}-[0-9]{2}$'),
  CONSTRAINT tax_certificates_no_range CHECK (serial_no BETWEEN 1 AND 999999),
  CONSTRAINT tax_certificates_number_shape
    CHECK (serial_number ~ '^[A-Za-z0-9][A-Za-z0-9/_.-]{0,39}$'),
  CONSTRAINT tax_certificates_amount_positive CHECK (amount > 0),
  CONSTRAINT tax_certificates_receipt_count_range CHECK (receipt_count BETWEEN 1 AND 500),
  CONSTRAINT tax_certificates_content_object CHECK (jsonb_typeof(content) = 'object'),
  CONSTRAINT tax_certificates_sha256_length
    CHECK (octet_length(content_sha256) = 32 AND octet_length(basis_sha256) = 32),
  CONSTRAINT tax_certificates_status_check CHECK (status IN ('issued','superseded')),
  CONSTRAINT tax_certificates_superseded
    CHECK ((status = 'superseded') = (superseded_by IS NOT NULL) AND superseded_by IS DISTINCT FROM id),
  CONSTRAINT tax_certificates_pdf_status_check CHECK (pdf_status IN ('queued','ready','failed')),
  CONSTRAINT tax_certificates_pdf_ready CHECK (pdf_status <> 'ready' OR document_id IS NOT NULL),
  CONSTRAINT tax_certificates_pdf_error_shape
    CHECK ((pdf_error IS NULL OR pdf_error ~ '^[a-z][a-z0-9_]{0,63}$')
           AND (pdf_status = 'failed') = (pdf_error IS NOT NULL)),
  CONSTRAINT tax_certificates_version_positive CHECK (version >= 1)
);
-- FR-FEE-052: at most one issued certificate per student and financial year.
CREATE UNIQUE INDEX tax_certificates_one_issued
  ON fin.tax_certificates (tenant_id, student_id, financial_year) WHERE status = 'issued';
CREATE INDEX tax_certificates_year_idx
  ON fin.tax_certificates (tenant_id, financial_year, serial_no);
CREATE TRIGGER tax_certificates_set_updated_at BEFORE UPDATE ON fin.tax_certificates
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- The frozen certificate: only issued -> superseded (once), the PDF state and the document link.
CREATE FUNCTION fin.tg_tax_certificates_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.student_id <> OLD.student_id
     OR NEW.financial_year <> OLD.financial_year OR NEW.serial_no <> OLD.serial_no
     OR NEW.serial_number <> OLD.serial_number OR NEW.as_of <> OLD.as_of
     OR NEW.amount <> OLD.amount OR NEW.receipt_count <> OLD.receipt_count
     OR NEW.content <> OLD.content OR NEW.content_sha256 <> OLD.content_sha256
     OR NEW.basis_sha256 <> OLD.basis_sha256 OR NEW.issued_by <> OLD.issued_by
     OR NEW.issued_at <> OLD.issued_at
     OR (OLD.status = 'superseded' AND (NEW.status <> OLD.status
         OR NEW.superseded_by IS DISTINCT FROM OLD.superseded_by))
     OR (OLD.document_id IS NOT NULL AND NEW.document_id IS DISTINCT FROM OLD.document_id) THEN
    RAISE EXCEPTION 'issued tax certificates are frozen'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'tax_certificates_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER tax_certificates_frozen BEFORE UPDATE ON fin.tax_certificates
  FOR EACH ROW EXECUTE FUNCTION fin.tg_tax_certificates_frozen();
"""

GRANTS_SQL = f"""
REVOKE UPDATE, DELETE, TRUNCATE ON {TABLE} FROM sos_app;
GRANT UPDATE (status, superseded_by, document_id, pdf_status, pdf_error, updated_at, version)
  ON {TABLE} TO sos_app;
REVOKE ALL ON {TABLE} FROM sos_readonly;
GRANT SELECT ON {TABLE} TO sos_readonly;
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS fin.tax_certificates;
DROP FUNCTION IF EXISTS fin.tg_tax_certificates_frozen();
ALTER TABLE fin.receipt_counters DROP CONSTRAINT receipt_counters_series_check;
ALTER TABLE fin.receipt_counters ADD CONSTRAINT receipt_counters_series_check
  CHECK (series IN ('RCT','RFD')) NOT VALID;
REVOKE UPDATE (tax_tuition) ON fin.fee_heads FROM sos_app;
ALTER TABLE fin.fee_heads DROP COLUMN IF EXISTS tax_tuition;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    op.execute(UNFORCE)
    op.execute("UPDATE fin.fee_heads SET tax_tuition = true WHERE kind = 'tuition'")
    op.execute(REFORCE)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger.
    op.execute(f"GRANT SELECT, DELETE ON {TABLE} TO sos_purger")
    op.execute(
        f"CREATE POLICY offboarding_purge ON {TABLE} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed())"
    )
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
