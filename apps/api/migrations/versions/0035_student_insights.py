"""Student timeline and early warning (M5; docs/05 §6.4; FR-ATT-*, FR-MRK-*, FR-EW-*).

Tenant tables (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FKs; offboarding ``offboarding_purge`` policy and ``sos_purger``
grants like 0032, ADR-0029). All in schema ``sis`` (student records):

School records (C2; ``app.academics``), corrected in place and never deleted by the app:

- ``sis.attendance_marks``: one status per student and IST date (``present``, ``absent``,
  ``late``, ``leave``) with the section the student was in; ``source`` ``mark`` or ``import``.
- ``sis.exams``: an exam of an academic year (name unique per year, date).
- ``sis.exam_marks``: marks per exam, student and subject with the maximum marks, or absent.

Restricted insights (C3; ``app.insights``; 08 §4 PRV-003..005), not readable by the reporting
role ``sos_readonly``:

- ``sis.behaviour_notes``: category, date and the note text **encrypted** with the school's DEK
  (``body_ciphertext``, AAD ``tenant|sis.behaviour_notes|body_ciphertext|id``); the app may only
  re-encrypt it (key rotation) or delete the row (erasure, retention).
- ``sis.insight_flags``: a flag raised by a versioned rule (or by a person, ``manual``): rule,
  rules version, the basis it was raised for (``UNIQUE (tenant_id, student_id, rule, basis)``),
  evidence (numbers and dates only), owner, raised/due dates, workflow
  ``open -> in_progress -> closed`` (reason); at most one open flag per student and rule
  (partial unique index ``insight_flags_one_open``).
- ``sis.flag_actions``: the intervention log of a flag (kind, date, optional note encrypted like
  notes); append-only for the app; deleted with its flag.
- ``sis.insight_settings``: the school's rule thresholds within the bounds of
  ``app/insights/rules.yaml`` (only differences from the defaults; one row per school).

Also: the permission catalog gets ``attendance.record``, ``attendance.read``, ``exam.manage``,
``marks.record``, ``marks.read``, ``insights.note``, ``insights.act`` and ``insights.manage``
(rows written out here, like 0019/0034). Role grants of existing schools are not changed (system
roles are tenant rows under FORCE RLS; see 0019): the release needs the post-migration
system-role sync (ADR-0022). ``insights.read`` already exists (0004 seed).

Expand-only (invariant 12). Downgrade drops the seven tables (lossy: attendance, marks, notes and
flags of M5) and removes the new catalog keys no role holds.

Revision ID: 0035_student_insights
Revises: 0034_circulars
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_student_insights"
down_revision = "0034_circulars"
branch_labels = None
depends_on = None


TABLES_SQL = r"""
CREATE TABLE sis.attendance_marks (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  student_id      uuid NOT NULL,
  section_id      uuid NOT NULL,
  on_date         date NOT NULL,
  status          text NOT NULL,
  source          text NOT NULL DEFAULT 'mark',
  recorded_by     uuid NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  version         int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT attendance_marks_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT attendance_marks_one_per_day UNIQUE (tenant_id, student_id, on_date),
  CONSTRAINT attendance_marks_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT attendance_marks_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT attendance_marks_section_fk FOREIGN KEY (tenant_id, section_id)
    REFERENCES core.sections (tenant_id, id),
  CONSTRAINT attendance_marks_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT attendance_marks_status_check
    CHECK (status IN ('present','absent','late','leave')),
  CONSTRAINT attendance_marks_source_check CHECK (source IN ('mark','import'))
);
CREATE INDEX attendance_marks_section_day_idx
  ON sis.attendance_marks (tenant_id, section_id, on_date);
CREATE INDEX attendance_marks_student_day_idx
  ON sis.attendance_marks (tenant_id, student_id, on_date DESC);
CREATE TRIGGER attendance_marks_set_updated_at BEFORE UPDATE ON sis.attendance_marks
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE sis.exams (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  academic_year_id  uuid NOT NULL,
  name              text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  held_on           date NOT NULL,
  created_by        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT exams_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT exams_name_per_year UNIQUE (tenant_id, academic_year_id, name),
  CONSTRAINT exams_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT exams_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT exams_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id)
);
CREATE INDEX exams_year_idx ON sis.exams (tenant_id, academic_year_id, held_on);
CREATE TRIGGER exams_set_updated_at BEFORE UPDATE ON sis.exams
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE sis.exam_marks (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  exam_id         uuid NOT NULL,
  student_id      uuid NOT NULL,
  section_id      uuid NOT NULL,
  subject         text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 60),
  max_marks       numeric(6,2) NOT NULL,
  marks           numeric(6,2),
  absent          boolean NOT NULL DEFAULT false,
  source          text NOT NULL DEFAULT 'mark',
  recorded_by     uuid NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  version         int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT exam_marks_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT exam_marks_one_per_subject UNIQUE (tenant_id, exam_id, student_id, subject),
  CONSTRAINT exam_marks_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT exam_marks_exam_fk FOREIGN KEY (tenant_id, exam_id)
    REFERENCES sis.exams (tenant_id, id),
  CONSTRAINT exam_marks_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT exam_marks_section_fk FOREIGN KEY (tenant_id, section_id)
    REFERENCES core.sections (tenant_id, id),
  CONSTRAINT exam_marks_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT exam_marks_max_range CHECK (max_marks > 0 AND max_marks <= 1000),
  CONSTRAINT exam_marks_marks_range CHECK (marks IS NULL OR (marks >= 0 AND marks <= max_marks)),
  CONSTRAINT exam_marks_absent_has_no_marks CHECK (absent = (marks IS NULL)),
  CONSTRAINT exam_marks_source_check CHECK (source IN ('mark','import'))
);
CREATE INDEX exam_marks_section_idx ON sis.exam_marks (tenant_id, exam_id, section_id);
CREATE INDEX exam_marks_student_idx ON sis.exam_marks (tenant_id, student_id);
CREATE TRIGGER exam_marks_set_updated_at BEFORE UPDATE ON sis.exam_marks
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE sis.behaviour_notes (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  student_id              uuid NOT NULL,
  section_id              uuid NOT NULL,
  category                text NOT NULL,
  noted_on                date NOT NULL,
  body_ciphertext         bytea NOT NULL,
  key_version             int NOT NULL CHECK (key_version >= 1),
  created_by              uuid NOT NULL,
  created_by_membership   uuid NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT behaviour_notes_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT behaviour_notes_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT behaviour_notes_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT behaviour_notes_section_fk FOREIGN KEY (tenant_id, section_id)
    REFERENCES core.sections (tenant_id, id),
  CONSTRAINT behaviour_notes_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT behaviour_notes_membership_fk FOREIGN KEY (tenant_id, created_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT behaviour_notes_category_check
    CHECK (category IN ('positive','observation','concern')),
  CONSTRAINT behaviour_notes_ciphertext_size CHECK (octet_length(body_ciphertext) <= 4096)
);
CREATE INDEX behaviour_notes_student_idx
  ON sis.behaviour_notes (tenant_id, student_id, noted_on DESC);
CREATE INDEX behaviour_notes_noted_idx ON sis.behaviour_notes (tenant_id, noted_on);

CREATE TABLE sis.insight_flags (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  student_id            uuid NOT NULL,
  section_id            uuid NOT NULL,
  indicator             text NOT NULL,
  rule                  text NOT NULL,
  rules_version         int NOT NULL CHECK (rules_version >= 1),
  basis                 text NOT NULL CHECK (basis ~ '^[a-z0-9][a-z0-9:._-]{0,79}$'),
  evidence              jsonb NOT NULL DEFAULT '{}'::jsonb,
  status                text NOT NULL DEFAULT 'open',
  owner_membership_id   uuid,
  raised_on             date NOT NULL,
  due_on                date NOT NULL,
  raised_by             uuid,
  first_action_at       timestamptz,
  closed_at             timestamptz,
  closed_by             uuid,
  close_reason          text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT insight_flags_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT insight_flags_basis_key UNIQUE (tenant_id, student_id, rule, basis),
  CONSTRAINT insight_flags_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT insight_flags_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT insight_flags_section_fk FOREIGN KEY (tenant_id, section_id)
    REFERENCES core.sections (tenant_id, id),
  CONSTRAINT insight_flags_owner_fk FOREIGN KEY (tenant_id, owner_membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT insight_flags_raised_by_fk FOREIGN KEY (raised_by) REFERENCES core.users (id),
  CONSTRAINT insight_flags_closed_by_fk FOREIGN KEY (closed_by) REFERENCES core.users (id),
  CONSTRAINT insight_flags_indicator_check
    CHECK (indicator IN ('attendance','behaviour','course')),
  CONSTRAINT insight_flags_rule_check CHECK (rule IN ('attendance_streak','attendance_rate',
    'course_low','course_decline','behaviour_concerns','manual')),
  CONSTRAINT insight_flags_status_check CHECK (status IN ('open','in_progress','closed')),
  CONSTRAINT insight_flags_close_reason_check CHECK (close_reason IS NULL OR close_reason IN
    ('improved','support_in_place','parent_informed','no_concern','student_left',
     'raised_in_error')),
  CONSTRAINT insight_flags_evidence_object CHECK (jsonb_typeof(evidence) = 'object'),
  CONSTRAINT insight_flags_due_after_raise CHECK (due_on >= raised_on),
  -- Rules raise flags on their own; only a manual flag names the person who raised it.
  CONSTRAINT insight_flags_manual_raiser CHECK ((rule = 'manual') = (raised_by IS NOT NULL)),
  -- PRV-005: a flag leaves "open" only after a person acted on it.
  CONSTRAINT insight_flags_actioned CHECK (status = 'open' OR first_action_at IS NOT NULL),
  CONSTRAINT insight_flags_closed_fields CHECK ((status = 'closed') =
    (closed_at IS NOT NULL AND closed_by IS NOT NULL AND close_reason IS NOT NULL))
);
-- FR-EW-003: at most one open flag per student and rule (manual flags excepted).
CREATE UNIQUE INDEX insight_flags_one_open ON sis.insight_flags (tenant_id, student_id, rule)
  WHERE status <> 'closed' AND rule <> 'manual';
CREATE INDEX insight_flags_owner_idx ON sis.insight_flags (tenant_id, owner_membership_id, due_on)
  WHERE status <> 'closed';
CREATE INDEX insight_flags_student_idx ON sis.insight_flags (tenant_id, student_id, raised_on DESC);
CREATE INDEX insight_flags_list_idx ON sis.insight_flags (tenant_id, status, due_on, id);
CREATE INDEX insight_flags_closed_idx ON sis.insight_flags (tenant_id, closed_at)
  WHERE status = 'closed';
CREATE TRIGGER insight_flags_set_updated_at BEFORE UPDATE ON sis.insight_flags
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE sis.flag_actions (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  flag_id                 uuid NOT NULL,
  kind                    text NOT NULL,
  acted_on                date NOT NULL,
  note_ciphertext         bytea,
  key_version             int CHECK (key_version >= 1),
  created_by              uuid NOT NULL,
  created_by_membership   uuid NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT flag_actions_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT flag_actions_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT flag_actions_flag_fk FOREIGN KEY (tenant_id, flag_id)
    REFERENCES sis.insight_flags (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT flag_actions_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT flag_actions_membership_fk FOREIGN KEY (tenant_id, created_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT flag_actions_kind_check CHECK (kind IN ('raised','talked_with_student',
    'called_parent','met_parent','home_visit','remedial_support','referred_counsellor',
    'referred_principal','other','closed')),
  CONSTRAINT flag_actions_note_key CHECK ((note_ciphertext IS NULL) = (key_version IS NULL)),
  CONSTRAINT flag_actions_ciphertext_size
    CHECK (note_ciphertext IS NULL OR octet_length(note_ciphertext) <= 8192)
);
CREATE INDEX flag_actions_flag_idx ON sis.flag_actions (tenant_id, flag_id, created_at);

CREATE TABLE sis.insight_settings (
  id            uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL,
  rules         jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  version       int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT insight_settings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT insight_settings_one_per_school UNIQUE (tenant_id),
  CONSTRAINT insight_settings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT insight_settings_updated_by_fk FOREIGN KEY (updated_by) REFERENCES core.users (id),
  CONSTRAINT insight_settings_rules_object CHECK (jsonb_typeof(rules) = 'object')
);
CREATE TRIGGER insight_settings_set_updated_at BEFORE UPDATE ON sis.insight_settings
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

# Children before parents (the order the modules' offboarding purges delete them in).
TENANT_TABLES = (
    "sis.flag_actions",
    "sis.insight_flags",
    "sis.behaviour_notes",
    "sis.insight_settings",
    "sis.exam_marks",
    "sis.exams",
    "sis.attendance_marks",
)

GRANTS_SQL = r"""
-- School records: corrected in place (audited), never deleted by the app.
REVOKE DELETE, UPDATE, TRUNCATE ON sis.attendance_marks FROM sos_app;
GRANT UPDATE (section_id, status, source, recorded_by, updated_at, version)
  ON sis.attendance_marks TO sos_app;
REVOKE DELETE, UPDATE, TRUNCATE ON sis.exams FROM sos_app;
GRANT UPDATE (name, held_on, updated_at, version) ON sis.exams TO sos_app;
REVOKE DELETE, UPDATE, TRUNCATE ON sis.exam_marks FROM sos_app;
GRANT UPDATE (section_id, max_marks, marks, absent, source, recorded_by, updated_at, version)
  ON sis.exam_marks TO sos_app;
-- Notes: the text changes only by key rotation; rows are deleted by erasure and retention.
REVOKE UPDATE, TRUNCATE ON sis.behaviour_notes FROM sos_app;
GRANT UPDATE (body_ciphertext, key_version) ON sis.behaviour_notes TO sos_app;
-- Flags: what the rule saw never changes; the workflow columns do.
REVOKE UPDATE, TRUNCATE ON sis.insight_flags FROM sos_app;
GRANT UPDATE (status, owner_membership_id, first_action_at, closed_at, closed_by, close_reason,
              updated_at, version)
  ON sis.insight_flags TO sos_app;
-- Actions are an append-only log (deleted only with their flag).
REVOKE DELETE, UPDATE, TRUNCATE ON sis.flag_actions FROM sos_app;
GRANT UPDATE (note_ciphertext, key_version) ON sis.flag_actions TO sos_app;
REVOKE DELETE, UPDATE, TRUNCATE ON sis.insight_settings FROM sos_app;
GRANT UPDATE (rules, updated_by, updated_at, version) ON sis.insight_settings TO sos_app;
-- Restricted insights are not for reporting (08 §4 PRV-004).
REVOKE ALL ON sis.behaviour_notes, sis.insight_flags, sis.flag_actions, sis.insight_settings
  FROM sos_readonly;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "attendance.record",
        "description": "Mark and import attendance (class teachers: their sections)",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "attendance.read",
        "description": "See attendance registers (class teachers: their sections)",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "exam.manage",
        "description": "Add exams to the academic year",
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "marks.record",
        "description": "Enter and import marks (class teachers: their sections)",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "marks.read",
        "description": "See marks (class teachers: their sections)",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "insights.note",
        "description": "Write behaviour notes about students (educational purposes only)",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "insights.act",
        "description": "Follow up early-warning flags: record actions, raise and close flags",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "insights.manage",
        "description": (
            "Reassign flags, set the school's early-warning thresholds and erase notes or flags"
        ),
        "sensitivity": "critical",
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
