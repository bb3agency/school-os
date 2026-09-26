-- SchoolOS database bootstrap (ADR-0013, docs/05 §3). Idempotent.
--
-- Run ONCE per database as an admin (superuser locally; the RDS master user in AWS;
-- the postgres user on a dedicated host), connected to the target database:
--
--   psql -v ON_ERROR_STOP=1 \
--        -v app_password=... -v migrator_password=... \
--        -v platform_password=... -v readonly_password=... \
--        -d schoolos -f infra/db/bootstrap.sql
--
-- The same file is used by docker compose, testcontainers, the ECS one-off task and
-- dedicated hosts. It never contains secrets. No role here has SUPERUSER or BYPASSRLS.

\set ON_ERROR_STOP on

-- 1. Roles -----------------------------------------------------------------------------
SELECT 'CREATE ROLE sos_owner NOLOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sos_owner') \gexec
SELECT 'CREATE ROLE sos_definer NOLOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sos_definer') \gexec
SELECT 'CREATE ROLE sos_migrator LOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sos_migrator') \gexec
SELECT 'CREATE ROLE sos_app LOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sos_app') \gexec
SELECT 'CREATE ROLE sos_platform LOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sos_platform') \gexec
SELECT 'CREATE ROLE sos_readonly LOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sos_readonly') \gexec

-- Enforce attributes every run (fail closed even if a role pre-existed with other attributes).
ALTER ROLE sos_owner    NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE sos_definer  NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE sos_migrator LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION
  PASSWORD :'migrator_password';
ALTER ROLE sos_app      LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION
  PASSWORD :'app_password';
ALTER ROLE sos_platform LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION
  PASSWORD :'platform_password';
ALTER ROLE sos_readonly LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION
  PASSWORD :'readonly_password';

-- The admin running this script must be able to act as sos_owner (needed on RDS).
GRANT sos_owner TO CURRENT_USER;
GRANT sos_definer TO CURRENT_USER;
-- The migrator creates objects as sos_owner (env.py runs SET ROLE sos_owner) and may hand
-- allowlisted SECURITY DEFINER functions to sos_definer (ALTER FUNCTION ... OWNER TO).
-- INHERIT FALSE: membership grants SET ROLE only, never implicit privileges.
GRANT sos_owner   TO sos_migrator WITH INHERIT FALSE, SET TRUE;
GRANT sos_definer TO sos_migrator WITH INHERIT FALSE, SET TRUE;

-- Safe search_path for login roles: application code always schema-qualifies names.
ALTER ROLE sos_app      SET search_path = pg_catalog, public;
ALTER ROLE sos_platform SET search_path = pg_catalog, public;
ALTER ROLE sos_readonly SET search_path = pg_catalog, public;
ALTER ROLE sos_app      SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE sos_platform SET idle_in_transaction_session_timeout = '30s';

-- 2. Extensions (need admin rights; created in public) ----------------------------------
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- 3. Schemas ------------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS core     AUTHORIZATION sos_owner;
CREATE SCHEMA IF NOT EXISTS sis      AUTHORIZATION sos_owner;
CREATE SCHEMA IF NOT EXISTS kb       AUTHORIZATION sos_owner;
CREATE SCHEMA IF NOT EXISTS audit    AUTHORIZATION sos_owner;
CREATE SCHEMA IF NOT EXISTS ops      AUTHORIZATION sos_owner;
CREATE SCHEMA IF NOT EXISTS platform AUTHORIZATION sos_owner;

REVOKE ALL ON SCHEMA core, sis, kb, audit, ops, platform FROM PUBLIC;
GRANT USAGE ON SCHEMA core, sis, kb, audit, ops TO sos_app, sos_readonly, sos_definer;
-- sos_app needs USAGE on platform only to read platform.feature_flags and call definer functions.
GRANT USAGE ON SCHEMA platform TO sos_app, sos_platform, sos_definer;
-- sos_platform gets USAGE on core only to call allowlisted definer functions (no table grants).
GRANT USAGE ON SCHEMA core TO sos_platform;
GRANT USAGE, CREATE ON SCHEMA core, sis, kb, audit, ops, platform TO sos_owner;

-- 4. Default privileges for objects created by sos_owner -----------------------------------
-- Functions are NOT executable by PUBLIC by default; each migration grants EXECUTE explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE sos_owner   REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE sos_definer REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

ALTER DEFAULT PRIVILEGES FOR ROLE sos_owner IN SCHEMA core, sis, kb, ops
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sos_owner IN SCHEMA core, sis, kb, ops
  GRANT USAGE, SELECT ON SEQUENCES TO sos_app;
-- Audit is append-only for the app: no UPDATE, DELETE or TRUNCATE (FR-AUD-002).
ALTER DEFAULT PRIVILEGES FOR ROLE sos_owner IN SCHEMA audit
  GRANT SELECT, INSERT ON TABLES TO sos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sos_owner IN SCHEMA core, sis, kb, audit
  GRANT SELECT ON TABLES TO sos_readonly;
-- The control plane only ever touches schema platform.
ALTER DEFAULT PRIVILEGES FOR ROLE sos_owner IN SCHEMA platform
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sos_platform;
ALTER DEFAULT PRIVILEGES FOR ROLE sos_owner IN SCHEMA platform
  GRANT USAGE, SELECT ON SEQUENCES TO sos_platform;

-- 5. Tenant context helpers (fail closed when unset) ---------------------------------------
SET ROLE sos_owner;

CREATE OR REPLACE FUNCTION core.current_tenant() RETURNS uuid
  LANGUAGE sql STABLE PARALLEL SAFE
  AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION core.current_user_id() RETURNS uuid
  LANGUAGE sql STABLE PARALLEL SAFE
  AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

REVOKE ALL ON FUNCTION core.current_tenant(), core.current_user_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.current_tenant(), core.current_user_id()
  TO sos_app, sos_readonly, sos_definer, sos_platform;

RESET ROLE;
