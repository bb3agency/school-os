"""FP-02 follow-ups: the parent page of a closure message and the "school reopens" message
(FR-MSG-056..065, FR-PUB-013..016; ADR-0043 amendment of 2026-11-12, ADR-0046 amendment; lead
decision D86, PO to confirm; docs/05 §15.3, §15.5).

- ``msg.closures`` gains ``notice_text`` (nullable, at most 1000 characters): the order text the
  principal may type for the parent page. It never goes into the WhatsApp message (template
  variables stay the dates and a reason code). The table stays insert-only for ``sos_app``.
- New tenant table ``msg.closure_reopenings`` follows ``0093_msg_reach_closure``: ``tenant_id``
  first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``, composite FK
  ``(tenant_id, closure_id)`` into ``msg.closures``, ``UNIQUE (tenant_id, closure_id)`` (one
  reopening message per closure), the restrictive ``offboarding_purge`` policy with
  ``SELECT, DELETE`` for ``sos_purger`` (ADR-0029), no ``definer_access``, nothing for
  ``sos_platform``. One row per "school reopens" message: the reopening date, the counts shown
  and queued, who sent it when. Insert-only for ``sos_app`` apart from DELETE for the retention
  job (13 months, with the messages).
- ``msg.messages.purpose`` also allows ``school_reopening`` (source: the reopening row).
- ``core.public_handles.kind`` also allows ``parent_closure``: a guardian-bound parent link
  (ADR-0046) to the closure's page, issued per message when it is sent; it expires
  ``lifetimes_days.parent_closure`` (7) days after the closure's last day. The resolver is
  unchanged (active schools only).

No permission changes: both messages need ``message.closure.send`` (step-up) from 0093.

Expand-only (invariant 12). Downgrade (lossy) deletes the ``school_reopening`` messages and the
``parent_closure`` links (closure messages keep their rows, the link column set NULL) as the
owner, FORCE lifted for those statements and restored in the same transaction, restores both
CHECKs, drops the table and the column.

Revision ID: 0100_closure_reopen
Revises: 0099_knowledge_gaps
Create Date: 2026-11-12
"""

from __future__ import annotations

from alembic import op

revision = "0100_closure_reopen"
down_revision = "0099_knowledge_gaps"
branch_labels = None
depends_on = None

TABLE = "msg.closure_reopenings"

OLD_PURPOSES = (
    "'optin_confirm','school_notice','absence_alert','fee_reminder','fee_receipt',"
    "'optin_stopped','school_closure'"
)
NEW_PURPOSES = OLD_PURPOSES + ",'school_reopening'"
OLD_KINDS = (
    "'cert_verify','parent_notice','parent_receipt','parent_dues','parent_optin',"
    "'razorpay_route','meta_waba','school_disclosure'"
)
NEW_KINDS = OLD_KINDS + ",'parent_closure'"

UPGRADE_SQL = f"""
ALTER TABLE msg.closures ADD COLUMN notice_text text;
ALTER TABLE msg.closures ADD CONSTRAINT closures_notice_text_length
  CHECK (notice_text IS NULL OR char_length(notice_text) BETWEEN 1 AND 1000);

CREATE TABLE msg.closure_reopenings (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  closure_id          uuid NOT NULL,
  reopens_on          date NOT NULL,
  recipients          int NOT NULL DEFAULT 0 CHECK (recipients >= 0),
  skipped             jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  languages           jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  estimated_cost_inr  numeric(12,2) NOT NULL DEFAULT 0 CHECK (estimated_cost_inr >= 0),
  queued              int NOT NULL DEFAULT 0 CHECK (queued >= 0),
  sent_by             uuid NOT NULL,
  sent_at             timestamptz NOT NULL DEFAULT now(),
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT closure_reopenings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT closure_reopenings_one_per_closure UNIQUE (tenant_id, closure_id),
  CONSTRAINT closure_reopenings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT closure_reopenings_closure_fk FOREIGN KEY (tenant_id, closure_id)
    REFERENCES msg.closures (tenant_id, id),
  CONSTRAINT closure_reopenings_sent_by_fk FOREIGN KEY (sent_by) REFERENCES core.users (id),
  CONSTRAINT closure_reopenings_skipped_object CHECK (jsonb_typeof(skipped) = 'object'),
  CONSTRAINT closure_reopenings_languages_object CHECK (jsonb_typeof(languages) = 'object')
);
CREATE INDEX closure_reopenings_sent_idx ON msg.closure_reopenings (tenant_id, sent_at DESC);

ALTER TABLE msg.messages DROP CONSTRAINT messages_purpose_check;
ALTER TABLE msg.messages ADD CONSTRAINT messages_purpose_check
  CHECK (purpose IN ({NEW_PURPOSES}));

ALTER TABLE core.public_handles DROP CONSTRAINT public_handles_kind_check;
ALTER TABLE core.public_handles ADD CONSTRAINT public_handles_kind_check
  CHECK (kind IN ({NEW_KINDS}));
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON msg.closure_reopenings FROM sos_app;
GRANT SELECT, INSERT ON msg.closure_reopenings TO sos_app;
GRANT DELETE ON msg.closure_reopenings TO sos_app;
"""

UNFORCE_MESSAGES = "ALTER TABLE msg.messages NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner deletes the school_reopening messages of every school on downgrade before narrowing the CHECKs; FORCE is restored in the same transaction
REFORCE_MESSAGES = "ALTER TABLE msg.messages FORCE ROW LEVEL SECURITY"
UNFORCE_HANDLES = "ALTER TABLE core.public_handles NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner deletes every school's parent_closure links on downgrade before narrowing the kind CHECK; FORCE is restored in the same transaction
REFORCE_HANDLES = "ALTER TABLE core.public_handles FORCE ROW LEVEL SECURITY"


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


def downgrade() -> None:
    # Lossy: closure links and reopening messages cannot exist under the old CHECKs.
    op.execute(UNFORCE_MESSAGES)
    op.execute("DELETE FROM msg.messages WHERE purpose = 'school_reopening'")
    op.execute(REFORCE_MESSAGES)
    # msg.messages.public_handle_id is ON DELETE SET NULL (0057): the closure messages stay.
    op.execute(UNFORCE_HANDLES)
    op.execute("DELETE FROM core.public_handles WHERE kind = 'parent_closure'")
    op.execute(REFORCE_HANDLES)
    op.execute("ALTER TABLE core.public_handles DROP CONSTRAINT public_handles_kind_check")
    op.execute(
        "ALTER TABLE core.public_handles ADD CONSTRAINT public_handles_kind_check "
        f"CHECK (kind IN ({OLD_KINDS}))"
    )
    op.execute("ALTER TABLE msg.messages DROP CONSTRAINT messages_purpose_check")
    op.execute(
        "ALTER TABLE msg.messages ADD CONSTRAINT messages_purpose_check "
        f"CHECK (purpose IN ({OLD_PURPOSES}))"
    )
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
    op.execute("ALTER TABLE msg.closures DROP CONSTRAINT IF EXISTS closures_notice_text_length")
    op.execute("ALTER TABLE msg.closures DROP COLUMN IF EXISTS notice_text")
