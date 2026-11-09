"""FP-02 urgent closure messages (ADR-0043 amendment of 2026-11-09, lead decision D85;
FR-MSG-047..055; docs/05 §15.3). FP-01 (opt-in coverage and reach follow-up, FR-MSG-039..046)
needs no schema change.

New tenant table ``msg.closures`` follows ``0061_messaging_sends``: ``tenant_id`` first,
``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``, composite FKs, the
restrictive ``offboarding_purge`` policy with ``SELECT, DELETE`` for ``sos_purger`` (ADR-0029),
no ``definer_access``, nothing for ``sos_platform``:

- one row per urgent closure message the principal sent: the audience (``whole_school`` or the
  chosen ``section_ids`` of the current year), the closure dates (``closed_from`` ..
  ``closed_to``, at most 31 days), the reason code (config ``closures.reasons``; no free text),
  the counts shown and queued (recipients, skipped by reason, languages, the estimated Meta
  cost) and who sent it when. Insert-only for ``sos_app`` apart from DELETE for the retention
  job (13 months, with the messages).
- ``msg.messages.purpose`` also allows ``school_closure`` (the message's source is the closure
  row; ``source_type`` ``school_closure``).

The permission catalog gets ``message.closure.send`` (critical, step-up; the principal only;
docs/07 §6.2). Role grants of existing schools change only with the post-migration system-role
sync (ADR-0022).

Expand-only (invariant 12). Downgrade deletes the ``school_closure`` messages, restores the
purpose CHECK, drops the table (lossy: the closure history) and removes the catalog key when no
role holds it.

Revision ID: 0093_msg_reach_closure
Revises: 0092_udise_corrections
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0093_msg_reach_closure"
down_revision = "0092_udise_corrections"
branch_labels = None
depends_on = None

TABLE = "msg.closures"

UPGRADE_SQL = r"""
CREATE TABLE msg.closures (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  whole_school        boolean NOT NULL,
  section_ids         uuid[] NOT NULL DEFAULT '{}',
  closed_from         date NOT NULL,
  closed_to           date NOT NULL,
  reason_code         text NOT NULL,
  recipients          int NOT NULL DEFAULT 0 CHECK (recipients >= 0),
  skipped             jsonb NOT NULL DEFAULT '{}'::jsonb,
  languages           jsonb NOT NULL DEFAULT '{}'::jsonb,
  estimated_cost_inr  numeric(12,2) NOT NULL DEFAULT 0 CHECK (estimated_cost_inr >= 0),
  queued              int NOT NULL DEFAULT 0 CHECK (queued >= 0),
  sent_by             uuid NOT NULL,
  sent_at             timestamptz NOT NULL DEFAULT now(),
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT closures_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT closures_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT closures_sent_by_fk FOREIGN KEY (sent_by) REFERENCES core.users (id),
  CONSTRAINT closures_audience CHECK (CASE WHEN whole_school THEN cardinality(section_ids) = 0
    ELSE cardinality(section_ids) BETWEEN 1 AND 300 END),
  CONSTRAINT closures_dates CHECK (closed_to >= closed_from AND closed_to - closed_from <= 30),
  CONSTRAINT closures_reason_format CHECK (reason_code ~ '^[a-z][a-z_]{1,39}$'),
  CONSTRAINT closures_skipped_object CHECK (jsonb_typeof(skipped) = 'object'),
  CONSTRAINT closures_languages_object CHECK (jsonb_typeof(languages) = 'object')
);
CREATE INDEX closures_sent_idx ON msg.closures (tenant_id, sent_at DESC);

ALTER TABLE msg.messages DROP CONSTRAINT messages_purpose_check;
ALTER TABLE msg.messages ADD CONSTRAINT messages_purpose_check CHECK (purpose IN ('optin_confirm',
  'school_notice','absence_alert','fee_reminder','fee_receipt','optin_stopped','school_closure'));
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON msg.closures FROM sos_app;
GRANT SELECT, INSERT ON msg.closures TO sos_app;
GRANT DELETE ON msg.closures TO sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "message.closure.send",
        "description": (
            "Send an urgent school closure message to parents on WhatsApp, also during quiet "
            "hours (06:00-21:30 IST)"
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
    op.execute(UPGRADE_SQL)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the school
    # is offboarding and the transaction-local purge flag names it.
    op.execute(f"GRANT SELECT, DELETE ON {TABLE} TO sos_purger")
    op.execute(
        f"CREATE POLICY offboarding_purge ON {TABLE} AS RESTRICTIVE FOR ALL TO sos_purger "
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
    # Lossy: closure messages cannot exist under the old purpose CHECK.
    op.execute("DELETE FROM msg.messages WHERE purpose = 'school_closure'")
    op.execute("ALTER TABLE msg.messages DROP CONSTRAINT messages_purpose_check")
    op.execute(
        "ALTER TABLE msg.messages ADD CONSTRAINT messages_purpose_check CHECK (purpose IN "
        "('optin_confirm','school_notice','absence_alert','fee_reminder','fee_receipt',"
        "'optin_stopped'))"
    )
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
