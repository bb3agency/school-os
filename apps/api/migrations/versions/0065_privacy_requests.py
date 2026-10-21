"""R3-1A: privacy notices, data-principal requests and grievances (ADR-0048; FR-DPDP-001..018;
docs/05 §16.1).

Tenant tables in schema ``ops`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs, the restrictive ``offboarding_purge`` policy with
``SELECT, DELETE`` for ``sos_purger``; nothing for ``sos_platform``):

- ``ops.privacy_notices``: **append-only** record of each notice the school issued (template key
  and version, audience, language, date, channel, optional final document).
- ``ops.privacy_requests``: one register for access, correction, erasure, nomination, consent
  withdrawal, grievance and other requests. ``reference`` ``PR-<year>-<nnnn>`` from a gap-free
  counter per school and calendar year. Exactly one subject (``student_id``, ``guardian_id`` or
  ``membership_id``; ``subject_kind`` remembers which, so a guardian deleted later leaves the
  request with ``guardian_id`` NULL and the label "former guardian"). ``due_on`` at most 7 days
  after ``received_on`` (NFR-PRV-004). The requester's name and words, staff notes, the response
  and nominee details are ciphertext under the school's DEK (AAD
  ``ops.privacy_requests|<column>|<id>``). Maker-checker in the database: ``decided_by`` and
  ``report_released_by`` differ from ``logged_by``; a decision needs a verification method.
  No DELETE for ``sos_app``; UPDATE only on the columns the workflow changes.
- ``ops.privacy_request_events``: **append-only** timeline (``seq`` per request; ``detail`` holds
  ids, codes and counts only).
- ``ops.privacy_request_links``: links to change requests, consents and exports (unique per
  request, kind and target); insert-only.
- ``ops.privacy_request_counters``: last reference number per school and year; never goes down,
  never deleted outside the offboarding purge.

Also ``ops.exports`` accepts kinds ``subject_report`` (the access report) and ``privacy_notice``
(a filled notice as PDF): one PDF each, no profile, no columns, zero or one frozen student; and the three ``privacy.*`` permissions are upserted into
``core.permissions`` (role grants of existing schools need the system-role sync, ADR-0022).

Expand-only (invariant 12). Downgrade drops the four tables (lossy: the request register and the
notice records), deletes ``subject_report`` and ``privacy_notice`` exports of every school and restores the 0054 export
checks, and removes the catalog keys no role holds.

Revision ID: 0065_privacy_requests
Revises: 0062_messaging_finish
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0065_privacy_requests"
# R3 waves were written against 0062; the lead re-chains (0063 -> 0064_portal_calendar -> 0065) at merge.
down_revision = "0062_messaging_finish"
branch_labels = None
depends_on = None

TABLES_SQL = r"""
CREATE FUNCTION ops.tg_privacy_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME
    USING ERRCODE = 'check_violation', CONSTRAINT = TG_TABLE_NAME || '_append_only';
END;
$$;

CREATE FUNCTION ops.tg_privacy_counters_never_down() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.last_no < OLD.last_no OR NEW.tenant_id <> OLD.tenant_id OR NEW.year <> OLD.year THEN
    RAISE EXCEPTION 'privacy request references are never reused'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'privacy_request_counters_never_down';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TABLE ops.privacy_notices (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  template_key            text NOT NULL,
  template_version        text NOT NULL,
  audience                text NOT NULL,
  language                text NOT NULL DEFAULT 'en',
  issued_on               date NOT NULL,
  channel                 text NOT NULL,
  document_id             uuid,
  recorded_by             uuid NOT NULL,
  recorded_by_membership  uuid NOT NULL,
  recorded_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacy_notices_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT privacy_notices_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT privacy_notices_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT privacy_notices_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT privacy_notices_membership_fk FOREIGN KEY (tenant_id, recorded_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT privacy_notices_template_key_check
    CHECK (template_key IN ('notice_parents','notice_staff')),
  CONSTRAINT privacy_notices_version_shape CHECK (template_version ~ '^[0-9]{1,3}\.[0-9]{1,3}$'),
  CONSTRAINT privacy_notices_audience_check CHECK (audience IN ('parents','staff')),
  CONSTRAINT privacy_notices_language_check CHECK (language IN ('en','te')),
  CONSTRAINT privacy_notices_channel_check
    CHECK (channel IN ('paper','school_whatsapp_group','sos_notice','email','other'))
);
CREATE INDEX privacy_notices_audience_idx
  ON ops.privacy_notices (tenant_id, audience, issued_on DESC);
CREATE INDEX privacy_notices_document_idx ON ops.privacy_notices (tenant_id, document_id)
  WHERE document_id IS NOT NULL;
CREATE TRIGGER privacy_notices_append_only BEFORE UPDATE OR DELETE ON ops.privacy_notices
  FOR EACH ROW EXECUTE FUNCTION ops.tg_privacy_append_only();

CREATE TABLE ops.privacy_request_counters (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  year        int NOT NULL,
  last_no     int NOT NULL DEFAULT 0,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacy_request_counters_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT privacy_request_counters_one_per_year UNIQUE (tenant_id, year),
  CONSTRAINT privacy_request_counters_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT privacy_request_counters_year_range CHECK (year BETWEEN 2000 AND 2999),
  CONSTRAINT privacy_request_counters_last_no_range CHECK (last_no BETWEEN 0 AND 999999)
);
CREATE TRIGGER privacy_request_counters_never_down BEFORE UPDATE
  ON ops.privacy_request_counters
  FOR EACH ROW EXECUTE FUNCTION ops.tg_privacy_counters_never_down();
CREATE TRIGGER privacy_request_counters_append_only BEFORE DELETE
  ON ops.privacy_request_counters
  FOR EACH ROW EXECUTE FUNCTION ops.tg_privacy_append_only();

CREATE TABLE ops.privacy_requests (
  id                         uuid PRIMARY KEY,
  tenant_id                  uuid NOT NULL,
  reference                  text NOT NULL,
  year                       int NOT NULL,
  number                     int NOT NULL,
  kind                       text NOT NULL,
  subject_kind               text NOT NULL,
  student_id                 uuid,
  guardian_id                uuid,
  membership_id              uuid,
  requester_relationship     text NOT NULL,
  verification_method        text,
  verified_at                timestamptz,
  received_on                date NOT NULL,
  due_on                     date NOT NULL,
  status                     text NOT NULL DEFAULT 'received',
  requester_name_ciphertext  bytea,
  request_ciphertext         bytea,
  notes_ciphertext           bytea,
  response_ciphertext        bytea,
  nominee_ciphertext         bytea,
  responded_on               date,
  nomination_withdrawn_at    timestamptz,
  report_export_id           uuid,
  report_released_by         uuid,
  report_released_at         timestamptz,
  logged_by                  uuid NOT NULL,
  logged_by_membership       uuid NOT NULL,
  decided_by                 uuid,
  decided_at                 timestamptz,
  closed_at                  timestamptz,
  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now(),
  version                    int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT privacy_requests_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT privacy_requests_reference_key UNIQUE (tenant_id, reference),
  CONSTRAINT privacy_requests_number_key UNIQUE (tenant_id, year, number),
  CONSTRAINT privacy_requests_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT privacy_requests_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT privacy_requests_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id) ON DELETE SET NULL (guardian_id),
  CONSTRAINT privacy_requests_membership_fk FOREIGN KEY (tenant_id, membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT privacy_requests_export_fk FOREIGN KEY (tenant_id, report_export_id)
    REFERENCES ops.exports (tenant_id, id) ON DELETE SET NULL (report_export_id),
  CONSTRAINT privacy_requests_logged_by_fk FOREIGN KEY (logged_by) REFERENCES core.users (id),
  CONSTRAINT privacy_requests_logged_membership_fk FOREIGN KEY (tenant_id, logged_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT privacy_requests_decided_by_fk FOREIGN KEY (decided_by) REFERENCES core.users (id),
  CONSTRAINT privacy_requests_released_by_fk FOREIGN KEY (report_released_by)
    REFERENCES core.users (id),
  CONSTRAINT privacy_requests_reference_shape CHECK (reference ~ '^PR-[0-9]{4}-[0-9]{4,6}$'),
  CONSTRAINT privacy_requests_kind_check CHECK (kind IN
    ('access','correction','erasure','nomination','consent_withdrawal','grievance','other')),
  CONSTRAINT privacy_requests_subject_kind_check
    CHECK (subject_kind IN ('student','guardian','member')),
  -- Exactly one subject, of the kind recorded; a guardian deleted later leaves none.
  CONSTRAINT privacy_requests_one_subject CHECK (
    (subject_kind = 'student' AND student_id IS NOT NULL AND guardian_id IS NULL
       AND membership_id IS NULL)
    OR (subject_kind = 'guardian' AND student_id IS NULL AND membership_id IS NULL)
    OR (subject_kind = 'member' AND membership_id IS NOT NULL AND student_id IS NULL
       AND guardian_id IS NULL)),
  -- Nominations are for adult data principals (FR-DPDP-015).
  CONSTRAINT privacy_requests_nomination_adults CHECK
    (kind <> 'nomination' OR subject_kind IN ('guardian','member')),
  CONSTRAINT privacy_requests_relationship_check CHECK (requester_relationship IN
    ('self','father','mother','guardian','nominee','other')),
  CONSTRAINT privacy_requests_verification_check CHECK (verification_method IS NULL OR
    verification_method IN ('in_person_document','guardian_phone_on_record','staff_sign_in')),
  CONSTRAINT privacy_requests_verified_at CHECK
    ((verification_method IS NULL) = (verified_at IS NULL)),
  CONSTRAINT privacy_requests_due_within_7_days
    CHECK (due_on >= received_on AND due_on <= received_on + 7),
  CONSTRAINT privacy_requests_status_check CHECK (status IN
    ('received','in_progress','waiting_for_requester','decided','closed','withdrawn')),
  CONSTRAINT privacy_requests_decided_verified
    CHECK (status <> 'decided' OR verification_method IS NOT NULL),
  -- Maker-checker (ADR-0048 decisions 6 and 8).
  CONSTRAINT privacy_requests_decided_by_other
    CHECK (decided_by IS NULL OR decided_by <> logged_by),
  CONSTRAINT privacy_requests_decided_fields
    CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
  CONSTRAINT privacy_requests_released_by_other
    CHECK (report_released_by IS NULL OR report_released_by <> logged_by),
  CONSTRAINT privacy_requests_released_fields
    CHECK ((report_released_by IS NULL) = (report_released_at IS NULL)),
  CONSTRAINT privacy_requests_closed_fields
    CHECK ((status IN ('closed','withdrawn')) = (closed_at IS NOT NULL)),
  -- A grievance is closed only with a written response (FR-DPDP-016).
  CONSTRAINT privacy_requests_grievance_response CHECK (kind <> 'grievance'
    OR status <> 'closed' OR (response_ciphertext IS NOT NULL AND responded_on IS NOT NULL)),
  CONSTRAINT privacy_requests_nominee_only_nomination
    CHECK (nominee_ciphertext IS NULL OR kind = 'nomination'),
  CONSTRAINT privacy_requests_number_range CHECK (number BETWEEN 1 AND 999999)
);
CREATE INDEX privacy_requests_status_due_idx
  ON ops.privacy_requests (tenant_id, status, due_on);
CREATE INDEX privacy_requests_student_idx ON ops.privacy_requests (tenant_id, student_id)
  WHERE student_id IS NOT NULL;
CREATE INDEX privacy_requests_guardian_idx ON ops.privacy_requests (tenant_id, guardian_id)
  WHERE guardian_id IS NOT NULL;
CREATE INDEX privacy_requests_membership_idx ON ops.privacy_requests (tenant_id, membership_id)
  WHERE membership_id IS NOT NULL;
CREATE INDEX privacy_requests_export_idx ON ops.privacy_requests (tenant_id, report_export_id)
  WHERE report_export_id IS NOT NULL;
CREATE TRIGGER privacy_requests_set_updated_at BEFORE UPDATE ON ops.privacy_requests
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.privacy_request_events (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  request_id           uuid NOT NULL,
  seq                  int NOT NULL CHECK (seq >= 1),
  event                text NOT NULL,
  detail               jsonb NOT NULL DEFAULT '{}'::jsonb,
  actor_user_id        uuid,
  actor_membership_id  uuid,
  at                   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacy_request_events_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT privacy_request_events_seq_key UNIQUE (tenant_id, request_id, seq),
  CONSTRAINT privacy_request_events_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT privacy_request_events_request_fk FOREIGN KEY (tenant_id, request_id)
    REFERENCES ops.privacy_requests (tenant_id, id),
  CONSTRAINT privacy_request_events_actor_fk FOREIGN KEY (actor_user_id)
    REFERENCES core.users (id),
  CONSTRAINT privacy_request_events_membership_fk FOREIGN KEY (tenant_id, actor_membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT privacy_request_events_event_check CHECK (event IN ('logged','verified',
    'status_changed','notes_changed','report_built','report_released','report_downloaded',
    'change_request_linked','erasure_planned','erasure_executed','consent_withdrawn',
    'nomination_withdrawn','responded','closed')),
  CONSTRAINT privacy_request_events_detail_object CHECK (jsonb_typeof(detail) = 'object')
);
CREATE TRIGGER privacy_request_events_append_only BEFORE UPDATE OR DELETE
  ON ops.privacy_request_events
  FOR EACH ROW EXECUTE FUNCTION ops.tg_privacy_append_only();

CREATE TABLE ops.privacy_request_links (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  request_id  uuid NOT NULL,
  link_kind   text NOT NULL,
  target_id   uuid NOT NULL,
  created_by  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacy_request_links_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT privacy_request_links_one UNIQUE (tenant_id, request_id, link_kind, target_id),
  CONSTRAINT privacy_request_links_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT privacy_request_links_request_fk FOREIGN KEY (tenant_id, request_id)
    REFERENCES ops.privacy_requests (tenant_id, id),
  CONSTRAINT privacy_request_links_created_by_fk FOREIGN KEY (created_by)
    REFERENCES core.users (id),
  CONSTRAINT privacy_request_links_kind_check
    CHECK (link_kind IN ('change_request','consent','export'))
);
CREATE INDEX privacy_request_links_target_idx
  ON ops.privacy_request_links (tenant_id, link_kind, target_id);
CREATE TRIGGER privacy_request_links_append_only BEFORE UPDATE OR DELETE
  ON ops.privacy_request_links
  FOR EACH ROW EXECUTE FUNCTION ops.tg_privacy_append_only();

-- The access report (subject_report) and a filled privacy notice (privacy_notice) are module
-- documents: one PDF, no profile or columns, zero or one frozen student.
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_check;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_check CHECK (kind IN (
  'board_precheck','portal_precheck','student_list','readiness_slips','apaar_consent_forms',
  'subject_report','privacy_notice'));
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_shape;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_shape CHECK (
  (kind = 'student_list' AND profile_key IS NULL AND profile_version IS NULL
     AND columns IS NOT NULL AND cardinality(columns) BETWEEN 1 AND 60)
  OR (kind IN ('board_precheck','portal_precheck','readiness_slips')
     AND profile_key IS NOT NULL AND profile_version IS NOT NULL AND columns IS NULL)
  OR (kind IN ('apaar_consent_forms','subject_report','privacy_notice')
     AND profile_key IS NULL AND profile_version IS NULL AND columns IS NULL));
ALTER TABLE ops.exports DROP CONSTRAINT exports_print_formats;
ALTER TABLE ops.exports ADD CONSTRAINT exports_print_formats CHECK (
  kind NOT IN ('readiness_slips','apaar_consent_forms','subject_report','privacy_notice')
  OR formats = ARRAY['pdf']::text[]);
ALTER TABLE ops.exports DROP CONSTRAINT exports_students_count;
ALTER TABLE ops.exports ADD CONSTRAINT exports_students_count CHECK (
  student_count = cardinality(student_ids) AND (
    (kind IN ('subject_report','privacy_notice') AND student_count BETWEEN 0 AND 1)
    OR (kind NOT IN ('subject_report','privacy_notice') AND student_count BETWEEN 1 AND 5000)));
"""

# Children before parents (the order the module's offboarding purge deletes them in).
TENANT_TABLES = (
    "ops.privacy_request_links",
    "ops.privacy_request_events",
    "ops.privacy_requests",
    "ops.privacy_request_counters",
    "ops.privacy_notices",
)
FUNCTIONS = ("ops.tg_privacy_append_only()", "ops.tg_privacy_counters_never_down()")

# Columns the workflow may change (no DELETE for sos_app on any of these tables).
UPDATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "ops.privacy_requests": (
        "status",
        "verification_method",
        "verified_at",
        # The requester's name and words change only on key rotation (re-encryption).
        "requester_name_ciphertext",
        "request_ciphertext",
        "notes_ciphertext",
        "response_ciphertext",
        "responded_on",
        "nominee_ciphertext",
        "nomination_withdrawn_at",
        "report_export_id",
        "report_released_by",
        "report_released_at",
        "decided_by",
        "decided_at",
        "closed_at",
        "updated_at",
        "version",
    ),
    "ops.privacy_request_counters": ("last_no", "updated_at"),
}

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "privacy.read",
        "description": (
            "See privacy requests and complaints about people you may see, the notices the "
            "school issued and the record classes"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "privacy.request.manage",
        "description": (
            "Log privacy requests and complaints, record how the requester was checked, link "
            "change requests, build access reports, record notices and write answers"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "privacy.request.decide",
        "description": (
            "Release an access report, approve an erasure or close a complaint (not for "
            "requests you logged yourself)"
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

# The owner lifts FORCE for the one DELETE (access reports of every school); FORCE is restored
# before the constraints change, in the same transaction.
UNFORCE = "ALTER TABLE ops.exports NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner deletes the access-report rows of every school on downgrade; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE ops.exports FORCE ROW LEVEL SECURITY"
DELETE_REPORTS = "DELETE FROM ops.exports WHERE kind IN ('subject_report','privacy_notice')"

EXPORTS_DOWNGRADE_SQL = r"""
ALTER TABLE ops.exports DROP CONSTRAINT exports_students_count;
ALTER TABLE ops.exports ADD CONSTRAINT exports_students_count CHECK (
  student_count = cardinality(student_ids) AND student_count BETWEEN 1 AND 5000);
ALTER TABLE ops.exports DROP CONSTRAINT exports_print_formats;
ALTER TABLE ops.exports ADD CONSTRAINT exports_print_formats CHECK (
  kind NOT IN ('readiness_slips','apaar_consent_forms') OR formats = ARRAY['pdf']::text[]);
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_shape;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_shape CHECK (
  (kind = 'student_list' AND profile_key IS NULL AND profile_version IS NULL
     AND columns IS NOT NULL AND cardinality(columns) BETWEEN 1 AND 60)
  OR (kind IN ('board_precheck','portal_precheck','readiness_slips')
     AND profile_key IS NOT NULL AND profile_version IS NOT NULL AND columns IS NULL)
  OR (kind = 'apaar_consent_forms'
     AND profile_key IS NULL AND profile_version IS NULL AND columns IS NULL));
ALTER TABLE ops.exports DROP CONSTRAINT exports_kind_check;
ALTER TABLE ops.exports ADD CONSTRAINT exports_kind_check CHECK (kind IN (
  'board_precheck','portal_precheck','student_list','readiness_slips','apaar_consent_forms'));
"""


def _grants_sql() -> str:
    lines: list[str] = []
    for table in TENANT_TABLES:
        lines.append(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM sos_app;")
        columns = UPDATE_COLUMNS.get(table)
        if columns:
            lines.append(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO sos_app;")
        # Request texts are ciphertext; the read-only role gets the rows like other ops tables.
        lines.append(f"REVOKE ALL ON {table} FROM sos_readonly;")
        lines.append(f"GRANT SELECT ON {table} TO sos_readonly;")
    return "\n".join(lines)


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
    op.execute(_grants_sql())
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
    for table in TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    op.execute(UNFORCE)
    op.execute(DELETE_REPORTS)
    op.execute(REFORCE)
    op.execute(EXPORTS_DOWNGRADE_SQL)
