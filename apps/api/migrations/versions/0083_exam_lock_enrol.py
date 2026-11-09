"""Exam lock and marks history (FT-01, audit YA-04) and real enrolment start dates (FT-09,
audit YA-06). Lead decision D84; FR-MRK-008..FR-MRK-020, FR-ATT-010..FR-ATT-016; docs/05 §6.4.

- ``sis.exams`` gains ``locked_at``/``locked_by`` (both set or both null) and the last unlock:
  ``unlocked_at``/``unlocked_by``/``unlock_reason`` (1..500 characters; the principal's reason,
  kept on the exam and copied into the history of every correction made while it stays
  unlocked). ``sos_app`` gets a column ``UPDATE`` grant on those five columns only.
- ``sis.exam_mark_history``: one append-only row per change of a stored mark (every change after
  the first entry, locked or not, so the history is useful): the previous and the new value
  (max marks, marks, absent), who, when, the source, ``after_lock`` (the exam had been locked
  before: shown as "Changed after lock") and the reason. C2 like the marks. Tenant table like
  0035 (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
  ``tenant_isolation``, composite FKs, offboarding ``offboarding_purge`` and ``sos_purger``
  grants; ADR-0029). ``sos_app``: SELECT and INSERT only (no UPDATE, DELETE or TRUNCATE).
- ``sis.enrollments.started_on_explicit`` (``NOT NULL DEFAULT false``; a constant default, no
  rewrite): the start date is a real date (given by a person, the admission date inside the
  year, or the date of a move between sections). Attendance before an explicit start is refused
  (422 ``before_enrolment``); implicit starts are not checked.
- One-off backfill of existing enrolments with the import rule (FT-09): an enrolment that was not
  a move and whose start date was defaulted (null, or the IST day it was created) starts on the
  student's admission date when it falls inside the enrolment's year (explicit), else, for a
  student created by a spreadsheet import, on the year start (implicit); other defaulted
  enrolments keep their date (implicit). Moves and dates a person gave are marked explicit. The
  update runs across schools as the table owner, so FORCE ROW LEVEL SECURITY is lifted on the
  three tables it reads for that one statement and restored in the same transaction (the
  0042/0049/0073 pattern); RLS stays ENABLEd throughout. Admission dates are C2 plaintext
  (``value_date``), not encrypted.
- Permission catalog: ``exam.unlock`` (critical, step-up; principal only).

Expand-only (invariant 12). Downgrade drops the history table and the new columns (lossy: the
marks history, the lock state and the explicit flags; backfilled start dates stay) and removes
``exam.unlock`` when no role holds it.

Revision ID: 0083_exam_lock_enrol
Revises: 0082_audit_ym_fixes
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0083_exam_lock_enrol"
down_revision = "0082_audit_ym_fixes"
branch_labels = None
depends_on = None

HISTORY = "sis.exam_mark_history"

EXAMS_UP = r"""
ALTER TABLE sis.exams
  ADD COLUMN locked_at     timestamptz,
  ADD COLUMN locked_by     uuid,
  ADD COLUMN unlocked_at   timestamptz,
  ADD COLUMN unlocked_by   uuid,
  ADD COLUMN unlock_reason text,
  ADD CONSTRAINT exams_locked_by_fk FOREIGN KEY (locked_by) REFERENCES core.users (id),
  ADD CONSTRAINT exams_unlocked_by_fk FOREIGN KEY (unlocked_by) REFERENCES core.users (id),
  ADD CONSTRAINT exams_lock_pair CHECK ((locked_at IS NULL) = (locked_by IS NULL)),
  ADD CONSTRAINT exams_unlock_reason_len
    CHECK (unlock_reason IS NULL OR char_length(unlock_reason) BETWEEN 1 AND 500);
GRANT UPDATE (locked_at, locked_by, unlocked_at, unlocked_by, unlock_reason)
  ON sis.exams TO sos_app;
"""

HISTORY_UP = r"""
CREATE TABLE sis.exam_mark_history (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  exam_id            uuid NOT NULL,
  mark_id            uuid NOT NULL,
  student_id         uuid NOT NULL,
  section_id         uuid NOT NULL,
  subject            text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 60),
  previous_max_marks numeric(6,2) NOT NULL,
  previous_marks     numeric(6,2),
  previous_absent    boolean NOT NULL,
  new_max_marks      numeric(6,2) NOT NULL,
  new_marks          numeric(6,2),
  new_absent         boolean NOT NULL,
  source             text NOT NULL CHECK (source IN ('mark','import')),
  after_lock         boolean NOT NULL DEFAULT false,
  reason             text CHECK (reason IS NULL OR char_length(reason) BETWEEN 1 AND 500),
  changed_by         uuid NOT NULL,
  changed_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT exam_mark_history_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT exam_mark_history_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT exam_mark_history_exam_fk FOREIGN KEY (tenant_id, exam_id)
    REFERENCES sis.exams (tenant_id, id),
  CONSTRAINT exam_mark_history_mark_fk FOREIGN KEY (tenant_id, mark_id)
    REFERENCES sis.exam_marks (tenant_id, id),
  CONSTRAINT exam_mark_history_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT exam_mark_history_section_fk FOREIGN KEY (tenant_id, section_id)
    REFERENCES core.sections (tenant_id, id),
  CONSTRAINT exam_mark_history_changed_by_fk FOREIGN KEY (changed_by) REFERENCES core.users (id)
);
CREATE INDEX exam_mark_history_exam_idx
  ON sis.exam_mark_history (tenant_id, exam_id, section_id, changed_at);
CREATE INDEX exam_mark_history_student_idx ON sis.exam_mark_history (tenant_id, student_id);
CREATE INDEX exam_mark_history_mark_idx ON sis.exam_mark_history (tenant_id, mark_id);
-- Append-only for the app: the history of a mark is never edited or removed (offboarding
-- purges it as sos_purger).
REVOKE UPDATE, DELETE, TRUNCATE ON sis.exam_mark_history FROM sos_app;
GRANT SELECT, INSERT ON sis.exam_mark_history TO sos_app;
"""

ENROL_UP = (
    "ALTER TABLE sis.enrollments ADD COLUMN started_on_explicit boolean NOT NULL DEFAULT false"
)

BACKFILL_TABLES = ("sis.enrollments", "sis.attribute_values", "core.academic_years")
UNFORCE = (
    "ALTER TABLE sis.enrollments NO FORCE ROW LEVEL SECURITY",  # nosemgrep: sos-migration-rls-bypass -- owner backfills enrolment start dates of every school in one statement; FORCE is restored in the same transaction
    "ALTER TABLE sis.attribute_values NO FORCE ROW LEVEL SECURITY",  # nosemgrep: sos-migration-rls-bypass -- read by the same backfill; FORCE is restored in the same transaction
    "ALTER TABLE core.academic_years NO FORCE ROW LEVEL SECURITY",  # nosemgrep: sos-migration-rls-bypass -- read by the same backfill; FORCE is restored in the same transaction
)
REFORCE = [f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY" for t in BACKFILL_TABLES]

# The FT-09 rule for existing rows (see the docstring). ``defaulted``: the start was never given
# (null, or the IST day the row was created, which is what the app put in for "today").
# ``moved``: the enrolment began with a move out of another section of the same year.
BACKFILL = r"""
WITH facts AS (
  SELECT e.id,
         y.starts_on,
         y.ends_on,
         (e.started_on IS NULL
           OR e.started_on = (e.created_at AT TIME ZONE 'Asia/Kolkata')::date) AS defaulted,
         EXISTS (
           SELECT 1 FROM sis.enrollments p
            WHERE p.tenant_id = e.tenant_id AND p.student_id = e.student_id
              AND p.academic_year_id = e.academic_year_id AND p.id <> e.id
              AND p.status = 'transferred' AND p.ended_on = e.started_on
         ) AS moved,
         (SELECT v.value_date
            FROM sis.attribute_values v
           WHERE v.tenant_id = e.tenant_id AND v.student_id = e.student_id
             AND v.attribute_key = 'admission_date' AND v.superseded_by IS NULL
             AND v.value_date IS NOT NULL
           ORDER BY (v.source = 'admission_register') DESC, v.recorded_at DESC
           LIMIT 1) AS admitted,
         EXISTS (
           SELECT 1 FROM sis.attribute_values v
            WHERE v.tenant_id = e.tenant_id AND v.student_id = e.student_id
              AND v.import_batch_id IS NOT NULL
         ) AS imported
    FROM sis.enrollments e
    JOIN core.academic_years y ON y.tenant_id = e.tenant_id AND y.id = e.academic_year_id
)
UPDATE sis.enrollments e
   SET started_on = CASE
         WHEN f.moved OR NOT f.defaulted THEN e.started_on
         WHEN f.admitted BETWEEN f.starts_on AND f.ends_on THEN f.admitted
         WHEN f.imported THEN f.starts_on
         ELSE e.started_on
       END,
       started_on_explicit = CASE
         WHEN f.moved OR NOT f.defaulted THEN true
         WHEN f.admitted BETWEEN f.starts_on AND f.ends_on THEN true
         ELSE false
       END
  FROM facts f
 WHERE f.id = e.id
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "exam.unlock",
        "description": "Unlock a locked exam so its marks can be corrected (with a reason)",
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
    op.execute(EXAMS_UP)
    op.execute(HISTORY_UP)
    op.execute(f"ALTER TABLE {HISTORY} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {HISTORY} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {HISTORY} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the school is
    # offboarding and the transaction-local purge flag names it.
    op.execute(f"GRANT SELECT, DELETE ON {HISTORY} TO sos_purger")
    op.execute(
        f"CREATE POLICY offboarding_purge ON {HISTORY} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed())"
    )
    op.execute(ENROL_UP)
    for stmt in UNFORCE:
        op.execute(stmt)
    op.execute(BACKFILL)
    for stmt in REFORCE:
        op.execute(stmt)
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
    op.execute("ALTER TABLE sis.enrollments DROP COLUMN IF EXISTS started_on_explicit")
    op.execute(f"DROP TABLE IF EXISTS {HISTORY}")
    op.execute(
        "REVOKE UPDATE (locked_at, locked_by, unlocked_at, unlocked_by, unlock_reason) "
        "ON sis.exams FROM sos_app"
    )
    op.execute(
        "ALTER TABLE sis.exams "
        "DROP CONSTRAINT IF EXISTS exams_unlock_reason_len, "
        "DROP CONSTRAINT IF EXISTS exams_lock_pair, "
        "DROP CONSTRAINT IF EXISTS exams_unlocked_by_fk, "
        "DROP CONSTRAINT IF EXISTS exams_locked_by_fk, "
        "DROP COLUMN IF EXISTS unlock_reason, "
        "DROP COLUMN IF EXISTS unlocked_by, "
        "DROP COLUMN IF EXISTS unlocked_at, "
        "DROP COLUMN IF EXISTS locked_by, "
        "DROP COLUMN IF EXISTS locked_at"
    )
