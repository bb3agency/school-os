"""Invoice PDFs: ``platform.invoice_pdfs`` (FR-PLT-016, FR-PLT-017; docs/16 §5.8, §7).

One row per issued invoice whose PDF has been rendered (worker queue ``pdf``) and stored in the
control-plane object prefix (ADR-0017 Amendment 2026-09-28). The row is written in the same
platform transaction as the audit event ``invoice.pdf_rendered``; its primary key makes the
render idempotent per invoice (a second render loses the insert and discards its own object).

- ``template_version`` names the layout the file used (``v0`` until the CA signs off; docs/16
  §19 Q2, Q3, Q11).
- ``object_key``: the object inside the control-plane bucket/prefix; ``sha256`` and
  ``size_bytes`` describe the stored bytes so a download can be checked against them.
- Append-only: a tax invoice document, once stored, never changes (``sos_platform`` may only
  SELECT and INSERT; a trigger refuses UPDATE and DELETE even for the owner).

Not tenant-owned (schema ``platform``, no RLS, no student data: invoice number, school legal
name, GSTIN and address live in ``platform.invoices`` already). ``sos_app`` and ``sos_readonly``
get nothing.

Downgrade drops the table (the stored objects stay in the bucket; the running code of 0028 does
not use them).

Revision ID: 0029_invoice_pdfs
Revises: 0028_profile_scope
Create Date: 2026-09-28
"""

from __future__ import annotations

from alembic import op

revision = "0029_invoice_pdfs"
down_revision = "0028_profile_scope"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE platform.invoice_pdfs (
  invoice_id        uuid PRIMARY KEY REFERENCES platform.invoices (id),
  template_version  text NOT NULL CHECK (template_version ~ '^v[0-9]{1,3}$'),
  object_key        text NOT NULL UNIQUE
                      CHECK (object_key ~ '^[a-z0-9][a-z0-9/_.-]{0,254}$'
                             AND object_key NOT LIKE 't/%'
                             AND position('..' in object_key) = 0),
  sha256            text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes        int  NOT NULL CHECK (size_bytes > 0),
  rendered_at       timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE platform.invoice_pdfs IS
  'Rendered invoice PDFs, one per issued invoice (docs/16 5.8). Append-only. No student data.';
COMMENT ON COLUMN platform.invoice_pdfs.object_key IS
  'Key in the control-plane invoice bucket/prefix, never under a school prefix t/<tenant>/.';

CREATE FUNCTION platform.tg_invoice_pdfs_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  RAISE EXCEPTION 'invoice PDFs are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'invoice_pdfs_append_only';
END;
$$;
CREATE TRIGGER invoice_pdfs_append_only BEFORE UPDATE OR DELETE ON platform.invoice_pdfs
  FOR EACH ROW EXECUTE FUNCTION platform.tg_invoice_pdfs_append_only();
CREATE TRIGGER invoice_pdfs_no_truncate BEFORE TRUNCATE ON platform.invoice_pdfs
  FOR EACH STATEMENT EXECUTE FUNCTION platform.tg_invoice_pdfs_append_only();

REVOKE ALL ON platform.invoice_pdfs FROM PUBLIC;
REVOKE ALL ON platform.invoice_pdfs FROM sos_platform, sos_app, sos_readonly;
GRANT SELECT, INSERT ON platform.invoice_pdfs TO sos_platform;
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS platform.invoice_pdfs;
DROP FUNCTION IF EXISTS platform.tg_invoice_pdfs_append_only();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
