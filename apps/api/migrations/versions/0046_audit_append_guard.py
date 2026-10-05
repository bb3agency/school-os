"""The database accepts an audit event only as the next, current link (SEC-007; audit 2026-10-05
DP-01).

Before this revision the hash chain protected events against change (no UPDATE/DELETE/TRUNCATE)
but not against a *forged append* by the role that writes them. ``sos_app`` holds INSERT on
``audit.events`` and UPDATE on ``audit.chain_heads``; ``sos_platform`` the same on the
control-plane chain. Code running with those rights (an injection, a compromised task, a bug)
could:

- append a correctly hashed event with any ``occurred_at``, for example three days ago, into a
  day whose signed archive is already under Object Lock, and advance the head: the daily
  verification still reported the chain intact and no archive ever contained the event;
- rewind or skip the head (detected only at the next verification).

Now, for those writer roles only (``sos_app`` and ``sos_definer`` on the school chain,
``sos_platform`` on the control-plane chain):

- ``BEFORE INSERT`` on the events: ``occurred_at`` within 5 minutes of the database clock
  (``clock_timestamp()``; app and database hosts are NTP-synchronised, PRV-019), ``seq`` = head
  + 1 and ``prev_hash`` = head hash (the head row is already locked ``FOR UPDATE`` by
  ``audit.record``). A school without a head starts at seq 1 from the zero hash.
- ``BEFORE UPDATE`` on the head: it moves forward by exactly one, onto the event just written
  (same seq, its hash, its ``prev_hash`` = the old head hash).
- ``BEFORE INSERT`` on ``audit.chain_heads``: a new head is the genesis head (seq 0, zero hash).

Owner, migrator and superuser sessions are not checked (restore drills and the offboarding tests
write historical events as the owner). An attacker who already holds those has other means and is
covered by the signed archive. All functions are SECURITY INVOKER with a pinned ``search_path``
and executable by no one directly (trigger functions).

Downgrade drops the triggers and functions (expand-only revision: nothing else changes).

Revision ID: 0046_audit_append_guard
Revises: 0045_invitation_consent
Create Date: 2026-10-05
"""

from __future__ import annotations

from alembic import op

revision = "0046_audit_append_guard"
down_revision = "0045_invitation_consent"
branch_labels = None
depends_on = None

UPGRADE_SQL: list[str] = [
    # ---- school chains (audit.events / audit.chain_heads) -------------------------------------
    """
    CREATE FUNCTION audit.guard_event_append() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp
      AS $$
      DECLARE
        v_seq  bigint;
        v_hash bytea;
      BEGIN
        IF current_user NOT IN ('sos_app', 'sos_definer') THEN
          RETURN NEW;
        END IF;
        IF NEW.occurred_at < clock_timestamp() - interval '5 minutes'
           OR NEW.occurred_at > clock_timestamp() + interval '5 minutes' THEN
          RAISE EXCEPTION 'audit event is not dated now'
            USING ERRCODE = 'check_violation',
                  HINT = 'Audit events are written with the current time (SEC-007).';
        END IF;
        SELECT h.last_seq, h.last_hash INTO v_seq, v_hash
          FROM audit.chain_heads AS h WHERE h.tenant_id = NEW.tenant_id;
        IF NOT FOUND THEN
          v_seq := 0;
          v_hash := decode(repeat('00', 32), 'hex');
        END IF;
        IF NEW.seq <> v_seq + 1 OR NEW.prev_hash <> v_hash THEN
          RAISE EXCEPTION 'audit event is not the next link of the chain'
            USING ERRCODE = 'check_violation',
                  HINT = 'Write audit events only through audit.record (SEC-007).';
        END IF;
        RETURN NEW;
      END
      $$
    """,
    """
    CREATE TRIGGER events_append_guard BEFORE INSERT ON audit.events
      FOR EACH ROW EXECUTE FUNCTION audit.guard_event_append()
    """,
    """
    CREATE FUNCTION audit.guard_head_advance() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp
      AS $$
      BEGIN
        IF current_user NOT IN ('sos_app', 'sos_definer') THEN
          RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
          IF NEW.last_seq <> 0 OR NEW.last_hash <> decode(repeat('00', 32), 'hex') THEN
            RAISE EXCEPTION 'audit chain head must start at the genesis'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END IF;
        IF NEW.tenant_id <> OLD.tenant_id
           OR NEW.last_seq <> OLD.last_seq + 1
           OR NOT EXISTS (
             SELECT 1 FROM audit.events AS e
              WHERE e.tenant_id = NEW.tenant_id
                AND e.seq = NEW.last_seq
                AND e.occurred_at >= now() - interval '10 minutes'
                AND e.hash = NEW.last_hash
                AND e.prev_hash = OLD.last_hash) THEN
          RAISE EXCEPTION 'audit chain head may only advance onto the event just written'
            USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
      END
      $$
    """,
    """
    CREATE TRIGGER chain_heads_advance_guard BEFORE INSERT OR UPDATE ON audit.chain_heads
      FOR EACH ROW EXECUTE FUNCTION audit.guard_head_advance()
    """,
    # ---- control-plane chain (platform.audit_events / platform.audit_chain_head) --------------
    """
    CREATE FUNCTION platform.guard_event_append() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp
      AS $$
      DECLARE
        v_seq  bigint;
        v_hash bytea;
      BEGIN
        IF current_user <> 'sos_platform' THEN
          RETURN NEW;
        END IF;
        IF NEW.occurred_at < clock_timestamp() - interval '5 minutes'
           OR NEW.occurred_at > clock_timestamp() + interval '5 minutes' THEN
          RAISE EXCEPTION 'audit event is not dated now'
            USING ERRCODE = 'check_violation';
        END IF;
        SELECT h.last_seq, h.last_hash INTO v_seq, v_hash
          FROM platform.audit_chain_head AS h WHERE h.id;
        IF NOT FOUND OR NEW.seq <> v_seq + 1 OR NEW.prev_hash <> v_hash THEN
          RAISE EXCEPTION 'audit event is not the next link of the chain'
            USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
      END
      $$
    """,
    """
    CREATE TRIGGER audit_events_append_guard BEFORE INSERT ON platform.audit_events
      FOR EACH ROW EXECUTE FUNCTION platform.guard_event_append()
    """,
    """
    CREATE FUNCTION platform.guard_head_advance() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp
      AS $$
      BEGIN
        IF current_user <> 'sos_platform' THEN
          RETURN NEW;
        END IF;
        IF NEW.last_seq <> OLD.last_seq + 1
           OR NOT EXISTS (
             SELECT 1 FROM platform.audit_events AS e
              WHERE e.seq = NEW.last_seq
                AND e.hash = NEW.last_hash
                AND e.prev_hash = OLD.last_hash) THEN
          RAISE EXCEPTION 'audit chain head may only advance onto the event just written'
            USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
      END
      $$
    """,
    """
    CREATE TRIGGER audit_chain_head_advance_guard BEFORE UPDATE ON platform.audit_chain_head
      FOR EACH ROW EXECUTE FUNCTION platform.guard_head_advance()
    """,
    "REVOKE ALL ON FUNCTION audit.guard_event_append() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION audit.guard_head_advance() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION platform.guard_event_append() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION platform.guard_head_advance() FROM PUBLIC",
]

DOWNGRADE_SQL: list[str] = [
    "DROP TRIGGER IF EXISTS audit_chain_head_advance_guard ON platform.audit_chain_head",
    "DROP TRIGGER IF EXISTS audit_events_append_guard ON platform.audit_events",
    "DROP FUNCTION IF EXISTS platform.guard_head_advance()",
    "DROP FUNCTION IF EXISTS platform.guard_event_append()",
    "DROP TRIGGER IF EXISTS chain_heads_advance_guard ON audit.chain_heads",
    "DROP TRIGGER IF EXISTS events_append_guard ON audit.events",
    "DROP FUNCTION IF EXISTS audit.guard_head_advance()",
    "DROP FUNCTION IF EXISTS audit.guard_event_append()",
]


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement)
