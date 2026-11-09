"""FA-05 management weekly brief (FR-BRF-001..FR-BRF-015; docs/05 §13).

Two tenant tables (``tenant_id`` first, ``UNIQUE (tenant_id, id)`` or a composite key, RLS
ENABLE + FORCE with ``tenant_isolation``, the restrictive ``offboarding_purge`` policy with
SELECT and DELETE for ``sos_purger``; ADR-0013, ADR-0029):

- ``ops.weekly_briefs``: one row per school and week (``week_start`` is a Monday, IST). ``counts``
  holds school-level counts only (readiness per profile, open findings, corrections waiting,
  certificates issued, portal deadlines, fee totals, Ask answers): never a name, an ID of a
  student or any child-level value. ``sos_app`` may SELECT, INSERT, DELETE (the weekly job
  deletes briefs older than the configured number of weeks) and UPDATE only ``notified`` (the
  counts of a stored week never change).
- ``ops.brief_preferences``: one row per member who changed their choice (composite FK into
  ``core.memberships``, ``ON DELETE CASCADE``). ``in_app`` (default on) and ``email`` (default
  off: e-mail is opt-in, lead decision D85).

Also: the permission catalog gets ``brief.read`` (normal, no step-up). Role grants of existing
schools change only with the post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops both tables (lossy: the stored briefs and the
members' choices) and removes the catalog key when no role holds it.

Revision ID: 0095_weekly_brief
Revises: 0094_tally_vouchers
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0095_weekly_brief"
down_revision = "0094_tally_vouchers"
branch_labels = None
depends_on = None

BRIEFS = "ops.weekly_briefs"
PREFS = "ops.brief_preferences"

TABLES_SQL = r"""
CREATE TABLE ops.weekly_briefs (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  week_start  date NOT NULL,
  counts      jsonb NOT NULL,
  notified    int NOT NULL DEFAULT 0 CHECK (notified >= 0),
  built_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT weekly_briefs_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT weekly_briefs_once UNIQUE (tenant_id, week_start),
  CONSTRAINT weekly_briefs_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT weekly_briefs_monday CHECK (extract(isodow FROM week_start) = 1),
  CONSTRAINT weekly_briefs_counts_object CHECK (jsonb_typeof(counts) = 'object')
);

CREATE TABLE ops.brief_preferences (
  tenant_id      uuid NOT NULL,
  membership_id  uuid NOT NULL,
  in_app         boolean NOT NULL DEFAULT true,
  email          boolean NOT NULL DEFAULT false,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  version        int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT brief_preferences_pkey PRIMARY KEY (tenant_id, membership_id),
  CONSTRAINT brief_preferences_member_fk FOREIGN KEY (tenant_id, membership_id)
    REFERENCES core.memberships (tenant_id, id) ON DELETE CASCADE
);
CREATE TRIGGER brief_preferences_set_updated_at BEFORE UPDATE ON ops.brief_preferences
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

GRANTS_SQL = r"""
REVOKE UPDATE, TRUNCATE, REFERENCES, TRIGGER ON ops.weekly_briefs FROM sos_app;
GRANT UPDATE (notified) ON ops.weekly_briefs TO sos_app;
REVOKE UPDATE, TRUNCATE, REFERENCES, TRIGGER ON ops.brief_preferences FROM sos_app;
GRANT UPDATE (in_app, email, updated_at, version) ON ops.brief_preferences TO sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "brief.read",
        "description": (
            "See the school's weekly management brief (counts only) and choose how to receive it"
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


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in (BRIEFS, PREFS):
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
    op.execute(f"DROP TABLE IF EXISTS {PREFS}")
    op.execute(f"DROP TABLE IF EXISTS {BRIEFS}")
