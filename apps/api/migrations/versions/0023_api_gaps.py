"""Archive academic years, classes and sections (US-202, FR-TEN-010; docs/05 §4, docs/09).

- ``core.academic_years``, ``core.classes``, ``core.sections`` gain ``archived_at timestamptz``
  (NULL = in use). Archived rows stay in place (enrolments, documents, exports and scopes keep
  referring to them); the API hides them from lists unless ``include_archived=true``.
- ``academic_years_current_not_archived``: the current year cannot be archived.
- ``core.tg_structure_archive_guard()`` (SECURITY INVOKER, like the 0003 helpers): archiving a
  year, class or section that still has an ACTIVE enrolment (``sis.enrollments.status =
  'active'``) fails with ``object_not_in_prerequisite_state`` and constraint name
  ``structure_active_enrolments`` (API: 409 ``structure_in_use``). It runs as the caller, so RLS
  applies; the explicit ``tenant_id`` comparisons keep it correct for any caller. Unarchiving is
  never refused by the guard.

Expand-only: the columns are nullable with no default, so code that predates them keeps working
(it never archives). Nothing is backfilled.

Downgrade drops the trigger, function, CHECK and columns: archived rows become ordinary rows
again (the ``*.archived`` audit events remain). No other data is lost.

Revision ID: 0023_api_gaps
Revises: 0022_promotions
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0023_api_gaps"
down_revision = "0022_promotions"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE core.academic_years ADD COLUMN archived_at timestamptz;
ALTER TABLE core.classes ADD COLUMN archived_at timestamptz;
ALTER TABLE core.sections ADD COLUMN archived_at timestamptz;
ALTER TABLE core.academic_years ADD CONSTRAINT academic_years_current_not_archived
  CHECK (NOT (is_current AND archived_at IS NOT NULL));

-- A year, class or section with an active enrolment cannot be archived (only on the transition
-- to archived; unarchiving and other updates pass).
CREATE FUNCTION core.tg_structure_archive_guard() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  in_use boolean;
BEGIN
  IF NEW.archived_at IS NULL OR OLD.archived_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  IF TG_TABLE_NAME = 'academic_years' THEN
    SELECT EXISTS (SELECT 1 FROM sis.enrollments AS e
                   WHERE e.tenant_id = NEW.tenant_id AND e.academic_year_id = NEW.id
                     AND e.status = 'active') INTO in_use;
  ELSIF TG_TABLE_NAME = 'sections' THEN
    SELECT EXISTS (SELECT 1 FROM sis.enrollments AS e
                   WHERE e.tenant_id = NEW.tenant_id AND e.section_id = NEW.id
                     AND e.status = 'active') INTO in_use;
  ELSE
    SELECT EXISTS (SELECT 1 FROM sis.enrollments AS e
                   JOIN core.sections AS s ON s.tenant_id = e.tenant_id AND s.id = e.section_id
                   WHERE e.tenant_id = NEW.tenant_id AND s.class_id = NEW.id
                     AND e.status = 'active') INTO in_use;
  END IF;
  IF in_use THEN
    RAISE EXCEPTION 'still has active enrolments'
      USING ERRCODE = 'object_not_in_prerequisite_state',
            CONSTRAINT = 'structure_active_enrolments';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION core.tg_structure_archive_guard() FROM PUBLIC;

CREATE TRIGGER academic_years_archive_guard BEFORE UPDATE OF archived_at ON core.academic_years
  FOR EACH ROW EXECUTE FUNCTION core.tg_structure_archive_guard();
CREATE TRIGGER classes_archive_guard BEFORE UPDATE OF archived_at ON core.classes
  FOR EACH ROW EXECUTE FUNCTION core.tg_structure_archive_guard();
CREATE TRIGGER sections_archive_guard BEFORE UPDATE OF archived_at ON core.sections
  FOR EACH ROW EXECUTE FUNCTION core.tg_structure_archive_guard();
"""

DOWNGRADE_SQL = r"""
DROP TRIGGER IF EXISTS sections_archive_guard ON core.sections;
DROP TRIGGER IF EXISTS classes_archive_guard ON core.classes;
DROP TRIGGER IF EXISTS academic_years_archive_guard ON core.academic_years;
DROP FUNCTION IF EXISTS core.tg_structure_archive_guard();
ALTER TABLE core.academic_years DROP CONSTRAINT IF EXISTS academic_years_current_not_archived;
ALTER TABLE core.sections DROP COLUMN IF EXISTS archived_at;
ALTER TABLE core.classes DROP COLUMN IF EXISTS archived_at;
ALTER TABLE core.academic_years DROP COLUMN IF EXISTS archived_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
