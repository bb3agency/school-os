"""Invitations to an existing account need that person's consent (audit DL-09, ADR-0023 amendment
2026-10-04; ADR-0019, US-102, SEC-001).

Before this revision ``core.accept_invitations(p_subject)`` activated *every* pending invitation of
the subject at sign-in. A school that knew another school's user's subject could invite them and be
attached on their next sign-in without a consent step, and it took no issuer (ADR-0023).

This revision (backward compatible, expand only):

- ``core.accept_invitations(p_subject, p_issuer)`` (new): the sign-in acceptance now activates an
  invitation only when it is the person's **only** membership anywhere (a brand-new account made by
  that invitation, e.g. a new school's owner), and only for the identity ``(p_issuer, p_subject)``.
- ``core.accept_invitations(p_subject)`` (pre-0045 signature) stays for an older API image, as a
  thin wrapper calling the new function with this environment's staff issuer, so an older image
  can no longer auto-accept an invitation to an existing account either.
- ``core.pending_invitations(p_subject, p_issuer)`` (new, STABLE): the caller's own pending,
  unexpired invitations in active schools, with the school's name and the invited role keys, so the
  person can decide.
- ``core.respond_to_invitation(p_subject, p_issuer, p_membership_id, p_accept)`` (new): accepts
  (``active``) or declines (``removed``) exactly one of the caller's own pending invitations.

All three are owned by ``sos_definer`` (NOBYPASSRLS), ``search_path`` pinned, EXECUTE for
``sos_app`` only, and touch only tables that already carry the ``definer_access`` policy
(``core.memberships``, ``core.users``, ``core.tenants``, ``core.membership_roles``,
``core.roles``) with the grants ``sos_definer`` already holds (0007, 0027). No new grant, no new
policy. The allowlist in ``tests/security/rls_allowlist.yaml`` pins them.

Downgrade drops the new functions and restores the 0007 body of ``core.accept_invitations(text)``.

Revision ID: 0045_invitation_consent
Revises: 0044_purge_flag_role
Create Date: 2026-10-05
"""

from __future__ import annotations

import re

from alembic import op

from app.core.config import get_settings

revision = "0045_invitation_consent"
down_revision = "0044_purge_flag_role"
branch_labels = None
depends_on = None

_ISSUER = re.compile(r"^[A-Za-z0-9:/._~%+@-]{1,255}$")

NEW_SIGNATURES = (
    "core.accept_invitations(text, text)",
    "core.pending_invitations(text, text)",
    "core.respond_to_invitation(text, text, uuid, boolean)",
)
WRAPPER_SIGNATURE = "core.accept_invitations(text)"

# "Invitation m of user u can be answered now": the 0007 conditions.
_OPEN_INVITE = """u.idp_subject = p_subject
       AND u.idp_issuer = p_issuer
       AND u.status = 'active'
       AND m.user_id = u.id
       AND m.status = 'invited'
       AND m.created_at > pg_catalog.now() - interval '30 days'
       AND (m.expires_at IS NULL OR m.expires_at > pg_catalog.now())
       AND t.id = m.tenant_id
       AND t.status = 'active'"""

NEW_FUNCTIONS_SQL = """
CREATE FUNCTION core.accept_invitations(p_subject text, p_issuer text)
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
COMMENT ON FUNCTION core.accept_invitations(text, text) IS
  'Sign-in acceptance of a brand-new account''s only invitation (ADR-0019, ADR-0023, DL-09)';

CREATE FUNCTION core.pending_invitations(p_subject text, p_issuer text)
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
COMMENT ON FUNCTION core.pending_invitations(text, text) IS
  'The caller''s own open invitations, to accept or decline (ADR-0023 amendment, DL-09)';

CREATE FUNCTION core.respond_to_invitation(
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
COMMENT ON FUNCTION core.respond_to_invitation(text, text, uuid, boolean) IS
  'Accept or decline one of the caller''s own open invitations (ADR-0023 amendment, DL-09)';
""".replace("__OPEN_INVITE__", _OPEN_INVITE)

WRAPPER_SQL = """
CREATE OR REPLACE FUNCTION core.accept_invitations(p_subject text)
  RETURNS TABLE (tenant_id uuid, membership_id uuid, user_id uuid)
  LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT a.tenant_id, a.membership_id, a.user_id
      FROM core.accept_invitations(p_subject, '__STAFF_ISSUER__') AS a
$$;
COMMENT ON FUNCTION core.accept_invitations(text) IS
  'Expand-phase wrapper (migration 0045, DL-09): staff issuer; a later release drops it';
"""

# The 0007 body, restored on downgrade.
OLD_WRAPPER_SQL = """
CREATE OR REPLACE FUNCTION core.accept_invitations(p_subject text)
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
COMMENT ON FUNCTION core.accept_invitations(text) IS NULL;
"""


def _staff_issuer() -> str:
    value = get_settings().oidc_issuer.strip()
    if not _ISSUER.fullmatch(value):
        raise RuntimeError("SOS_OIDC_ISSUER is not a usable issuer URL for migration 0045")
    return value


def upgrade() -> None:
    staff = _staff_issuer()
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(NEW_FUNCTIONS_SQL)
    # The issuer passed _ISSUER (URL characters only, no quote), so it is a safe literal.
    op.execute(WRAPPER_SQL.replace("__STAFF_ISSUER__", staff))
    for signature in (*NEW_SIGNATURES, WRAPPER_SIGNATURE):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def downgrade() -> None:
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(OLD_WRAPPER_SQL)
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")
    for signature in NEW_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
