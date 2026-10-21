"""R3-1B: consents for optional purposes, the school's breach register and SchoolOS's incident
records (ADR-0049, ADR-0050; FR-DPDP-020..026, FR-DPDP-030..038; docs/05 §16.2, §16.3).

Tenant tables (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FKs, the restrictive ``offboarding_purge`` policy with SELECT and
DELETE for ``sos_purger``; ADR-0013, ADR-0029):

- ``sis.consent_purposes``: the school's optional purposes (``key``, ``label_en``, ``label_te``,
  ``description``, ``form_version``, ``active``, ``suggested_key`` from
  ``app/privacy/consent_purposes.yaml``). A trigger refuses a change of the label or description
  while a consent refers to the current ``form_version`` (the service bumps the version
  instead). ``sos_app`` may update only the text, ``form_version``, ``active``, ``updated_at`` and
  ``version``; never delete.
- ``sis.consents``: **append-only** decisions per student and purpose (``given``, ``refused``,
  ``pending``, ``withdrawn``), the rules of ``sis.apaar_consents`` (``seq`` per student and
  purpose, ``given`` only with the signed form, a decision names who decided and the date on the
  form). ``privacy_request_id`` names the withdrawal request of the privacy register (R3-1A,
  ``ops.privacy_requests``; composite FK ``consents_privacy_request_fk``: the table is new, so
  no existing row can violate it). ``sos_app`` has SELECT and INSERT only; a trigger refuses UPDATE and
  DELETE outside the offboarding purge.
- ``ops.breach_records``: the school's breach register, ``origin`` ``school`` or
  ``processor_notice`` (pulled from ``platform.incident_schools``; ``platform_incident_id`` is a
  plain uuid, another schema, unique per school). The description and containment steps are
  encrypted with the school's DEK (C3; ``app.students.rotation.CIPHERTEXT_COLUMNS``). Category
  codes, approximate counts, status and the three duties (Board notice, Board report, people
  notified) with when and how. ``processor_facts`` keeps what SchoolOS reported (severity,
  approximate count, window; codes and counts only). Deadlines are computed, not stored.
- ``ops.breach_affected``: optional affected students, guardians or staff (ids only; exactly
  one per row; unique per breach and subject).
- ``ops.breach_reminders``: one row per (breach, reminder) sent: idempotency of the 24 h and
  60 h reminders (FR-DPDP-035).

Control plane (schema ``platform``, no RLS, ``sos_platform`` only; no personal data, ADR-0050
decision 1):

- ``platform.incidents``: T0, severity, status, a summary of categories and counts (CHECK: at
  most 500 characters, no run of 6 or more digits, no ``@``), CERT-In fields, the lead operator
  and the two confirmations (``second_confirmed_by <> confirmed_by``).
- ``platform.incident_schools``: per affected school the category codes, an approximate count,
  the window, when the school was notified and when its worker pulled the notice.

Also: ``ops.exports.kind`` gains ``consent_forms`` (print job: no profile, no columns, PDF only;
FR-DPDP-023), next to 0066's ``subject_report`` and ``privacy_notice`` (``exports_students_count``
already treats it like every other print job), and the permission catalog gets
``consent.read``, ``consent.record``, ``privacy.breach.read``, ``privacy.breach.manage`` (step-up), ``platform.incidents.read`` and
``platform.incidents.manage`` (step-up, two operators). Role grants of existing schools change
only with the post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the tables (lossy: consents, breach records and
incident records), deletes the consent-form print jobs and restores the export CHECKs, and
removes the catalog keys no role holds.

Revision ID: 0067_consents_breach
Revises: 0066_privacy_requests
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_consents_breach"
down_revision = "0066_privacy_requests"
branch_labels = None
depends_on = None

CATEGORIES = (
    "'identity','contact','aadhaar_partial','academic','attendance','financial','documents',"
    "'insights','staff','other'"
)
METHODS = "'email','portal','letter','phone','in_person','whatsapp','sos_notice','other'"

TABLES_SQL = r"""
CREATE TABLE sis.consent_purposes (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  key             text NOT NULL,
  label_en        text NOT NULL,
  label_te        text,
  description     text NOT NULL,
  form_version    int NOT NULL DEFAULT 1 CHECK (form_version >= 1),
  active          boolean NOT NULL DEFAULT true,
  suggested_key   text,
  created_by      uuid NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  version         int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT consent_purposes_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT consent_purposes_key_key UNIQUE (tenant_id, key),
  CONSTRAINT consent_purposes_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT consent_purposes_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT consent_purposes_key_format CHECK (key ~ '^[a-z][a-z0-9_]{2,40}$'),
  CONSTRAINT consent_purposes_label_en_length CHECK (char_length(label_en) BETWEEN 2 AND 120),
  CONSTRAINT consent_purposes_label_te_length
    CHECK (label_te IS NULL OR char_length(label_te) BETWEEN 2 AND 120),
  CONSTRAINT consent_purposes_description_length
    CHECK (char_length(description) BETWEEN 10 AND 1000),
  CONSTRAINT consent_purposes_suggested_key_format
    CHECK (suggested_key IS NULL OR suggested_key ~ '^[a-z][a-z0-9_]{2,40}$')
);
CREATE TRIGGER consent_purposes_set_updated_at BEFORE UPDATE ON sis.consent_purposes
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE sis.consents (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  student_id              uuid NOT NULL,
  purpose_id              uuid NOT NULL,
  form_version            int NOT NULL CHECK (form_version >= 1),
  seq                     int NOT NULL CHECK (seq >= 1),
  status                  text NOT NULL,
  relationship            text,
  guardian_id             uuid,
  decided_on              date,
  form_language           text,
  evidence_document_id    uuid,
  note                    text,
  privacy_request_id      uuid,
  recorded_by             uuid NOT NULL,
  recorded_by_membership  uuid NOT NULL,
  recorded_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT consents_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT consents_seq_key UNIQUE (tenant_id, student_id, purpose_id, seq),
  CONSTRAINT consents_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT consents_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT consents_purpose_fk FOREIGN KEY (tenant_id, purpose_id)
    REFERENCES sis.consent_purposes (tenant_id, id),
  CONSTRAINT consents_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id) ON DELETE SET NULL (guardian_id),
  CONSTRAINT consents_evidence_fk FOREIGN KEY (tenant_id, evidence_document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT consents_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT consents_membership_fk FOREIGN KEY (tenant_id, recorded_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT consents_privacy_request_fk FOREIGN KEY (tenant_id, privacy_request_id)
    REFERENCES ops.privacy_requests (tenant_id, id),
  CONSTRAINT consents_status_check CHECK (status IN ('given','refused','pending','withdrawn')),
  CONSTRAINT consents_relationship_check
    CHECK (relationship IS NULL OR relationship IN ('father','mother','guardian')),
  CONSTRAINT consents_language_check CHECK (form_language IS NULL OR form_language IN ('en','te')),
  CONSTRAINT consents_note_length CHECK (note IS NULL OR char_length(note) <= 500),
  CONSTRAINT consents_decided CHECK (status = 'pending' OR
    (relationship IS NOT NULL AND decided_on IS NOT NULL)),
  -- Consent is never recorded without the signed form (PRV-006, FR-DPDP-021).
  CONSTRAINT consents_given_has_form CHECK (status <> 'given' OR evidence_document_id IS NOT NULL)
);
CREATE INDEX consents_student_idx
  ON sis.consents (tenant_id, student_id, purpose_id, seq DESC);
CREATE INDEX consents_purpose_idx ON sis.consents (tenant_id, purpose_id);
CREATE INDEX consents_evidence_idx ON sis.consents (tenant_id, evidence_document_id)
  WHERE evidence_document_id IS NOT NULL;
CREATE INDEX consents_guardian_idx ON sis.consents (tenant_id, guardian_id)
  WHERE guardian_id IS NOT NULL;
CREATE INDEX consents_privacy_request_idx ON sis.consents (tenant_id, privacy_request_id)
  WHERE privacy_request_id IS NOT NULL;

CREATE FUNCTION sis.tg_consents_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'consent decisions are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'consents_append_only';
END $$;
CREATE TRIGGER consents_append_only BEFORE UPDATE OR DELETE ON sis.consents
  FOR EACH ROW EXECUTE FUNCTION sis.tg_consents_append_only();

-- A purpose's text is frozen once a decision refers to its current form version (ADR-0049
-- decision 1): changing it must also raise form_version.
CREATE FUNCTION sis.tg_consent_purposes_frozen() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.label_en IS DISTINCT FROM OLD.label_en
      OR NEW.label_te IS DISTINCT FROM OLD.label_te
      OR NEW.description IS DISTINCT FROM OLD.description)
     AND NEW.form_version <= OLD.form_version
     AND EXISTS (SELECT 1 FROM sis.consents c
                  WHERE c.tenant_id = OLD.tenant_id AND c.purpose_id = OLD.id
                    AND c.form_version = OLD.form_version) THEN
    RAISE EXCEPTION 'an answered purpose text needs a new form version'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'consent_purposes_frozen';
  END IF;
  IF NEW.form_version < OLD.form_version THEN
    RAISE EXCEPTION 'form_version never goes down'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'consent_purposes_frozen';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER consent_purposes_frozen BEFORE UPDATE ON sis.consent_purposes
  FOR EACH ROW EXECUTE FUNCTION sis.tg_consent_purposes_frozen();

CREATE TABLE ops.breach_records (
  id                        uuid PRIMARY KEY,
  tenant_id                 uuid NOT NULL,
  origin                    text NOT NULL,
  platform_incident_id      uuid,
  noticed_at                timestamptz NOT NULL,
  description_ciphertext    bytea,
  containment_ciphertext    bytea,
  categories                text[] NOT NULL,
  approx_children           int NOT NULL DEFAULT 0 CHECK (approx_children >= 0),
  approx_guardians          int NOT NULL DEFAULT 0 CHECK (approx_guardians >= 0),
  approx_staff              int NOT NULL DEFAULT 0 CHECK (approx_staff >= 0),
  processor_facts           jsonb,
  status                    text NOT NULL DEFAULT 'open',
  board_notice_done_at      timestamptz,
  board_notice_method       text,
  board_report_done_at      timestamptz,
  board_report_method       text,
  people_notified_at        timestamptz,
  people_notified_method    text,
  close_reason              text,
  closed_at                 timestamptz,
  recorded_by               uuid,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  version                   int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT breach_records_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT breach_records_incident_key UNIQUE (tenant_id, platform_incident_id),
  CONSTRAINT breach_records_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT breach_records_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT breach_records_origin_check CHECK (origin IN ('school','processor_notice')),
  CONSTRAINT breach_records_incident_origin
    CHECK (platform_incident_id IS NULL OR origin = 'processor_notice'),
  CONSTRAINT breach_records_school_has_author CHECK (origin <> 'school' OR recorded_by IS NOT NULL),
  CONSTRAINT breach_records_facts_origin
    CHECK (processor_facts IS NULL OR origin = 'processor_notice'),
  CONSTRAINT breach_records_categories_check
    CHECK (cardinality(categories) BETWEEN 1 AND 10 AND categories <@ ARRAY[%CATEGORIES%]::text[]),
  CONSTRAINT breach_records_status_check CHECK (status IN ('open','contained','closed')),
  CONSTRAINT breach_records_closed CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
  CONSTRAINT breach_records_board_notice
    CHECK ((board_notice_done_at IS NULL) = (board_notice_method IS NULL)),
  CONSTRAINT breach_records_board_report
    CHECK ((board_report_done_at IS NULL) = (board_report_method IS NULL)),
  CONSTRAINT breach_records_people_notified
    CHECK ((people_notified_at IS NULL) = (people_notified_method IS NULL)),
  CONSTRAINT breach_records_methods CHECK (
    (board_notice_method IS NULL OR board_notice_method IN (%METHODS%))
    AND (board_report_method IS NULL OR board_report_method IN (%METHODS%))
    AND (people_notified_method IS NULL OR people_notified_method IN (%METHODS%))),
  CONSTRAINT breach_records_close_reason_length
    CHECK (close_reason IS NULL OR char_length(close_reason) BETWEEN 10 AND 500)
);
CREATE INDEX breach_records_status_idx ON ops.breach_records (tenant_id, status, noticed_at);
CREATE TRIGGER breach_records_set_updated_at BEFORE UPDATE ON ops.breach_records
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.breach_affected (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  breach_id       uuid NOT NULL,
  student_id      uuid,
  guardian_id     uuid,
  membership_id   uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT breach_affected_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT breach_affected_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT breach_affected_breach_fk FOREIGN KEY (tenant_id, breach_id)
    REFERENCES ops.breach_records (tenant_id, id),
  CONSTRAINT breach_affected_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT breach_affected_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT breach_affected_membership_fk FOREIGN KEY (tenant_id, membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT breach_affected_one_subject
    CHECK (num_nonnulls(student_id, guardian_id, membership_id) = 1)
);
CREATE UNIQUE INDEX breach_affected_subject_key ON ops.breach_affected
  (tenant_id, breach_id, COALESCE(student_id, guardian_id, membership_id));
CREATE INDEX breach_affected_student_idx ON ops.breach_affected (tenant_id, student_id)
  WHERE student_id IS NOT NULL;
CREATE INDEX breach_affected_guardian_idx ON ops.breach_affected (tenant_id, guardian_id)
  WHERE guardian_id IS NOT NULL;
CREATE INDEX breach_affected_membership_idx ON ops.breach_affected (tenant_id, membership_id)
  WHERE membership_id IS NOT NULL;

CREATE TABLE ops.breach_reminders (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  breach_id       uuid NOT NULL,
  reminder        text NOT NULL,
  sent_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT breach_reminders_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT breach_reminders_key UNIQUE (tenant_id, breach_id, reminder),
  CONSTRAINT breach_reminders_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT breach_reminders_breach_fk FOREIGN KEY (tenant_id, breach_id)
    REFERENCES ops.breach_records (tenant_id, id),
  CONSTRAINT breach_reminders_reminder_check CHECK (reminder IN ('board_24h','report_60h'))
);
""".replace("%CATEGORIES%", CATEGORIES).replace("%METHODS%", METHODS)

# Children before parents (the order the modules' offboarding purges delete them in).
TENANT_TABLES = (
    "sis.consents",
    "sis.consent_purposes",
    "ops.breach_reminders",
    "ops.breach_affected",
    "ops.breach_records",
)

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE ON sis.consents FROM sos_app;
REVOKE UPDATE, DELETE, TRUNCATE ON sis.consent_purposes FROM sos_app;
GRANT UPDATE (label_en, label_te, description, form_version, active, updated_at, version)
  ON sis.consent_purposes TO sos_app;
REVOKE UPDATE, DELETE, TRUNCATE ON ops.breach_records FROM sos_app;
GRANT UPDATE (description_ciphertext, containment_ciphertext, categories, approx_children,
  approx_guardians, approx_staff, status, board_notice_done_at, board_notice_method,
  board_report_done_at, board_report_method, people_notified_at, people_notified_method,
  close_reason, closed_at, updated_at, version) ON ops.breach_records TO sos_app;
REVOKE UPDATE, TRUNCATE ON ops.breach_affected FROM sos_app;
REVOKE UPDATE, DELETE, TRUNCATE ON ops.breach_reminders FROM sos_app;
"""

PLATFORM_SQL = r"""
CREATE TABLE platform.incidents (
  id                    uuid PRIMARY KEY,
  opened_at             timestamptz NOT NULL,
  severity              text NOT NULL,
  status                text NOT NULL DEFAULT 'open',
  summary               text NOT NULL,
  cert_in_required      boolean NOT NULL DEFAULT false,
  cert_in_reported_at   timestamptz,
  lead_operator_id      uuid NOT NULL REFERENCES platform.operators (id),
  created_by            uuid NOT NULL REFERENCES platform.operators (id),
  confirmed_by          uuid REFERENCES platform.operators (id),
  first_confirmed_at    timestamptz,
  second_confirmed_by   uuid REFERENCES platform.operators (id),
  confirmed_at          timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT incidents_severity_check CHECK (severity IN ('P1','P2','P3','P4')),
  CONSTRAINT incidents_status_check CHECK (status IN ('open','contained','resolved','closed')),
  -- Categories and counts only (ADR-0050 decision 1): no long digit runs, no e-mail addresses.
  CONSTRAINT incidents_summary_check CHECK (char_length(summary) BETWEEN 10 AND 500
    AND summary !~ '[0-9]{6,}' AND position('@' IN summary) = 0),
  CONSTRAINT incidents_two_people CHECK (second_confirmed_by IS NULL OR
    (confirmed_by IS NOT NULL AND second_confirmed_by <> confirmed_by)),
  CONSTRAINT incidents_confirmed CHECK ((confirmed_at IS NULL) = (second_confirmed_by IS NULL)),
  CONSTRAINT incidents_first_confirmed
    CHECK ((first_confirmed_at IS NULL) = (confirmed_by IS NULL)),
  CONSTRAINT incidents_cert_in CHECK (cert_in_reported_at IS NULL OR cert_in_required)
);
CREATE INDEX incidents_opened_idx ON platform.incidents (opened_at DESC, id DESC);
CREATE TRIGGER incidents_set_updated_at BEFORE UPDATE ON platform.incidents
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE platform.incident_schools (
  id                    uuid PRIMARY KEY,
  incident_id           uuid NOT NULL REFERENCES platform.incidents (id),
  tenant_id             uuid NOT NULL,
  categories            text[] NOT NULL,
  approx_people         int NOT NULL CHECK (approx_people >= 0),
  window_from           timestamptz,
  window_to             timestamptz,
  school_notified_at    timestamptz,
  pulled_at             timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT incident_schools_key UNIQUE (incident_id, tenant_id),
  CONSTRAINT incident_schools_categories_check
    CHECK (cardinality(categories) BETWEEN 1 AND 10 AND categories <@ ARRAY[{categories}]::text[]),
  CONSTRAINT incident_schools_window CHECK (window_from IS NULL OR window_to IS NULL
    OR window_from <= window_to)
);
CREATE INDEX incident_schools_tenant_idx ON platform.incident_schools (tenant_id, pulled_at);

REVOKE ALL ON platform.incidents, platform.incident_schools FROM PUBLIC;
REVOKE ALL ON platform.incidents, platform.incident_schools FROM sos_app, sos_readonly;
GRANT SELECT, INSERT, UPDATE ON platform.incidents, platform.incident_schools TO sos_platform;
""".replace("{categories}", CATEGORIES)

EXPORTS_UP = r"""
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_check;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_check CHECK (kind IN (
  'board_precheck','portal_precheck','student_list','readiness_slips','apaar_consent_forms',
  'subject_report','privacy_notice','consent_forms'));
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_shape;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_shape CHECK (
  (kind = 'student_list' AND profile_key IS NULL AND profile_version IS NULL
     AND columns IS NOT NULL AND cardinality(columns) BETWEEN 1 AND 60)
  OR (kind IN ('board_precheck','portal_precheck','readiness_slips')
     AND profile_key IS NOT NULL AND profile_version IS NOT NULL AND columns IS NULL)
  OR (kind IN ('apaar_consent_forms','subject_report','privacy_notice','consent_forms')
     AND profile_key IS NULL AND profile_version IS NULL AND columns IS NULL));
ALTER TABLE ops.exports DROP CONSTRAINT exports_print_formats;
ALTER TABLE ops.exports ADD CONSTRAINT exports_print_formats CHECK (
  kind NOT IN ('readiness_slips','apaar_consent_forms','subject_report','privacy_notice',
               'consent_forms')
  OR formats = ARRAY['pdf']::text[]);
"""

# Exactly 0066_privacy_requests' definitions.
EXPORTS_DOWN = r"""
ALTER TABLE ops.exports DROP CONSTRAINT exports_print_formats;
ALTER TABLE ops.exports ADD CONSTRAINT exports_print_formats CHECK (
  kind NOT IN ('readiness_slips','apaar_consent_forms','subject_report','privacy_notice')
  OR formats = ARRAY['pdf']::text[]);
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_shape;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_shape CHECK (
  (kind = 'student_list' AND profile_key IS NULL AND profile_version IS NULL
     AND columns IS NOT NULL AND cardinality(columns) BETWEEN 1 AND 60)
  OR (kind IN ('board_precheck','portal_precheck','readiness_slips')
     AND profile_key IS NOT NULL AND profile_version IS NOT NULL AND columns IS NULL)
  OR (kind IN ('apaar_consent_forms','subject_report','privacy_notice')
     AND profile_key IS NULL AND profile_version IS NULL AND columns IS NULL));
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_check;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_check CHECK (kind IN (
  'board_precheck','portal_precheck','student_list','readiness_slips','apaar_consent_forms',
  'subject_report','privacy_notice'));
"""

# The owner lifts FORCE for the one DELETE (print-job rows of every school); FORCE is restored
# before the constraints change, in the same transaction.
UNFORCE = "ALTER TABLE ops.exports NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner deletes the consent-form print jobs of every school on downgrade; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE ops.exports FORCE ROW LEVEL SECURITY"
DELETE_PRINT_JOBS = "DELETE FROM ops.exports WHERE kind = 'consent_forms'"

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "consent.read",
        "description": (
            "See consents for the school's optional purposes, the consent view (with APAAR and "
            "WhatsApp opt-ins) and section counts, and print consent forms (class teachers: "
            "their sections)"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "consent.record",
        "description": (
            "Record a family's decision on an optional purpose with the signed form, and print "
            "consent forms"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "privacy.breach.read",
        "description": "See the school's breach register, its duties, clocks and drafts",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "privacy.breach.manage",
        "description": (
            "Record and update breaches, their duties and affected people, and turn the family "
            "notice into a parent-notice draft"
        ),
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": False,
    },
    {
        "key": "platform.incidents.read",
        "description": "See SchoolOS incident records and their CERT-In and school-notice clocks",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": True,
    },
    {
        "key": "platform.incidents.manage",
        "description": (
            "Open and update incident records and mark affected schools (two operators confirm)"
        ),
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": True,
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
    op.execute(PLATFORM_SQL)
    op.execute(EXPORTS_UP)
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)


def downgrade() -> None:
    op.execute(UNFORCE)
    op.execute(DELETE_PRINT_JOBS)
    op.execute(REFORCE)
    op.execute(EXPORTS_DOWN)
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
    op.execute("DROP TABLE IF EXISTS platform.incident_schools")
    op.execute("DROP TABLE IF EXISTS platform.incidents")
    for table in TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_consents_append_only()")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_consent_purposes_frozen()")
