"""Invitation acceptance on first sign-in (ADR-0019).

Adds the allowlisted SECURITY DEFINER function ``core.accept_invitations(p_subject)``: it
activates the caller's own ``invited`` memberships (IdP subject from the verified access
token) in active schools, for invites created within the last 30 days, and returns the
accepted (tenant_id, membership_id) pairs so the API can audit each one in that school's
chain. It never touches suspended/removed memberships or other people's invitations.

Revision ID: 0007_accept_invitations
Revises: 0006_ops
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op

revision = "0007_accept_invitations"
down_revision = "0006_ops"
branch_labels = None
depends_on = None

SIGNATURE = "core.accept_invitations(text)"

FUNCTION_SQL = r"""
CREATE FUNCTION core.accept_invitations(p_subject text)
  RETURNS TABLE (tenant_id uuid, membership_id uuid, user_id uuid)
  LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    UPDATE core.memberships AS m
       SET status = 'active', updated_at = pg_catalog.now(), version = m.version + 1
      FROM core.users AS u, core.tenants AS t
     WHERE u.idp_subject = p_subject
       AND u.status = 'active'
       AND m.user_id = u.id
       AND m.status = 'invited'
       AND m.created_at > pg_catalog.now() - interval '30 days'
       AND (m.expires_at IS NULL OR m.expires_at > pg_catalog.now())
       AND t.id = m.tenant_id
       AND t.status = 'active'
    RETURNING m.tenant_id, m.id, m.user_id
$$;
"""


def upgrade() -> None:
    # sos_definer needs to change exactly one column family on memberships.
    op.execute("GRANT UPDATE (status, updated_at, version) ON core.memberships TO sos_definer")
    op.execute("GRANT SELECT ON core.users, core.tenants TO sos_definer")
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(FUNCTION_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
    op.execute("REVOKE UPDATE (status, updated_at, version) ON core.memberships FROM sos_definer")
