"""FA-06 knowledge gaps: questions Ask could not answer, grouped for the library's keepers
(FR-KB-081..FR-KB-095; docs/05 §6.6, docs/06 §8.1).

Two tenant tables (``tenant_id`` first, ``UNIQUE (tenant_id, id)`` where referenced, RLS
ENABLE + FORCE with ``tenant_isolation``, composite FKs only, the ADR-0029 ``offboarding_purge``
policy and ``sos_purger`` SELECT, DELETE; no ``definer_access`` policy, no ``SECURITY DEFINER``
function):

- ``kb.knowledge_gaps``: one group of repeated questions that were answered "not found", came
  back search-only because the answer could not be checked against its sources, or were marked
  "not helpful". ``question_ciphertext`` holds ONE redacted sample question (``core.redaction``
  plus every remaining digit run masked; then AES-256-GCM under the tenant DEK, AAD
  ``tenant|kb.knowledge_gaps|question_ciphertext|id``); ``question_hmac`` is the tenant-keyed
  HMAC of the normalised redacted question (the grouping key; never a bare hash). No asker:
  there is no user, membership or query id in this table. Counts per signal
  (``not_found_count``, ``unchecked_count``, ``not_helpful_count``), ``first_seen_at`` /
  ``last_seen_at``. ``status`` ``open`` | ``closed`` (``resolution`` ``verified_answer`` or
  ``document``, with the answer or document it points at, ``ON DELETE SET NULL`` of that column)
  | ``dismissed``; who closed it (membership) and when. At most one ``open`` gap per question
  key (partial unique index): a question asked again after its gap was closed opens a new gap.
- ``kb.knowledge_gap_queries``: which logged question was counted into which gap, so the
  collection job never counts a question twice. ``(tenant_id, query_id)`` is the key; both FKs
  ``ON DELETE CASCADE`` (the 180-day query purge and the gap purge remove the marks).

Grants: the ``kb`` default privileges give ``sos_app`` DML and ``sos_readonly`` SELECT;
reporting has no use for redacted questions of other people, so ``sos_readonly`` gets nothing on
either table. ``sos_platform`` has nothing on them.

Expand-only (invariant 12). Downgrade drops both tables (lossy: the open gaps and the record of
how they were closed; the verified answers and documents themselves stay).

Revision ID: 0099_knowledge_gaps
Revises: 0098_drills_policies
Create Date: 2026-11-12
"""

from __future__ import annotations

from alembic import op

revision = "0099_knowledge_gaps"
down_revision = "0098_drills_policies"
branch_labels = None
depends_on = None

GAPS = "kb.knowledge_gaps"
MARKS = "kb.knowledge_gap_queries"
TABLES = (GAPS, MARKS)

GAPS_SQL = r"""
CREATE TABLE kb.knowledge_gaps (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  question_ciphertext  bytea NOT NULL,
  question_hmac        bytea NOT NULL,
  key_version          int NOT NULL CHECK (key_version >= 1),
  language             text,
  not_found_count      int NOT NULL DEFAULT 0 CHECK (not_found_count >= 0),
  unchecked_count      int NOT NULL DEFAULT 0 CHECK (unchecked_count >= 0),
  not_helpful_count    int NOT NULL DEFAULT 0 CHECK (not_helpful_count >= 0),
  first_seen_at        timestamptz NOT NULL,
  last_seen_at         timestamptz NOT NULL,
  status               text NOT NULL DEFAULT 'open',
  resolution           text,
  verified_answer_id   uuid,
  document_id          uuid,
  closed_by            uuid,
  closed_at            timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT knowledge_gaps_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT knowledge_gaps_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT knowledge_gaps_answer_fk FOREIGN KEY (tenant_id, verified_answer_id)
    REFERENCES kb.verified_answers (tenant_id, id) ON DELETE SET NULL (verified_answer_id),
  CONSTRAINT knowledge_gaps_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id),
  CONSTRAINT knowledge_gaps_closed_by_fk FOREIGN KEY (tenant_id, closed_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT knowledge_gaps_hmac_len CHECK (octet_length(question_hmac) = 32),
  CONSTRAINT knowledge_gaps_language CHECK (language IS NULL OR language IN ('en','te')),
  CONSTRAINT knowledge_gaps_counted
    CHECK (not_found_count + unchecked_count + not_helpful_count >= 1),
  CONSTRAINT knowledge_gaps_seen CHECK (last_seen_at >= first_seen_at),
  CONSTRAINT knowledge_gaps_status_check CHECK (status IN ('open','closed','dismissed')),
  CONSTRAINT knowledge_gaps_resolution_check
    CHECK (resolution IS NULL OR resolution IN ('verified_answer','document')),
  CONSTRAINT knowledge_gaps_closed
    CHECK ((status = 'open') = (closed_at IS NULL AND closed_by IS NULL)),
  CONSTRAINT knowledge_gaps_resolved CHECK ((status = 'closed') = (resolution IS NOT NULL)),
  CONSTRAINT knowledge_gaps_links CHECK (
    (resolution = 'verified_answer' OR verified_answer_id IS NULL)
    AND (resolution = 'document' OR document_id IS NULL))
);
CREATE UNIQUE INDEX knowledge_gaps_one_open
  ON kb.knowledge_gaps (tenant_id, question_hmac) WHERE status = 'open';
CREATE INDEX knowledge_gaps_status_idx
  ON kb.knowledge_gaps (tenant_id, status, last_seen_at DESC);
CREATE TRIGGER knowledge_gaps_set_updated_at BEFORE UPDATE ON kb.knowledge_gaps
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

MARKS_SQL = r"""
CREATE TABLE kb.knowledge_gap_queries (
  tenant_id     uuid NOT NULL,
  query_id      uuid NOT NULL,
  gap_id        uuid NOT NULL,
  signal        text NOT NULL,
  collected_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, query_id),
  CONSTRAINT knowledge_gap_queries_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT knowledge_gap_queries_query_fk FOREIGN KEY (tenant_id, query_id)
    REFERENCES kb.queries (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT knowledge_gap_queries_gap_fk FOREIGN KEY (tenant_id, gap_id)
    REFERENCES kb.knowledge_gaps (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT knowledge_gap_queries_signal_check
    CHECK (signal IN ('not_found','unchecked','not_helpful'))
);
CREATE INDEX knowledge_gap_queries_gap_idx ON kb.knowledge_gap_queries (tenant_id, gap_id);
"""


def upgrade() -> None:
    op.execute(GAPS_SQL)
    op.execute(MARKS_SQL)
    for table in TABLES:
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
        op.execute(f"REVOKE ALL ON {table} FROM sos_readonly")
        op.execute(f"REVOKE TRUNCATE, REFERENCES, TRIGGER ON {table} FROM sos_app")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {MARKS}")
    op.execute(f"DROP TABLE IF EXISTS {GAPS}")
