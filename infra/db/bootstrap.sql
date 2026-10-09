-- SchoolOS database bootstrap (ADR-0013, docs/05 §3). Idempotent.
--
-- Run ONCE per database as an admin (superuser locally; the RDS master user in AWS;
-- the postgres user on a dedicated host), connected to the target database:
--
--   psql -v ON_ERROR_STOP=1 \
--        -c '\getenv app_password SOS_APP_DB_PASSWORD' \
--        -c '\getenv migrator_password SOS_MIGRATOR_DB_PASSWORD' \
--        -c '\getenv platform_password SOS_PLATFORM_DB_PASSWORD' \
--        -c '\getenv readonly_password SOS_READONLY_DB_PASSWORD' \
--        -d schoolos -f infra/db/bootstrap.sql
--
-- (deployments read the passwords from the environment with \getenv so they never appear on a
-- command line; `-v name=value` works too, for local tools and tests).
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
-- ADR-0029: deletes a school's rows at offboarding. Reachable only by SET ROLE from sos_app.
SELECT 'CREATE ROLE sos_purger NOLOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sos_purger') \gexec

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
ALTER ROLE sos_purger   NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;

-- The admin running this script must be able to act as sos_owner (needed on RDS).
GRANT sos_owner TO CURRENT_USER;
GRANT sos_definer TO CURRENT_USER;
-- The migrator creates objects as sos_owner (env.py runs SET ROLE sos_owner) and may hand
-- allowlisted SECURITY DEFINER functions to sos_definer (ALTER FUNCTION ... OWNER TO).
-- INHERIT FALSE: membership grants SET ROLE only, never implicit privileges.
GRANT sos_owner   TO sos_migrator WITH INHERIT FALSE, SET TRUE;
GRANT sos_definer TO sos_migrator WITH INHERIT FALSE, SET TRUE;
-- ADR-0029: the offboarding deletion job (worker, as sos_app inside the school's tenant_session)
-- runs SET LOCAL ROLE sos_purger. INHERIT FALSE: sos_app gains no privilege from this; its own
-- grants stay exactly as narrowed in the migrations. sos_purger is still subject to RLS
-- (tenant_isolation) and to the restrictive policy offboarding_purge (migration 0032).
GRANT sos_purger  TO sos_app WITH INHERIT FALSE, SET TRUE;

-- Safe search_path for login roles: application code always schema-qualifies names.
ALTER ROLE sos_app      SET search_path = pg_catalog, public;
ALTER ROLE sos_platform SET search_path = pg_catalog, public;
ALTER ROLE sos_readonly SET search_path = pg_catalog, public;
ALTER ROLE sos_app      SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE sos_platform SET idle_in_transaction_session_timeout = '30s';
-- Role timeouts (audit 2026-10-05 hardening; SEC-002). The app sets statement_timeout per
-- transaction (core.db); the role default is the backstop for anything outside that path.
-- sos_readonly (people, reporting tools) gets short limits and read-only transactions.
-- sos_migrator is exempt from the dedicated host's server-wide statement_timeout (index builds).
ALTER ROLE sos_app      SET statement_timeout = '5min';
ALTER ROLE sos_platform SET statement_timeout = '5min';
ALTER ROLE sos_readonly SET statement_timeout = '60s';
ALTER ROLE sos_readonly SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE sos_readonly SET idle_session_timeout = '30min';
ALTER ROLE sos_readonly SET default_transaction_read_only = on;
ALTER ROLE sos_migrator SET statement_timeout = 0;

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
-- ADR-0029: table grants for sos_purger are made per table in migration 0032.
GRANT USAGE ON SCHEMA core, sis, kb, audit, ops TO sos_purger;

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
  TO sos_app, sos_readonly, sos_definer, sos_platform, sos_purger;

RESET ROLE;
