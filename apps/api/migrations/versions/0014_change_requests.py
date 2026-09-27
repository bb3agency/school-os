"""Identity-field change requests with maker-checker (US-601; FR-CR-001..005; SEC-014; BR-04;
ADR-0010; docs/05 §5 ``sis.change_requests``).

- One row per requested correction of an identity attribute of one student and one source
  (``target_source``, default the admission register, the legal anchor BR-01).
- ``old_value_*`` is a snapshot of the value being corrected (``old_value_id``); ``new_value_*``
  the requested value. C3 attributes are stored only as ciphertext (tenant DEK, AAD
  ``tenant|sis.change_requests|<column>|<id>``), never in the text/date columns (CHECK).
- ``reason`` (10..1000 characters) and ``evidence_document_id`` (composite FK to
  ``kb.documents``) are mandatory (FR-CR-001). A rejection needs a ``decision_note`` of the same
  length (FR-CR-004).
- ``requested_by`` / ``decided_by`` are memberships of the same school (composite FKs) and
  ``CHECK (decided_by IS NULL OR decided_by <> requested_by)`` blocks self-approval in the
  database as well as in the service (SEC-014).
- One pending request per (student, attribute, target source) (partial unique index).
- A decided row is frozen by a trigger; the app may update only the workflow columns (column
  grants) and may never delete a request. Pending requests expire at ``expires_at``.
- ``sis.attribute_values.change_request_id`` gets its composite FK to this table (docs/05 §5).
  The FK is added ``NOT VALID`` and then validated; after a downgrade has dropped the requests
  (lossy, see below) the stale ids remain and the FK stays ``NOT VALID`` (still enforced for new
  rows) instead of failing the upgrade.

Downgrade drops the table and the FK: the requests are lost (the values they produced stay in the
student history with their ``change_request_id``).

Revision ID: 0014_change_requests
Revises: 0011_breakglass
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0014_change_requests"
down_revision = "0011_breakglass"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE TABLE sis.change_requests (
  id                     uuid PRIMARY KEY,
  tenant_id              uuid NOT NULL,
  student_id             uuid NOT NULL,
  attribute_key          text NOT NULL,
  target_source          text NOT NULL DEFAULT 'admission_register',
  old_value_id           uuid,
  old_value_text         text,
  old_value_date         date,
  old_value_ciphertext   bytea,
  new_value_text         text,
  new_value_date         date,
  new_value_ciphertext   bytea,
  key_version            int,
  reason                 text NOT NULL,
  evidence_document_id   uuid NOT NULL,
  status                 text NOT NULL DEFAULT 'pending',
  requested_by           uuid NOT NULL,
  requested_at           timestamptz NOT NULL DEFAULT now(),
  decided_by             uuid,
  decided_at             timestamptz,
  decision_note          text,
  applied_value_id       uuid,
  expires_at             timestamptz NOT NULL,
  updated_at             timestamptz NOT NULL DEFAULT now(),
  version                int NOT NULL DEFAULT 1,
  CONSTRAINT change_requests_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT change_requests_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT change_requests_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT change_requests_old_value_fk FOREIGN KEY (tenant_id, old_value_id)
    REFERENCES sis.attribute_values (tenant_id, id),
  CONSTRAINT change_requests_applied_value_fk FOREIGN KEY (tenant_id, applied_value_id)
    REFERENCES sis.attribute_values (tenant_id, id),
  CONSTRAINT change_requests_evidence_fk FOREIGN KEY (tenant_id, evidence_document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT change_requests_requested_by_fk FOREIGN KEY (tenant_id, requested_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT change_requests_decided_by_fk FOREIGN KEY (tenant_id, decided_by)
    REFERENCES core.memberships (tenant_id, id),
  -- SEC-014 / FR-CR-002: the checker is never the maker, even for direct SQL.
  CONSTRAINT change_requests_no_self_approval
    CHECK (decided_by IS NULL OR decided_by <> requested_by),
  CONSTRAINT change_requests_status_check
    CHECK (status IN ('pending','approved','rejected','expired','cancelled')),
  CONSTRAINT change_requests_key_format CHECK (attribute_key ~ '^[a-z][a-z0-9_]{1,63}$'),
  CONSTRAINT change_requests_source_check CHECK (target_source IN ('admission_register',
    'aadhaar_as_printed','udise_plus','board_registration','birth_certificate','parent_form',
    'tc_incoming','manual_entry')),
  CONSTRAINT change_requests_reason_length
    CHECK (char_length(btrim(reason)) BETWEEN 10 AND 1000),
  CONSTRAINT change_requests_note_length
    CHECK (decision_note IS NULL OR char_length(btrim(decision_note)) BETWEEN 10 AND 1000),
  -- FR-CR-004: a rejection always says why.
  CONSTRAINT change_requests_reject_needs_note
    CHECK (status <> 'rejected' OR decision_note IS NOT NULL),
  -- Exactly one representation of the requested value; C3 only as ciphertext.
  CONSTRAINT change_requests_one_new_value CHECK (
    (new_value_text IS NOT NULL)::int + (new_value_date IS NOT NULL)::int
    + (new_value_ciphertext IS NOT NULL)::int = 1),
  CONSTRAINT change_requests_one_old_value CHECK (
    (old_value_text IS NOT NULL)::int + (old_value_date IS NOT NULL)::int
    + (old_value_ciphertext IS NOT NULL)::int <= 1),
  CONSTRAINT change_requests_c3_exclusive CHECK (
    (new_value_ciphertext IS NULL OR (old_value_text IS NULL AND old_value_date IS NULL))
    AND (old_value_ciphertext IS NULL OR (new_value_text IS NULL AND new_value_date IS NULL))),
  CONSTRAINT change_requests_ciphertext_key_version CHECK (
    (new_value_ciphertext IS NULL AND old_value_ciphertext IS NULL) = (key_version IS NULL)),
  CONSTRAINT change_requests_old_snapshot_has_id
    CHECK (old_value_id IS NOT NULL OR (old_value_text IS NULL AND old_value_date IS NULL
                                        AND old_value_ciphertext IS NULL)),
  CONSTRAINT change_requests_value_text_length
    CHECK (char_length(new_value_text) <= 1000 AND char_length(old_value_text) <= 1000),
  -- Decision bookkeeping: approvals/rejections name the checker; cancel/expiry do not.
  CONSTRAINT change_requests_decided_at_when_closed
    CHECK ((status = 'pending') = (decided_at IS NULL)),
  CONSTRAINT change_requests_decided_by_when_decided
    CHECK ((status IN ('approved','rejected')) = (decided_by IS NOT NULL)),
  CONSTRAINT change_requests_applied_when_approved
    CHECK ((status = 'approved') = (applied_value_id IS NOT NULL)),
  CONSTRAINT change_requests_expiry_after_request CHECK (expires_at > requested_at),
  CONSTRAINT change_requests_version_positive CHECK (version >= 1)
);
-- One open request per student, attribute and source (FR-CR-001).
CREATE UNIQUE INDEX change_requests_one_pending
  ON sis.change_requests (tenant_id, student_id, attribute_key, target_source)
  WHERE status = 'pending';
CREATE INDEX change_requests_tenant_list
  ON sis.change_requests (tenant_id, requested_at DESC, id DESC);
CREATE INDEX change_requests_tenant_status
  ON sis.change_requests (tenant_id, status, requested_at DESC, id DESC);
CREATE INDEX change_requests_student ON sis.change_requests (tenant_id, student_id);
CREATE INDEX change_requests_pending_expiry
  ON sis.change_requests (tenant_id, expires_at) WHERE status = 'pending';

CREATE TRIGGER change_requests_set_updated_at BEFORE UPDATE ON sis.change_requests
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- A decided request is a record: nothing about it changes afterwards. While pending, only the
-- workflow columns change (and ciphertext re-encryption under a newer key version).
CREATE FUNCTION sis.tg_change_requests_frozen() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.status <> 'pending' THEN
    RAISE EXCEPTION 'decided change requests are immutable'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'change_requests_frozen';
  END IF;
  IF (NEW.id, NEW.tenant_id, NEW.student_id, NEW.attribute_key, NEW.target_source,
      NEW.old_value_id, NEW.old_value_text, NEW.old_value_date, NEW.new_value_text,
      NEW.new_value_date, NEW.reason, NEW.evidence_document_id, NEW.requested_by,
      NEW.requested_at, NEW.expires_at)
     IS DISTINCT FROM
     (OLD.id, OLD.tenant_id, OLD.student_id, OLD.attribute_key, OLD.target_source,
      OLD.old_value_id, OLD.old_value_text, OLD.old_value_date, OLD.new_value_text,
      OLD.new_value_date, OLD.reason, OLD.evidence_document_id, OLD.requested_by,
      OLD.requested_at, OLD.expires_at) THEN
    RAISE EXCEPTION 'a change request keeps what was requested; submit a new one instead'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'change_requests_frozen';
  END IF;
  IF (NEW.new_value_ciphertext, NEW.old_value_ciphertext, NEW.key_version)
       IS DISTINCT FROM (OLD.new_value_ciphertext, OLD.old_value_ciphertext, OLD.key_version)
     AND NOT (OLD.key_version IS NOT NULL AND NEW.key_version > OLD.key_version) THEN
    RAISE EXCEPTION 'change request ciphertext changes only by re-encryption to a newer key'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'change_requests_frozen';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION sis.tg_change_requests_frozen() FROM PUBLIC;
CREATE TRIGGER change_requests_frozen BEFORE UPDATE ON sis.change_requests
  FOR EACH ROW EXECUTE FUNCTION sis.tg_change_requests_frozen();

ALTER TABLE sis.change_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE sis.change_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON sis.change_requests
  USING (tenant_id = core.current_tenant())
  WITH CHECK (tenant_id = core.current_tenant());

-- Requests are part of the school's record of who changed identity data: never deleted by the
-- app, and only the workflow columns are updatable.
REVOKE DELETE, UPDATE ON sis.change_requests FROM sos_app;
GRANT UPDATE (status, decided_by, decided_at, decision_note, applied_value_id, version,
              updated_at, new_value_ciphertext, old_value_ciphertext, key_version)
  ON sis.change_requests TO sos_app;

-- docs/05 §5: sis.attribute_values.change_request_id -> sis.change_requests (composite).
ALTER TABLE sis.attribute_values ADD CONSTRAINT attribute_values_change_request_fk
  FOREIGN KEY (tenant_id, change_request_id) REFERENCES sis.change_requests (tenant_id, id)
  NOT VALID;
DO $$
BEGIN
  ALTER TABLE sis.attribute_values VALIDATE CONSTRAINT attribute_values_change_request_fk;
EXCEPTION WHEN foreign_key_violation THEN
  -- Only after a lossy downgrade: old values keep ids of dropped requests (see docstring).
  RAISE NOTICE 'attribute_values_change_request_fk left NOT VALID (ids of dropped requests)';
END
$$;
"""

DOWNGRADE_SQL = r"""
ALTER TABLE sis.attribute_values DROP CONSTRAINT IF EXISTS attribute_values_change_request_fk;
DROP TABLE IF EXISTS sis.change_requests;
DROP FUNCTION IF EXISTS sis.tg_change_requests_frozen();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
