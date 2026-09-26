"""Core schema: tenancy, identity, authorization tables and allowlisted definer functions.

Implements docs/05 §4 as amended by ADR-0013 and the build contract:
- every tenant-owned table has ``UNIQUE (tenant_id, id)`` and every tenant->tenant FK is composite
  (FK checks bypass RLS, so a single-column FK could link rows across tenants);
- RLS ENABLE + FORCE with ``tenant_isolation`` everywhere; approved variants on ``core.tenants``
  (``own_tenant``) and ``core.users`` (``users_in_tenant``);
- ``definer_access`` only on the tables the SECURITY DEFINER functions below must reach;
- functions owned by ``sos_definer`` (NOLOGIN, NOBYPASSRLS), ``search_path`` pinned, bodies fully
  schema-qualified, EXECUTE granted per function.

Requirements: FR-TEN-001, FR-TEN-002, FR-TEN-003, FR-TEN-010, FR-IAM-010..013, SEC-001, SEC-026.

Revision ID: 0003_core_schema
Revises: 0001_baseline  (TEMPORARY: the lead relinks this to 0002_audit at merge)
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op

revision = "0003_core_schema"
# TEMPORARY: relink to "0002_audit" when merging (the audit migration is written concurrently).
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# Tables with tenant_id that get the standard tenant_isolation policy (docs/05 §3).
TENANT_TABLES = (
    "core.tenant_keys",
    "core.memberships",
    "core.roles",
    "core.role_permissions",
    "core.membership_roles",
    "core.membership_scopes",
    "core.academic_years",
    "core.classes",
    "core.sections",
)

# Tables the definer functions in this revision must reach (subset of rls_allowlist.yaml).
DEFINER_ACCESS_TABLES = (
    "core.tenants",  # resolve_login, list_tenant_ids, provision_tenant, set_tenant_status
    "core.users",  # resolve_login, find_user_id_by_subject, create_user_for_invite
    "core.memberships",  # resolve_login, create_user_for_invite, tenant_usage_summary
    "core.tenant_keys",  # set_tenant_status (provisioning -> active requires a DEK)
    "core.sections",  # tenant_usage_summary (count)
    "core.academic_years",  # tenant_usage_summary (count)
)

TABLES_SQL = r"""
CREATE TABLE core.tenants (
  id               uuid PRIMARY KEY,
  code             text NOT NULL,
  name             text NOT NULL,
  boards           text[] NOT NULL DEFAULT '{}',
  state_code       text NOT NULL DEFAULT 'AP',
  status           text NOT NULL DEFAULT 'provisioning',
  plan_tier        text NOT NULL DEFAULT 'shared',
  deployment_mode  text NOT NULL DEFAULT 'shared',
  settings         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          int NOT NULL DEFAULT 1,
  CONSTRAINT tenants_code_key UNIQUE (code),
  CONSTRAINT tenants_code_format CHECK (code ~ '^[a-z][a-z0-9-]{1,31}$'),
  CONSTRAINT tenants_name_length CHECK (char_length(name) BETWEEN 1 AND 200),
  CONSTRAINT tenants_state_code_format CHECK (state_code ~ '^[A-Z]{2}$'),
  CONSTRAINT tenants_status_check
    CHECK (status IN ('provisioning','active','suspended','offboarding','deleted')),
  CONSTRAINT tenants_plan_tier_check CHECK (plan_tier IN ('shared','dedicated')),
  CONSTRAINT tenants_deployment_mode_check CHECK (deployment_mode IN ('shared','dedicated')),
  CONSTRAINT tenants_settings_object CHECK (jsonb_typeof(settings) = 'object'),
  CONSTRAINT tenants_version_positive CHECK (version >= 1)
);

CREATE TABLE core.tenant_keys (
  tenant_id     uuid NOT NULL,
  key_version   int  NOT NULL,
  wrapped_dek   bytea NOT NULL,
  wrapped_hmac  bytea NOT NULL,
  kms_key_arn   text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  retired_at    timestamptz,
  CONSTRAINT tenant_keys_pkey PRIMARY KEY (tenant_id, key_version),
  CONSTRAINT tenant_keys_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  -- key_version is stored in 2 bytes of every ciphertext header (docs/05 §9).
  CONSTRAINT tenant_keys_version_range CHECK (key_version BETWEEN 1 AND 65535),
  CONSTRAINT tenant_keys_retired_after_created CHECK (retired_at IS NULL OR retired_at >= created_at)
);

CREATE TABLE core.users (
  id                 uuid PRIMARY KEY,
  idp_subject        text NOT NULL,
  display_name       text NOT NULL,
  email              public.citext,
  phone_ciphertext   bytea,
  preferred_language text NOT NULL DEFAULT 'en',
  status             text NOT NULL DEFAULT 'active',
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  last_login_at      timestamptz,
  version            int NOT NULL DEFAULT 1,
  CONSTRAINT users_idp_subject_key UNIQUE (idp_subject),
  CONSTRAINT users_idp_subject_length CHECK (char_length(idp_subject) BETWEEN 1 AND 255),
  CONSTRAINT users_display_name_length CHECK (char_length(display_name) BETWEEN 1 AND 200),
  CONSTRAINT users_preferred_language_check CHECK (preferred_language IN ('en','te')),
  CONSTRAINT users_status_check CHECK (status IN ('active','disabled')),
  CONSTRAINT users_version_positive CHECK (version >= 1)
);

CREATE TABLE core.memberships (
  id            uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL,
  user_id       uuid NOT NULL,
  status        text NOT NULL DEFAULT 'invited',
  expires_at    timestamptz,
  mfa_required  boolean NOT NULL DEFAULT false,
  created_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  version       int NOT NULL DEFAULT 1,
  CONSTRAINT memberships_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT memberships_tenant_id_user_id_key UNIQUE (tenant_id, user_id),
  CONSTRAINT memberships_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT memberships_user_fk FOREIGN KEY (user_id) REFERENCES core.users (id),
  CONSTRAINT memberships_created_by_fk FOREIGN KEY (created_by)
    REFERENCES core.users (id) ON DELETE SET NULL,
  CONSTRAINT memberships_status_check
    CHECK (status IN ('invited','active','suspended','removed')),
  CONSTRAINT memberships_expiry_after_creation CHECK (expires_at IS NULL OR expires_at > created_at),
  CONSTRAINT memberships_version_positive CHECK (version >= 1)
);
CREATE INDEX memberships_user_id_idx ON core.memberships (user_id);

CREATE TABLE core.permissions (
  key          text PRIMARY KEY,
  description  text NOT NULL,
  sensitivity  text NOT NULL DEFAULT 'normal',
  step_up      boolean NOT NULL DEFAULT false,
  is_platform  boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT permissions_key_format CHECK (key ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
  CONSTRAINT permissions_sensitivity_check
    CHECK (sensitivity IN ('normal','sensitive','critical')),
  -- Platform permissions are exactly the platform.* keys; they can never be granted to a
  -- tenant role (trigger on core.role_permissions below).
  CONSTRAINT permissions_platform_prefix CHECK (is_platform = starts_with(key, 'platform.'))
);

CREATE TABLE core.roles (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  key         text NOT NULL,
  name_en     text NOT NULL,
  name_te     text NOT NULL,
  is_system   boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  version     int NOT NULL DEFAULT 1,
  CONSTRAINT roles_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT roles_tenant_id_key_key UNIQUE (tenant_id, key),
  CONSTRAINT roles_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT roles_key_format CHECK (key ~ '^[a-z][a-z0-9_]{1,63}$'),
  CONSTRAINT roles_name_en_length CHECK (char_length(name_en) BETWEEN 1 AND 100),
  CONSTRAINT roles_name_te_length CHECK (char_length(name_te) BETWEEN 1 AND 100),
  CONSTRAINT roles_version_positive CHECK (version >= 1)
);

CREATE TABLE core.role_permissions (
  tenant_id       uuid NOT NULL,
  role_id         uuid NOT NULL,
  permission_key  text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT role_permissions_pkey PRIMARY KEY (tenant_id, role_id, permission_key),
  CONSTRAINT role_permissions_role_fk FOREIGN KEY (tenant_id, role_id)
    REFERENCES core.roles (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT role_permissions_permission_fk FOREIGN KEY (permission_key)
    REFERENCES core.permissions (key)
);
CREATE INDEX role_permissions_permission_key_idx ON core.role_permissions (permission_key);

CREATE TABLE core.membership_roles (
  tenant_id      uuid NOT NULL,
  membership_id  uuid NOT NULL,
  role_id        uuid NOT NULL,
  granted_by     uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT membership_roles_pkey PRIMARY KEY (tenant_id, membership_id, role_id),
  CONSTRAINT membership_roles_membership_fk FOREIGN KEY (tenant_id, membership_id)
    REFERENCES core.memberships (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT membership_roles_role_fk FOREIGN KEY (tenant_id, role_id)
    REFERENCES core.roles (tenant_id, id),
  CONSTRAINT membership_roles_granted_by_fk FOREIGN KEY (granted_by)
    REFERENCES core.users (id) ON DELETE SET NULL
);
CREATE INDEX membership_roles_role_idx ON core.membership_roles (tenant_id, role_id);

CREATE TABLE core.academic_years (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  label       text NOT NULL,
  starts_on   date NOT NULL,
  ends_on     date NOT NULL,
  is_current  boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  version     int NOT NULL DEFAULT 1,
  CONSTRAINT academic_years_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT academic_years_tenant_id_label_key UNIQUE (tenant_id, label),
  CONSTRAINT academic_years_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT academic_years_label_length CHECK (char_length(label) BETWEEN 1 AND 32),
  CONSTRAINT academic_years_dates_ordered CHECK (starts_on < ends_on),
  CONSTRAINT academic_years_version_positive CHECK (version >= 1)
);
-- FR-TEN-010: exactly one current year per tenant.
CREATE UNIQUE INDEX one_current_year ON core.academic_years (tenant_id) WHERE is_current;

CREATE TABLE core.classes (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  code        text NOT NULL,
  display_en  text NOT NULL,
  display_te  text NOT NULL,
  sort_order  int  NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  version     int NOT NULL DEFAULT 1,
  CONSTRAINT classes_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT classes_tenant_id_code_key UNIQUE (tenant_id, code),
  CONSTRAINT classes_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT classes_code_format CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,15}$'),
  CONSTRAINT classes_display_en_length CHECK (char_length(display_en) BETWEEN 1 AND 100),
  CONSTRAINT classes_display_te_length CHECK (char_length(display_te) BETWEEN 1 AND 100),
  CONSTRAINT classes_sort_order_range CHECK (sort_order BETWEEN 0 AND 10000),
  CONSTRAINT classes_version_positive CHECK (version >= 1)
);

CREATE TABLE core.sections (
  id                           uuid PRIMARY KEY,
  tenant_id                    uuid NOT NULL,
  class_id                     uuid NOT NULL,
  academic_year_id             uuid NOT NULL,
  name                         text NOT NULL,
  class_teacher_membership_id  uuid,
  created_at                   timestamptz NOT NULL DEFAULT now(),
  updated_at                   timestamptz NOT NULL DEFAULT now(),
  version                      int NOT NULL DEFAULT 1,
  CONSTRAINT sections_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT sections_year_class_name_key UNIQUE (tenant_id, academic_year_id, class_id, name),
  CONSTRAINT sections_class_fk FOREIGN KEY (tenant_id, class_id)
    REFERENCES core.classes (tenant_id, id),
  CONSTRAINT sections_academic_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT sections_class_teacher_fk FOREIGN KEY (tenant_id, class_teacher_membership_id)
    REFERENCES core.memberships (tenant_id, id) ON DELETE SET NULL (class_teacher_membership_id),
  CONSTRAINT sections_name_length CHECK (char_length(name) BETWEEN 1 AND 16),
  CONSTRAINT sections_version_positive CHECK (version >= 1)
);
CREATE INDEX sections_class_idx ON core.sections (tenant_id, class_id);
CREATE INDEX sections_class_teacher_idx ON core.sections (tenant_id, class_teacher_membership_id)
  WHERE class_teacher_membership_id IS NOT NULL;

CREATE TABLE core.membership_scopes (
  id             uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL,
  membership_id  uuid NOT NULL,
  scope_type     text NOT NULL,
  scope_ref      uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT membership_scopes_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT membership_scopes_unique_scope
    UNIQUE NULLS NOT DISTINCT (tenant_id, membership_id, scope_type, scope_ref),
  CONSTRAINT membership_scopes_membership_fk FOREIGN KEY (tenant_id, membership_id)
    REFERENCES core.memberships (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT membership_scopes_type_check CHECK (scope_type IN ('school','class','section')),
  CONSTRAINT membership_scopes_ref_matches_type CHECK ((scope_type = 'school') = (scope_ref IS NULL))
);
CREATE INDEX membership_scopes_ref_idx ON core.membership_scopes (tenant_id, scope_ref)
  WHERE scope_ref IS NOT NULL;
"""

# SECURITY INVOKER helpers owned by sos_owner (they run with the caller's privileges and RLS).
INVOKER_FUNCTIONS_SQL = r"""
CREATE FUNCTION core.tg_set_updated_at() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  NEW.updated_at := pg_catalog.now();
  RETURN NEW;
END
$$;

CREATE FUNCTION core.tg_role_permission_not_platform() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM core.permissions AS p
             WHERE p.key = NEW.permission_key AND p.is_platform) THEN
    RAISE EXCEPTION 'platform permissions cannot be granted to tenant roles'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'role_permissions_not_platform';
  END IF;
  RETURN NEW;
END
$$;

-- scope_ref must be a class/section of the SAME tenant. Runs as the caller, so RLS also hides
-- other tenants' rows; the explicit tenant_id comparison keeps it correct for any caller.
CREATE FUNCTION core.tg_membership_scope_ref_valid() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.scope_ref IS NULL THEN
    RETURN NEW;  -- membership_scopes_ref_matches_type decides whether NULL is allowed
  END IF;
  IF NEW.scope_type = 'class' AND NOT EXISTS (
       SELECT 1 FROM core.classes AS c
       WHERE c.id = NEW.scope_ref AND c.tenant_id = NEW.tenant_id) THEN
    RAISE EXCEPTION 'scope_ref is not a class of this school'
      USING ERRCODE = 'foreign_key_violation', CONSTRAINT = 'membership_scopes_ref_exists';
  ELSIF NEW.scope_type = 'section' AND NOT EXISTS (
       SELECT 1 FROM core.sections AS s
       WHERE s.id = NEW.scope_ref AND s.tenant_id = NEW.tenant_id) THEN
    RAISE EXCEPTION 'scope_ref is not a section of this school'
      USING ERRCODE = 'foreign_key_violation', CONSTRAINT = 'membership_scopes_ref_exists';
  END IF;
  RETURN NEW;
END
$$;

-- A class/section that still scopes a membership cannot be deleted (acts like ON DELETE RESTRICT).
CREATE FUNCTION core.tg_scope_target_not_in_use() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM core.membership_scopes AS ms
             WHERE ms.tenant_id = OLD.tenant_id AND ms.scope_ref = OLD.id) THEN
    RAISE EXCEPTION 'still used as a membership scope'
      USING ERRCODE = 'foreign_key_violation', CONSTRAINT = 'membership_scopes_ref_exists';
  END IF;
  RETURN OLD;
END
$$;

REVOKE ALL ON FUNCTION core.tg_set_updated_at(), core.tg_role_permission_not_platform(),
  core.tg_membership_scope_ref_valid(), core.tg_scope_target_not_in_use() FROM PUBLIC;

CREATE TRIGGER role_permissions_not_platform
  BEFORE INSERT OR UPDATE OF permission_key ON core.role_permissions
  FOR EACH ROW EXECUTE FUNCTION core.tg_role_permission_not_platform();

CREATE TRIGGER membership_scopes_ref_valid
  BEFORE INSERT OR UPDATE OF tenant_id, scope_type, scope_ref ON core.membership_scopes
  FOR EACH ROW EXECUTE FUNCTION core.tg_membership_scope_ref_valid();

CREATE TRIGGER classes_scope_restrict BEFORE DELETE ON core.classes
  FOR EACH ROW EXECUTE FUNCTION core.tg_scope_target_not_in_use();
CREATE TRIGGER sections_scope_restrict BEFORE DELETE ON core.sections
  FOR EACH ROW EXECUTE FUNCTION core.tg_scope_target_not_in_use();
"""

UPDATED_AT_TABLES = (
    "core.tenants",
    "core.users",
    "core.memberships",
    "core.roles",
    "core.academic_years",
    "core.classes",
    "core.sections",
)

POLICIES_SQL = """
ALTER TABLE core.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY own_tenant ON core.tenants
  USING (id = core.current_tenant()) WITH CHECK (id = core.current_tenant());

ALTER TABLE core.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.users FORCE ROW LEVEL SECURITY;
-- A user row is visible/updatable only while the user has a membership in the current tenant.
-- core.memberships is itself RLS-filtered to the current tenant. No INSERT/DELETE path for the
-- app (grants below); new users come only from core.create_user_for_invite().
CREATE POLICY users_in_tenant ON core.users FOR SELECT
  USING (EXISTS (SELECT 1 FROM core.memberships AS m
                 WHERE m.user_id = users.id AND m.tenant_id = core.current_tenant()));
CREATE POLICY users_in_tenant_update ON core.users FOR UPDATE
  USING (EXISTS (SELECT 1 FROM core.memberships AS m
                 WHERE m.user_id = users.id AND m.tenant_id = core.current_tenant()))
  WITH CHECK (EXISTS (SELECT 1 FROM core.memberships AS m
                      WHERE m.user_id = users.id AND m.tenant_id = core.current_tenant()));
"""

GRANTS_SQL = """
-- sos_app received SELECT/INSERT/UPDATE/DELETE via default privileges; narrow it (ADR-0013).
-- Tenants: created only by core.provision_tenant(); status/plan only via core.set_tenant_status()
-- and the control plane. The school may edit its own name and settings.
REVOKE INSERT, UPDATE, DELETE ON core.tenants FROM sos_app;
GRANT UPDATE (name, settings, version) ON core.tenants TO sos_app;
-- Users: created only by core.create_user_for_invite(); never deleted by the app; identity
-- subject and global status are not editable from a tenant context.
REVOKE INSERT, UPDATE, DELETE ON core.users FROM sos_app;
GRANT UPDATE (display_name, email, phone_ciphertext, preferred_language, last_login_at, version)
  ON core.users TO sos_app;
-- Permission catalog: read-only for the app (seeded by migrations).
REVOKE INSERT, UPDATE, DELETE ON core.permissions FROM sos_app;
-- Wrapped keys: the app adds key versions and retires them; it never rewrites or deletes them
-- (crypto-shredding is an offboarding action).
REVOKE UPDATE, DELETE ON core.tenant_keys FROM sos_app;
GRANT UPDATE (retired_at) ON core.tenant_keys TO sos_app;
-- Reporting never needs wrapped keys.
REVOKE ALL ON core.tenant_keys FROM sos_readonly;

-- sos_definer is not covered by default privileges: grant exactly what the functions need.
GRANT SELECT, INSERT, UPDATE ON core.tenants TO sos_definer;
GRANT SELECT, INSERT ON core.users TO sos_definer;
GRANT SELECT ON core.memberships, core.tenant_keys, core.sections, core.academic_years
  TO sos_definer;
"""

# Created while SET ROLE sos_definer so the functions are owned by sos_definer (sos_owner cannot
# hand ownership to a role it is not a member of). Bodies are fully schema-qualified.
DEFINER_FUNCTIONS_SQL = r"""
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
  -- id: UUIDv7 (docs/05 §1) = 48-bit unix-ms timestamp over a random v4 UUID with the version
  -- nibble raised from 4 to 7. overlay/substring/extract are SQL-standard syntax, which the
  -- parser always binds to pg_catalog.
  VALUES (pg_catalog.encode(pg_catalog.set_bit(pg_catalog.set_bit(
            overlay(pg_catalog.uuid_send(pg_catalog.gen_random_uuid())
                    PLACING substring(pg_catalog.int8send(
                      (extract(epoch FROM pg_catalog.clock_timestamp()) * 1000)::bigint) FROM 3)
                    FROM 1 FOR 6),
            52, 1), 53, 1), 'hex')::uuid,
          p_subject, p_display_name, p_email, COALESCE(p_language, 'en'), 'active')
  ON CONFLICT (idp_subject) DO NOTHING
  RETURNING u.id INTO v_id;
  IF v_id IS NULL THEN
    SELECT u.id INTO v_id FROM core.users AS u WHERE u.idp_subject = p_subject;
  END IF;
  RETURN v_id;
END
$$;

CREATE FUNCTION core.list_tenant_ids(p_status text[]) RETURNS TABLE (tenant_id uuid)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT t.id FROM core.tenants AS t
    WHERE p_status IS NULL OR t.status = ANY (p_status)
    ORDER BY t.id
$$;

CREATE FUNCTION core.provision_tenant(
    p_id uuid, p_code text, p_name text, p_boards text[], p_plan_tier text,
    p_deployment_mode text)
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF p_id IS NULL THEN
    RAISE EXCEPTION 'tenant id is required' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO core.tenants (id, code, name, boards, status, plan_tier, deployment_mode)
  VALUES (p_id, p_code, p_name, COALESCE(p_boards, '{}'::text[]), 'provisioning',
          COALESCE(p_plan_tier, 'shared'), COALESCE(p_deployment_mode, 'shared'));
  RETURN p_id;
END
$$;

CREATE FUNCTION core.set_tenant_status(p_tenant uuid, p_status text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v_old text;
BEGIN
  SELECT t.status INTO v_old FROM core.tenants AS t WHERE t.id = p_tenant FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'tenant not found' USING ERRCODE = 'no_data_found';
  END IF;
  IF NOT (v_old || '>' || p_status) = ANY (ARRAY[
      'provisioning>active', 'active>suspended', 'suspended>active',
      'active>offboarding', 'suspended>offboarding', 'offboarding>deleted']) THEN
    RAISE EXCEPTION 'illegal tenant status transition'
      USING ERRCODE = 'object_not_in_prerequisite_state',
            DETAIL = v_old || ' -> ' || COALESCE(p_status, 'NULL');
  END IF;
  -- FR-TEN-003: a school never goes live without a data encryption key.
  IF p_status = 'active' AND v_old = 'provisioning' AND NOT EXISTS (
      SELECT 1 FROM core.tenant_keys AS k
      WHERE k.tenant_id = p_tenant AND k.retired_at IS NULL) THEN
    RAISE EXCEPTION 'tenant has no data encryption key'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  UPDATE core.tenants AS t SET status = p_status, version = t.version + 1
  WHERE t.id = p_tenant;
  RETURN v_old;
END
$$;

CREATE FUNCTION core.tenant_usage_summary(p_tenant uuid)
  RETURNS TABLE (active_memberships int, users int, sections int, academic_years int)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT
      (SELECT pg_catalog.count(*)::int FROM core.memberships AS m
        WHERE m.tenant_id = p_tenant AND m.status = 'active'
          AND (m.expires_at IS NULL OR m.expires_at > pg_catalog.now())),
      (SELECT pg_catalog.count(DISTINCT m.user_id)::int FROM core.memberships AS m
        WHERE m.tenant_id = p_tenant AND m.status <> 'removed'),
      (SELECT pg_catalog.count(*)::int FROM core.sections AS s WHERE s.tenant_id = p_tenant),
      (SELECT pg_catalog.count(*)::int FROM core.academic_years AS y
        WHERE y.tenant_id = p_tenant)
$$;
"""

# (signature, roles granted EXECUTE)
DEFINER_FUNCTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("core.resolve_login(text)", ("sos_app",)),
    ("core.find_user_id_by_subject(text)", ("sos_app",)),
    ("core.create_user_for_invite(text, text, public.citext, text)", ("sos_app",)),
    ("core.list_tenant_ids(text[])", ("sos_app", "sos_platform")),
    ("core.provision_tenant(uuid, text, text, text[], text, text)", ("sos_platform",)),
    ("core.set_tenant_status(uuid, text)", ("sos_platform",)),
    ("core.tenant_usage_summary(uuid)", ("sos_platform",)),
)

DROP_ORDER = (
    "core.membership_scopes",
    "core.sections",
    "core.classes",
    "core.academic_years",
    "core.membership_roles",
    "core.role_permissions",
    "core.roles",
    "core.permissions",
    "core.memberships",
    "core.users",
    "core.tenant_keys",
    "core.tenants",
)


def upgrade() -> None:
    op.execute(TABLES_SQL)
    op.execute(INVOKER_FUNCTIONS_SQL)
    for table in UPDATED_AT_TABLES:
        name = table.split(".", 1)[1]
        op.execute(
            f"CREATE TRIGGER {name}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at()"
        )

    op.execute(POLICIES_SQL)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
    for table in DEFINER_ACCESS_TABLES:
        op.execute(
            f"CREATE POLICY definer_access ON {table} AS PERMISSIVE FOR ALL TO PUBLIC "
            "USING (current_user = 'sos_definer') WITH CHECK (current_user = 'sos_definer')"
        )

    op.execute(GRANTS_SQL)

    # Definer functions: create them AS sos_definer. The CREATE grant on schema core exists only
    # inside this transaction (revoked below before commit).
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(DEFINER_FUNCTIONS_SQL)
    for signature, roles in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {', '.join(roles)}")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def downgrade() -> None:
    # sos_owner owns schema core, so it may drop the sos_definer-owned functions.
    for signature, _roles in reversed(DEFINER_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    # These policies on core.users reference core.memberships.
    op.execute("DROP POLICY IF EXISTS users_in_tenant_update ON core.users")
    op.execute("DROP POLICY IF EXISTS users_in_tenant ON core.users")
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute(
        "DROP FUNCTION IF EXISTS core.tg_scope_target_not_in_use(), "
        "core.tg_membership_scope_ref_valid(), core.tg_role_permission_not_platform(), "
        "core.tg_set_updated_at()"
    )
