"""An invited owner or principal of a suspended school can answer the invitation (lead decision
D67, ADR-0056; ADR-0013, ADR-0019, ADR-0023 amendment; BR-08, FR-PLT-004, FR-IAM-013).

Before this revision the three invitation functions of 0045 (``core.accept_invitations(text,
text)``, ``core.pending_invitations(text, text)``, ``core.respond_to_invitation(text, text, uuid,
boolean)``) answered an invitation only while its school was ``active``. An owner invited into a
suspended school (D65 lets operators re-send that invitation) could not accept, so could not reach
Plan & billing or the data export that the suspended-school allowlist keeps for the owner and
principal (docs/16 §5.5).

This revision replaces only the school-status condition, in all three functions alike: the school
is ``active``, **or** it is ``suspended`` and the invited membership holds a role whose key is
``owner`` or ``principal`` (the same test as ``SUSPENDED_ROLES`` in
``app/authz/resolver.py``). Every other invitation in a suspended school stays unanswered; schools
that are ``provisioning``, ``offboarding`` or ``deleted`` are unchanged (never answered).

``CREATE OR REPLACE`` keeps each signature, owner (``sos_definer``, NOBYPASSRLS), ``SECURITY
DEFINER``, pinned ``search_path`` and EXECUTE grant (``sos_app`` only; re-applied below, as 0045
did). The new condition reads ``core.membership_roles`` and ``core.roles``, which
``core.pending_invitations`` already reads under their ``definer_access`` policy: no new function,
no new grant, no new policy. The 0045 wrapper ``core.accept_invitations(text)`` calls the
two-argument function and is not touched.

Downgrade restores the 0045 bodies (``active`` schools only).

Revision ID: 0078_accept_invite_suspended
Revises: 0075_breach_retention
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0078_accept_invite_suspended"
down_revision = "0075_breach_retention"
branch_labels = None
depends_on = None

SIGNATURES = (
    "core.accept_invitations(text, text)",
    "core.pending_invitations(text, text)",
    "core.respond_to_invitation(text, text, uuid, boolean)",
)

# 0045's status condition: only active schools.
_ACTIVE_ONLY = "t.status = 'active'"

# D67 (ADR-0056): or a suspended school, for an invited owner or principal (the role keys of
# SUSPENDED_ROLES in app/authz/resolver.py).
_ACTIVE_OR_SUSPENDED_OWNER = """(t.status = 'active'
            OR (t.status = 'suspended'
                AND EXISTS (SELECT 1
                              FROM core.membership_roles AS mr
                              JOIN core.roles AS r
                                ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id
                             WHERE mr.tenant_id = m.tenant_id
                               AND mr.membership_id = m.id
                               AND r.key IN ('owner', 'principal'))))"""

# "Invitation m of user u can be answered now": the 0045 conditions, the school status last.
_OPEN_INVITE = """u.idp_subject = p_subject
       AND u.idp_issuer = p_issuer
       AND u.status = 'active'
       AND m.user_id = u.id
       AND m.status = 'invited'
       AND m.created_at > pg_catalog.now() - interval '30 days'
       AND (m.expires_at IS NULL OR m.expires_at > pg_catalog.now())
       AND t.id = m.tenant_id
       AND __TENANT_STATUS__"""

# The 0045 function bodies with CREATE OR REPLACE (signatures, owner, search_path unchanged).
FUNCTIONS_SQL = """
CREATE OR REPLACE FUNCTION core.accept_invitations(p_subject text, p_issuer text)
  RETURNS TABLE (tenant_id uuid, membership_id uuid, user_id uuid)
  LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    UPDATE core.memberships AS m
       SET status = 'active', updated_at = pg_catalog.now(), version = m.version + 1
      FROM core.users AS u, core.tenants AS t
     WHERE __OPEN_INVITE__
       AND NOT EXISTS (SELECT 1 FROM core.memberships AS o
                       WHERE o.user_id = u.id AND o.id <> m.id)
    RETURNING m.tenant_id, m.id, m.user_id
$$;

CREATE OR REPLACE FUNCTION core.pending_invitations(p_subject text, p_issuer text)
  RETURNS TABLE (tenant_id uuid, membership_id uuid, school_name text, role_keys text[],
                 invited_at timestamptz, expires_at timestamptz)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT m.tenant_id, m.id, t.name,
           COALESCE((SELECT pg_catalog.array_agg(r.key ORDER BY r.key)
                       FROM core.membership_roles AS mr
                       JOIN core.roles AS r ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id
                      WHERE mr.tenant_id = m.tenant_id AND mr.membership_id = m.id),
                    ARRAY[]::text[]),
           m.created_at,
           LEAST(COALESCE(m.expires_at, 'infinity'::timestamptz),
                 m.created_at + interval '30 days')
      FROM core.memberships AS m, core.users AS u, core.tenants AS t
     WHERE __OPEN_INVITE__
     ORDER BY m.created_at, m.id
$$;

CREATE OR REPLACE FUNCTION core.respond_to_invitation(
    p_subject text, p_issuer text, p_membership_id uuid, p_accept boolean)
  RETURNS TABLE (tenant_id uuid, membership_id uuid, user_id uuid)
  LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    UPDATE core.memberships AS m
       SET status = CASE WHEN p_accept THEN 'active' ELSE 'removed' END,
           updated_at = pg_catalog.now(), version = m.version + 1
      FROM core.users AS u, core.tenants AS t
     WHERE m.id = p_membership_id
       AND p_accept IS NOT NULL
       AND __OPEN_INVITE__
    RETURNING m.tenant_id, m.id, m.user_id
$$;
""".replace("__OPEN_INVITE__", _OPEN_INVITE)

NEW_SQL = FUNCTIONS_SQL.replace("__TENANT_STATUS__", _ACTIVE_OR_SUSPENDED_OWNER)
OLD_SQL = FUNCTIONS_SQL.replace("__TENANT_STATUS__", _ACTIVE_ONLY)


def _replace(sql: str) -> None:
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(sql)
    for signature in SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def upgrade() -> None:
    _replace(NEW_SQL)


def downgrade() -> None:
    _replace(OLD_SQL)
