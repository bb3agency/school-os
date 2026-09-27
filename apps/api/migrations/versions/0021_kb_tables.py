"""Knowledge (M2): kb.document_chunks, kb.embedding_cache, kb.queries, kb.verified_answers.

docs/05 §6 and docs/06 §4.6-4.8, §6, §13. Every table is tenant-owned: RLS ENABLE + FORCE with
the standard ``tenant_isolation`` policy, ``UNIQUE (tenant_id, id)`` where referenced, and only
composite tenant foreign keys (docs/05 §3.3, §3.5). No ``definer_access`` policy and no
``SECURITY DEFINER`` function (ADR-0013). Grants come from the ``kb`` default privileges
(``sos_app`` DML, ``sos_readonly`` SELECT) except ``kb.embedding_cache``, which reporting has no
use for (``sos_readonly`` gets nothing on it). ``sos_platform`` has nothing on any of them.

``kb.document_chunks`` (differences from the docs/05 §6 sketch, recorded in docs/05 §6.2):
- The chunk's version must belong to its document: composite FK ``(tenant_id, document_id,
  version_id)`` to a new ``UNIQUE (tenant_id, document_id, id)`` on ``kb.document_versions``.
  Deleting a document or version cascades to its chunks (docs/06 §4.8).
- ``context_header`` is stored (FTS and the keyword branch use it; users never see it) and
  ``content_tsv`` is GENERATED from ``to_tsvector('simple', context_header || ' ' || content)``
  (no stemming: Telugu and code-mixed text, docs/06 §11), so ingestion cannot let it drift.
- ``embedding halfvec(1024) NOT NULL`` (ADR-0006 placeholder; ingestion embeds before it writes)
  and ``embedding_model`` (docs/06 §4.6: re-embedding migrations switch by model).
- ``is_latest`` defaults to **false**: a new version's chunks are invisible until ingestion
  promotes the version in one statement (fail closed; half-indexed versions never surface).
- HNSW (``halfvec_cosine_ops``, m = 16, ef_construction = 64) for the vector branch and a
  tenant-leading btree for the full-text and keyword branches. No GIN on ``content_tsv``,
  ``content``, ``context_header`` or the ACL arrays: under FORCE RLS PostgreSQL may use an index
  only for leakproof operators, and ``@@``, ``%``/``<%`` and ``&&`` are not leakproof, so such
  GIN indexes are never used by ``sos_app`` (pinned by tests/knowledge/test_retrieval_explain.py;
  docs/06 §6 as built). ``<=>`` is an index ORDER BY, not a qual, so HNSW is unaffected.

``kb.queries`` keeps no plaintext question or answer (FR-KB-009, invariant 5): ciphertext under
the tenant key, a keyed HMAC for repeat detection (never a bare sha256 of low-entropy text), IDs
and scores of what was retrieved, error and feedback codes only.

Downgrade drops the four tables and the added unique key (lossy for chunks, cache, query log and
verified answers; the code of 0020 does not use them).

Requirements: FR-KB-001, FR-KB-002, FR-KB-009, SEC-001, SEC-018, NFR-PRV-001.

Revision ID: 0021_kb_tables
Revises: 0020_provisioning_runs
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0021_kb_tables"
down_revision = "0020_provisioning_runs"
branch_labels = None
depends_on = None

DOC_TYPES = (
    "'circular','policy','minutes','register_scan','certificate','letter','form','report',"
    "'verified_answer','other','evidence','import_file'"
)
LANGUAGES = "'en','te','mixed'"
CODE = r"^[a-z][a-z0-9_]{0,63}$"
MODEL = r"^[a-z0-9][a-z0-9._:/-]{0,99}$"

UPGRADE_SQL = rf"""
ALTER TABLE kb.document_versions
  ADD CONSTRAINT document_versions_tenant_document_id_key UNIQUE (tenant_id, document_id, id);

CREATE TABLE kb.document_chunks (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL REFERENCES core.tenants (id),
  document_id       uuid NOT NULL,
  version_id        uuid NOT NULL,
  chunk_no          int NOT NULL CHECK (chunk_no BETWEEN 0 AND 999999),
  page_from         int CHECK (page_from BETWEEN 1 AND 999999),
  page_to           int CHECK (page_to BETWEEN 1 AND 999999),
  heading_path      text[] NOT NULL DEFAULT '{{}}',
  context_header    text NOT NULL DEFAULT '' CHECK (char_length(context_header) <= 1000),
  content           text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 20000),
  content_tsv       tsvector NOT NULL GENERATED ALWAYS AS
                      (to_tsvector('simple'::regconfig, context_header || ' ' || content)) STORED,
  language          text CHECK (language IN ({LANGUAGES})),
  token_count       int NOT NULL CHECK (token_count >= 0),
  is_table          boolean NOT NULL DEFAULT false,
  embedding         halfvec(1024) NOT NULL,
  embedding_model   text NOT NULL CHECK (embedding_model ~ '{MODEL}'),
  -- Denormalised copies (docs/05 §6) so retrieval is one indexed query per branch; rewritten
  -- by the ACL refresh job whenever the document's ACL or metadata changes.
  doc_type          text NOT NULL CHECK (doc_type IN ({DOC_TYPES})),
  issued_on         date,
  academic_year_id  uuid,
  sensitivity       text NOT NULL CHECK (sensitivity IN ('C1','C2','C3')),
  acl_roles         text[] NOT NULL DEFAULT '{{}}',
  acl_sections      uuid[] NOT NULL DEFAULT '{{}}',
  acl_classes       uuid[] NOT NULL DEFAULT '{{}}',
  acl_memberships   uuid[] NOT NULL DEFAULT '{{}}',
  is_latest         boolean NOT NULL DEFAULT false,
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT document_chunks_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT document_chunks_version_chunk_key UNIQUE (tenant_id, version_id, chunk_no),
  CONSTRAINT document_chunks_pages_ordered
    CHECK (page_from IS NULL OR page_to IS NULL OR page_to >= page_from),
  CONSTRAINT document_chunks_acl_no_nulls CHECK (
    array_position(acl_roles, NULL) IS NULL AND array_position(acl_sections, NULL) IS NULL
    AND array_position(acl_classes, NULL) IS NULL
    AND array_position(acl_memberships, NULL) IS NULL),
  CONSTRAINT document_chunks_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT document_chunks_version_fk FOREIGN KEY (tenant_id, document_id, version_id)
    REFERENCES kb.document_versions (tenant_id, document_id, id) ON DELETE CASCADE,
  CONSTRAINT document_chunks_academic_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id)
);
-- Vector branch: ORDER BY embedding <=> query vector (docs/06 §6; SET LOCAL hnsw.ef_search per query).
CREATE INDEX document_chunks_embedding_hnsw ON kb.document_chunks
  USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64);
-- Full-text and keyword branches reach one school's latest chunks through this index (RLS
-- makes GIN unusable for their non-leakproof operators; see the module docstring).
CREATE INDEX document_chunks_filter ON kb.document_chunks (tenant_id, is_latest, doc_type, issued_on);
-- Ingestion: replace, promote, refresh ACL copies and delete per document/version.
CREATE INDEX document_chunks_document ON kb.document_chunks (tenant_id, document_id, version_id);

CREATE TABLE kb.embedding_cache (                  -- per tenant; never shared (ADR-0006)
  tenant_id       uuid NOT NULL REFERENCES core.tenants (id),
  model           text NOT NULL CHECK (model ~ '{MODEL}'),
  input_type      text NOT NULL CHECK (input_type IN ('document','query')),
  content_sha256  bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
  embedding       halfvec(1024) NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, model, input_type, content_sha256)
);
CREATE INDEX embedding_cache_expiry ON kb.embedding_cache (tenant_id, input_type, created_at);

CREATE TABLE kb.queries (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL REFERENCES core.tenants (id),
  session_id           uuid NOT NULL,
  user_id              uuid NOT NULL,
  -- AES-256-GCM under the tenant DEK (docs/05 §9); never plaintext (FR-KB-009, invariant 5).
  question_ciphertext  bytea NOT NULL,
  -- HMAC-SHA256 with the tenant HMAC key (docs/05 §9 blind index), for repeat questions.
  question_hmac        bytea NOT NULL CHECK (octet_length(question_hmac) = 32),
  answer_ciphertext    bytea,
  key_version          int NOT NULL CHECK (key_version >= 1),
  language             text CHECK (language IN ({LANGUAGES})),
  mode                 text NOT NULL CHECK (mode IN ('full','search_only')),
  route                text CHECK (route IN ('tools','documents','both','refused')),
  status               text NOT NULL
                         CHECK (status IN ('answered','not_found','refused','search_only','error')),
  error                text CHECK (error ~ '{CODE}'),
  -- IDs, source URIs and scores only; never chunk text or record values.
  tools                jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(tools) = 'array'),
  retrieved            jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(retrieved) = 'array'),
  citations            jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(citations) = 'array'),
  model_ids            jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(model_ids) = 'array'),
  input_tokens         int CHECK (input_tokens >= 0),
  output_tokens        int CHECK (output_tokens >= 0),
  latency_ms           int CHECK (latency_ms >= 0),
  feedback             text CHECK (feedback IN ('helpful','not_helpful')),
  feedback_reason      text CHECK (feedback_reason ~ '{CODE}'),
  feedback_at          timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT queries_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT queries_feedback_complete CHECK (
    (feedback IS NULL AND feedback_reason IS NULL AND feedback_at IS NULL)
    OR (feedback IS NOT NULL AND feedback_at IS NOT NULL))
);
CREATE INDEX queries_retention ON kb.queries (tenant_id, created_at);
CREATE INDEX queries_user ON kb.queries (tenant_id, user_id, created_at DESC);
CREATE INDEX queries_repeat ON kb.queries (tenant_id, question_hmac);

CREATE TABLE kb.verified_answers (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL REFERENCES core.tenants (id),
  question_canonical  text NOT NULL CHECK (char_length(question_canonical) BETWEEN 1 AND 500),
  language            text NOT NULL CHECK (language IN ({LANGUAGES})),
  answer_text         text NOT NULL CHECK (char_length(answer_text) BETWEEN 1 AND 5000),
  -- [{{"source": "sos://...", "cited_text": "..."}}, ...] (docs/06 §8-9)
  citations           jsonb NOT NULL
                        CHECK (jsonb_typeof(citations) = 'array' AND jsonb_array_length(citations) >= 1),
  -- The kb.documents row (doc_type verified_answer) that indexes it for retrieval (docs/06 §2).
  document_id         uuid,
  verified_by         uuid NOT NULL,
  verified_at         timestamptz NOT NULL,
  review_due          date,
  status              text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','needs_review','retired')),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  version             int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT verified_answers_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT verified_answers_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id)
);
CREATE INDEX verified_answers_status ON kb.verified_answers (tenant_id, status, review_due);
CREATE TRIGGER verified_answers_set_updated_at BEFORE UPDATE ON kb.verified_answers
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- Reporting has no use for cached vectors of document and question text.
REVOKE ALL ON kb.embedding_cache FROM sos_readonly;
"""

TENANT_TABLES = ("kb.document_chunks", "kb.embedding_cache", "kb.queries", "kb.verified_answers")


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


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute(
        "ALTER TABLE kb.document_versions "
        "DROP CONSTRAINT IF EXISTS document_versions_tenant_document_id_key"
    )
