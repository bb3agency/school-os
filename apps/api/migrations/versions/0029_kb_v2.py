"""Knowledge v2 (M2 wave 5): streamed and cancelled questions, conversation history lookup.

docs/06 §5.1 (streaming) and §5 (conversation, FR-KB-012):

- ``kb.queries.status`` also allows ``streaming`` (the row and its audit event are written
  before the first SSE event; the stream then completes the row) and ``cancelled`` (the client
  went away before the answer was complete). FR-KB-008, FR-KB-009.
- ``kb.llm_calls.outcome`` also allows ``cancelled`` (a streamed model call stopped because the
  client went away; the tokens used so far are still metered). FR-KB-009, NFR-CST-001.
- ``queries_session`` index ``(tenant_id, user_id, session_id, created_at DESC)``: the previous
  questions of the caller's OWN session (conversation history is never read across users).

Expand-only (CLAUDE.md invariant 12). Downgrade drops the index and restores the 0021/0024 checks
as ``NOT VALID``: rows already in a new state are kept as they are (the migrator cannot rewrite
them under FORCE RLS, and the old code reads ``status``/``outcome`` as plain text), while new rows
must satisfy the old checks again. Walking up once more re-adds the full, validated checks.

Revision ID: 0029_kb_v2
Revises: 0029_invoice_pdfs
Create Date: 2026-09-28
"""

from __future__ import annotations

from alembic import op

revision = "0029_kb_v2"
down_revision = "0029_invoice_pdfs"
branch_labels = None
depends_on = None

QUERY_STATUSES = "'answered','not_found','refused','search_only','error'"
LLM_OUTCOMES = "'ok','refused','max_tokens','invalid_output','unavailable','rejected'"


def upgrade() -> None:
    op.execute("ALTER TABLE kb.queries DROP CONSTRAINT queries_status_check")
    op.execute(
        "ALTER TABLE kb.queries ADD CONSTRAINT queries_status_check "
        f"CHECK (status IN ({QUERY_STATUSES},'streaming','cancelled'))"
    )
    op.execute("ALTER TABLE kb.llm_calls DROP CONSTRAINT llm_calls_outcome_check")
    op.execute(
        "ALTER TABLE kb.llm_calls ADD CONSTRAINT llm_calls_outcome_check "
        f"CHECK (outcome IN ({LLM_OUTCOMES},'cancelled'))"
    )
    op.execute(
        "CREATE INDEX queries_session ON kb.queries (tenant_id, user_id, session_id, "
        "created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS kb.queries_session")
    op.execute("ALTER TABLE kb.queries DROP CONSTRAINT queries_status_check")
    op.execute(
        "ALTER TABLE kb.queries ADD CONSTRAINT queries_status_check "
        f"CHECK (status IN ({QUERY_STATUSES})) NOT VALID"
    )
    op.execute("ALTER TABLE kb.llm_calls DROP CONSTRAINT llm_calls_outcome_check")
    op.execute(
        "ALTER TABLE kb.llm_calls ADD CONSTRAINT llm_calls_outcome_check "
        f"CHECK (outcome IN ({LLM_OUTCOMES})) NOT VALID"
    )
