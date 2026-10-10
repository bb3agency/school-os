"""APAAR consent register and the 11-digit UDISE+ PEN (ADR-0039; FR-APC-001..006, FR-STU-017..019,
PRV-021).

Tenant tables in schema ``sis`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs; offboarding ``offboarding_purge`` policy and
``sos_purger`` grants like 0032/0035, ADR-0029):

- ``sis.apaar_consents``: the consent register, **append-only**. One row per decision recorded
  for a student: ``seq`` (1, 2, 3 ... per student; ``UNIQUE (tenant_id, student_id, seq)`` makes
  a concurrent second write fail instead of forking the history), ``status`` (``given``,
  ``refused``, ``pending``, ``withdrawn``), who decided (``relationship`` father / mother /
  guardian and, when the guardian is on the record, ``guardian_id``), the date on the form
  (``decided_on``), the form's language, the signed form as an evidence document (composite FK
  to ``kb.documents``; required for ``given``), a short note, who recorded it and when. The
  current state of a student is the row with the highest ``seq``; no row means pending. The app
  role may only SELECT and INSERT: nothing is ever overwritten (history kept). The guardian
  link is ``ON DELETE SET NULL (guardian_id)``: a guardian record deleted for data minimisation
  (unlinked from every student) leaves the decision and its relationship in place.
- ``sis.apaar_consent_settings``: one row per school, the language of the printed parent form
  (``en`` or ``te``; owner decision D9 of 2026-10-10: parent-facing material may be Telugu or
  English per school, independently of ``SOS_TELUGU_ENABLED``, which keeps the staff UI
  English). The app may update only ``form_language``, ``updated_by``, ``updated_at`` and
  ``version``.

Also:

- ``sis.attribute_definitions`` row ``udise_pen`` re-seeded from ``app/students/attributes.yaml``:
  11 digits (spaces and hyphens removed first, error ``digits11_required``), sources
  ``udise_plus``, ``tc_incoming`` (the PEN on the previous school's transfer certificate) and
  ``manual_entry``. Values recorded before stay as they are (the DQ checks and the export show
  them); only new writes meet the new format.
- The permission catalog gets ``apaar.consent.read`` and ``apaar.consent.record`` (rows written
  out here, like 0035). Role grants of existing schools are not changed (system roles are tenant
  rows under FORCE RLS): the release needs the post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the two tables (lossy: the consent register; the
signed forms stay as documents), restores the previous ``udise_pen`` definition (1-20 letters or
digits, sources ``udise_plus`` and ``manual_entry``) and removes the new catalog keys no role
holds.

Revision ID: 0051_apaar_consent_pen
Revises: 0050_verified_answer_drafter
Create Date: 2026-10-10
"""

from __future__ import annotations

import json
import uuid
from importlib import resources
from typing import Any

import sqlalchemy as sa
import yaml
from alembic import op

revision = "0051_apaar_consent_pen"
down_revision = "0050_verified_answer_drafter"
branch_labels = None
depends_on = None

ATTRDEF_NAMESPACE = uuid.UUID("5f0c8a52-6f0e-4d8e-9d55-2b8f6b1d0a01")  # as in 0008 / 0042
PEN_KEY = "udise_pen"

TABLES_SQL = r"""
CREATE TABLE sis.apaar_consents (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  student_id              uuid NOT NULL,
  seq                     int NOT NULL CHECK (seq >= 1),
  status                  text NOT NULL,
  relationship            text,
  guardian_id             uuid,
  decided_on              date,
  form_language           text,
  evidence_document_id    uuid,
  note                    text,
  recorded_by             uuid NOT NULL,
  recorded_by_membership  uuid NOT NULL,
  recorded_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT apaar_consents_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT apaar_consents_seq_key UNIQUE (tenant_id, student_id, seq),
  CONSTRAINT apaar_consents_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT apaar_consents_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT apaar_consents_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id) ON DELETE SET NULL (guardian_id),
  CONSTRAINT apaar_consents_evidence_fk FOREIGN KEY (tenant_id, evidence_document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT apaar_consents_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT apaar_consents_membership_fk FOREIGN KEY (tenant_id, recorded_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT apaar_consents_status_check
    CHECK (status IN ('given','refused','pending','withdrawn')),
  CONSTRAINT apaar_consents_relationship_check
    CHECK (relationship IS NULL OR relationship IN ('father','mother','guardian')),
  CONSTRAINT apaar_consents_language_check
    CHECK (form_language IS NULL OR form_language IN ('en','te')),
  CONSTRAINT apaar_consents_note_length CHECK (note IS NULL OR char_length(note) <= 500),
  -- A decision names who decided and the date on the form; "pending" does not.
  CONSTRAINT apaar_consents_decided CHECK (status = 'pending' OR
    (relationship IS NOT NULL AND decided_on IS NOT NULL)),
  -- Consent is never recorded without the signed form (PRV-021, DPDP).
  CONSTRAINT apaar_consents_given_has_form CHECK (status <> 'given' OR
    evidence_document_id IS NOT NULL)
);
CREATE INDEX apaar_consents_student_idx ON sis.apaar_consents (tenant_id, student_id, seq DESC);
CREATE INDEX apaar_consents_evidence_idx ON sis.apaar_consents (tenant_id, evidence_document_id)
  WHERE evidence_document_id IS NOT NULL;
CREATE INDEX apaar_consents_guardian_idx ON sis.apaar_consents (tenant_id, guardian_id)
  WHERE guardian_id IS NOT NULL;

CREATE TABLE sis.apaar_consent_settings (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  form_language   text NOT NULL DEFAULT 'en',
  updated_by      uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  version         int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT apaar_consent_settings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT apaar_consent_settings_one_per_school UNIQUE (tenant_id),
  CONSTRAINT apaar_consent_settings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT apaar_consent_settings_updated_by_fk FOREIGN KEY (updated_by)
    REFERENCES core.users (id),
  CONSTRAINT apaar_consent_settings_language_check CHECK (form_language IN ('en','te'))
);
CREATE TRIGGER apaar_consent_settings_set_updated_at BEFORE UPDATE
  ON sis.apaar_consent_settings FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

# Children before parents (the order the module's offboarding purge deletes them in).
TENANT_TABLES = ("sis.apaar_consents", "sis.apaar_consent_settings")

GRANTS_SQL = r"""
-- The register is append-only for the app: decisions are added, never changed or deleted.
REVOKE UPDATE, DELETE, TRUNCATE ON sis.apaar_consents FROM sos_app;
REVOKE UPDATE, DELETE, TRUNCATE ON sis.apaar_consent_settings FROM sos_app;
GRANT UPDATE (form_language, updated_by, updated_at, version)
  ON sis.apaar_consent_settings TO sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "apaar.consent.read",
        "description": (
            "See parents' APAAR consent decisions, the class summary and pending list, and "
            "print consent forms (class teachers: their sections)"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "apaar.consent.record",
        "description": (
            "Record a parent's APAAR consent decision (given, refused, pending or withdrawn) "
            "with the signed form, and print consent forms"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
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

# The owner lifts FORCE for these statements only; the block's own rollback restores it.
UNFORCE = "ALTER TABLE sis.attribute_definitions NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner writes a global catalogue row; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE sis.attribute_definitions FORCE ROW LEVEL SECURITY"

SET_PEN = sa.text(
    """
    UPDATE sis.attribute_definitions
       SET validation = CAST(:validation AS jsonb),
           canonical_policy = CAST(:canonical_policy AS jsonb)
     WHERE id = :id AND tenant_id IS NULL
    """
)

# 0042's definition, restored by the downgrade.
PREVIOUS_PEN: dict[str, Any] = {
    "validation": {
        "max_length": 20,
        "pattern": "^[A-Za-z0-9]{1,20}$",
        "sources": ["udise_plus", "manual_entry"],
    },
    "canonical_policy": {
        "precedence": ["udise_plus", "manual_entry"],
        "require_verified": True,
        "show_conflicts_from": [],
    },
}


def attribute_id(key: str) -> uuid.UUID:
    return uuid.uuid5(ATTRDEF_NAMESPACE, f"sis.attribute_definitions:{key}")


def _pen_spec() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    spec: dict[str, Any] = raw["attributes"][PEN_KEY]
    return spec


def _set_pen(validation: dict[str, Any], policy: dict[str, Any]) -> None:
    bind = op.get_bind()
    op.execute(UNFORCE)
    bind.execute(
        SET_PEN,
        {
            "id": attribute_id(PEN_KEY),
            "validation": json.dumps(validation, sort_keys=True),
            "canonical_policy": json.dumps(policy, sort_keys=True),
        },
    )
    op.execute(REFORCE)


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the
        # school is offboarding and the transaction-local purge flag names it.
        op.execute(f"GRANT SELECT, DELETE ON {table} TO sos_purger")
        op.execute(
            f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
            "USING (core.tenant_purge_allowed())"
        )
    op.execute(GRANTS_SQL)
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)
    spec = _pen_spec()
    _set_pen(spec.get("validation", {}), spec["canonical_policy"])


def downgrade() -> None:
    _set_pen(PREVIOUS_PEN["validation"], PREVIOUS_PEN["canonical_policy"])
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
    for table in TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
