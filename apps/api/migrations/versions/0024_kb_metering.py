"""Knowledge (M2): kb.llm_calls, the durable per-call AI metering ledger (FR-KB-009, FR-KB-011).

One row per model call the gateway makes (docs/06 §12 as built: ``MeteringEvent``): tenant,
feature, role, the ``kb.queries`` id it served (if any), provider, model, outcome, attempts,
latency, input/output/cache tokens and list-price cost in USD. It is the durable record behind
the Valkey month counter (``sos:kb:spend:{tenant}:{YYYY-MM}``), so spend per school and month
can be recomputed and billed (NFR-CST-001). No prompt, completion, question or answer text, and
no personal data: ids, codes and counts only (invariant 5).

``query_id`` is deliberately NOT a foreign key: a call is metered in its own short transaction
(the spend happened even when the question's transaction later rolls back), so its
``kb.queries`` row may never exist.

Tenant-owned: RLS ENABLE + FORCE with the standard ``tenant_isolation`` policy; grants from the
``kb`` default privileges (``sos_app`` DML, ``sos_readonly`` SELECT); ``sos_app`` may not UPDATE
(append-only ledger; retention deletes by age). ``sos_platform`` has nothing on it. No
``definer_access`` policy and no ``SECURITY DEFINER`` function (ADR-0013).

Downgrade drops the table (lossy for the ledger; the Valkey counter and ``kb.queries`` token
totals remain).

Requirements: FR-KB-009, FR-KB-011, NFR-CST-001, SEC-001.

Revision ID: 0024_kb_metering
Revises: 0026_dek_rotation
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0024_kb_metering"
down_revision = "0026_dek_rotation"
branch_labels = None
depends_on = None

MODEL = r"^[a-z0-9][a-z0-9._:/-]{0,99}$"
CODE = r"^[a-z][a-z0-9_]{0,63}$"

UPGRADE_SQL = rf"""
CREATE TABLE kb.llm_calls (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL REFERENCES core.tenants (id),
  occurred_at         timestamptz NOT NULL DEFAULT now(),
  feature             text NOT NULL
                        CHECK (feature IN ('ask','metadata','translation','extraction',
                                           'embeddings','eval')),
  role                text NOT NULL
                        CHECK (role IN ('answer','router','metadata','translation','extraction',
                                        'eval_judge')),
  query_id            uuid,
  provider            text NOT NULL CHECK (provider ~ '{CODE}'),
  model               text NOT NULL CHECK (model ~ '{MODEL}'),
  outcome             text NOT NULL
                        CHECK (outcome IN ('ok','refused','max_tokens','invalid_output',
                                           'unavailable','rejected')),
  attempts            int NOT NULL CHECK (attempts BETWEEN 0 AND 100),
  latency_ms          int NOT NULL CHECK (latency_ms >= 0),
  input_tokens        int NOT NULL CHECK (input_tokens >= 0),
  output_tokens       int NOT NULL CHECK (output_tokens >= 0),
  cache_write_tokens  int NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
  cache_read_tokens   int NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
  cost_usd            numeric(14, 6) NOT NULL CHECK (cost_usd >= 0),
  CONSTRAINT llm_calls_tenant_id_id_key UNIQUE (tenant_id, id)
);
-- Month-to-date spend per school (IST months are computed by the reader).
CREATE INDEX llm_calls_spend ON kb.llm_calls (tenant_id, occurred_at);
CREATE INDEX llm_calls_query ON kb.llm_calls (tenant_id, query_id) WHERE query_id IS NOT NULL;

-- Append-only for the application: a metered call is never rewritten.
REVOKE UPDATE ON kb.llm_calls FROM sos_app;

ALTER TABLE kb.llm_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb.llm_calls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kb.llm_calls
  USING (tenant_id = core.current_tenant())
  WITH CHECK (tenant_id = core.current_tenant());
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kb.llm_calls")
