"""School calendar: working days and holidays per academic year (FT-06; lead decision D85;
FR-CAL-001..FR-CAL-020; docs/05 §6.4).

- ``sis.school_calendars``: one row per school and academic year (``UNIQUE (tenant_id,
  academic_year_id)``): the weekly pattern. ``weekly_off`` holds the weekdays off (Python
  numbering, 0 = Monday .. 6 = Sunday), ``saturdays_off`` the Saturdays of the month that are off
  (1..5, e.g. ``{2}`` for the second Saturday). A year without a row has no calendar: attendance
  keeps the old rule (a school day is a date with any mark) and nothing is refused.
- ``sis.calendar_entries``: a holiday, a vacation or an extra working day (``kind``), named,
  from ``starts_on`` to ``ends_on`` (at most 92 days), for the whole school (``class_id`` null)
  or one class (a per-class exception, e.g. an exam day off for the other classes).
  ``suggestion_key`` names the unverified suggestion (``app/academics/calendar.yaml``) the school
  adopted, once per year and scope. The composite FK into ``sis.school_calendars`` means an entry
  exists only for a year that has a calendar.

No personal data: dates, names of holidays, class ids. Tenant tables like 0035/0084
(``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``,
composite FKs, offboarding ``offboarding_purge`` and ``sos_purger`` grants; ADR-0029).
``sos_app``: calendars SELECT, INSERT and UPDATE of the pattern columns only; entries SELECT,
INSERT and DELETE (a mistaken holiday is removed, audited), never UPDATE or TRUNCATE.

Expand-only (invariant 12). Downgrade drops both tables (lossy: the schools' calendars).

Revision ID: 0088_school_calendar
Revises: 0087_legal_holds
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "0088_school_calendar"
down_revision = "0087_legal_holds"
branch_labels = None
depends_on = None

CALENDARS = "sis.school_calendars"
ENTRIES = "sis.calendar_entries"

TABLES_SQL = r"""
CREATE TABLE sis.school_calendars (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  academic_year_id uuid NOT NULL,
  weekly_off       smallint[] NOT NULL DEFAULT '{6}',
  saturdays_off    smallint[] NOT NULL DEFAULT '{}',
  created_by       uuid NOT NULL,
  updated_by       uuid NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT school_calendars_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT school_calendars_year_key UNIQUE (tenant_id, academic_year_id),
  CONSTRAINT school_calendars_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT school_calendars_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT school_calendars_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT school_calendars_updated_by_fk FOREIGN KEY (updated_by) REFERENCES core.users (id),
  CONSTRAINT school_calendars_weekly_off_check
    CHECK (weekly_off <@ '{0,1,2,3,4,5,6}'::smallint[] AND cardinality(weekly_off) <= 6),
  CONSTRAINT school_calendars_saturdays_off_check
    CHECK (saturdays_off <@ '{1,2,3,4,5}'::smallint[])
);
CREATE TRIGGER school_calendars_set_updated_at BEFORE UPDATE ON sis.school_calendars
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE sis.calendar_entries (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  academic_year_id uuid NOT NULL,
  class_id         uuid,
  kind             text NOT NULL,
  name             text NOT NULL,
  starts_on        date NOT NULL,
  ends_on          date NOT NULL,
  suggestion_key   text,
  created_by       uuid NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT calendar_entries_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT calendar_entries_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT calendar_entries_calendar_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES sis.school_calendars (tenant_id, academic_year_id),
  CONSTRAINT calendar_entries_class_fk FOREIGN KEY (tenant_id, class_id)
    REFERENCES core.classes (tenant_id, id),
  CONSTRAINT calendar_entries_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT calendar_entries_kind_check CHECK (kind IN ('holiday','vacation','working_day')),
  CONSTRAINT calendar_entries_name_len CHECK (char_length(name) BETWEEN 1 AND 80),
  CONSTRAINT calendar_entries_dates CHECK (ends_on >= starts_on AND ends_on - starts_on <= 92),
  CONSTRAINT calendar_entries_suggestion_format
    CHECK (suggestion_key IS NULL OR suggestion_key ~ '^[a-z][a-z0-9_]{1,62}$')
);
CREATE INDEX calendar_entries_year_idx
  ON sis.calendar_entries (tenant_id, academic_year_id, starts_on);
CREATE UNIQUE INDEX calendar_entries_suggestion_key
  ON sis.calendar_entries (tenant_id, academic_year_id,
    coalesce(class_id, '00000000-0000-0000-0000-000000000000'::uuid), suggestion_key)
  WHERE suggestion_key IS NOT NULL;
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE ON sis.school_calendars FROM sos_app;
GRANT SELECT, INSERT ON sis.school_calendars TO sos_app;
GRANT UPDATE (weekly_off, saturdays_off, updated_by, updated_at, version)
  ON sis.school_calendars TO sos_app;
REVOKE UPDATE, TRUNCATE ON sis.calendar_entries FROM sos_app;
GRANT SELECT, INSERT, DELETE ON sis.calendar_entries TO sos_app;
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in (CALENDARS, ENTRIES):
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


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {ENTRIES}")
    op.execute(f"DROP TABLE IF EXISTS {CALENDARS}")
