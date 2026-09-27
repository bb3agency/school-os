"""DEK rotation: history rows and decided change requests can be re-encrypted (SEC-012; docs/05
§9, 07 §8).

Rotation adds a key version and re-encrypts every C3 value to it, so that older versions can be
retired. Two triggers blocked that for rows that must otherwise never change:

- ``sis.tg_attribute_values_immutable()`` refused *any* update of a superseded value, although
  0008 documents re-encryption to a newer key as allowed. Superseded (history) rows now accept an
  update that changes only ``value_ciphertext``, ``value_blind_index`` and ``key_version``, and
  only to a newer key version (the existing rule); anything else still fails with
  ``attribute_values_immutable``. Rules for current rows are unchanged.
- ``sis.tg_change_requests_frozen()`` refused every update of a decided request. A decided
  request now accepts an update that changes only ``new_value_ciphertext``,
  ``old_value_ciphertext``, ``key_version`` (to a newer version) and ``updated_at`` (set by the
  ``change_requests_set_updated_at`` trigger); anything else still fails with
  ``change_requests_frozen``.

The comparison uses ``to_jsonb(row)`` minus the re-encryption columns, so a column added later is
frozen automatically. Both functions stay ``SECURITY INVOKER`` (RLS applies; no definer function
is added) and keep their owner, triggers and grants (``CREATE OR REPLACE``). No data changes.

Downgrade restores the 0008/0014 function bodies. Rows already re-encrypted stay valid (their
format is unchanged); only further re-encryption of history rows is refused again.

Revision ID: 0026_dek_rotation
Revises: 0023_api_gaps
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0026_dek_rotation"
down_revision = "0023_api_gaps"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION sis.tg_attribute_values_immutable() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  -- Re-encryption (key rotation, SEC-012) must move to a newer key version.
  IF (NEW.value_ciphertext, NEW.value_blind_index, NEW.key_version)
       IS DISTINCT FROM (OLD.value_ciphertext, OLD.value_blind_index, OLD.key_version)
     AND NOT (OLD.value_ciphertext IS NOT NULL AND NEW.value_ciphertext IS NOT NULL
              AND NEW.key_version > OLD.key_version) THEN
    RAISE EXCEPTION 'attribute value ciphertext changes only by re-encryption to a newer key'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'attribute_values_immutable';
  END IF;
  -- History rows change only by re-encryption.
  IF OLD.superseded_by IS NOT NULL
     AND (to_jsonb(NEW) - 'value_ciphertext' - 'value_blind_index' - 'key_version')
         IS DISTINCT FROM
         (to_jsonb(OLD) - 'value_ciphertext' - 'value_blind_index' - 'key_version') THEN
    RAISE EXCEPTION 'superseded attribute values are immutable'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'attribute_values_immutable';
  END IF;
  IF (NEW.id, NEW.tenant_id, NEW.student_id, NEW.attribute_key, NEW.source, NEW.value_text,
      NEW.value_date, NEW.value_norm, NEW.confidence, NEW.evidence_document_id,
      NEW.import_batch_id, NEW.change_request_id, NEW.recorded_by, NEW.recorded_at)
     IS DISTINCT FROM
     (OLD.id, OLD.tenant_id, OLD.student_id, OLD.attribute_key, OLD.source, OLD.value_text,
      OLD.value_date, OLD.value_norm, OLD.confidence, OLD.evidence_document_id,
      OLD.import_batch_id, OLD.change_request_id, OLD.recorded_by, OLD.recorded_at) THEN
    RAISE EXCEPTION 'attribute values are immutable; record a new value instead'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'attribute_values_immutable';
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION sis.tg_change_requests_frozen() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  -- Re-encryption (key rotation, SEC-012) must move to a newer key version.
  IF (NEW.new_value_ciphertext, NEW.old_value_ciphertext, NEW.key_version)
       IS DISTINCT FROM (OLD.new_value_ciphertext, OLD.old_value_ciphertext, OLD.key_version)
     AND NOT (OLD.key_version IS NOT NULL AND NEW.key_version > OLD.key_version) THEN
    RAISE EXCEPTION 'change request ciphertext changes only by re-encryption to a newer key'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'change_requests_frozen';
  END IF;
  -- A decided request is a record: it changes only by re-encryption.
  IF OLD.status <> 'pending'
     AND (to_jsonb(NEW) - 'new_value_ciphertext' - 'old_value_ciphertext' - 'key_version'
          - 'updated_at')
         IS DISTINCT FROM
         (to_jsonb(OLD) - 'new_value_ciphertext' - 'old_value_ciphertext' - 'key_version'
          - 'updated_at') THEN
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
  RETURN NEW;
END
$$;
"""

# The 0008 / 0014 bodies, verbatim.
DOWNGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION sis.tg_attribute_values_immutable() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.superseded_by IS NOT NULL THEN
    RAISE EXCEPTION 'superseded attribute values are immutable'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'attribute_values_immutable';
  END IF;
  IF (NEW.id, NEW.tenant_id, NEW.student_id, NEW.attribute_key, NEW.source, NEW.value_text,
      NEW.value_date, NEW.value_norm, NEW.confidence, NEW.evidence_document_id,
      NEW.import_batch_id, NEW.change_request_id, NEW.recorded_by, NEW.recorded_at)
     IS DISTINCT FROM
     (OLD.id, OLD.tenant_id, OLD.student_id, OLD.attribute_key, OLD.source, OLD.value_text,
      OLD.value_date, OLD.value_norm, OLD.confidence, OLD.evidence_document_id,
      OLD.import_batch_id, OLD.change_request_id, OLD.recorded_by, OLD.recorded_at) THEN
    RAISE EXCEPTION 'attribute values are immutable; record a new value instead'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'attribute_values_immutable';
  END IF;
  -- Re-encryption (key rotation) must move to a newer key version.
  IF (NEW.value_ciphertext, NEW.value_blind_index, NEW.key_version)
       IS DISTINCT FROM (OLD.value_ciphertext, OLD.value_blind_index, OLD.key_version)
     AND NOT (OLD.value_ciphertext IS NOT NULL AND NEW.value_ciphertext IS NOT NULL
              AND NEW.key_version > OLD.key_version) THEN
    RAISE EXCEPTION 'attribute value ciphertext changes only by re-encryption to a newer key'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'attribute_values_immutable';
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION sis.tg_change_requests_frozen() RETURNS trigger
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
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
