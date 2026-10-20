"""R2-3G WhatsApp sending: notice broadcasts, automatic runs, retries and the send window
(ADR-0043 decisions 3, 10, 11; ADR-0046; owner decisions D25, D27, D31; FR-MSG-011..017;
docs/05 §15.3).

New tenant tables in schema ``msg`` follow ``0057_messaging_core``: ``tenant_id`` first,
``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``, composite FKs, the
restrictive ``offboarding_purge`` policy and ``SELECT, DELETE`` for ``sos_purger`` (ADR-0029), no
``definer_access``, nothing for ``sos_platform``; the app role's default grants are narrowed:

- ``msg.broadcasts``: one row per notice broadcast. A preview creates it (``previewed``, with the
  counts shown: recipients, skipped by reason, languages, the estimated Meta cost, and
  ``expires_at``; ``resend`` for "Send again"); confirming moves it to ``sending``
  (``confirmed_by``/``confirmed_at``, the number queued) and the sender marks it ``done`` when no message of it is queued any more. The
  audience is the list of sections (current academic year). ``resend_reason`` (10..300) is set
  only for "Send again" (FR-MSG-012, US-2204 AC3). The app may update the status and counts only;
  DELETE only for the retention job (previews after a day, broadcasts without messages after
  13 months).
- ``msg.send_runs``: one row per school, automatic job and IST day (``absence_alerts``,
  ``fee_reminders``): when it ran and how many messages it queued (the "Automatic messages"
  report; a second run of the same day is refused by ``UNIQUE``).
- ``msg.messages`` gains ``broadcast_id`` (composite FK; insert-only) and ``next_attempt_at``
  (when the sender may try a queued message again: quiet hours, the daily cap, retry backoff and
  the claim lease; UPDATE granted). Index of queued messages by ``next_attempt_at``.
- ``msg.settings`` gains ``no_message_dates`` (IST dates without automatic messages, at most 60;
  US-2205 AC2).

The permission catalog gets ``message.send`` (send an approved notice, send again, revoke a
parent link; principal and office admin, school-wide, no step-up; docs/07 §6.2). Role grants of
existing schools need the post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the two tables and the new columns (lossy:
broadcast reports, run history, retry times and no-message dates) and deletes ``message.send``
unless a role still holds it.

Revision ID: 0061_messaging_sends
Revises: 0060_fees_upi
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0061_messaging_sends"
down_revision = "0060_fees_upi"
branch_labels = None
depends_on = None

# Children before parents (the order the module's offboarding purge deletes them in).
TENANT_TABLES = ("msg.send_runs", "msg.broadcasts")

UPGRADE_SQL = r"""
CREATE TABLE msg.broadcasts (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  notice_id           uuid NOT NULL,
  section_ids         uuid[] NOT NULL,
  status              text NOT NULL DEFAULT 'previewed',
  resend              boolean NOT NULL DEFAULT false,
  resend_reason       text,
  recipients          int NOT NULL DEFAULT 0 CHECK (recipients >= 0),
  skipped             jsonb NOT NULL DEFAULT '{}'::jsonb,
  languages           jsonb NOT NULL DEFAULT '{}'::jsonb,
  language_fallbacks  int NOT NULL DEFAULT 0 CHECK (language_fallbacks >= 0),
  already_sent        int NOT NULL DEFAULT 0 CHECK (already_sent >= 0),
  estimated_cost_inr  numeric(12,2) NOT NULL DEFAULT 0 CHECK (estimated_cost_inr >= 0),
  queued              int NOT NULL DEFAULT 0 CHECK (queued >= 0),
  requested_by        uuid NOT NULL,
  confirmed_by        uuid,
  confirmed_at        timestamptz,
  completed_at        timestamptz,
  expires_at          timestamptz NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  version             int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT broadcasts_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT broadcasts_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT broadcasts_notice_fk FOREIGN KEY (tenant_id, notice_id)
    REFERENCES ops.parent_notices (tenant_id, id),
  CONSTRAINT broadcasts_requested_by_fk FOREIGN KEY (requested_by) REFERENCES core.users (id),
  CONSTRAINT broadcasts_confirmed_by_fk FOREIGN KEY (confirmed_by) REFERENCES core.users (id),
  CONSTRAINT broadcasts_status_check CHECK (status IN ('previewed','sending','done')),
  CONSTRAINT broadcasts_sections CHECK (cardinality(section_ids) BETWEEN 1 AND 300),
  CONSTRAINT broadcasts_resend_reason
    CHECK (resend_reason IS NULL OR char_length(resend_reason) BETWEEN 10 AND 300),
  CONSTRAINT broadcasts_resend_has_reason CHECK (status = 'previewed' OR
    resend = (resend_reason IS NOT NULL)),
  CONSTRAINT broadcasts_confirmed CHECK ((status = 'previewed') =
    (confirmed_at IS NULL AND confirmed_by IS NULL)),
  CONSTRAINT broadcasts_completed CHECK ((status = 'done') = (completed_at IS NOT NULL)),
  CONSTRAINT broadcasts_skipped_object CHECK (jsonb_typeof(skipped) = 'object'),
  CONSTRAINT broadcasts_languages_object CHECK (jsonb_typeof(languages) = 'object')
);
CREATE INDEX broadcasts_notice_idx ON msg.broadcasts (tenant_id, notice_id, created_at DESC);
CREATE INDEX broadcasts_created_idx ON msg.broadcasts (tenant_id, created_at DESC);
CREATE TRIGGER broadcasts_set_updated_at BEFORE UPDATE ON msg.broadcasts
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE msg.send_runs (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL,
  job          text NOT NULL,
  run_on       date NOT NULL,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  considered   int NOT NULL DEFAULT 0 CHECK (considered >= 0),
  queued       int NOT NULL DEFAULT 0 CHECK (queued >= 0),
  CONSTRAINT send_runs_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT send_runs_once UNIQUE (tenant_id, job, run_on),
  CONSTRAINT send_runs_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT send_runs_job_check CHECK (job IN ('absence_alerts','fee_reminders'))
);

ALTER TABLE msg.messages ADD COLUMN broadcast_id uuid;
ALTER TABLE msg.messages ADD COLUMN next_attempt_at timestamptz;
ALTER TABLE msg.messages ADD CONSTRAINT messages_broadcast_fk FOREIGN KEY (tenant_id, broadcast_id)
  REFERENCES msg.broadcasts (tenant_id, id);
CREATE INDEX messages_due_idx ON msg.messages (tenant_id, next_attempt_at)
  WHERE status = 'queued';
CREATE INDEX messages_broadcast_idx ON msg.messages (tenant_id, broadcast_id)
  WHERE broadcast_id IS NOT NULL;

ALTER TABLE msg.settings ADD COLUMN no_message_dates date[] NOT NULL DEFAULT '{}';
ALTER TABLE msg.settings ADD CONSTRAINT settings_no_message_dates
  CHECK (cardinality(no_message_dates) <= 60);
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON msg.broadcasts, msg.send_runs FROM sos_app;
GRANT UPDATE (status, resend_reason, recipients, skipped, languages, language_fallbacks,
  already_sent, estimated_cost_inr, queued, confirmed_by, confirmed_at, completed_at, updated_at, version)
  ON msg.broadcasts TO sos_app;
GRANT DELETE ON msg.broadcasts TO sos_app;
GRANT UPDATE (finished_at, considered, queued) ON msg.send_runs TO sos_app;
GRANT DELETE ON msg.send_runs TO sos_app;
GRANT UPDATE (next_attempt_at) ON msg.messages TO sos_app;
GRANT UPDATE (no_message_dates) ON msg.settings TO sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "message.send",
        "description": (
            "Send an approved notice to parents on WhatsApp, send it again with a reason, and "
            "revoke a parent link"
        ),
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
    op.execute(UPGRADE_SQL)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
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
    op.execute("ALTER TABLE msg.settings DROP CONSTRAINT IF EXISTS settings_no_message_dates")
    op.execute("ALTER TABLE msg.settings DROP COLUMN IF EXISTS no_message_dates")
    op.execute("DROP INDEX IF EXISTS msg.messages_broadcast_idx")
    op.execute("DROP INDEX IF EXISTS msg.messages_due_idx")
    op.execute("ALTER TABLE msg.messages DROP CONSTRAINT IF EXISTS messages_broadcast_fk")
    op.execute("ALTER TABLE msg.messages DROP COLUMN IF EXISTS next_attempt_at")
    op.execute("ALTER TABLE msg.messages DROP COLUMN IF EXISTS broadcast_id")
    for table in TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
