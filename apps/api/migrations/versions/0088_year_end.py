"""FT-08 year-end progression pack: annual results per student (lead decision D85;
FR-YRE-001..FR-YRE-020; docs/05 §6.4).

Tenant table ``sis.year_results`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs into ``core.academic_years``, ``sis.students`` and
``core.sections``, the restrictive ``offboarding_purge`` policy with SELECT and DELETE for
``sos_purger``; ADR-0013, ADR-0029). One row per student and academic year: the result a person
confirmed (``passed`` | ``promoted`` | ``detained`` | ``not_appeared``), what SchoolOS proposed
then (``proposed_result``), the figures shown when it was confirmed (``percent`` over the year's
exams, ``days_attended`` and ``working_days`` from the register), the reason (required for
``detained``: CHECK), who confirmed it and when, the rule version. C2 like the marks it comes
from. ``sos_app`` may SELECT, INSERT and UPDATE the decision columns; never DELETE (a decision is
a school record; only the offboarding purge removes it).

Also: the permission catalog gets ``year_result.read`` and ``year_result.confirm`` (sensitive,
no step-up). Role grants of existing schools change only with the post-migration system-role sync
(ADR-0022).

Expand-only (invariant 12). Downgrade drops the table (lossy: the confirmed results) and removes
the catalog keys when no role holds them.

Revision ID: 0088_year_end
Revises: 0087_legal_holds
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0088_year_end"
down_revision = "0087_legal_holds"
branch_labels = None
depends_on = None

TABLE = "sis.year_results"

TABLE_UP = r"""
CREATE TABLE sis.year_results (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  academic_year_id uuid NOT NULL,
  student_id       uuid NOT NULL,
  section_id       uuid NOT NULL,
  result           text NOT NULL
    CHECK (result IN ('passed','promoted','detained','not_appeared')),
  proposed_result  text
    CHECK (proposed_result IS NULL
           OR proposed_result IN ('passed','promoted','detained','not_appeared')),
  percent          numeric(4,1) CHECK (percent IS NULL OR percent BETWEEN 0 AND 100),
  days_attended    integer CHECK (days_attended IS NULL OR days_attended BETWEEN 0 AND 366),
  working_days     integer CHECK (working_days IS NULL OR working_days BETWEEN 0 AND 366),
  reason           text CHECK (reason IS NULL OR char_length(reason) BETWEEN 1 AND 500),
  rule_version     integer NOT NULL CHECK (rule_version >= 1),
  confirmed_by     uuid NOT NULL,
  confirmed_at     timestamptz NOT NULL DEFAULT now(),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          integer NOT NULL DEFAULT 1,
  CONSTRAINT year_results_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT year_results_one_per_year UNIQUE (tenant_id, academic_year_id, student_id),
  CONSTRAINT year_results_detained_reason CHECK (result <> 'detained' OR reason IS NOT NULL),
  CONSTRAINT year_results_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT year_results_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT year_results_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT year_results_section_fk FOREIGN KEY (tenant_id, section_id)
    REFERENCES core.sections (tenant_id, id),
  CONSTRAINT year_results_confirmed_by_fk FOREIGN KEY (confirmed_by) REFERENCES core.users (id)
);
CREATE INDEX year_results_section_idx
  ON sis.year_results (tenant_id, academic_year_id, section_id);
CREATE INDEX year_results_student_idx ON sis.year_results (tenant_id, student_id);
-- A decision is a school record: the app never deletes it (offboarding purges it as sos_purger).
REVOKE DELETE, UPDATE, TRUNCATE ON sis.year_results FROM sos_app;
GRANT SELECT, INSERT ON sis.year_results TO sos_app;
GRANT UPDATE (section_id, result, proposed_result, percent, days_attended, working_days, reason,
              rule_version, confirmed_by, confirmed_at, updated_at, version)
  ON sis.year_results TO sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "year_result.read",
        "description": "See annual results, held-back decisions and the UDISE+ progression sheet",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "year_result.confirm",
        "description": "Confirm each student's annual result (passed, promoted, detained, "
        "not appeared) with a reason for detention",
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


def upgrade() -> None:
    op.execute(TABLE_UP)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the school is
    # offboarding and the transaction-local purge flag names it.
    op.execute(f"GRANT SELECT, DELETE ON {TABLE} TO sos_purger")
    op.execute(
        f"CREATE POLICY offboarding_purge ON {TABLE} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed())"
    )
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
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
