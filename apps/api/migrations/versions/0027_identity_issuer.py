"""Issuer-qualified identities in ``core.users`` (ADR-0023 option C, EXPAND step; SEC-021, T1/T2).

A person's sign-in is identified by ``(idp_issuer, idp_subject)``: the staff pool and the
operator pool are separate namespaces, so a staff account whose ``sub`` equals an operator's can
never receive that operator's break-glass (``platform_support``) membership.

Expand (this revision, backward compatible):

- ``core.users.idp_issuer text`` (nullable) with DEFAULT = the staff issuer
  (``SOS_OIDC_ISSUER`` of the environment running the migration). Adding the column with a
  constant default fills every existing row with the staff issuer (the backfill) without a table
  rewrite; rows written by code that does not know the column yet (``core.create_owner_invite``,
  the synthetic seeder, an older API image during a rolling deploy) get it as well.
- Existing break-glass identities (users whose every membership holds only ``platform_support``)
  are moved to the operator issuer (``SOS_SUPPORT_OIDC_ISSUER``, default
  ``SOS_PLATFORM_OIDC_ISSUER``). They were created from ``platform.operators.idp_subject``.
- ``UNIQUE (idp_issuer, idp_subject)``. The old ``UNIQUE (idp_subject)`` stays until the contract
  step, so during this release an operator whose ``sub`` equals a staff ``sub`` cannot get a
  support identity at all (fail closed).
- The three allowlisted definer functions named by ADR-0023 take the issuer (ADR-0013 allowlist
  changes, decided by ADR-0023; no new definer function, no new ``definer_access`` policy):

  * ``core.resolve_login(p_subject, p_issuer DEFAULT NULL, p_support_only DEFAULT false)``:
    with an issuer, only that issuer's identity. ``p_support_only = true`` (support principals)
    returns only memberships that hold exactly the system role ``platform_support`` and carry an
    ``expires_at`` in the future; ``false`` (staff) never returns a membership holding
    ``platform_support``. Without an issuer (an older API image) it behaves as before.
  * ``core.find_user_id_by_subject(p_subject, p_issuer DEFAULT NULL)``.
  * ``core.create_user_for_invite(p_subject, p_display_name, p_email, p_language, p_issuer)``
    (issuer required): creates or finds exactly that identity; if the subject is taken by
    ANOTHER issuer it fails with ``unique_violation`` (never returns the other identity).
  * Backward compatibility (CLAUDE.md §6.12): the pre-0027 four-argument
    ``core.create_user_for_invite(text, text, citext, text)`` stays, as a thin SQL wrapper owned
    by ``sos_definer`` (EXECUTE ``sos_app`` only, as before) that calls the new function with the
    staff issuer of this environment, so an older API image keeps inviting staff during a
    rolling deploy and after a rollback. The one-argument ``resolve_login`` and
    ``find_user_id_by_subject`` calls keep working through the parameter defaults. The new
    five-argument function has no default, so the four-argument call is never ambiguous.

  ``sos_definer`` gains ``SELECT`` on ``core.membership_roles`` (the table already carries the
  ``definer_access`` policy) for the ``platform_support`` filter.

Contract (a later release, after every API image passes the issuer): DROP the four-argument
``core.create_user_for_invite`` wrapper, drop the parameter defaults, backfill any remaining
NULL, ``SET NOT NULL``, drop ``UNIQUE (idp_subject)`` and give ``core.create_owner_invite`` the
issuer. Not part of this revision.

Downgrade restores the 0003 functions, revokes the extra grant and drops the constraints and the
column (the issuer of each identity is lost; subjects stay unique, so nothing else changes).

Revision ID: 0027_identity_issuer
Revises: 0023_api_gaps
Create Date: 2026-09-27
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0027_identity_issuer"
down_revision = "0023_api_gaps"
branch_labels = None
depends_on = None

# A conservative URL character set: the value is written into DDL as a literal.
_ISSUER = re.compile(r"^[A-Za-z0-9:/._~%+@-]{1,255}$")
_DEV_HOSTS = frozenset({"localhost", "oidc", "mock-oauth2-server", "0.0.0.0", "::1"})  # noqa: S104

NEW_SIGNATURES = (
    "core.resolve_login(text, text, boolean)",
    "core.find_user_id_by_subject(text, text)",
    "core.create_user_for_invite(text, text, public.citext, text, text)",
    # Expand-phase wrapper with the pre-0027 signature; the contract migration drops it.
    "core.create_user_for_invite(text, text, public.citext, text)",
)
OLD_SIGNATURES = (
    "core.resolve_login(text)",
    "core.find_user_id_by_subject(text)",
    "core.create_user_for_invite(text, text, public.citext, text)",
)

# UUIDv7 in SQL (docs/05 §1), as in 0003/0005.
_UUID7 = """pg_catalog.encode(pg_catalog.set_bit(pg_catalog.set_bit(
            overlay(pg_catalog.uuid_send(pg_catalog.gen_random_uuid())
                    PLACING substring(pg_catalog.int8send(
                      (extract(epoch FROM pg_catalog.clock_timestamp()) * 1000)::bigint) FROM 3)
                    FROM 1 FOR 6),
            52, 1), 53, 1), 'hex')::uuid"""

# "Membership m holds the platform_support role" (any role row with that key).
_SUPPORT_ROLE = """EXISTS (
        SELECT 1 FROM core.membership_roles AS mr
        JOIN core.roles AS r ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id
        WHERE mr.tenant_id = m.tenant_id AND mr.membership_id = m.id
          AND r.key = 'platform_support')"""
_SYSTEM_SUPPORT_ROLE = """EXISTS (
        SELECT 1 FROM core.membership_roles AS mr
        JOIN core.roles AS r ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id
        WHERE mr.tenant_id = m.tenant_id AND mr.membership_id = m.id
          AND r.key = 'platform_support' AND r.is_system)"""
_OTHER_ROLE = """EXISTS (
        SELECT 1 FROM core.membership_roles AS mr
        JOIN core.roles AS r ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id
        WHERE mr.tenant_id = m.tenant_id AND mr.membership_id = m.id
          AND r.key <> 'platform_support')"""

NEW_FUNCTIONS_SQL = (
    r"""
CREATE FUNCTION core.resolve_login(
    p_subject text, p_issuer text DEFAULT NULL, p_support_only boolean DEFAULT false)
  RETURNS TABLE (user_id uuid, tenant_id uuid, membership_id uuid, tenant_status text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT u.id, m.tenant_id, m.id, t.status
    FROM core.users AS u
    JOIN core.memberships AS m ON m.user_id = u.id
    JOIN core.tenants AS t ON t.id = m.tenant_id
    WHERE u.idp_subject = p_subject
      AND (p_issuer IS NULL OR u.idp_issuer = p_issuer)
      AND u.status = 'active'
      AND m.status = 'active'
      AND (m.expires_at IS NULL OR m.expires_at > pg_catalog.now())
      AND (p_issuer IS NULL
           OR (COALESCE(p_support_only, false)
               AND m.expires_at IS NOT NULL
               AND __SYSTEM_SUPPORT_ROLE__
               AND NOT __OTHER_ROLE__)
           OR (NOT COALESCE(p_support_only, false) AND NOT __SUPPORT_ROLE__))
    ORDER BY m.created_at, m.id
$$;

CREATE FUNCTION core.find_user_id_by_subject(p_subject text, p_issuer text DEFAULT NULL)
  RETURNS uuid
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT u.id FROM core.users AS u
    WHERE u.idp_subject = p_subject
      AND (p_issuer IS NULL OR u.idp_issuer = p_issuer)
$$;

CREATE FUNCTION core.create_user_for_invite(
    p_subject text, p_display_name text, p_email public.citext, p_language text,
    p_issuer text)
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v_tenant  uuid := core.current_tenant();
  v_inviter uuid := core.current_user_id();
  v_id      uuid;
BEGIN
  IF v_tenant IS NULL OR v_inviter IS NULL THEN
    RAISE EXCEPTION 'tenant and inviter context are required'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (
      SELECT 1
      FROM core.memberships AS m
      JOIN core.users AS u ON u.id = m.user_id
      JOIN core.tenants AS t ON t.id = m.tenant_id
      WHERE m.tenant_id = v_tenant
        AND m.user_id = v_inviter
        AND m.status = 'active'
        AND (m.expires_at IS NULL OR m.expires_at > pg_catalog.now())
        AND u.status = 'active'
        AND t.status IN ('provisioning', 'active')) THEN
    RAISE EXCEPTION 'inviter has no active membership in the current school'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_issuer IS NULL THEN
    RAISE EXCEPTION 'an identity issuer is required' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO core.users AS u (id, idp_issuer, idp_subject, display_name, email,
                               preferred_language, status)
  VALUES (__UUID7__, p_issuer, p_subject, p_display_name, p_email, COALESCE(p_language, 'en'),
          'active')
  ON CONFLICT DO NOTHING
  RETURNING u.id INTO v_id;
  IF v_id IS NULL THEN
    SELECT u.id INTO v_id FROM core.users AS u
    WHERE u.idp_issuer = p_issuer AND u.idp_subject = p_subject;
  END IF;
  IF v_id IS NULL THEN
    -- The subject belongs to an identity of another issuer: never hand that identity out.
    RAISE EXCEPTION 'this sign-in belongs to another identity provider'
      USING ERRCODE = 'unique_violation', CONSTRAINT = 'users_idp_subject_key';
  END IF;
  RETURN v_id;
END
$$;

-- Expand phase only (CLAUDE.md §6.12): the pre-0027 signature for older API images. It calls
-- the new function with this environment's staff issuer; the contract migration drops it.
CREATE FUNCTION core.create_user_for_invite(
    p_subject text, p_display_name text, p_email public.citext, p_language text)
  RETURNS uuid
  LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT core.create_user_for_invite(p_subject, p_display_name, p_email, p_language,
                                       '__STAFF_ISSUER__')
$$;
COMMENT ON FUNCTION core.create_user_for_invite(text, text, public.citext, text) IS
  'Expand-phase wrapper (ADR-0023, migration 0027): dropped by the contract migration';
""".replace("__UUID7__", _UUID7)
    .replace("__SYSTEM_SUPPORT_ROLE__", _SYSTEM_SUPPORT_ROLE)
    .replace("__OTHER_ROLE__", _OTHER_ROLE)
    .replace("__SUPPORT_ROLE__", _SUPPORT_ROLE)
)

# The 0003 definitions, restored on downgrade.
OLD_FUNCTIONS_SQL = r"""
CREATE FUNCTION core.resolve_login(p_subject text)
  RETURNS TABLE (user_id uuid, tenant_id uuid, membership_id uuid, tenant_status text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT u.id, m.tenant_id, m.id, t.status
    FROM core.users AS u
    JOIN core.memberships AS m ON m.user_id = u.id
    JOIN core.tenants AS t ON t.id = m.tenant_id
    WHERE u.idp_subject = p_subject
      AND u.status = 'active'
      AND m.status = 'active'
      AND (m.expires_at IS NULL OR m.expires_at > pg_catalog.now())
    ORDER BY m.created_at, m.id
$$;

CREATE FUNCTION core.find_user_id_by_subject(p_subject text) RETURNS uuid
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT u.id FROM core.users AS u WHERE u.idp_subject = p_subject
$$;

CREATE FUNCTION core.create_user_for_invite(
    p_subject text, p_display_name text, p_email public.citext, p_language text)
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v_tenant  uuid := core.current_tenant();
  v_inviter uuid := core.current_user_id();
  v_id      uuid;
BEGIN
  IF v_tenant IS NULL OR v_inviter IS NULL THEN
    RAISE EXCEPTION 'tenant and inviter context are required'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (
      SELECT 1
      FROM core.memberships AS m
      JOIN core.users AS u ON u.id = m.user_id
      JOIN core.tenants AS t ON t.id = m.tenant_id
      WHERE m.tenant_id = v_tenant
        AND m.user_id = v_inviter
        AND m.status = 'active'
        AND (m.expires_at IS NULL OR m.expires_at > pg_catalog.now())
        AND u.status = 'active'
        AND t.status IN ('provisioning', 'active')) THEN
    RAISE EXCEPTION 'inviter has no active membership in the current school'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO core.users AS u (id, idp_subject, display_name, email, preferred_language, status)
  VALUES (__UUID7__, p_subject, p_display_name, p_email, COALESCE(p_language, 'en'), 'active')
  ON CONFLICT (idp_subject) DO NOTHING
  RETURNING u.id INTO v_id;
  IF v_id IS NULL THEN
    SELECT u.id INTO v_id FROM core.users AS u WHERE u.idp_subject = p_subject;
  END IF;
  RETURN v_id;
END
$$;
""".replace("__UUID7__", _UUID7)

# Break-glass identities created before this revision: every membership holds only
# platform_support. They came from platform.operators.idp_subject (operator pool).
RECLASSIFY_SQL = """
UPDATE core.users AS u SET idp_issuer = :operator_issuer
WHERE EXISTS (SELECT 1 FROM core.memberships AS m WHERE m.user_id = u.id)
  AND NOT EXISTS (
    SELECT 1 FROM core.memberships AS m
    WHERE m.user_id = u.id AND (NOT __SUPPORT_ROLE__ OR __OTHER_ROLE__))
""".replace("__SUPPORT_ROLE__", _SUPPORT_ROLE).replace("__OTHER_ROLE__", _OTHER_ROLE)


def _checked(issuer: str, name: str) -> str:
    value = issuer.strip()
    if not _ISSUER.fullmatch(value):
        raise RuntimeError(f"{name} is not a usable issuer URL for migration 0027")
    return value


def _staff_issuer() -> str:
    settings = get_settings()
    value = _checked(settings.oidc_issuer, "SOS_OIDC_ISSUER")
    host = (value.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]).lower()
    if settings.is_production_like and (
        not value.startswith("https://")
        or host in _DEV_HOSTS
        or host.startswith("127.")
        or host.endswith(".localhost")
    ):
        # Every existing identity is stamped with this issuer: refuse a dev default in prod.
        raise RuntimeError("SOS_OIDC_ISSUER must be the real staff pool issuer in staging/prod")
    return value


def upgrade() -> None:
    settings = get_settings()
    staff = _staff_issuer()
    operator = _checked(settings.resolved_support_issuer, "SOS_SUPPORT_OIDC_ISSUER")
    op.execute(f"ALTER TABLE core.users ADD COLUMN idp_issuer text DEFAULT '{staff}'")
    op.execute(
        "ALTER TABLE core.users ADD CONSTRAINT users_idp_issuer_length "
        "CHECK (idp_issuer IS NULL OR char_length(idp_issuer) BETWEEN 1 AND 255)"
    )
    op.execute(
        "ALTER TABLE core.users ADD CONSTRAINT users_idp_issuer_subject_key "
        "UNIQUE (idp_issuer, idp_subject)"
    )
    op.execute(
        "COMMENT ON COLUMN core.users.idp_issuer IS "
        "'OIDC issuer of idp_subject (ADR-0023); identity = (idp_issuer, idp_subject)'"
    )
    # resolve_login's platform_support filter reads membership roles (definer_access exists).
    op.execute("GRANT SELECT ON core.membership_roles TO sos_definer")

    if operator.rstrip("/") != staff.rstrip("/"):
        # Through the definer_access policy (FORCE RLS applies to the table owner too); the
        # UPDATE grant exists only inside this transaction.
        op.execute("GRANT UPDATE (idp_issuer) ON core.users TO sos_definer")
        op.execute("SET ROLE sos_definer")
        op.execute(sa.text(RECLASSIFY_SQL).bindparams(operator_issuer=operator))
        op.execute("SET ROLE sos_owner")
        op.execute("REVOKE UPDATE (idp_issuer) ON core.users FROM sos_definer")

    for signature in OLD_SIGNATURES:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    # The staff issuer passed _checked (URL characters only, no quote), so it is a safe literal.
    op.execute(NEW_FUNCTIONS_SQL.replace("__STAFF_ISSUER__", staff))
    for signature in NEW_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def downgrade() -> None:
    # The four-argument wrapper first (it depends on the five-argument function).
    for signature in reversed(NEW_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(OLD_FUNCTIONS_SQL)
    for signature in OLD_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")
    op.execute("REVOKE SELECT ON core.membership_roles FROM sos_definer")
    op.execute("ALTER TABLE core.users DROP CONSTRAINT IF EXISTS users_idp_issuer_subject_key")
    op.execute("ALTER TABLE core.users DROP CONSTRAINT IF EXISTS users_idp_issuer_length")
    op.execute("ALTER TABLE core.users DROP COLUMN IF EXISTS idp_issuer")
