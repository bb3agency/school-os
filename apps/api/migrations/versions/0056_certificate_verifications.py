"""R2-1A certificates: TC formats and public certificate verification (ADR-0045, ADR-0047;
FR-CERT-015, FR-CERT-019..024; docs/05 §15.4).

``sis.certificates`` gains ``format_key`` and ``format_version``: the TC format a request was
prepared in (``generic-v1`` or a versioned file in ``app/certificates/formats/``; NULL for the
other types and for certificates issued before R2, which read as ``generic-v1``). Both are part of
what was requested, so ``certificates_frozen`` now refuses any change to them after the insert.

``sis.certificate_verifications`` (tenant-owned, C1): one row per verification code of a
certificate. ``code`` is the 16-character code printed on the paper (Crockford base32, 80 random
bits; not secret once printed, never derived from the serial, student or school); ``handle_id``
is the ``cert_verify`` public handle keyed by the code's SHA-256 (ADR-0047), through which the
public page finds the school. ``created_reason`` ``issued`` | ``duplicate`` | ``added_later``
(with the principal's note for ``added_later``); ``withdrawn_*`` when the principal withdrew the
code (10..500-character reason; the handle is revoked in the same transaction); ``check_count``
and ``last_checked_at`` count successful public lookups. At most one live (not withdrawn) code
per certificate. ``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FKs to the certificate, the handle and the members,
``offboarding_purge`` for ``sos_purger`` (ADR-0029). ``sos_app`` may SELECT and INSERT and UPDATE
only the withdrawal and counter columns; a trigger keeps identity columns fixed, a withdrawal
final and the counters moving forward, and refuses DELETE except in the offboarding purge.
``sos_readonly`` may read every column except ``code`` (it opens the printed content publicly).

Expand-only (invariant 12). Downgrade drops the verification table (lossy: printed codes stop
working, which is the point of a rollback; their handles stay in ``core.public_handles`` and are
revoked here so nothing resolves to a missing row) and the two format columns (lossy: the format of
TCs issued in the AP layout; their frozen content keeps every printed line), and restores the 0033
body of ``certificates_frozen``.

Revision ID: 0056_certificate_verifications
Revises: 0055_public_handles
Create Date: 2026-10-17
"""

from __future__ import annotations

from alembic import op

revision = "0056_certificate_verifications"
down_revision = "0055_public_handles"
branch_labels = None
depends_on = None

FORMAT_SQL = r"""
ALTER TABLE sis.certificates ADD COLUMN format_key text;
ALTER TABLE sis.certificates ADD COLUMN format_version int;
ALTER TABLE sis.certificates ADD CONSTRAINT certificates_format_key_shape
  CHECK (format_key IS NULL OR format_key ~ '^[a-z][a-z0-9-]{1,39}$');
ALTER TABLE sis.certificates ADD CONSTRAINT certificates_format_pair
  CHECK ((format_key IS NULL) = (format_version IS NULL)
         AND (format_version IS NULL OR format_version BETWEEN 1 AND 999));
-- Only a transfer certificate has a format choice (FR-CERT-015).
ALTER TABLE sis.certificates ADD CONSTRAINT certificates_format_only_transfer
  CHECK (format_key IS NULL OR certificate_type = 'transfer');
"""

# 0033's body with format_key and format_version added to what a request never changes.
FROZEN_V2 = r"""
CREATE OR REPLACE FUNCTION sis.tg_certificates_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (NEW.tenant_id, NEW.student_id, NEW.certificate_type, NEW.inputs,
      NEW.original_certificate_id, NEW.duplicate_reason, NEW.requested_by, NEW.requested_at,
      NEW.format_key, NEW.format_version)
     IS DISTINCT FROM
     (OLD.tenant_id, OLD.student_id, OLD.certificate_type, OLD.inputs,
      OLD.original_certificate_id, OLD.duplicate_reason, OLD.requested_by, OLD.requested_at,
      OLD.format_key, OLD.format_version)
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
"""

# 0033_certificates' body, restored on downgrade.
FROZEN_V1 = FROZEN_V2.replace(
    ",\n      NEW.format_key, NEW.format_version)",
    ")",
).replace(
    ",\n      OLD.format_key, OLD.format_version)",
    ")",
)

TABLE_SQL = r"""
CREATE TABLE sis.certificate_verifications (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  certificate_id   uuid NOT NULL,
  code             text NOT NULL,
  handle_id        uuid NOT NULL,
  created_reason   text NOT NULL,
  created_note     text,
  created_by       uuid NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  withdrawn_at     timestamptz,
  withdrawn_by     uuid,
  withdraw_reason  text,
  check_count      int NOT NULL DEFAULT 0,
  last_checked_at  timestamptz,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          int NOT NULL DEFAULT 1,
  CONSTRAINT certificate_verifications_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT certificate_verifications_code_key UNIQUE (tenant_id, code),
  CONSTRAINT certificate_verifications_handle_key UNIQUE (tenant_id, handle_id),
  CONSTRAINT certificate_verifications_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT certificate_verifications_certificate_fk FOREIGN KEY (tenant_id, certificate_id)
    REFERENCES sis.certificates (tenant_id, id),
  CONSTRAINT certificate_verifications_handle_fk FOREIGN KEY (tenant_id, handle_id)
    REFERENCES core.public_handles (tenant_id, id),
  CONSTRAINT certificate_verifications_created_by_fk FOREIGN KEY (tenant_id, created_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT certificate_verifications_withdrawn_by_fk FOREIGN KEY (tenant_id, withdrawn_by)
    REFERENCES core.memberships (tenant_id, id),
  -- 16 characters of Crockford base32 (0-9, A-Z without I L O U): 80 bits (FR-CERT-019).
  CONSTRAINT certificate_verifications_code_shape CHECK (code ~ '^[0-9A-HJKMNP-TV-Z]{16}$'),
  CONSTRAINT certificate_verifications_reason_check
    CHECK (created_reason IN ('issued', 'duplicate', 'added_later')),
  -- A code added later carries the principal's reason (FR-CERT-023a).
  CONSTRAINT certificate_verifications_note CHECK (
    (created_reason = 'added_later') = (created_note IS NOT NULL)
    AND (created_note IS NULL OR char_length(created_note) BETWEEN 10 AND 500)),
  CONSTRAINT certificate_verifications_withdrawal CHECK (
    (withdrawn_at IS NULL) = (withdrawn_by IS NULL)
    AND (withdrawn_at IS NULL) = (withdraw_reason IS NULL)
    AND (withdraw_reason IS NULL OR char_length(withdraw_reason) BETWEEN 10 AND 500)),
  CONSTRAINT certificate_verifications_checks CHECK (
    check_count >= 0 AND (last_checked_at IS NULL) = (check_count = 0))
);
-- At most one live code per certificate (a duplicate is its own certificate row).
CREATE UNIQUE INDEX certificate_verifications_one_live
  ON sis.certificate_verifications (tenant_id, certificate_id) WHERE withdrawn_at IS NULL;
CREATE INDEX certificate_verifications_certificate_idx
  ON sis.certificate_verifications (tenant_id, certificate_id, created_at);

CREATE TRIGGER certificate_verifications_set_updated_at
  BEFORE UPDATE ON sis.certificate_verifications
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- Identity columns fixed, a withdrawal final, counters only forward (next to the column grants).
CREATE FUNCTION sis.tg_certificate_verifications_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF core.tenant_purge_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'certificate verifications are kept with the register'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'certificate_verifications_append_only';
  END IF;
  IF (NEW.id, NEW.tenant_id, NEW.certificate_id, NEW.code, NEW.handle_id, NEW.created_reason,
      NEW.created_note, NEW.created_by, NEW.created_at)
     IS DISTINCT FROM
     (OLD.id, OLD.tenant_id, OLD.certificate_id, OLD.code, OLD.handle_id, OLD.created_reason,
      OLD.created_note, OLD.created_by, OLD.created_at) THEN
    RAISE EXCEPTION 'a verification code never changes'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'certificate_verifications_fixed';
  END IF;
  IF OLD.withdrawn_at IS NOT NULL AND (NEW.withdrawn_at, NEW.withdrawn_by, NEW.withdraw_reason)
     IS DISTINCT FROM (OLD.withdrawn_at, OLD.withdrawn_by, OLD.withdraw_reason) THEN
    RAISE EXCEPTION 'a withdrawn verification code stays withdrawn'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'certificate_verifications_fixed';
  END IF;
  IF NEW.check_count < OLD.check_count
     OR (OLD.last_checked_at IS NOT NULL AND NEW.last_checked_at < OLD.last_checked_at) THEN
    RAISE EXCEPTION 'verification check counters only move forward'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'certificate_verifications_fixed';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION sis.tg_certificate_verifications_guard() FROM PUBLIC;
CREATE TRIGGER certificate_verifications_guard
  BEFORE UPDATE OR DELETE ON sis.certificate_verifications
  FOR EACH ROW EXECUTE FUNCTION sis.tg_certificate_verifications_guard();
CREATE TRIGGER certificate_verifications_no_truncate
  BEFORE TRUNCATE ON sis.certificate_verifications
  FOR EACH STATEMENT EXECUTE FUNCTION sis.tg_certificates_append_only();
"""

POLICIES_SQL = r"""
ALTER TABLE sis.certificate_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE sis.certificate_verifications FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON sis.certificate_verifications
  USING (tenant_id = core.current_tenant()) WITH CHECK (tenant_id = core.current_tenant());
CREATE POLICY offboarding_purge ON sis.certificate_verifications AS RESTRICTIVE FOR ALL
  TO sos_purger USING (core.tenant_purge_allowed());
"""

GRANTS_SQL = r"""
REVOKE ALL ON sis.certificate_verifications FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON sis.certificate_verifications
  FROM sos_app;
GRANT SELECT, INSERT ON sis.certificate_verifications TO sos_app;
GRANT UPDATE (withdrawn_at, withdrawn_by, withdraw_reason, check_count, last_checked_at,
              updated_at, version)
  ON sis.certificate_verifications TO sos_app;
REVOKE ALL ON sis.certificate_verifications FROM sos_readonly;
GRANT SELECT (id, tenant_id, certificate_id, handle_id, created_reason, created_note, created_by,
              created_at, withdrawn_at, withdrawn_by, withdraw_reason, check_count,
              last_checked_at, updated_at, version)
  ON sis.certificate_verifications TO sos_readonly;
GRANT SELECT, DELETE ON sis.certificate_verifications TO sos_purger;
"""

# Downgrade: the codes stop working with the table. Their handles are revoked first (as the
# owner, across schools: FORCE is lifted for this one UPDATE and restored in the same
# transaction), so the resolver never answers for a code whose row is gone.
UNFORCE_HANDLES = "ALTER TABLE core.public_handles NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner revokes every school's certificate handles on downgrade; FORCE is restored in the same transaction
REFORCE_HANDLES = "ALTER TABLE core.public_handles FORCE ROW LEVEL SECURITY"
REVOKE_HANDLES = r"""
UPDATE core.public_handles SET revoked_at = now(), revoked_reason = 'downgrade'
 WHERE kind = 'cert_verify' AND revoked_at IS NULL
"""


def upgrade() -> None:
    op.execute(FORMAT_SQL)
    op.execute(FROZEN_V2)
    op.execute(TABLE_SQL)
    op.execute(POLICIES_SQL)
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    op.execute(UNFORCE_HANDLES)
    op.execute(REVOKE_HANDLES)
    op.execute(REFORCE_HANDLES)
    op.execute("DROP TABLE IF EXISTS sis.certificate_verifications")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_certificate_verifications_guard()")
    op.execute(FROZEN_V1)
    op.execute("ALTER TABLE sis.certificates DROP CONSTRAINT certificates_format_only_transfer")
    op.execute("ALTER TABLE sis.certificates DROP CONSTRAINT certificates_format_pair")
    op.execute("ALTER TABLE sis.certificates DROP CONSTRAINT certificates_format_key_shape")
    op.execute("ALTER TABLE sis.certificates DROP COLUMN format_version")
    op.execute("ALTER TABLE sis.certificates DROP COLUMN format_key")
