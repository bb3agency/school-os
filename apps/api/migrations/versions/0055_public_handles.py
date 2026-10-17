"""R2-0 foundation: public handles and their resolver, parent language (ADR-0047, ADR-0046,
ADR-0044; FR-PUB-002, FR-PUB-005..007, FR-LANG-001, FR-LANG-003, FR-LANG-006, SEC-037, SEC-039).

``core.public_handles`` (tenant-owned, docs/05 §15.5): one row per public key that leads to one
school without a signed-in member: a parent link token (``parent_notice``, ``parent_receipt``,
``parent_dues``, ``parent_optin``), a certificate verification code (``cert_verify``), a
Razorpay webhook route (``razorpay_route``) or a WhatsApp Business account id (``meta_waba``).
Only the SHA-256 of the key is stored (``key_hash``, 32 bytes); ``UNIQUE (kind, key_hash)`` is
global, so a lookup by kind and hash finds at most one row in any school. ``tenant_id`` first,
``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``, the guardian as a
composite FK (``ON DELETE SET NULL (guardian_id)``), ``offboarding_purge`` for ``sos_purger``
(ADR-0029). The app role may SELECT and INSERT, UPDATE only the revocation and view columns, and
DELETE only rows that expired more than 30 days ago (restrictive policy ``purge_expired_only``:
the daily purge, FR-PUB-006); a trigger refuses un-revoking and rewinding the view counters.
``sos_readonly`` gets nothing (key hashes never leave, docs/05 §15.6).

``core.resolve_public_handle(p_kind, p_key_hash)``: the one new allowlisted ``SECURITY
DEFINER`` function (ADR-0047 decision 2, amending ADR-0013). Owned by ``sos_definer``
(NOLOGIN, NOBYPASSRLS), ``search_path`` pinned, EXECUTE for ``sos_app`` only. It returns
``(tenant_id, handle_id)`` and nothing else, only for a handle that is not revoked, not expired
and whose school is ``active`` (``parent_receipt`` and ``cert_verify`` also ``suspended``);
otherwise no row. It reads ``core.public_handles`` (new ``definer_access`` policy, SELECT for
``sos_definer``) and ``core.tenants`` (already has both). Callers then open the school's
``tenant_session`` and re-read the handle under RLS.

Parent language (ADR-0044): ``sis.guardians.preferred_language`` (``en``, ``te`` or NULL = the
school's ``parent_language``, which lives in ``core.tenants.settings`` and needs no DDL);
``sis.apaar_consent_settings.form_language`` becomes nullable without a default (NULL follows the
school's parent language; existing explicit values are kept, decision 7).

Schemas ``fin`` and ``msg`` (D14) are created by ``infra/db/bootstrap.sql`` (``make migrate``
re-runs it); this migration refuses to run without them, so a database that missed the bootstrap
fails here with a clear message rather than in a later wave.

Expand-only (invariant 12). Downgrade drops the function, the table (lossy: open links stop
working, which is the point of a rollback) and the guardian column (lossy: per-guardian language
choices), and gives NULL consent-form languages back the school's parent language (or ``en``)
before restoring NOT NULL and the default. The school setting key is left in
``core.tenants.settings``; older code ignores unknown keys.

Revision ID: 0055_public_handles
Revises: 0054_r1_finish
Create Date: 2026-10-17
"""

from __future__ import annotations

from alembic import op

revision = "0055_public_handles"
down_revision = "0054_r1_finish"
branch_labels = None
depends_on = None

SIGNATURE = "core.resolve_public_handle(text,bytea)"

SCHEMAS_PRESENT = r"""
DO $$
BEGIN
  IF to_regnamespace('fin') IS NULL OR to_regnamespace('msg') IS NULL THEN
    RAISE EXCEPTION 'schemas fin and msg are missing: run infra/db/bootstrap.sql first '
      '(make migrate / make db-bootstrap; the dedicated tools profile db-bootstrap)';
  END IF;
END
$$;
"""

TABLE_SQL = r"""
CREATE TABLE core.public_handles (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  kind             text NOT NULL,
  key_hash         bytea NOT NULL,
  target_type      text NOT NULL,
  target_id        uuid NOT NULL,
  guardian_id      uuid,
  expires_at       timestamptz,
  revoked_at       timestamptz,
  revoked_reason   text,
  created_by       uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  first_viewed_at  timestamptz,
  last_viewed_at   timestamptz,
  view_count       int NOT NULL DEFAULT 0,
  CONSTRAINT public_handles_tenant_id_id_key UNIQUE (tenant_id, id),
  -- One row per key across all schools: the resolver's lookup (ADR-0047 decision 1).
  CONSTRAINT public_handles_kind_key_hash_key UNIQUE (kind, key_hash),
  CONSTRAINT public_handles_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT public_handles_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id) ON DELETE SET NULL (guardian_id),
  CONSTRAINT public_handles_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT public_handles_kind_check CHECK (kind IN ('cert_verify','parent_notice',
    'parent_receipt','parent_dues','parent_optin','razorpay_route','meta_waba')),
  CONSTRAINT public_handles_key_hash_length CHECK (octet_length(key_hash) = 32),
  CONSTRAINT public_handles_target_type_format CHECK (target_type ~ '^[a-z][a-z_]{1,39}$'),
  -- Parent links always expire (ADR-0046 decision 4); provider routes and certificate codes
  -- never do (ADR-0047 decision 1).
  CONSTRAINT public_handles_expiry_by_kind CHECK (
    (kind LIKE 'parent\_%') = (expires_at IS NOT NULL)),
  CONSTRAINT public_handles_expiry_after_creation CHECK (
    expires_at IS NULL OR expires_at > created_at),
  -- Only parent links name a guardian (one token per guardian per message, FR-PUB-005).
  CONSTRAINT public_handles_guardian_by_kind CHECK (
    guardian_id IS NULL OR kind LIKE 'parent\_%'),
  CONSTRAINT public_handles_revocation CHECK (
    (revoked_at IS NULL) = (revoked_reason IS NULL)),
  CONSTRAINT public_handles_revoked_reason_format CHECK (
    revoked_reason IS NULL OR revoked_reason ~ '^[a-z][a-z_]{1,39}$'),
  CONSTRAINT public_handles_views CHECK (view_count >= 0 AND
    (first_viewed_at IS NULL) = (view_count = 0) AND
    (last_viewed_at IS NULL) = (view_count = 0))
);
CREATE INDEX public_handles_target_idx
  ON core.public_handles (tenant_id, target_type, target_id);
CREATE INDEX public_handles_guardian_open_idx ON core.public_handles (tenant_id, guardian_id)
  WHERE guardian_id IS NOT NULL AND revoked_at IS NULL;
CREATE INDEX public_handles_expires_idx ON core.public_handles (tenant_id, expires_at)
  WHERE expires_at IS NOT NULL;

-- A revocation is final and view counters only move forward (defence in depth next to the
-- column grants; the guardian FK's SET NULL action passes).
CREATE FUNCTION core.tg_public_handles_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.revoked_at IS NOT NULL AND (NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
      OR NEW.revoked_reason IS DISTINCT FROM OLD.revoked_reason) THEN
    RAISE EXCEPTION 'a revoked public handle stays revoked' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.view_count < OLD.view_count
     OR (OLD.first_viewed_at IS NOT NULL
         AND NEW.first_viewed_at IS DISTINCT FROM OLD.first_viewed_at) THEN
    RAISE EXCEPTION 'public handle view counters only move forward'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.kind <> OLD.kind
     OR NEW.key_hash <> OLD.key_hash OR NEW.target_type <> OLD.target_type
     OR NEW.target_id <> OLD.target_id
     OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
     OR NEW.created_at <> OLD.created_at OR NEW.created_by IS DISTINCT FROM OLD.created_by
     OR (NEW.guardian_id IS DISTINCT FROM OLD.guardian_id AND NEW.guardian_id IS NOT NULL) THEN
    RAISE EXCEPTION 'public handle identity columns are fixed' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION core.tg_public_handles_guard() FROM PUBLIC;
CREATE TRIGGER public_handles_guard BEFORE UPDATE ON core.public_handles
  FOR EACH ROW EXECUTE FUNCTION core.tg_public_handles_guard();
"""

POLICIES_SQL = r"""
ALTER TABLE core.public_handles ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.public_handles FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON core.public_handles
  USING (tenant_id = core.current_tenant()) WITH CHECK (tenant_id = core.current_tenant());
-- ADR-0047: the resolver (owned by sos_definer) reads handles of every school.
CREATE POLICY definer_access ON core.public_handles AS PERMISSIVE FOR ALL TO PUBLIC
  USING (current_user = 'sos_definer') WITH CHECK (current_user = 'sos_definer');
-- ADR-0029: the offboarding purge, only while the school is offboarding.
CREATE POLICY offboarding_purge ON core.public_handles AS RESTRICTIVE FOR ALL TO sos_purger
  USING (core.tenant_purge_allowed());
-- FR-PUB-006: the app deletes only handles that expired more than 30 days ago (the daily purge;
-- app/public/config.yaml may keep them longer, never shorter).
CREATE POLICY purge_expired_only ON core.public_handles AS RESTRICTIVE FOR DELETE TO sos_app
  USING (expires_at IS NOT NULL AND expires_at < now() - interval '30 days');
"""

GRANTS_SQL = r"""
REVOKE ALL ON core.public_handles FROM PUBLIC;
REVOKE UPDATE, TRUNCATE, REFERENCES, TRIGGER ON core.public_handles FROM sos_app;
GRANT SELECT, INSERT, DELETE ON core.public_handles TO sos_app;
GRANT UPDATE (revoked_at, revoked_reason, first_viewed_at, last_viewed_at, view_count)
  ON core.public_handles TO sos_app;
REVOKE ALL ON core.public_handles FROM sos_readonly;
GRANT SELECT ON core.public_handles TO sos_definer;
GRANT SELECT, DELETE ON core.public_handles TO sos_purger;
"""

FUNCTION_SQL = r"""
CREATE FUNCTION core.resolve_public_handle(p_kind text, p_key_hash bytea)
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
         OR (t.status = 'suspended' AND h.kind IN ('parent_receipt', 'cert_verify')))
$$;
"""

LANGUAGE_SQL = r"""
ALTER TABLE sis.guardians ADD COLUMN preferred_language text;
ALTER TABLE sis.guardians ADD CONSTRAINT guardians_preferred_language_check
  CHECK (preferred_language IS NULL OR preferred_language IN ('en','te'));
ALTER TABLE sis.apaar_consent_settings ALTER COLUMN form_language DROP NOT NULL;
ALTER TABLE sis.apaar_consent_settings ALTER COLUMN form_language DROP DEFAULT;
"""

# Downgrade: the owner lifts FORCE on the two tables for the one UPDATE (rows of every school);
# FORCE is restored in the same transaction.
UNFORCE_SETTINGS = "ALTER TABLE sis.apaar_consent_settings NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner restores NOT NULL languages of every school on downgrade; FORCE is restored in the same transaction
UNFORCE_TENANTS = "ALTER TABLE core.tenants NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner reads every school's parent_language on downgrade; FORCE is restored in the same transaction
REFORCE_SETTINGS = "ALTER TABLE sis.apaar_consent_settings FORCE ROW LEVEL SECURITY"
REFORCE_TENANTS = "ALTER TABLE core.tenants FORCE ROW LEVEL SECURITY"
FILL_FORM_LANGUAGE = r"""
UPDATE sis.apaar_consent_settings AS s
   SET form_language = CASE WHEN t.settings ->> 'parent_language' = 'te' THEN 'te' ELSE 'en' END
  FROM core.tenants AS t
 WHERE t.id = s.tenant_id AND s.form_language IS NULL
"""
RESTORE_FORM_LANGUAGE = r"""
ALTER TABLE sis.apaar_consent_settings ALTER COLUMN form_language SET DEFAULT 'en';
ALTER TABLE sis.apaar_consent_settings ALTER COLUMN form_language SET NOT NULL;
"""


def upgrade() -> None:
    op.execute(SCHEMAS_PRESENT)
    op.execute(TABLE_SQL)
    op.execute(POLICIES_SQL)
    op.execute(GRANTS_SQL)
    # The definer function is created AS sos_definer; the CREATE grant on schema core exists
    # only inside this transaction (as in 0003 and 0028).
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(FUNCTION_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")
    op.execute(LANGUAGE_SQL)


def downgrade() -> None:
    op.execute(UNFORCE_SETTINGS)
    op.execute(UNFORCE_TENANTS)
    op.execute(FILL_FORM_LANGUAGE)
    op.execute(REFORCE_TENANTS)
    op.execute(REFORCE_SETTINGS)
    op.execute(RESTORE_FORM_LANGUAGE)
    op.execute("ALTER TABLE sis.guardians DROP CONSTRAINT guardians_preferred_language_check")
    op.execute("ALTER TABLE sis.guardians DROP COLUMN preferred_language")
    # sos_owner owns schema core, so it may drop the sos_definer-owned function.
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
    op.execute("DROP TABLE IF EXISTS core.public_handles")
    op.execute("DROP FUNCTION IF EXISTS core.tg_public_handles_guard()")
