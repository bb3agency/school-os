"""Audit: append-only, partitioned, per-tenant hash-chained events + platform audit chain.

Requirements: FR-AUD-001..003, SEC-007, threat T4 (docs/07), docs/05 §7 and §12, ADR-0013.

Tenant chain (schema ``audit``):
- ``audit.events`` partitioned by RANGE (occurred_at), monthly partitions ``events_yYYYYmMM``.
  The app reaches rows only through the parent (partitions: REVOKE ALL, RLS forced).
- ``audit.chain_heads``: one row per tenant (last_seq, last_hash); ``SELECT ... FOR UPDATE``
  serialises audit writes per tenant.
- Append-only: no UPDATE/DELETE/TRUNCATE grants, plus BEFORE UPDATE/DELETE row triggers and a
  BEFORE TRUNCATE statement trigger on the parent and every partition (even the owner is blocked).
- Partitions are created by ``audit.create_month_partition(date)`` /
  ``audit.ensure_partitions(int)``: SECURITY INVOKER functions owned by sos_owner and executable
  only by it. The deploy pipeline runs ``python -m app.audit.partitions`` with migrator
  credentials after ``alembic upgrade head`` (no SECURITY DEFINER function needed).

Platform chain (schema ``platform``, no RLS: privilege separation instead):
- ``platform.audit_events`` + single-row ``platform.audit_chain_head``; only sos_platform may
  SELECT/INSERT (and UPDATE the head).

Revision ID: 0002_audit
Revises: 0001_baseline
Create Date: 2026-09-26
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from alembic import op
from sqlalchemy import text

revision = "0002_audit"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# First month with partitions, and the minimum runway created by this migration.
FIRST_MONTH = date(2026, 9, 1)
MIN_MONTHS_FROM_FIRST = 24
MIN_MONTHS_AHEAD_OF_NOW = 12

ACTION_CHECK = r"action ~ '^[a-z_]+(\.[a-z_]+)+$'"

UPGRADE_SQL: list[str] = [
    # ---- append-only guard (shared by parent and partitions) ---------------------------------
    """
    CREATE FUNCTION audit.block_mutation() RETURNS trigger
      LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
      AS $$
      BEGIN
        RAISE EXCEPTION 'audit.events is append-only'
          USING ERRCODE = 'insufficient_privilege',
                HINT = 'Audit events can never be updated, deleted or truncated (FR-AUD-002).';
      END
      $$
    """,
    # ---- tenant audit events --------------------------------------------------------------------
    f"""
    CREATE TABLE audit.events (
      id            uuid        NOT NULL,
      tenant_id     uuid        NOT NULL,
      seq           bigint      NOT NULL CHECK (seq > 0),
      occurred_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
      actor_type    text        NOT NULL CHECK (actor_type IN ('user', 'system', 'platform')),
      actor_id      uuid,
      action        text        NOT NULL CHECK ({ACTION_CHECK}),
      resource_type text        NOT NULL,
      resource_id   uuid,
      summary       jsonb       NOT NULL CHECK (jsonb_typeof(summary) = 'object'),
      request_id    text,
      ip_hash       bytea,
      prev_hash     bytea       NOT NULL CHECK (octet_length(prev_hash) = 32),
      hash          bytea       NOT NULL CHECK (octet_length(hash) = 32),
      PRIMARY KEY (tenant_id, seq, occurred_at)
    ) PARTITION BY RANGE (occurred_at)
    """,
    "CREATE INDEX events_tenant_occurred_idx ON audit.events (tenant_id, occurred_at)",
    "ALTER TABLE audit.events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit.events FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON audit.events
      USING (tenant_id = core.current_tenant())
      WITH CHECK (tenant_id = core.current_tenant())
    """,
    """
    CREATE POLICY definer_access ON audit.events
      USING (current_user = 'sos_definer')
      WITH CHECK (current_user = 'sos_definer')
    """,
    """
    CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON audit.events
      FOR EACH ROW EXECUTE FUNCTION audit.block_mutation()
    """,
    """
    CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON audit.events
      FOR EACH STATEMENT EXECUTE FUNCTION audit.block_mutation()
    """,
    "REVOKE ALL ON audit.events FROM PUBLIC, sos_app, sos_readonly, sos_definer",
    "GRANT SELECT, INSERT ON audit.events TO sos_app",
    "GRANT SELECT ON audit.events TO sos_readonly",
    "GRANT SELECT, INSERT ON audit.events TO sos_definer",
    # ---- partition management (owner-only, SECURITY INVOKER) ------------------------------------
    """
    CREATE FUNCTION audit.create_month_partition(p_month date) RETURNS text
      LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp
      AS $$
      DECLARE
        v_start date := date_trunc('month', p_month)::date;
        v_end   date := (date_trunc('month', p_month) + interval '1 month')::date;
        v_name  text := format('events_y%sm%s', to_char(v_start, 'YYYY'), to_char(v_start, 'MM'));
      BEGIN
        IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'audit' AND c.relname = v_name) THEN
          RETURN NULL;
        END IF;
        -- Bounds are explicit UTC instants, independent of the session TimeZone.
        EXECUTE format(
          'CREATE TABLE audit.%I PARTITION OF audit.events FOR VALUES FROM (%L) TO (%L)',
          v_name, v_start::text || ' 00:00:00+00', v_end::text || ' 00:00:00+00');
        EXECUTE format('ALTER TABLE audit.%I ENABLE ROW LEVEL SECURITY', v_name);
        EXECUTE format('ALTER TABLE audit.%I FORCE ROW LEVEL SECURITY', v_name);
        EXECUTE format(
          'CREATE POLICY tenant_isolation ON audit.%I '
          'USING (tenant_id = core.current_tenant()) '
          'WITH CHECK (tenant_id = core.current_tenant())', v_name);
        -- Row triggers are cloned from the parent; TRUNCATE triggers are not.
        EXECUTE format(
          'CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON audit.%I '
          'FOR EACH STATEMENT EXECUTE FUNCTION audit.block_mutation()', v_name);
        -- The app only ever goes through the parent table.
        EXECUTE format('REVOKE ALL ON audit.%I FROM PUBLIC, sos_app, sos_readonly, sos_definer',
                       v_name);
        RETURN v_name;
      END
      $$
    """,
    """
    CREATE FUNCTION audit.ensure_partitions(p_months_ahead int) RETURNS SETOF text
      LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp
      AS $$
      DECLARE
        v_first date := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
        v_name  text;
      BEGIN
        IF p_months_ahead < 0 OR p_months_ahead > 60 THEN
          RAISE EXCEPTION 'months_ahead must be between 0 and 60';
        END IF;
        FOR i IN 0..p_months_ahead LOOP
          v_name := audit.create_month_partition((v_first + make_interval(months => i))::date);
          IF v_name IS NOT NULL THEN
            RETURN NEXT v_name;
          END IF;
        END LOOP;
      END
      $$
    """,
    "REVOKE ALL ON FUNCTION audit.create_month_partition(date) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION audit.ensure_partitions(int) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION audit.block_mutation() FROM PUBLIC",
    # ---- per-tenant chain heads -------------------------------------------------------------------
    """
    CREATE TABLE audit.chain_heads (
      tenant_id  uuid        PRIMARY KEY,
      last_seq   bigint      NOT NULL CHECK (last_seq >= 0),
      last_hash  bytea       NOT NULL CHECK (octet_length(last_hash) = 32),
      updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE audit.chain_heads ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit.chain_heads FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON audit.chain_heads
      USING (tenant_id = core.current_tenant())
      WITH CHECK (tenant_id = core.current_tenant())
    """,
    """
    CREATE POLICY definer_access ON audit.chain_heads
      USING (current_user = 'sos_definer')
      WITH CHECK (current_user = 'sos_definer')
    """,
    "REVOKE ALL ON audit.chain_heads FROM PUBLIC, sos_app, sos_readonly, sos_definer",
    "GRANT SELECT, INSERT, UPDATE ON audit.chain_heads TO sos_app",
    "GRANT SELECT ON audit.chain_heads TO sos_readonly",
    # Genesis heads are created lazily by audit.record() on a tenant's first audited action;
    # sos_definer keeps SELECT/INSERT for definer functions that audit before the first event.
    "GRANT SELECT, INSERT ON audit.chain_heads TO sos_definer",
    # ---- platform (control-plane) chain -----------------------------------------------------------
    """
    CREATE FUNCTION platform.block_mutation() RETURNS trigger
      LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
      AS $$
      BEGIN
        RAISE EXCEPTION 'platform.audit_events is append-only'
          USING ERRCODE = 'insufficient_privilege';
      END
      $$
    """,
    "REVOKE ALL ON FUNCTION platform.block_mutation() FROM PUBLIC",
    f"""
    CREATE TABLE platform.audit_events (
      id                uuid        PRIMARY KEY,
      seq               bigint      NOT NULL UNIQUE CHECK (seq > 0),
      occurred_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
      actor_type        text        NOT NULL CHECK (actor_type IN ('operator', 'system')),
      actor_id          uuid,
      action            text        NOT NULL CHECK ({ACTION_CHECK}),
      resource_type     text        NOT NULL,
      resource_id       uuid,
      subject_tenant_id uuid,
      summary           jsonb       NOT NULL CHECK (jsonb_typeof(summary) = 'object'),
      request_id        text,
      ip_hash           bytea,
      prev_hash         bytea       NOT NULL CHECK (octet_length(prev_hash) = 32),
      hash              bytea       NOT NULL CHECK (octet_length(hash) = 32)
    )
    """,
    "CREATE INDEX audit_events_occurred_idx ON platform.audit_events (occurred_at)",
    """
    CREATE INDEX audit_events_subject_idx ON platform.audit_events (subject_tenant_id, occurred_at)
      WHERE subject_tenant_id IS NOT NULL
    """,
    """
    CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON platform.audit_events
      FOR EACH ROW EXECUTE FUNCTION platform.block_mutation()
    """,
    """
    CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON platform.audit_events
      FOR EACH STATEMENT EXECUTE FUNCTION platform.block_mutation()
    """,
    """
    CREATE TABLE platform.audit_chain_head (
      id         boolean     PRIMARY KEY DEFAULT true CHECK (id),
      last_seq   bigint      NOT NULL CHECK (last_seq >= 0),
      last_hash  bytea       NOT NULL CHECK (octet_length(last_hash) = 32),
      updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    INSERT INTO platform.audit_chain_head (id, last_seq, last_hash)
      VALUES (true, 0, decode(repeat('00', 32), 'hex'))
    """,
    """
    REVOKE ALL ON platform.audit_events, platform.audit_chain_head
      FROM PUBLIC, sos_app, sos_readonly, sos_platform, sos_definer
    """,
    "GRANT SELECT, INSERT ON platform.audit_events TO sos_platform",
    "GRANT SELECT, UPDATE ON platform.audit_chain_head TO sos_platform",
]

DOWNGRADE_SQL: list[str] = [
    "DROP TABLE IF EXISTS platform.audit_chain_head",
    "DROP TABLE IF EXISTS platform.audit_events",
    "DROP FUNCTION IF EXISTS platform.block_mutation()",
    "DROP TABLE IF EXISTS audit.chain_heads",
    "DROP FUNCTION IF EXISTS audit.ensure_partitions(int)",
    "DROP FUNCTION IF EXISTS audit.create_month_partition(date)",
    # Dropping the parent drops every partition with it.
    "DROP TABLE IF EXISTS audit.events",
    "DROP FUNCTION IF EXISTS audit.block_mutation()",
]


def _add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def initial_partition_months(today: date) -> list[date]:
    """Months to create: FIRST_MONTH .. max(FIRST_MONTH + 24, current month + 12)."""
    current = date(today.year, today.month, 1)
    last = max(
        _add_months(FIRST_MONTH, MIN_MONTHS_FROM_FIRST - 1),
        _add_months(current, MIN_MONTHS_AHEAD_OF_NOW),
    )
    months: list[date] = []
    month = FIRST_MONTH
    while month <= last:
        months.append(month)
        month = _add_months(month, 1)
    return months


def upgrade() -> None:
    bind = op.get_bind()
    for statement in UPGRADE_SQL:
        bind.execute(text(statement))
    for month in initial_partition_months(datetime.now(UTC).date()):
        bind.execute(text("SELECT audit.create_month_partition(:m)"), {"m": month})


def downgrade() -> None:
    bind = op.get_bind()
    for statement in DOWNGRADE_SQL:
        bind.execute(text(statement))
