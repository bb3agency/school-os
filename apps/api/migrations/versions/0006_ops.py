"""Tenant-side operations tables (docs/05 §7.2, ADR-0013 §9).

- ``ops.job_runs`` (tenant_id NOT NULL; platform-level jobs use ``platform.job_runs``)
- ``ops.outbox`` (transactional outbox; payloads carry IDs only) + definer
  ``ops.claim_outbox(batch int)`` for the dispatcher (``FOR UPDATE SKIP LOCKED``)
- ``ops.idempotency_keys`` (09 §2; tenant + user + key)
- ``ops.break_glass_grants`` (07 §6.4; access window at most 8 hours; workflow in M1)

Every table is tenant-owned: RLS ENABLE + FORCE with ``tenant_isolation``; ``ops.outbox`` also
carries the allowlisted ``definer_access`` policy.

Requirements: FR-OPS-004, SEC-001, SEC-026.

Revision ID: 0006_ops
Revises: 0005_platform
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op

revision = "0006_ops"
down_revision = "0005_platform"
branch_labels = None
depends_on = None

TABLES_SQL = r"""
CREATE TABLE ops.job_runs (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL REFERENCES core.tenants (id),
  task_name        text NOT NULL CHECK (task_name ~ '^[a-z_]+(\.[a-z_]+)+$'),
  idempotency_key  text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 200),
  status           text NOT NULL CHECK (status IN ('pending','running','succeeded','failed','dead')),
  attempts         int NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  progress         jsonb,
  error            text CHECK (char_length(error) <= 500),
  created_by       uuid,
  started_at       timestamptz,
  finished_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT job_runs_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT job_runs_tenant_key UNIQUE (tenant_id, idempotency_key),
  CONSTRAINT job_runs_finished_after_start CHECK (finished_at IS NULL OR started_at IS NOT NULL)
);

CREATE TABLE ops.outbox (
  id             uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL REFERENCES core.tenants (id),
  event_type     text NOT NULL CHECK (event_type ~ '^[a-z_]+(\.[a-z_]+)+$'),
  payload        jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  created_at     timestamptz NOT NULL DEFAULT now(),
  dispatched_at  timestamptz,
  CONSTRAINT outbox_tenant_id_id_key UNIQUE (tenant_id, id)
);
CREATE INDEX outbox_pending ON ops.outbox (created_at) WHERE dispatched_at IS NULL;

CREATE TABLE ops.idempotency_keys (
  tenant_id        uuid NOT NULL REFERENCES core.tenants (id),
  user_id          uuid NOT NULL,
  key              text NOT NULL CHECK (char_length(key) BETWEEN 8 AND 128),
  method           text NOT NULL CHECK (method IN ('POST','PUT','PATCH','DELETE')),
  route            text NOT NULL CHECK (char_length(route) BETWEEN 1 AND 200),
  request_sha256   bytea NOT NULL CHECK (octet_length(request_sha256) = 32),
  status           text NOT NULL CHECK (status IN ('in_progress','completed')),
  response_status  int CHECK (response_status BETWEEN 100 AND 599),
  resource_type    text,
  resource_id      uuid,
  location         text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL DEFAULT now() + interval '24 hours',
  PRIMARY KEY (tenant_id, user_id, key),
  CONSTRAINT idempotency_keys_completed CHECK (status = 'in_progress' OR response_status IS NOT NULL)
);
CREATE INDEX idempotency_expiry ON ops.idempotency_keys (expires_at);

CREATE TABLE ops.break_glass_grants (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL REFERENCES core.tenants (id),
  platform_user_id        uuid NOT NULL,           -- platform.operators.id (no cross-schema FK)
  platform_request_id     uuid,                    -- platform.breakglass_requests.id
  approved_by_membership  uuid,
  reason                  text NOT NULL CHECK (char_length(reason) BETWEEN 10 AND 500),
  scope                   jsonb NOT NULL CHECK (jsonb_typeof(scope) = 'object'),
  status                  text NOT NULL CHECK (status IN
                            ('requested','approved','active','expired','revoked','denied')),
  starts_at               timestamptz,
  expires_at              timestamptz,
  revoked_at              timestamptz,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT break_glass_grants_tenant_id_id_key UNIQUE (tenant_id, id),
  -- 07 §6.4: a grant lasts at most 8 hours.
  CONSTRAINT break_glass_grants_max_8h CHECK (
    expires_at IS NULL
    OR (starts_at IS NOT NULL AND expires_at > starts_at
        AND expires_at <= starts_at + interval '8 hours')),
  CONSTRAINT break_glass_grants_window_when_active
    CHECK (status NOT IN ('active','approved') OR (starts_at IS NOT NULL AND expires_at IS NOT NULL)),
  CONSTRAINT break_glass_grants_revoked_at CHECK ((status = 'revoked') = (revoked_at IS NOT NULL)),
  CONSTRAINT break_glass_grants_approver_fk FOREIGN KEY (tenant_id, approved_by_membership)
    REFERENCES core.memberships (tenant_id, id)
);
"""

TENANT_TABLES = ("ops.job_runs", "ops.outbox", "ops.idempotency_keys", "ops.break_glass_grants")

DEFINER_SQL = r"""
CREATE FUNCTION ops.claim_outbox(p_batch int)
  RETURNS TABLE (id uuid, tenant_id uuid, event_type text, payload jsonb)
  LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    WITH picked AS (
      SELECT o.id FROM ops.outbox AS o
      WHERE o.dispatched_at IS NULL
      ORDER BY o.created_at, o.id
      LIMIT GREATEST(LEAST(COALESCE(p_batch, 100), 500), 1)
      FOR UPDATE SKIP LOCKED
    )
    UPDATE ops.outbox AS o SET dispatched_at = pg_catalog.clock_timestamp()
    FROM picked WHERE o.id = picked.id
    RETURNING o.id, o.tenant_id, o.event_type, o.payload
$$;
REVOKE ALL ON FUNCTION ops.claim_outbox(int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.claim_outbox(int) TO sos_app;
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in ("ops.job_runs", "ops.break_glass_grants"):
        op.execute(
            f"CREATE TRIGGER {table.split('.')[1]}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at()"
        )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
    op.execute(
        "CREATE POLICY definer_access ON ops.outbox AS PERMISSIVE FOR ALL TO PUBLIC "
        "USING (current_user = 'sos_definer') WITH CHECK (current_user = 'sos_definer')"
    )
    # Outbox rows are written by the app and only claimed by the dispatcher (never edited).
    op.execute("REVOKE UPDATE, DELETE ON ops.outbox FROM sos_app")
    op.execute("GRANT SELECT, UPDATE (dispatched_at) ON ops.outbox TO sos_definer")

    op.execute("GRANT CREATE ON SCHEMA ops TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(DEFINER_SQL)
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA ops FROM sos_definer")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ops.claim_outbox(int)")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
