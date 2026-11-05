"""Breach register retention: a closed breach is deleted 3 years after closing, with its affected
list and sent reminders (lead decision D58; ADR-0050; docs/05 §16.3, docs/08 §11).

The nightly ``privacy.breach_purge`` (``app/privacy/breach_tasks.py``) deletes, per school in its
own ``tenant_session`` (``sos_app``, RLS), the breaches closed more than
``retention.closed_years`` ago (``app/privacy/breach_templates.yaml``, at least 3). 0067 left
``sos_app`` without DELETE on ``ops.breach_records`` and ``ops.breach_reminders`` (records were
kept forever); ``ops.breach_affected`` already had DELETE (the affected list is replaced as a
whole while the breach is open).

- ``GRANT DELETE ON ops.breach_records, ops.breach_reminders TO sos_app``.
- Restrictive ``FOR DELETE TO sos_app`` policies (the floor; a bug or a lower config value can
  never delete an open, reopened or recently closed breach):

  - ``breach_records_delete_expired``: ``status = 'closed'`` and ``closed_at`` more than
    3 years ago. A reopened breach is ``open`` again with ``closed_at`` NULL (CHECK
    ``breach_records_closed``), so it is never deletable; re-closing restarts the clock.
  - ``breach_reminders_delete_expired``: only reminders of such a breach.
  - ``breach_affected_delete_open_or_expired``: the affected list of an open breach may still be
    replaced; a closed breach's list is frozen until the breach itself expires.

  The ``tenant_isolation`` policies still apply (RLS ENABLE + FORCE unchanged). The offboarding
  purge deletes as ``sos_purger`` (``offboarding_purge``), which these policies do not cover; FK
  cascades (``sis.guardians`` → ``breach_affected``) bypass row security as before.

No append-only trigger exists on these tables (only ``sis.consents`` has one in 0067), so none
changes. Audit tables are untouched (they stay append-only; the purge writes one
``privacy.breach.retention_purged`` event per school with counts). No personal data.

Downgrade: drops the three policies and revokes DELETE on the two tables again (the purge then
fails per school and is logged; nothing else depends on it).

Revision ID: 0075_breach_retention
Revises: 0074_tenant_audit_retention
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0075_breach_retention"
down_revision = "0074_tenant_audit_retention"
branch_labels = None
depends_on = None

UP = r"""
CREATE POLICY breach_records_delete_expired ON ops.breach_records
  AS RESTRICTIVE FOR DELETE TO sos_app
  USING (status = 'closed' AND closed_at < now() - interval '3 years');
CREATE POLICY breach_reminders_delete_expired ON ops.breach_reminders
  AS RESTRICTIVE FOR DELETE TO sos_app
  USING (EXISTS (
    SELECT 1 FROM ops.breach_records r
    WHERE r.tenant_id = breach_reminders.tenant_id AND r.id = breach_reminders.breach_id
      AND r.status = 'closed' AND r.closed_at < now() - interval '3 years'));
CREATE POLICY breach_affected_delete_open_or_expired ON ops.breach_affected
  AS RESTRICTIVE FOR DELETE TO sos_app
  USING (EXISTS (
    SELECT 1 FROM ops.breach_records r
    WHERE r.tenant_id = breach_affected.tenant_id AND r.id = breach_affected.breach_id
      AND (r.status <> 'closed' OR r.closed_at < now() - interval '3 years')));
GRANT DELETE ON ops.breach_records, ops.breach_reminders TO sos_app;
"""

DOWN = r"""
REVOKE DELETE ON ops.breach_records, ops.breach_reminders FROM sos_app;
DROP POLICY IF EXISTS breach_affected_delete_open_or_expired ON ops.breach_affected;
DROP POLICY IF EXISTS breach_reminders_delete_expired ON ops.breach_reminders;
DROP POLICY IF EXISTS breach_records_delete_expired ON ops.breach_records;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
