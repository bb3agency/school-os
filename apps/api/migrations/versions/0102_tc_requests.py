"""FO-09 TC request log (FR-CERT-026..FR-CERT-045; docs/05 §5.7).

Tenant table ``sis.tc_requests`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs into ``sis.students``, ``sis.certificates`` and
``core.memberships``, the restrictive ``offboarding_purge`` policy with SELECT and DELETE for
``sos_purger``; ADR-0013, ADR-0029). One row per transfer certificate request that reached the
office on paper, by phone or in person (staff log it; there is no parent-facing page, ADR-0046).

- ``status`` ``received`` -> ``in_preparation`` (a TC was prepared in SchoolOS, or the clerk
  started it) -> ``ready`` (the TC was issued: serial number given, waiting to be collected) ->
  ``issued`` (handed over); ``cancelled`` from any open state, with a reason. Each step keeps
  who and when (``*_by`` memberships, ``*_at``); CHECKs pin the shape.
- ``received_on`` becomes the TC's ``application_date`` (suggested, never filled silently).
- ``leaving_date`` / ``leaving_reason``: what the parent asked for, suggested to the TC.
- ``certificate_id``: the transfer certificate prepared for the request (required from
  ``ready`` on).
- At most one open request (received, in preparation, ready) per student (partial unique).
- No names, phone numbers or free-text notes about the requester (least personal data); the
  cancel reason is staff text, 10..1000 characters.
- ``sos_app`` may SELECT, INSERT and UPDATE only the workflow columns; never DELETE (only the
  offboarding purge removes rows).

Expand-only (invariant 12). Downgrade drops the table (lossy: the school's request log).

Revision ID: 0102_tc_requests
Revises: 0101_inspection_packs
Create Date: 2026-11-12
"""

from __future__ import annotations

from alembic import op

revision = "0102_tc_requests"
down_revision = "0101_inspection_packs"
branch_labels = None
depends_on = None

TABLE = "sis.tc_requests"

TABLE_SQL = r"""
CREATE TABLE sis.tc_requests (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  student_id         uuid NOT NULL,
  status             text NOT NULL DEFAULT 'received',
  received_on        date NOT NULL,
  channel            text NOT NULL,
  leaving_date       date,
  leaving_reason     text,
  certificate_id     uuid,
  received_by        uuid NOT NULL,
  received_at        timestamptz NOT NULL DEFAULT now(),
  preparing_by       uuid,
  preparing_at       timestamptz,
  ready_by           uuid,
  ready_at           timestamptz,
  issued_by          uuid,
  issued_at          timestamptz,
  cancelled_by       uuid,
  cancelled_at       timestamptz,
  cancel_reason      text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  version            int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT tc_requests_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tc_requests_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tc_requests_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT tc_requests_certificate_fk FOREIGN KEY (tenant_id, certificate_id)
    REFERENCES sis.certificates (tenant_id, id),
  CONSTRAINT tc_requests_received_by_fk FOREIGN KEY (tenant_id, received_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tc_requests_preparing_by_fk FOREIGN KEY (tenant_id, preparing_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tc_requests_ready_by_fk FOREIGN KEY (tenant_id, ready_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tc_requests_issued_by_fk FOREIGN KEY (tenant_id, issued_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tc_requests_cancelled_by_fk FOREIGN KEY (tenant_id, cancelled_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tc_requests_status_check
    CHECK (status IN ('received','in_preparation','ready','issued','cancelled')),
  CONSTRAINT tc_requests_channel_check
    CHECK (channel IN ('paper','phone','in_person','other')),
  CONSTRAINT tc_requests_reason_check CHECK (leaving_reason IS NULL OR leaving_reason IN
    ('parent_request','parent_transferred','course_completed','other')),
  CONSTRAINT tc_requests_preparing CHECK ((preparing_at IS NULL) = (preparing_by IS NULL)),
  CONSTRAINT tc_requests_ready CHECK (
    (ready_at IS NULL) = (ready_by IS NULL)
    AND (status NOT IN ('ready','issued') OR (ready_at IS NOT NULL AND certificate_id IS NOT NULL))),
  CONSTRAINT tc_requests_issued CHECK (
    (issued_at IS NULL) = (issued_by IS NULL)
    AND (status = 'issued') = (issued_at IS NOT NULL)),
  CONSTRAINT tc_requests_cancelled CHECK (
    (status = 'cancelled') = (cancelled_at IS NOT NULL)
    AND (cancelled_at IS NULL) = (cancelled_by IS NULL)
    AND (cancelled_at IS NULL) = (cancel_reason IS NULL)
    AND (cancel_reason IS NULL OR char_length(cancel_reason) BETWEEN 10 AND 1000))
);
CREATE UNIQUE INDEX tc_requests_one_open
  ON sis.tc_requests (tenant_id, student_id)
  WHERE status IN ('received','in_preparation','ready');
CREATE INDEX tc_requests_list_idx ON sis.tc_requests (tenant_id, received_on DESC, id DESC);
CREATE INDEX tc_requests_certificate_idx ON sis.tc_requests (tenant_id, certificate_id)
  WHERE certificate_id IS NOT NULL;
CREATE TRIGGER tc_requests_set_updated_at BEFORE UPDATE ON sis.tc_requests
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

GRANTS_SQL = r"""
REVOKE ALL ON sis.tc_requests FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON sis.tc_requests FROM sos_app;
GRANT SELECT, INSERT ON sis.tc_requests TO sos_app;
GRANT UPDATE (status, certificate_id, preparing_by, preparing_at, ready_by, ready_at, issued_by,
              issued_at, cancelled_by, cancelled_at, cancel_reason, updated_at, version)
  ON sis.tc_requests TO sos_app;
GRANT SELECT, DELETE ON sis.tc_requests TO sos_purger;
"""


def upgrade() -> None:
    op.execute(TABLE_SQL)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the school
    # is offboarding and the transaction-local purge flag names it.
    op.execute(
        f"CREATE POLICY offboarding_purge ON {TABLE} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed())"
    )
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
