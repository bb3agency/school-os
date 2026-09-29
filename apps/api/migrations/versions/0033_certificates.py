"""Certificates and registers (M3; US-1101..US-1108, FR-CERT-001..014, FR-REG-001..005).

Tenant tables in schema ``sis`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs):

- ``sis.certificates``: one row per certificate request and, once issued, the register entry
  (TC register / certificate issue register). ``status`` ``pending -> issued | rejected |
  withdrawn`` and ``issued -> cancelled``. Maker-checker for types that need approval: the
  decider is never the requester (``certificates_maker_checker``, ADR-0010). An issued
  certificate carries its serial number (per school, type and academic year; unique, never
  reused), the printed values frozen as they were (``content`` + SHA-256) and the template
  version; duplicates point at their original (``original_certificate_id``), carry no serial of
  their own and get a copy number at issue. At most one pending or issued original TC per
  student. The PDF is a ``kb.documents`` row (composite FK: the document cannot be deleted while
  the register entry refers to it).
- ``sis.certificate_counters``: the last serial number per (school, type, academic year). The
  service allocates with ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` in the issuing
  transaction, which locks the row: concurrent issues get consecutive numbers and a rolled-back
  issue releases its number (gap-free, FR-CERT-006). Numbers never go down (trigger).

Register entries are append-only (FR-REG-005): ``sos_app`` has no ``DELETE``/``TRUNCATE`` and may
update only the workflow columns; trigger ``certificates_frozen`` refuses any change to what was
requested and, once issued, to the number, content and dates (only ``issued -> cancelled`` with
its reason, the PDF state and the document link change). Deletion is refused except by the
offboarding purge (``core.tenant_purge_allowed()``, ADR-0029), which gets ``SELECT, DELETE`` as
``sos_purger`` behind the restrictive ``offboarding_purge`` policy like every other tenant table.

Also: ``kb.documents.purpose`` accepts ``certificate`` (generated certificate PDFs; never
uploadable, docs/05 §6.1), and the four permissions ``certificate.read``, ``certificate.issue``,
``certificate.approve`` (step-up) and ``register.read`` (step-up) are upserted into the global
catalog ``core.permissions`` (written out here, like ``0019_export_access``). Role grants of
existing schools are delivered by ``python -m app.identity.sync_system_roles`` (ADR-0022).

Expand-only (invariant 12). Downgrade drops both tables (lossy: the certificate register is
removed; PDFs stay as documents) and re-adds the old ``kb.documents`` purpose CHECK as
``NOT VALID`` (certificate documents already stored are left as they are: the migrator cannot
see tenant rows under FORCE RLS). The permission keys are deleted unless a role still holds
them (as ``0019_export_access``).

Revision ID: 0033_certificates
Revises: 0032_offboarding
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_certificates"
down_revision = "0032_offboarding"
branch_labels = None
depends_on = None

TENANT_TABLES = ("sis.certificate_counters", "sis.certificates")

UPGRADE_SQL = r"""
CREATE TABLE sis.certificate_counters (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  certificate_type  text NOT NULL,
  academic_year_id  uuid NOT NULL,
  last_no           int NOT NULL DEFAULT 0,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT certificate_counters_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT certificate_counters_one_per_type_year
    UNIQUE (tenant_id, certificate_type, academic_year_id),
  CONSTRAINT certificate_counters_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT certificate_counters_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT certificate_counters_type_check CHECK (certificate_type IN ('transfer','bonafide','study','conduct')),
  CONSTRAINT certificate_counters_last_no_range CHECK (last_no BETWEEN 0 AND 999999)
);

CREATE FUNCTION sis.tg_certificate_counters_never_down() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.last_no < OLD.last_no OR NEW.tenant_id <> OLD.tenant_id
     OR NEW.certificate_type <> OLD.certificate_type
     OR NEW.academic_year_id <> OLD.academic_year_id THEN
    RAISE EXCEPTION 'certificate serial numbers are never reused'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'certificate_counters_never_down';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER certificate_counters_never_down BEFORE UPDATE ON sis.certificate_counters
  FOR EACH ROW EXECUTE FUNCTION sis.tg_certificate_counters_never_down();

CREATE TABLE sis.certificates (
  id                       uuid PRIMARY KEY,
  tenant_id                uuid NOT NULL,
  student_id               uuid NOT NULL,
  certificate_type         text NOT NULL,
  status                   text NOT NULL DEFAULT 'pending',
  inputs                   jsonb NOT NULL DEFAULT '{}'::jsonb,
  original_certificate_id  uuid,
  duplicate_reason         text,
  duplicate_no             int,
  academic_year_id         uuid,
  serial_no                int,
  serial                   text,
  content                  jsonb,
  content_sha256           bytea,
  template_version         text,
  requested_by             uuid NOT NULL,
  requested_at             timestamptz NOT NULL DEFAULT now(),
  decided_by               uuid,
  decided_at               timestamptz,
  decision_note            text,
  issued_by                uuid,
  issued_at                timestamptz,
  cancelled_by             uuid,
  cancelled_at             timestamptz,
  cancel_reason            text,
  document_id              uuid,
  pdf_status               text NOT NULL DEFAULT 'none',
  pdf_error                text,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  version                  int NOT NULL DEFAULT 1,
  CONSTRAINT certificates_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT certificates_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT certificates_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT certificates_original_fk FOREIGN KEY (tenant_id, original_certificate_id)
    REFERENCES sis.certificates (tenant_id, id),
  CONSTRAINT certificates_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT certificates_requested_by_fk FOREIGN KEY (tenant_id, requested_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT certificates_decided_by_fk FOREIGN KEY (tenant_id, decided_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT certificates_issued_by_fk FOREIGN KEY (tenant_id, issued_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT certificates_cancelled_by_fk FOREIGN KEY (tenant_id, cancelled_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT certificates_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT certificates_type_check CHECK (certificate_type IN ('transfer','bonafide','study','conduct')),
  CONSTRAINT certificates_status_check
    CHECK (status IN ('pending','issued','rejected','withdrawn','cancelled')),
  CONSTRAINT certificates_pdf_status_check
    CHECK (pdf_status IN ('none','queued','ready','failed')),
  CONSTRAINT certificates_inputs_object CHECK (jsonb_typeof(inputs) = 'object'),
  CONSTRAINT certificates_content_object CHECK (content IS NULL OR jsonb_typeof(content) = 'object'),
  -- ADR-0010 / 07 §6.3: whoever decides a request never made it.
  CONSTRAINT certificates_maker_checker CHECK (decided_by IS NULL OR decided_by <> requested_by),
  -- Issued (and later cancelled) rows are register entries: number, content, who and when.
  CONSTRAINT certificates_issued_fields CHECK (
    (status IN ('issued','cancelled')) = (issued_at IS NOT NULL)
    AND (issued_at IS NULL OR (issued_by IS NOT NULL AND academic_year_id IS NOT NULL
         AND content IS NOT NULL AND content_sha256 IS NOT NULL
         AND template_version IS NOT NULL))),
  -- Originals carry a serial once issued; duplicates carry the original's (no serial of their
  -- own) and a copy number once issued.
  CONSTRAINT certificates_serial_shape CHECK (
    (serial IS NULL) = (serial_no IS NULL)
    AND (serial IS NULL OR serial ~ '^[A-Za-z0-9][A-Za-z0-9/_.-]{0,39}$')
    AND (serial_no IS NULL OR serial_no BETWEEN 1 AND 999999)),
  CONSTRAINT certificates_serial_when_issued CHECK (
    CASE WHEN original_certificate_id IS NULL
         THEN (serial IS NOT NULL) = (issued_at IS NOT NULL) AND duplicate_no IS NULL
         ELSE serial IS NULL AND (duplicate_no IS NOT NULL) = (issued_at IS NOT NULL)
    END),
  CONSTRAINT certificates_duplicate_fields CHECK (
    (original_certificate_id IS NULL) = (duplicate_reason IS NULL)
    AND (duplicate_no IS NULL OR duplicate_no BETWEEN 1 AND 999)
    AND (duplicate_reason IS NULL OR char_length(duplicate_reason) BETWEEN 10 AND 1000)),
  CONSTRAINT certificates_decision CHECK (
    (decided_at IS NULL) = (decided_by IS NULL)
    AND (status <> 'rejected' OR (decided_by IS NOT NULL AND decision_note IS NOT NULL))
    AND (decision_note IS NULL OR char_length(decision_note) BETWEEN 10 AND 1000)),
  CONSTRAINT certificates_cancelled_fields CHECK (
    (status = 'cancelled') = (cancelled_at IS NOT NULL)
    AND (cancelled_at IS NULL) = (cancelled_by IS NULL)
    AND (cancelled_at IS NULL) = (cancel_reason IS NULL)
    AND (cancel_reason IS NULL OR char_length(cancel_reason) BETWEEN 10 AND 1000)),
  CONSTRAINT certificates_template_version_shape
    CHECK (template_version IS NULL OR template_version ~ '^v[0-9]{1,3}$'),
  CONSTRAINT certificates_sha256_length
    CHECK (content_sha256 IS NULL OR octet_length(content_sha256) = 32),
  CONSTRAINT certificates_pdf_ready CHECK (pdf_status <> 'ready' OR document_id IS NOT NULL),
  CONSTRAINT certificates_pdf_error_shape
    CHECK ((pdf_error IS NULL OR pdf_error ~ '^[a-z][a-z0-9_]{0,63}$')
           AND (pdf_status = 'failed') = (pdf_error IS NOT NULL)),
  CONSTRAINT certificates_version_positive CHECK (version >= 1),
  CONSTRAINT certificates_serial_unique UNIQUE (tenant_id, certificate_type, academic_year_id,
                                                serial_no),
  CONSTRAINT certificates_serial_text_unique UNIQUE (tenant_id, serial),
  CONSTRAINT certificates_duplicate_no_unique UNIQUE (tenant_id, original_certificate_id,
                                                      duplicate_no)
);
CREATE INDEX certificates_list_idx ON sis.certificates (tenant_id, requested_at DESC, id DESC);
CREATE INDEX certificates_student_idx ON sis.certificates (tenant_id, student_id);
CREATE INDEX certificates_register_idx
  ON sis.certificates (tenant_id, certificate_type, academic_year_id, serial_no)
  WHERE issued_at IS NOT NULL;
CREATE INDEX certificates_document_idx ON sis.certificates (tenant_id, document_id)
  WHERE document_id IS NOT NULL;
-- US-1102 AC4: one pending or issued original TC per student.
CREATE UNIQUE INDEX certificates_one_live_tc ON sis.certificates (tenant_id, student_id)
  WHERE certificate_type = 'transfer' AND original_certificate_id IS NULL
    AND status IN ('pending','issued');
-- One pending duplicate per original at a time.
CREATE UNIQUE INDEX certificates_one_pending_duplicate
  ON sis.certificates (tenant_id, original_certificate_id)
  WHERE original_certificate_id IS NOT NULL AND status = 'pending';

CREATE TRIGGER certificates_set_updated_at BEFORE UPDATE ON sis.certificates
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- FR-REG-005 / FR-CERT-003: what was requested never changes; an issued register entry keeps
-- its number, content and dates; the only transitions are the workflow's.
CREATE FUNCTION sis.tg_certificates_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (NEW.tenant_id, NEW.student_id, NEW.certificate_type, NEW.inputs,
      NEW.original_certificate_id, NEW.duplicate_reason, NEW.requested_by, NEW.requested_at)
     IS DISTINCT FROM
     (OLD.tenant_id, OLD.student_id, OLD.certificate_type, OLD.inputs,
      OLD.original_certificate_id, OLD.duplicate_reason, OLD.requested_by, OLD.requested_at)
  THEN
    RAISE EXCEPTION 'a certificate request never changes'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'certificates_frozen';
  END IF;
  IF OLD.status = 'pending' THEN
    RETURN NEW;
  END IF;
  IF OLD.status IN ('rejected', 'withdrawn') THEN
    IF NEW.status <> OLD.status OR (NEW.decided_by, NEW.decided_at, NEW.decision_note,
       NEW.serial, NEW.content, NEW.issued_at, NEW.document_id)
       IS DISTINCT FROM (OLD.decided_by, OLD.decided_at, OLD.decision_note,
       OLD.serial, OLD.content, OLD.issued_at, OLD.document_id) THEN
      RAISE EXCEPTION 'a closed certificate request never changes'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'certificates_frozen';
    END IF;
    RETURN NEW;
  END IF;
  -- issued or cancelled: the register entry is frozen.
  IF NOT (NEW.status = OLD.status OR (OLD.status = 'issued' AND NEW.status = 'cancelled'))
     OR (NEW.academic_year_id, NEW.serial_no, NEW.serial, NEW.content, NEW.content_sha256,
         NEW.template_version, NEW.duplicate_no, NEW.decided_by, NEW.decided_at,
         NEW.decision_note, NEW.issued_by, NEW.issued_at)
        IS DISTINCT FROM
        (OLD.academic_year_id, OLD.serial_no, OLD.serial, OLD.content, OLD.content_sha256,
         OLD.template_version, OLD.duplicate_no, OLD.decided_by, OLD.decided_at,
         OLD.decision_note, OLD.issued_by, OLD.issued_at)
     OR (OLD.status = 'cancelled' AND (NEW.cancelled_by, NEW.cancelled_at, NEW.cancel_reason)
         IS DISTINCT FROM (OLD.cancelled_by, OLD.cancelled_at, OLD.cancel_reason))
     OR (OLD.document_id IS NOT NULL AND NEW.document_id IS DISTINCT FROM OLD.document_id)
  THEN
    RAISE EXCEPTION 'an issued certificate is a register entry and never changes'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'certificates_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER certificates_frozen BEFORE UPDATE ON sis.certificates
  FOR EACH ROW EXECUTE FUNCTION sis.tg_certificates_frozen();

-- Register entries are removed only by the offboarding purge (ADR-0029).
CREATE FUNCTION sis.tg_certificates_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'certificate registers are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'certificates_append_only';
END;
$$;
CREATE TRIGGER certificates_append_only BEFORE DELETE ON sis.certificates
  FOR EACH ROW EXECUTE FUNCTION sis.tg_certificates_append_only();
CREATE TRIGGER certificates_no_truncate BEFORE TRUNCATE ON sis.certificates
  FOR EACH STATEMENT EXECUTE FUNCTION sis.tg_certificates_append_only();
CREATE TRIGGER certificate_counters_append_only BEFORE DELETE ON sis.certificate_counters
  FOR EACH ROW EXECUTE FUNCTION sis.tg_certificates_append_only();
CREATE TRIGGER certificate_counters_no_truncate BEFORE TRUNCATE ON sis.certificate_counters
  FOR EACH STATEMENT EXECUTE FUNCTION sis.tg_certificates_append_only();

-- Generated certificate PDFs are documents (FR-CERT-010).
ALTER TABLE kb.documents DROP CONSTRAINT documents_purpose_check;
ALTER TABLE kb.documents ADD CONSTRAINT documents_purpose_check CHECK (purpose IN
  ('evidence','register_scan','circular','policy','other','import_file','certificate'));
"""

GRANTS_SQL = r"""
-- Default privileges gave sos_app full DML. Registers are append-only: no DELETE/TRUNCATE; only
-- the workflow columns change (FR-REG-005).
REVOKE DELETE, UPDATE, TRUNCATE ON sis.certificates FROM sos_app;
GRANT UPDATE (status, academic_year_id, serial_no, serial, content, content_sha256,
              template_version, duplicate_no, decided_by, decided_at, decision_note, issued_by,
              issued_at, cancelled_by, cancelled_at, cancel_reason, document_id, pdf_status,
              pdf_error, updated_at, version)
  ON sis.certificates TO sos_app;
REVOKE DELETE, UPDATE, TRUNCATE ON sis.certificate_counters FROM sos_app;
GRANT UPDATE (last_no, updated_at) ON sis.certificate_counters TO sos_app;
REVOKE ALL ON sis.certificates, sis.certificate_counters FROM sos_readonly;
GRANT SELECT ON sis.certificates, sis.certificate_counters TO sos_readonly;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "certificate.read",
        "description": "See certificates, print them and download their PDFs",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "certificate.issue",
        "description": "Prepare and issue certificates (transfer certificates need approval)",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "certificate.approve",
        "description": ("Approve or reject transfer certificates and cancel issued certificates"),
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": False,
    },
    {
        "key": "register.read",
        "description": (
            "Print the TC, certificate issue and admission and withdrawal registers "
            "(recent MFA sign-in)"
        ),
        "sensitivity": "sensitive",
        "step_up": True,
        "is_platform": False,
    },
)

UPSERT = sa.text(
    """
    INSERT INTO core.permissions (key, description, sensitivity, step_up, is_platform)
    VALUES (:key, :description, :sensitivity, :step_up, :is_platform)
    ON CONFLICT (key) DO UPDATE SET
      description = EXCLUDED.description,
      sensitivity = EXCLUDED.sensitivity,
      step_up = EXCLUDED.step_up,
      is_platform = EXCLUDED.is_platform
    """
)


def _purge_sql(table: str) -> str:
    """ADR-0029: the offboarding purge deletes these rows as sos_purger (restrictive policy)."""
    return (
        f"GRANT SELECT, DELETE ON {table} TO sos_purger;\n"
        f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed());\n"
    )


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
        op.execute(_purge_sql(table))
    op.execute(GRANTS_SQL)
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)


def downgrade() -> None:
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
    op.execute("DROP TABLE IF EXISTS sis.certificates")
    op.execute("DROP TABLE IF EXISTS sis.certificate_counters")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_certificates_append_only()")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_certificates_frozen()")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_certificate_counters_never_down()")
    op.execute("ALTER TABLE kb.documents DROP CONSTRAINT documents_purpose_check")
    op.execute(
        "ALTER TABLE kb.documents ADD CONSTRAINT documents_purpose_check CHECK (purpose IN "
        "('evidence','register_scan','circular','policy','other','import_file')) NOT VALID"
    )
