"""Student record schema ``sis`` (docs/05 §5, §8-10; M1 US-301..303).

Tables: ``sis.students``, ``sis.enrollments``, ``sis.attribute_definitions`` (global rows with
``tenant_id`` NULL + tenant custom rows), ``sis.attribute_values`` (one row per student,
attribute, source and point in time), ``sis.student_profiles`` (C2-only projection for lists and
search), ``sis.guardians`` and ``sis.student_guardians``.

- Every tenant->tenant reference is a composite ``(tenant_id, x_id)`` FK, including
  enrolments -> ``core.sections`` / ``core.academic_years`` (ADR-0013 §7).
- RLS ENABLE + FORCE with ``tenant_isolation`` on every table; ``sis.attribute_definitions``
  carries the reviewed variant: ``attrdef_read`` (SELECT: global rows + own rows, and nothing at
  all without a tenant context) and ``attrdef_write`` (own rows only), so the app can never
  insert, update or delete a global row.
- C3 values live only in ``value_ciphertext`` (CHECK), history is immutable: a trigger allows
  only supersession (once), verification of the current row, and key-rotation re-encryption.
- Global attribute definitions are seeded from ``app/students/attributes.yaml`` with
  deterministic ids, before RLS is forced (the migrator owns the table, and FORCE applies to it).

Requirements: FR-STU-001..008, FR-STU-010..012, SEC-001, SEC-012, SEC-013, SEC-015.

Revision ID: 0008_sis_students
Revises: 0007_accept_invitations
Create Date: 2026-09-26
"""

from __future__ import annotations

import json
import uuid
from importlib import resources
from typing import Any

import sqlalchemy as sa
import yaml
from alembic import op

revision = "0008_sis_students"
down_revision = "0007_accept_invitations"
branch_labels = None
depends_on = None

# Deterministic ids for global attribute definitions (re-seeding is an idempotent upsert).
ATTRDEF_NAMESPACE = uuid.UUID("5f0c8a52-6f0e-4d8e-9d55-2b8f6b1d0a01")

TABLES_SQL = r"""
CREATE TABLE sis.students (
  id            uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL,
  status        text NOT NULL DEFAULT 'active',
  admission_no  text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  version       int NOT NULL DEFAULT 1,
  CONSTRAINT students_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT students_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT students_status_check CHECK (status IN ('provisional','active','left','graduated')),
  CONSTRAINT students_admission_no_length CHECK (char_length(admission_no) BETWEEN 1 AND 32),
  CONSTRAINT students_version_positive CHECK (version >= 1)
);
CREATE UNIQUE INDEX students_adm_no ON sis.students (tenant_id, admission_no)
  WHERE admission_no IS NOT NULL;

CREATE TABLE sis.enrollments (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  student_id        uuid NOT NULL,
  section_id        uuid NOT NULL,
  academic_year_id  uuid NOT NULL,
  roll_no           text,
  status            text NOT NULL DEFAULT 'active',
  started_on        date,
  ended_on          date,
  created_by        uuid,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1,
  CONSTRAINT enrollments_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT enrollments_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT enrollments_section_fk FOREIGN KEY (tenant_id, section_id)
    REFERENCES core.sections (tenant_id, id),
  CONSTRAINT enrollments_academic_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT enrollments_created_by_fk FOREIGN KEY (created_by)
    REFERENCES core.users (id) ON DELETE SET NULL,
  CONSTRAINT enrollments_status_check CHECK (status IN ('active','transferred','completed')),
  CONSTRAINT enrollments_roll_no_length CHECK (char_length(roll_no) BETWEEN 1 AND 16),
  CONSTRAINT enrollments_dates_ordered
    CHECK (ended_on IS NULL OR started_on IS NULL OR ended_on >= started_on),
  CONSTRAINT enrollments_version_positive CHECK (version >= 1)
);
CREATE UNIQUE INDEX one_active_enrollment ON sis.enrollments (tenant_id, student_id, academic_year_id)
  WHERE status = 'active';
CREATE INDEX enrollments_student_idx ON sis.enrollments (tenant_id, student_id);
CREATE INDEX enrollments_section_active_idx
  ON sis.enrollments (tenant_id, academic_year_id, section_id) WHERE status = 'active';

-- Attribute catalog: global defaults (tenant_id NULL) + tenant custom fields.
CREATE TABLE sis.attribute_definitions (
  id                uuid PRIMARY KEY,
  tenant_id         uuid,
  key               text NOT NULL,
  data_type         text NOT NULL,
  classification    text NOT NULL,
  is_identity       boolean NOT NULL DEFAULT false,
  validation        jsonb NOT NULL DEFAULT '{}'::jsonb,
  canonical_policy  jsonb NOT NULL,
  label_en          text NOT NULL,
  label_te          text NOT NULL,
  sort_order        int NOT NULL DEFAULT 1000,
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT attribute_definitions_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT attribute_definitions_key_format CHECK (key ~ '^[a-z][a-z0-9_]{1,63}$'),
  CONSTRAINT attribute_definitions_data_type_check
    CHECK (data_type IN ('text','date','enum','digits4')),
  CONSTRAINT attribute_definitions_classification_check
    CHECK (classification IN ('C1','C2','C3')),
  CONSTRAINT attribute_definitions_validation_object CHECK (jsonb_typeof(validation) = 'object'),
  CONSTRAINT attribute_definitions_policy_object
    CHECK (jsonb_typeof(canonical_policy) = 'object'),
  CONSTRAINT attribute_definitions_label_en_length CHECK (char_length(label_en) BETWEEN 1 AND 100),
  CONSTRAINT attribute_definitions_label_te_length CHECK (char_length(label_te) BETWEEN 1 AND 100)
);
CREATE UNIQUE INDEX attrdef_key ON sis.attribute_definitions
  (coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid), key);

-- The heart of "enter once": one row per (student, attribute, source, point in time).
CREATE TABLE sis.attribute_values (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  student_id            uuid NOT NULL,
  attribute_key         text NOT NULL,
  source                text NOT NULL,
  value_text            text,
  value_date            date,
  value_norm            text,
  value_ciphertext      bytea,
  value_blind_index     bytea,
  key_version           int,
  confidence            numeric(4,3),
  verification_status   text NOT NULL DEFAULT 'unverified',
  verified_by           uuid,
  verified_at           timestamptz,
  evidence_document_id  uuid,              -- composite FK to kb.documents added in 0009 (docs/05 §3.5)
  import_batch_id       uuid,              -- composite FK added with sis.import_batches (0012)
  change_request_id     uuid,              -- composite FK added with sis.change_requests (0014)
  recorded_by           uuid NOT NULL,
  recorded_at           timestamptz NOT NULL DEFAULT now(),
  superseded_by         uuid,
  CONSTRAINT attribute_values_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT attribute_values_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  -- Deferred: the previous row is marked superseded before its successor is inserted, because
  -- the partial unique index av_current admits only one current row per source.
  CONSTRAINT attribute_values_superseded_by_fk FOREIGN KEY (tenant_id, superseded_by)
    REFERENCES sis.attribute_values (tenant_id, id) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT attribute_values_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT attribute_values_verified_by_fk FOREIGN KEY (verified_by) REFERENCES core.users (id),
  CONSTRAINT attribute_values_source_check CHECK (source IN ('admission_register',
    'aadhaar_as_printed','udise_plus','board_registration','birth_certificate','parent_form',
    'tc_incoming','manual_entry')),
  CONSTRAINT attribute_values_verification_check
    CHECK (verification_status IN ('unverified','verified','rejected')),
  CONSTRAINT attribute_values_verified_fields
    CHECK ((verification_status = 'unverified') = (verified_at IS NULL)),
  CONSTRAINT attribute_values_key_format CHECK (attribute_key ~ '^[a-z][a-z0-9_]{1,63}$'),
  -- C3 values are ciphertext only (docs/05 §5): never also in value_text/value_norm/value_date.
  CONSTRAINT attribute_values_c3_exclusive CHECK (NOT (value_ciphertext IS NOT NULL AND
    (value_text IS NOT NULL OR value_norm IS NOT NULL OR value_date IS NOT NULL))),
  CONSTRAINT attribute_values_ciphertext_key_version
    CHECK ((value_ciphertext IS NULL) = (key_version IS NULL)),
  CONSTRAINT attribute_values_has_value CHECK (value_text IS NOT NULL OR value_date IS NOT NULL
    OR value_ciphertext IS NOT NULL),
  CONSTRAINT attribute_values_value_text_length CHECK (char_length(value_text) <= 1000),
  CONSTRAINT attribute_values_confidence_range CHECK (confidence BETWEEN 0 AND 1),
  CONSTRAINT attribute_values_not_self_superseded CHECK (superseded_by IS DISTINCT FROM id)
);
-- Exactly one current value per (student, attribute, source).
CREATE UNIQUE INDEX av_current ON sis.attribute_values (tenant_id, student_id, attribute_key, source)
  WHERE superseded_by IS NULL;
CREATE INDEX av_student ON sis.attribute_values (tenant_id, student_id);
CREATE INDEX av_superseded_by ON sis.attribute_values (tenant_id, superseded_by)
  WHERE superseded_by IS NOT NULL;

-- Denormalised projection for search and lists (C2 only), maintained in the same transaction.
CREATE TABLE sis.student_profiles (
  tenant_id           uuid NOT NULL,
  student_id          uuid NOT NULL,
  full_name           text,
  full_name_norm      text,
  full_name_translit  text,
  dob                 date,
  gender              text,
  father_name_norm    text,
  mother_name_norm    text,
  current_section_id  uuid,
  status              text,
  search_tsv          tsvector,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  -- (tenant_id, student_id): tenant first, and a probe with another school's id meets only the
  -- composite FK, never a global unique key.
  CONSTRAINT student_profiles_pkey PRIMARY KEY (tenant_id, student_id),
  CONSTRAINT student_profiles_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT student_profiles_section_fk FOREIGN KEY (tenant_id, current_section_id)
    REFERENCES core.sections (tenant_id, id)
);
CREATE INDEX sp_name_trgm ON sis.student_profiles USING gin (full_name_norm gin_trgm_ops);
CREATE INDEX sp_translit ON sis.student_profiles USING gin (full_name_translit gin_trgm_ops);
CREATE INDEX sp_tsv ON sis.student_profiles USING gin (search_tsv);
CREATE INDEX sp_section ON sis.student_profiles (tenant_id, current_section_id);
-- RLS adds the leakproof qual tenant_id = core.current_tenant(); pg_trgm operators are not
-- leakproof, so under RLS the planner reaches a school's rows through this btree index and
-- filters by similarity (2,000 rows per school; FR-STU-011 budget 300 ms).
CREATE INDEX sp_tenant_name ON sis.student_profiles (tenant_id, full_name_norm, student_id);

CREATE TABLE sis.guardians (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  full_name           text NOT NULL,
  full_name_norm      text,
  phone_ciphertext    bytea,
  phone_blind_index   bytea,
  address_ciphertext  bytea,
  key_version         int,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  version             int NOT NULL DEFAULT 1,
  CONSTRAINT guardians_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT guardians_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT guardians_full_name_length CHECK (char_length(full_name) BETWEEN 1 AND 120),
  CONSTRAINT guardians_key_version_present CHECK (
    (phone_ciphertext IS NULL AND address_ciphertext IS NULL) OR key_version IS NOT NULL),
  CONSTRAINT guardians_blind_index_with_phone
    CHECK ((phone_blind_index IS NULL) = (phone_ciphertext IS NULL)),
  CONSTRAINT guardians_version_positive CHECK (version >= 1)
);
CREATE INDEX guardians_phone_bidx ON sis.guardians (tenant_id, phone_blind_index)
  WHERE phone_blind_index IS NOT NULL;

CREATE TABLE sis.student_guardians (
  tenant_id     uuid NOT NULL,
  student_id    uuid NOT NULL,
  guardian_id   uuid NOT NULL,
  relationship  text NOT NULL,
  is_primary    boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_guardians_pkey PRIMARY KEY (tenant_id, student_id, guardian_id),
  CONSTRAINT student_guardians_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT student_guardians_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id),
  CONSTRAINT student_guardians_relationship_check
    CHECK (relationship IN ('father','mother','guardian'))
);
CREATE INDEX student_guardians_guardian_idx ON sis.student_guardians (tenant_id, guardian_id);
CREATE UNIQUE INDEX student_guardians_one_primary ON sis.student_guardians (tenant_id, student_id)
  WHERE is_primary;
"""

# History is immutable (FR-STU-005): a value row may only be superseded (once), have its
# verification recorded while it is current, or be re-encrypted under a newer key version.
IMMUTABILITY_SQL = r"""
CREATE FUNCTION sis.tg_attribute_values_immutable() RETURNS trigger
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
REVOKE ALL ON FUNCTION sis.tg_attribute_values_immutable() FROM PUBLIC;
CREATE TRIGGER attribute_values_immutable BEFORE UPDATE ON sis.attribute_values
  FOR EACH ROW EXECUTE FUNCTION sis.tg_attribute_values_immutable();
"""

TENANT_TABLES = (
    "sis.students",
    "sis.enrollments",
    "sis.attribute_values",
    "sis.student_profiles",
    "sis.guardians",
    "sis.student_guardians",
)
UPDATED_AT_TABLES = ("sis.students", "sis.enrollments", "sis.guardians")

ATTRDEF_POLICIES_SQL = """
ALTER TABLE sis.attribute_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sis.attribute_definitions FORCE ROW LEVEL SECURITY;
-- Reviewed variant (rls_allowlist.yaml): global rows are readable by every school, but only
-- inside a tenant context (fail closed), and never writable by the app.
CREATE POLICY attrdef_read ON sis.attribute_definitions FOR SELECT
  USING (core.current_tenant() IS NOT NULL
         AND (tenant_id IS NULL OR tenant_id = core.current_tenant()));
CREATE POLICY attrdef_write ON sis.attribute_definitions FOR ALL
  USING (tenant_id = core.current_tenant())
  WITH CHECK (tenant_id = core.current_tenant());
"""

GRANTS_SQL = """
-- History rows are appended and superseded, never deleted by the app (FR-STU-005). Retention
-- and offboarding deletion run as dedicated jobs with their own reviewed path.
REVOKE DELETE ON sis.attribute_values FROM sos_app;
REVOKE UPDATE ON sis.attribute_values FROM sos_app;
GRANT UPDATE (superseded_by, verification_status, verified_by, verified_at,
              value_ciphertext, value_blind_index, key_version) ON sis.attribute_values TO sos_app;
"""

DROP_ORDER = (
    "sis.student_guardians",
    "sis.guardians",
    "sis.student_profiles",
    "sis.attribute_values",
    "sis.attribute_definitions",
    "sis.enrollments",
    "sis.students",
)


def _attribute_rows() -> list[dict[str, Any]]:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    rows: list[dict[str, Any]] = []
    for key, spec in raw["attributes"].items():
        rows.append(
            {
                "id": uuid.uuid5(ATTRDEF_NAMESPACE, f"sis.attribute_definitions:{key}"),
                "key": key,
                "data_type": spec["data_type"],
                "classification": spec["classification"],
                "is_identity": bool(spec.get("is_identity", False)),
                "validation": json.dumps(spec.get("validation", {}), sort_keys=True),
                "canonical_policy": json.dumps(spec["canonical_policy"], sort_keys=True),
                "label_en": spec["label_en"],
                "label_te": spec["label_te"],
                "sort_order": int(spec.get("sort_order", 1000)),
            }
        )
    return rows


SEED = sa.text(
    """
    INSERT INTO sis.attribute_definitions
      (id, tenant_id, key, data_type, classification, is_identity, validation, canonical_policy,
       label_en, label_te, sort_order)
    VALUES (:id, NULL, :key, :data_type, :classification, :is_identity,
            CAST(:validation AS jsonb), CAST(:canonical_policy AS jsonb), :label_en, :label_te,
            :sort_order)
    ON CONFLICT (id) DO UPDATE SET
      data_type = EXCLUDED.data_type, classification = EXCLUDED.classification,
      is_identity = EXCLUDED.is_identity, validation = EXCLUDED.validation,
      canonical_policy = EXCLUDED.canonical_policy, label_en = EXCLUDED.label_en,
      label_te = EXCLUDED.label_te, sort_order = EXCLUDED.sort_order
    """
)


def upgrade() -> None:
    op.execute(TABLES_SQL)
    op.execute(IMMUTABILITY_SQL)
    for table in UPDATED_AT_TABLES:
        name = table.split(".", 1)[1]
        op.execute(
            f"CREATE TRIGGER {name}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at()"
        )
    # Seed global definitions BEFORE forcing RLS: FORCE applies to the owning role as well.
    bind = op.get_bind()
    for row in _attribute_rows():
        bind.execute(SEED, row)
    op.execute(ATTRDEF_POLICIES_SQL)
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
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_attribute_values_immutable()")
