"""Year-end promotions: ``sis.promotion_runs`` and ``sis.promotion_items`` (FR-TEN-011,
US-202 AC2; docs/05 §14; owner decisions 2026-09-27).

A promotion moves the active enrolments of one academic year (the *source*) into the next one
(the *target*) in one transaction: class N -> N+1 by class order, held-back students into the
same class again, students in the final class graduate (no new enrolment), students who left are
skipped. Each committed promotion is one ``promotion_runs`` row; ``promotion_items`` records, per
student, the enrolment that was closed, the enrolment that was opened and the versions they were
left at, so the promotion can be undone within 24 hours while nothing changed on top of it (the
same rule as an import revert).

- Both tables are tenant-owned: ``tenant_id NOT NULL``, RLS ``ENABLE`` + ``FORCE`` with the
  standard ``tenant_isolation`` policy, ``UNIQUE (tenant_id, id)`` on the run, composite FKs.
- One committed (not undone) promotion per source year: partial unique index.
- ``to_enrollment_id`` is set to NULL when an undo removes the enrolment it opened; the other
  references block deletes (an import revert of a promoted student answers
  ``409 import_has_dependents``).
- The app may insert runs and items and update only the run's undo columns; nothing is deleted
  by the app (column grants).

Downgrade drops both tables. Enrolments opened and closed by promotions stay as they are (they
are ordinary enrolments); only the undo bookkeeping is lost.

Revision ID: 0022_promotions
Revises: 0020_provisioning_runs
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0022_promotions"
down_revision = "0020_provisioning_runs"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE sis.promotion_runs (
  id                     uuid PRIMARY KEY,
  tenant_id              uuid NOT NULL,
  from_academic_year_id  uuid NOT NULL,
  to_academic_year_id    uuid NOT NULL,
  status                 text NOT NULL DEFAULT 'committed',
  promoted_count         int NOT NULL DEFAULT 0,
  held_back_count        int NOT NULL DEFAULT 0,
  graduated_count        int NOT NULL DEFAULT 0,
  skipped_count          int NOT NULL DEFAULT 0,
  plan_fingerprint       text NOT NULL,
  committed_by           uuid,
  committed_at           timestamptz NOT NULL DEFAULT now(),
  undone_by              uuid,
  undone_at              timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  version                int NOT NULL DEFAULT 1,
  CONSTRAINT promotion_runs_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT promotion_runs_from_year_fk FOREIGN KEY (tenant_id, from_academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT promotion_runs_to_year_fk FOREIGN KEY (tenant_id, to_academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT promotion_runs_committed_by_fk FOREIGN KEY (committed_by)
    REFERENCES core.users (id) ON DELETE SET NULL,
  CONSTRAINT promotion_runs_undone_by_fk FOREIGN KEY (undone_by)
    REFERENCES core.users (id) ON DELETE SET NULL,
  CONSTRAINT promotion_runs_status_check CHECK (status IN ('committed','undone')),
  CONSTRAINT promotion_runs_years_differ CHECK (from_academic_year_id <> to_academic_year_id),
  CONSTRAINT promotion_runs_counts_positive CHECK (
    promoted_count >= 0 AND held_back_count >= 0 AND graduated_count >= 0
    AND skipped_count >= 0),
  CONSTRAINT promotion_runs_fingerprint_format CHECK (plan_fingerprint ~ '^[0-9a-f]{64}$'),
  CONSTRAINT promotion_runs_undone_consistent CHECK ((status = 'undone') = (undone_at IS NOT NULL)),
  CONSTRAINT promotion_runs_version_positive CHECK (version >= 1)
);
CREATE UNIQUE INDEX promotion_runs_one_committed
  ON sis.promotion_runs (tenant_id, from_academic_year_id) WHERE status = 'committed';
CREATE INDEX promotion_runs_from_year_idx
  ON sis.promotion_runs (tenant_id, from_academic_year_id, committed_at DESC);

CREATE TABLE sis.promotion_items (
  tenant_id                uuid NOT NULL,
  run_id                   uuid NOT NULL,
  student_id               uuid NOT NULL,
  outcome                  text NOT NULL,
  from_enrollment_id       uuid NOT NULL,
  from_enrollment_version  int NOT NULL,
  to_enrollment_id         uuid,
  to_enrollment_version    int,
  previous_student_status  text NOT NULL,
  created_at               timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT promotion_items_pkey PRIMARY KEY (tenant_id, run_id, student_id),
  CONSTRAINT promotion_items_run_fk FOREIGN KEY (tenant_id, run_id)
    REFERENCES sis.promotion_runs (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT promotion_items_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT promotion_items_from_enrollment_fk FOREIGN KEY (tenant_id, from_enrollment_id)
    REFERENCES sis.enrollments (tenant_id, id),
  CONSTRAINT promotion_items_to_enrollment_fk FOREIGN KEY (tenant_id, to_enrollment_id)
    REFERENCES sis.enrollments (tenant_id, id) ON DELETE SET NULL (to_enrollment_id),
  CONSTRAINT promotion_items_outcome_check
    CHECK (outcome IN ('promoted','held_back','graduated')),
  CONSTRAINT promotion_items_previous_status_check
    CHECK (previous_student_status IN ('provisional','active','left','graduated')),
  CONSTRAINT promotion_items_versions_positive CHECK (
    from_enrollment_version >= 1 AND (to_enrollment_version IS NULL OR to_enrollment_version >= 1)),
  CONSTRAINT promotion_items_graduates_have_no_enrollment CHECK (
    (outcome = 'graduated') = (to_enrollment_version IS NULL))
);
CREATE INDEX promotion_items_student_idx ON sis.promotion_items (tenant_id, student_id);
CREATE INDEX promotion_items_to_enrollment_idx ON sis.promotion_items (tenant_id, to_enrollment_id)
  WHERE to_enrollment_id IS NOT NULL;
CREATE INDEX promotion_items_from_enrollment_idx
  ON sis.promotion_items (tenant_id, from_enrollment_id);
"""

TENANT_TABLES = ("sis.promotion_runs", "sis.promotion_items")

GRANTS_SQL = r"""
-- Runs and items are bookkeeping for undo: the app inserts them and may only record the undo.
REVOKE UPDATE, DELETE ON sis.promotion_runs FROM sos_app;
GRANT UPDATE (status, undone_by, undone_at, version, updated_at) ON sis.promotion_runs TO sos_app;
REVOKE UPDATE, DELETE ON sis.promotion_items FROM sos_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    op.execute(
        "CREATE TRIGGER promotion_runs_set_updated_at BEFORE UPDATE ON sis.promotion_runs "
        "FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at()"
    )
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
    op.execute("DROP TABLE IF EXISTS sis.promotion_items")
    op.execute("DROP TABLE IF EXISTS sis.promotion_runs")
