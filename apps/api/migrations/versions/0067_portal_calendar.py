"""R3-1D portal calendar and readiness reminders (ADR-0053; FR-RDY-001..010; docs/05 §16.6).

The expected portal windows ship in ``app/dq/config/portal_calendar.yaml`` (``verified: false``,
D3); the school's own confirmations and events, and the reminders already sent, are tenant
tables in ``ops`` (no new schema, D46). Both follow the R3 rules (docs/05 §16): ``tenant_id``
first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``, composite FKs,
the restrictive ``offboarding_purge`` policy with ``SELECT, DELETE`` for ``sos_purger``
(ADR-0029), no ``definer_access``, nothing for ``sos_platform``.

- ``ops.portal_calendar_dates`` (C1): per school, academic year and event the confirmed
  ``opens_on``/``closes_on``, where the date came from (``source_kind``: ``circular``,
  ``portal``, ``phone``, ``other``; optional ``document_id``, cleared if the document is
  deleted), who confirmed it (user and membership) and the owner of the work (a membership,
  optional). An event key from the calendar file (``udise_plus_2026_freeze``), or
  ``school:<slug>`` for the school's own event, which also carries its ``label`` and the
  readiness ``profile_keys`` it concerns. One row per (school, year, event). The app may
  update the confirmation columns and delete a row (undo a confirmation, remove an own event).
- ``ops.portal_reminders`` (C1): **append-only**. One row per (school, year, event, lead day):
  the idempotency key of the daily reminder job (FR-RDY-008), with the task it created or
  updated (nullable: an event without an owner gets no task) and how many people were
  notified. Trigger ``portal_reminders_append_only`` refuses UPDATE and DELETE except in the
  offboarding purge.
- ``ops.tasks``: ``source`` gains ``portal_calendar``; new ``portal_event_key`` (set exactly when
  ``source = 'portal_calendar'``, never changed by the app); the citation rule applies to
  ``circular`` tasks only (portal tasks cite no circular).

The permission catalog gets ``readiness.calendar.manage`` (confirm portal dates, add the
school's own events, choose owners; principal, office admin, exam coordinator; school-wide, no
step-up; docs/07 §6.2). Role grants of existing schools need the post-migration system-role
sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the two tables (lossy: confirmed dates, the
school's own events and the reminder history), turns portal tasks into manual tasks (the
owner keeps the work; FORCE lifted for that one UPDATE and restored in the same transaction,
as 0054), drops ``portal_event_key``, restores the 0034 checks, and deletes the permission
unless a role still holds it.

Revision ID: 0067_portal_calendar
Revises: 0062_messaging_finish
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_portal_calendar"
down_revision = "0062_messaging_finish"
branch_labels = None
depends_on = None

# Children before parents (the order the dq module's offboarding purge deletes them in).
TENANT_TABLES = ("ops.portal_reminders", "ops.portal_calendar_dates")

UPGRADE_SQL = r"""
CREATE TABLE ops.portal_calendar_dates (
  id                       uuid PRIMARY KEY,
  tenant_id                uuid NOT NULL,
  academic_year_id         uuid NOT NULL,
  event_key                text NOT NULL,
  label                    text,
  opens_on                 date NOT NULL,
  closes_on                date NOT NULL,
  profile_keys             text[] NOT NULL DEFAULT '{}',
  source_kind              text NOT NULL,
  document_id              uuid,
  owner_membership_id      uuid,
  confirmed_by             uuid NOT NULL,
  confirmed_by_membership  uuid NOT NULL,
  confirmed_at             timestamptz NOT NULL DEFAULT now(),
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  version                  int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT portal_calendar_dates_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT portal_calendar_dates_once UNIQUE (tenant_id, academic_year_id, event_key),
  CONSTRAINT portal_calendar_dates_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT portal_calendar_dates_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT portal_calendar_dates_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id),
  CONSTRAINT portal_calendar_dates_owner_fk FOREIGN KEY (tenant_id, owner_membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT portal_calendar_dates_confirmer_fk FOREIGN KEY (tenant_id, confirmed_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT portal_calendar_dates_confirmed_by_fk FOREIGN KEY (confirmed_by)
    REFERENCES core.users (id),
  CONSTRAINT portal_calendar_dates_event_key CHECK (
    event_key ~ '^[a-z][a-z0-9_]{2,63}$' OR event_key ~ '^school:[a-z0-9][a-z0-9-]{1,47}$'),
  -- The school's own events carry their label; calendar-file events take it from the file.
  CONSTRAINT portal_calendar_dates_own_label CHECK (
    (event_key LIKE 'school:%') = (label IS NOT NULL)),
  CONSTRAINT portal_calendar_dates_label CHECK (
    label IS NULL OR char_length(label) BETWEEN 3 AND 120),
  CONSTRAINT portal_calendar_dates_profiles CHECK (
    cardinality(profile_keys) <= 5 AND (event_key LIKE 'school:%' OR cardinality(profile_keys) = 0)),
  CONSTRAINT portal_calendar_dates_window CHECK (closes_on >= opens_on),
  CONSTRAINT portal_calendar_dates_source_kind_check CHECK (
    source_kind IN ('circular','portal','phone','other'))
);
CREATE INDEX portal_calendar_dates_owner_idx ON ops.portal_calendar_dates
  (tenant_id, owner_membership_id) WHERE owner_membership_id IS NOT NULL;
CREATE INDEX portal_calendar_dates_document_idx ON ops.portal_calendar_dates
  (tenant_id, document_id) WHERE document_id IS NOT NULL;
CREATE TRIGGER portal_calendar_dates_set_updated_at BEFORE UPDATE ON ops.portal_calendar_dates
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

ALTER TABLE ops.tasks ADD COLUMN portal_event_key text;
ALTER TABLE ops.tasks DROP CONSTRAINT tasks_source_check;
ALTER TABLE ops.tasks ADD CONSTRAINT tasks_source_check
  CHECK (source IN ('manual','circular','portal_calendar'));
ALTER TABLE ops.tasks DROP CONSTRAINT tasks_circular_has_citation;
ALTER TABLE ops.tasks ADD CONSTRAINT tasks_circular_has_citation
  CHECK (source <> 'circular' OR citation IS NOT NULL);
ALTER TABLE ops.tasks ADD CONSTRAINT tasks_portal_event_key CHECK (
  (source = 'portal_calendar') = (portal_event_key IS NOT NULL)
  AND (portal_event_key IS NULL OR char_length(portal_event_key) BETWEEN 3 AND 70));
CREATE INDEX tasks_portal_event_idx ON ops.tasks (tenant_id, portal_event_key, due_on)
  WHERE portal_event_key IS NOT NULL;

CREATE TABLE ops.portal_reminders (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  academic_year_id  uuid NOT NULL,
  event_key         text NOT NULL,
  lead_day          int NOT NULL CHECK (lead_day BETWEEN 0 AND 120),
  closes_on         date NOT NULL,
  task_id           uuid,
  notified          int NOT NULL DEFAULT 0 CHECK (notified >= 0),
  sent_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT portal_reminders_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT portal_reminders_once UNIQUE (tenant_id, academic_year_id, event_key, lead_day),
  CONSTRAINT portal_reminders_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT portal_reminders_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT portal_reminders_task_fk FOREIGN KEY (tenant_id, task_id)
    REFERENCES ops.tasks (tenant_id, id),
  CONSTRAINT portal_reminders_event_key CHECK (
    event_key ~ '^[a-z][a-z0-9_]{2,63}$' OR event_key ~ '^school:[a-z0-9][a-z0-9-]{1,47}$')
);
CREATE INDEX portal_reminders_task_idx ON ops.portal_reminders (tenant_id, task_id)
  WHERE task_id IS NOT NULL;

-- Append-only: UPDATE always refused; DELETE only by the offboarding purge.
CREATE FUNCTION ops.tg_portal_reminders_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'portal reminders are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'portal_reminders_append_only';
END;
$$;
CREATE TRIGGER portal_reminders_append_only BEFORE UPDATE OR DELETE ON ops.portal_reminders
  FOR EACH ROW EXECUTE FUNCTION ops.tg_portal_reminders_append_only();
"""

GRANTS_SQL = r"""
REVOKE UPDATE, TRUNCATE, REFERENCES, TRIGGER ON ops.portal_calendar_dates FROM sos_app;
GRANT UPDATE (opens_on, closes_on, label, profile_keys, source_kind, document_id,
  owner_membership_id, confirmed_by, confirmed_by_membership, confirmed_at, updated_at, version)
  ON ops.portal_calendar_dates TO sos_app;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ops.portal_reminders FROM sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "readiness.calendar.manage",
        "description": (
            "Confirm portal and board dates, add the school's own events and choose who owns "
            "the work"
        ),
        "sensitivity": "normal",
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

# The owner lifts FORCE for the one UPDATE (portal tasks of every school become manual tasks);
# FORCE is restored before the constraints change, in the same transaction (0054 pattern).
UNFORCE = "ALTER TABLE ops.tasks NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner turns the portal tasks of every school into manual tasks on downgrade; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE ops.tasks FORCE ROW LEVEL SECURITY"
MANUAL_TASKS = (
    "UPDATE ops.tasks SET source = 'manual', portal_event_key = NULL "
    "WHERE source = 'portal_calendar'"
)

DOWNGRADE_SQL = r"""
DROP INDEX IF EXISTS ops.tasks_portal_event_idx;
ALTER TABLE ops.tasks DROP CONSTRAINT IF EXISTS tasks_portal_event_key;
ALTER TABLE ops.tasks DROP COLUMN IF EXISTS portal_event_key;
ALTER TABLE ops.tasks DROP CONSTRAINT tasks_circular_has_citation;
ALTER TABLE ops.tasks ADD CONSTRAINT tasks_circular_has_citation
  CHECK (source = 'manual' OR citation IS NOT NULL);
ALTER TABLE ops.tasks DROP CONSTRAINT tasks_source_check;
ALTER TABLE ops.tasks ADD CONSTRAINT tasks_source_check CHECK (source IN ('manual','circular'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
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
    op.execute("DROP FUNCTION IF EXISTS ops.tg_portal_reminders_append_only()")
    op.execute(UNFORCE)
    op.execute(MANUAL_TASKS)
    op.execute(REFORCE)
    op.execute(DOWNGRADE_SQL)
