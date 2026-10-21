"""R2-4 close-out: a suspended school's WhatsApp account still resolves for the Meta webhook
(owner decision D35 of 2026-10-21; ADR-0047 decision 2 follow-up; ADR-0043; FR-MSG-010).

``core.resolve_public_handle`` answered only for active schools (``parent_receipt`` and
``cert_verify`` also while suspended). D31 kept everything of a suspended school unprocessed,
including a parent's STOP. D35 keeps that, with one exception that protects the parent: a STOP
(opt-out) reply is still recorded. So ``meta_waba`` now also resolves while the school is
``suspended``; the messaging service then records inbound STOP replies only, silently (no
confirmation is sent), and drops every other event as before (``messaging.service
.record_suspended_stops``). An offboarding or deleted school still resolves nothing.

The function keeps its signature, owner (``sos_definer``, NOBYPASSRLS) and grant (``sos_app``
only); only the list of kinds allowed while suspended changes. No table, column, permission or
setting changes.

Downgrade restores the 0055 body (a suspended school's STOP is dropped again, D31).

Revision ID: 0063_r2_closeout
Revises: 0062_messaging_finish
Create Date: 2026-10-21
"""

from __future__ import annotations

from alembic import op

revision = "0063_r2_closeout"
down_revision = "0062_messaging_finish"
branch_labels = None
depends_on = None

SIGNATURE = "core.resolve_public_handle(text,bytea)"

_BODY = r"""
CREATE OR REPLACE FUNCTION core.resolve_public_handle(p_kind text, p_key_hash bytea)
  RETURNS TABLE (tenant_id uuid, handle_id uuid)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT h.tenant_id, h.id
  FROM core.public_handles AS h
  JOIN core.tenants AS t ON t.id = h.tenant_id
  WHERE h.kind = p_kind
    AND h.key_hash = p_key_hash
    AND h.revoked_at IS NULL
    AND (h.expires_at IS NULL OR h.expires_at > pg_catalog.now())
    AND (t.status = 'active'
         OR (t.status = 'suspended' AND h.kind IN ({kinds})))
$$;
"""

SUSPENDED_KINDS_0055 = "'parent_receipt', 'cert_verify'"
SUSPENDED_KINDS_0063 = "'parent_receipt', 'cert_verify', 'meta_waba'"


def _replace(kinds: str) -> None:
    # The definer function is replaced AS sos_definer (its owner); the CREATE grant on schema
    # core exists only inside this transaction (as in 0055).
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(_BODY.replace("{kinds}", kinds))
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def upgrade() -> None:
    _replace(SUSPENDED_KINDS_0063)


def downgrade() -> None:
    _replace(SUSPENDED_KINDS_0055)
