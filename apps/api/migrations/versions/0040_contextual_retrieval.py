"""Contextual retrieval: chunk contexts and per-document metering (docs/05 §6.2, docs/06 §4.11).

PO approval 2026-09-30 (behind ``retrieval.yaml`` ``contextual_chunks``, off by default).

``kb.document_chunks`` (existing tenant table: RLS, grants, the offboarding purge policy and the
purge/export paths are unchanged):

- ``chunk_context text NOT NULL DEFAULT ''`` (<= 1000 characters): a model-written context that
  situates the chunk in its document. Embedding, full-text and keyword input only; never shown to
  users and never sent to the answer model. Plain text like ``content`` (C1/C2 only: C3 is never
  indexed, docs/06 §4.9), so no new ciphertext column, no DEK re-encryptor and no SEC-012 census
  entry.
- ``context_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', chunk_context)) STORED``: the
  full-text branch reads ``content_tsv || context_tsv`` when contextual chunks are on (generated,
  like ``content_tsv``, so ingestion cannot let it drift). Adding a stored generated column
  rewrites the table once (seconds at today's volumes; docs/05 §12).
- ``context_status`` ``none`` (not asked: the flag was off or the chunk predates it), ``ok``,
  ``rejected`` (the output failed the checks; indexed without a context) or ``deferred`` (the
  school's AI budget was used up, AI was off or the provider was down; indexed plainly, the
  backfill task asks again). ``ok`` exactly when ``chunk_context`` is not empty, and then with
  ``context_model`` and ``context_prompt`` (``<prompt id>.v<n>``) set.
- Partial index ``document_chunks_context_pending (tenant_id, document_id, version_id) WHERE
  is_latest AND context_status IN ('none','deferred')`` for the backfill.

``kb.llm_calls``: nullable ``document_id`` (the document a ``contextualize`` call served; no FK:
the ledger outlives deleted documents, like ``query_id``) with a partial index, and the feature
and model role ``contextualize`` in the metering checks.

Expand-only (invariant 12). Downgrade drops the index and the new columns (lossy: the contexts
and the per-document attribution; the previous code reads neither) and restores the metering
checks as ``NOT VALID`` (rows metered for ``contextualize`` stay; new ones are refused).

Revision ID: 0040_contextual_retrieval
Revises: 0038_ask_conversations
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op

revision = "0040_contextual_retrieval"
down_revision = "0038_ask_conversations"
branch_labels = None
depends_on = None

MODEL = r"^[a-z0-9][a-z0-9._:/-]{0,99}$"
PROMPT = r"^[a-z][a-z0-9_]{0,63}\.v[1-9][0-9]{0,3}$"
OLD_FEATURES = (
    "'ask','metadata','translation','extraction','embeddings','eval','circulars','notices'"
)
OLD_ROLES = (
    "'answer','router','metadata','translation','extraction','eval_judge','circular','notice',"
    "'followups','summary','memory_screen','query_rewrite'"
)
NEW_FEATURES = OLD_FEATURES + ",'contextualize'"
NEW_ROLES = OLD_ROLES + ",'contextualize'"

UPGRADE_SQL = rf"""
ALTER TABLE kb.document_chunks
  ADD COLUMN chunk_context text NOT NULL DEFAULT ''
    CONSTRAINT document_chunks_context_length CHECK (char_length(chunk_context) <= 1000),
  ADD COLUMN context_status text NOT NULL DEFAULT 'none'
    CONSTRAINT document_chunks_context_status
      CHECK (context_status IN ('none','ok','rejected','deferred')),
  ADD COLUMN context_model text
    CONSTRAINT document_chunks_context_model CHECK (context_model ~ '{MODEL}'),
  ADD COLUMN context_prompt text
    CONSTRAINT document_chunks_context_prompt CHECK (context_prompt ~ '{PROMPT}'),
  ADD COLUMN context_tsv tsvector NOT NULL
    GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, chunk_context)) STORED,
  ADD CONSTRAINT document_chunks_context_ok CHECK (
    (context_status = 'ok') = (chunk_context <> '')
    AND (context_status <> 'ok' OR (context_model IS NOT NULL AND context_prompt IS NOT NULL)));
-- Backfill: latest chunks still without a context (flag turned on later, or deferred).
CREATE INDEX document_chunks_context_pending ON kb.document_chunks
  (tenant_id, document_id, version_id)
  WHERE is_latest AND context_status IN ('none','deferred');

ALTER TABLE kb.llm_calls ADD COLUMN document_id uuid;
CREATE INDEX llm_calls_document ON kb.llm_calls (tenant_id, document_id)
  WHERE document_id IS NOT NULL;
"""

DOWNGRADE_SQL = """
DROP INDEX IF EXISTS kb.llm_calls_document;
ALTER TABLE kb.llm_calls DROP COLUMN IF EXISTS document_id;
DROP INDEX IF EXISTS kb.document_chunks_context_pending;
ALTER TABLE kb.document_chunks
  DROP CONSTRAINT IF EXISTS document_chunks_context_ok,
  DROP COLUMN IF EXISTS context_tsv,
  DROP COLUMN IF EXISTS context_prompt,
  DROP COLUMN IF EXISTS context_model,
  DROP COLUMN IF EXISTS context_status,
  DROP COLUMN IF EXISTS chunk_context;
"""


def _metering_checks(features: str, roles: str, *, not_valid: bool) -> None:
    suffix = " NOT VALID" if not_valid else ""
    op.execute("ALTER TABLE kb.llm_calls DROP CONSTRAINT llm_calls_feature_check")
    op.execute(
        "ALTER TABLE kb.llm_calls ADD CONSTRAINT llm_calls_feature_check "
        f"CHECK (feature IN ({features})){suffix}"
    )
    op.execute("ALTER TABLE kb.llm_calls DROP CONSTRAINT llm_calls_role_check")
    op.execute(
        "ALTER TABLE kb.llm_calls ADD CONSTRAINT llm_calls_role_check "
        f"CHECK (role IN ({roles})){suffix}"
    )


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    _metering_checks(NEW_FEATURES, NEW_ROLES, not_valid=False)


def downgrade() -> None:
    _metering_checks(OLD_FEATURES, OLD_ROLES, not_valid=True)
    op.execute(DOWNGRADE_SQL)
