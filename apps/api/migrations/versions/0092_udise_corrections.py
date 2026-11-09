"""UDISE+ correction requests to the MEO and UDISE+ list import matched by PEN (FO-02, FO-04;
FR-UDI-001..FR-UDI-020; docs/05 §5.3a).

Tenant tables (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FKs, the restrictive ``offboarding_purge`` policy with SELECT and
DELETE for ``sos_purger``; ADR-0013, ADR-0029):

- ``sis.udise_correction_requests``: one batch of corrections the school asks the MEO / MIS
  coordinator to make in UDISE+ (a request, never a change: invariant 6). ``status``
  ``prepared`` | ``sent`` | ``done`` | ``rejected``; ``sent_on`` and ``sent_to`` (a role code
  from ``app/dq/config/udise_corrections.yaml``, never a person's name) once sent; ``closed_*``
  when done or rejected. ``sos_app`` may SELECT, INSERT and UPDATE only the workflow columns;
  never DELETE (the batch is the school's record that it asked).
- ``sis.udise_correction_lines``: which student and which field each request covers (ids and
  an attribute key only; the values are read live when the letter is printed, so no copy of a
  name or date of birth is kept here). Append-only for ``sos_app``; a student removed by an
  import revert takes its lines with it (``ON DELETE CASCADE``).

``sis.import_batches.row_matches`` (FO-04): the office's own decisions for UDISE+ rows the
import could not match by PEN or by name + date of birth + class: ``{"<row_no>": "<student
id>" | "skip"}``. The import never creates students from these rows (BR-01).

Expand-only (invariant 12). Downgrade drops both tables and the column (lossy: the school's
correction requests and pending row decisions).

Revision ID: 0092_udise_corrections
Revises: 0091_udise_followup
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "0092_udise_corrections"
down_revision = "0091_udise_followup"
branch_labels = None
depends_on = None

REQUESTS = "sis.udise_correction_requests"
LINES = "sis.udise_correction_lines"

TABLES_SQL = r"""
CREATE TABLE sis.udise_correction_requests (
  id            uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL,
  profile_key   text NOT NULL,
  status        text NOT NULL DEFAULT 'prepared',
  lines_count   int NOT NULL CHECK (lines_count >= 1),
  students_count int NOT NULL CHECK (students_count >= 1),
  prepared_by   uuid NOT NULL,
  prepared_at   timestamptz NOT NULL DEFAULT now(),
  sent_on       date,
  sent_to       text,
  sent_by       uuid,
  closed_by     uuid,
  closed_at     timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  version       int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT udise_correction_requests_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT udise_correction_requests_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT udise_correction_requests_prepared_by_fk FOREIGN KEY (prepared_by)
    REFERENCES core.users (id),
  CONSTRAINT udise_correction_requests_sent_by_fk FOREIGN KEY (sent_by)
    REFERENCES core.users (id),
  CONSTRAINT udise_correction_requests_closed_by_fk FOREIGN KEY (closed_by)
    REFERENCES core.users (id),
  CONSTRAINT udise_correction_requests_profile_format
    CHECK (profile_key ~ '^[a-z0-9][a-z0-9-]{0,63}$'),
  CONSTRAINT udise_correction_requests_sent_to_format
    CHECK (sent_to IS NULL OR sent_to ~ '^[a-z][a-z0-9_]{1,62}$'),
  CONSTRAINT udise_correction_requests_status_check
    CHECK (status IN ('prepared','sent','done','rejected')),
  CONSTRAINT udise_correction_requests_sent
    CHECK ((status = 'prepared') = (sent_on IS NULL)
      AND (sent_on IS NULL) = (sent_to IS NULL)
      AND (sent_on IS NULL) = (sent_by IS NULL)),
  CONSTRAINT udise_correction_requests_closed
    CHECK ((status IN ('done','rejected')) = (closed_at IS NOT NULL)
      AND (closed_at IS NULL) = (closed_by IS NULL))
);
CREATE INDEX udise_correction_requests_status_idx
  ON sis.udise_correction_requests (tenant_id, status, prepared_at DESC);
CREATE TRIGGER udise_correction_requests_set_updated_at
  BEFORE UPDATE ON sis.udise_correction_requests
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE sis.udise_correction_lines (
  id             uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL,
  request_id     uuid NOT NULL,
  student_id     uuid NOT NULL,
  attribute_key  text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT udise_correction_lines_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT udise_correction_lines_once UNIQUE (tenant_id, request_id, student_id, attribute_key),
  CONSTRAINT udise_correction_lines_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT udise_correction_lines_request_fk FOREIGN KEY (tenant_id, request_id)
    REFERENCES sis.udise_correction_requests (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT udise_correction_lines_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT udise_correction_lines_attribute_format
    CHECK (attribute_key ~ '^[a-z][a-z0-9_]{1,62}$')
);
CREATE INDEX udise_correction_lines_student_idx
  ON sis.udise_correction_lines (tenant_id, student_id);
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE ON sis.udise_correction_requests FROM sos_app;
GRANT UPDATE (status, sent_on, sent_to, sent_by, closed_by, closed_at, updated_at, version)
  ON sis.udise_correction_requests TO sos_app;
REVOKE UPDATE, DELETE, TRUNCATE ON sis.udise_correction_lines FROM sos_app;
"""


def _tenant_table(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the school
    # is offboarding and the transaction-local purge flag names it.
    op.execute(f"GRANT SELECT, DELETE ON {table} TO sos_purger")
    op.execute(
        f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed())"
    )


def upgrade() -> None:
    op.execute(TABLES_SQL)
    _tenant_table(REQUESTS)
    _tenant_table(LINES)
    op.execute(GRANTS_SQL)
    op.execute(
        "ALTER TABLE sis.import_batches ADD COLUMN row_matches jsonb NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE sis.import_batches ADD CONSTRAINT import_batches_row_matches_object "
        "CHECK (jsonb_typeof(row_matches) = 'object')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sis.import_batches DROP COLUMN IF EXISTS row_matches")
    op.execute(f"DROP TABLE IF EXISTS {LINES}")
    op.execute(f"DROP TABLE IF EXISTS {REQUESTS}")
