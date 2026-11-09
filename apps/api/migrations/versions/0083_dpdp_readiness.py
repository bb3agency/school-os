"""FC-03 / FP-13: DPDP readiness checklist, notice given at admission and the privacy notice sent
on WhatsApp (FR-DPDP-053..FR-DPDP-066; docs/05 §16.1; ADR-0048).

Tenant tables in schema ``ops`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs, the restrictive ``offboarding_purge`` policy with
``SELECT, DELETE`` for ``sos_purger``; nothing for ``sos_platform``):

- ``ops.privacy_readiness_marks``: **append-only** record that the school did one readiness step
  SchoolOS cannot see in its other records (``dpa_signed``, ``dpia_done``, ``breach_contact``,
  ``retention_reviewed``): the date and optionally the document kept as evidence. The latest
  mark per item counts. No free text.
- ``ops.privacy_admission_notices``: **append-only**, at most one per student: the date and the
  channel through which the family of a student admitted after the school-wide notice got the
  privacy notice. Composite FKs to ``sis.students`` and optionally ``ops.privacy_notices`` (the
  notice issue the family got).

``ops.privacy_notices`` gains ``parent_notice_id`` and ``broadcast_id`` (set when the notice was
sent to parents on WhatsApp from SchoolOS; channel ``sos_whatsapp``). No FKs to the parent notice
or the broadcast: the messaging module deletes old previews and broadcasts on its own schedule;
the notice record stays as evidence. The append-only trigger keeps both set once.

Expand-only (invariant 12). Downgrade drops the two tables (lossy: the readiness marks and the
admission notice records), deletes WhatsApp notice records of every school (the restored channel
check would refuse them; lossy) and drops the two columns.

Revision ID: 0083_dpdp_readiness
Revises: 0082_audit_ym_fixes
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "0083_dpdp_readiness"
# Feature wave F1 agents share this parent; the lead re-chains them at merge.
down_revision = "0082_audit_ym_fixes"
branch_labels = None
depends_on = None

TABLES_SQL = r"""
CREATE TABLE ops.privacy_readiness_marks (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  item                    text NOT NULL,
  done_on                 date NOT NULL,
  document_id             uuid,
  recorded_by             uuid NOT NULL,
  recorded_by_membership  uuid NOT NULL,
  recorded_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacy_readiness_marks_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT privacy_readiness_marks_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT privacy_readiness_marks_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT privacy_readiness_marks_recorded_by_fk FOREIGN KEY (recorded_by)
    REFERENCES core.users (id),
  CONSTRAINT privacy_readiness_marks_membership_fk FOREIGN KEY (tenant_id, recorded_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT privacy_readiness_marks_item_check
    CHECK (item IN ('dpa_signed','dpia_done','breach_contact','retention_reviewed'))
);
CREATE INDEX privacy_readiness_marks_item_idx
  ON ops.privacy_readiness_marks (tenant_id, item, done_on DESC);
CREATE INDEX privacy_readiness_marks_document_idx
  ON ops.privacy_readiness_marks (tenant_id, document_id) WHERE document_id IS NOT NULL;
CREATE TRIGGER privacy_readiness_marks_append_only BEFORE UPDATE OR DELETE
  ON ops.privacy_readiness_marks
  FOR EACH ROW EXECUTE FUNCTION ops.tg_privacy_append_only();

CREATE TABLE ops.privacy_admission_notices (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  student_id              uuid NOT NULL,
  given_on                date NOT NULL,
  channel                 text NOT NULL,
  notice_id               uuid,
  recorded_by             uuid NOT NULL,
  recorded_by_membership  uuid NOT NULL,
  recorded_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacy_admission_notices_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT privacy_admission_notices_one_per_student UNIQUE (tenant_id, student_id),
  CONSTRAINT privacy_admission_notices_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT privacy_admission_notices_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT privacy_admission_notices_notice_fk FOREIGN KEY (tenant_id, notice_id)
    REFERENCES ops.privacy_notices (tenant_id, id),
  CONSTRAINT privacy_admission_notices_recorded_by_fk FOREIGN KEY (recorded_by)
    REFERENCES core.users (id),
  CONSTRAINT privacy_admission_notices_membership_fk
    FOREIGN KEY (tenant_id, recorded_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT privacy_admission_notices_channel_check
    CHECK (channel IN ('paper','in_person','school_whatsapp_group','sos_whatsapp','email','other'))
);
CREATE INDEX privacy_admission_notices_notice_idx
  ON ops.privacy_admission_notices (tenant_id, notice_id) WHERE notice_id IS NOT NULL;
CREATE TRIGGER privacy_admission_notices_append_only BEFORE UPDATE OR DELETE
  ON ops.privacy_admission_notices
  FOR EACH ROW EXECUTE FUNCTION ops.tg_privacy_append_only();

ALTER TABLE ops.privacy_notices ADD COLUMN parent_notice_id uuid;
ALTER TABLE ops.privacy_notices ADD COLUMN broadcast_id uuid;
ALTER TABLE ops.privacy_notices DROP CONSTRAINT privacy_notices_channel_check;
ALTER TABLE ops.privacy_notices ADD CONSTRAINT privacy_notices_channel_check
  CHECK (channel IN ('paper','school_whatsapp_group','sos_notice','email','other','sos_whatsapp'));
ALTER TABLE ops.privacy_notices ADD CONSTRAINT privacy_notices_whatsapp_shape CHECK (
  (channel = 'sos_whatsapp') = (parent_notice_id IS NOT NULL AND broadcast_id IS NOT NULL));
CREATE INDEX privacy_notices_broadcast_idx ON ops.privacy_notices (tenant_id, broadcast_id)
  WHERE broadcast_id IS NOT NULL;
"""

TENANT_TABLES = ("ops.privacy_admission_notices", "ops.privacy_readiness_marks")

GRANTS_SQL = "\n".join(
    line
    for table in TENANT_TABLES
    for line in (
        # Insert-only for the app role (append-only, also enforced by the trigger).
        f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM sos_app;",
        f"REVOKE ALL ON {table} FROM sos_readonly;",
        f"GRANT SELECT ON {table} TO sos_readonly;",
    )
)

# The owner lifts FORCE for the one DELETE (WhatsApp notice records of every school); FORCE is
# restored before the constraint changes, in the same transaction.
UNFORCE = "ALTER TABLE ops.privacy_notices NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner deletes the WhatsApp notice records of every school on downgrade; FORCE is restored in the same transaction
REFORCE = "ALTER TABLE ops.privacy_notices FORCE ROW LEVEL SECURITY"

DOWNGRADE_SQL = r"""
DROP INDEX IF EXISTS ops.privacy_notices_broadcast_idx;
ALTER TABLE ops.privacy_notices DROP CONSTRAINT privacy_notices_whatsapp_shape;
ALTER TABLE ops.privacy_notices DROP CONSTRAINT privacy_notices_channel_check;
ALTER TABLE ops.privacy_notices ADD CONSTRAINT privacy_notices_channel_check
  CHECK (channel IN ('paper','school_whatsapp_group','sos_notice','email','other'));
ALTER TABLE ops.privacy_notices DROP COLUMN broadcast_id;
ALTER TABLE ops.privacy_notices DROP COLUMN parent_notice_id;
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
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


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    # The append-only trigger refuses DELETE outside the offboarding purge: lift it for this one
    # statement as the owner (no app session sees the table meanwhile: DDL holds the lock).
    op.execute(UNFORCE)
    op.execute("ALTER TABLE ops.privacy_notices DISABLE TRIGGER privacy_notices_append_only")
    op.execute("DELETE FROM ops.privacy_notices WHERE channel = 'sos_whatsapp'")
    op.execute("ALTER TABLE ops.privacy_notices ENABLE TRIGGER privacy_notices_append_only")
    op.execute(REFORCE)
    op.execute(DOWNGRADE_SQL)
