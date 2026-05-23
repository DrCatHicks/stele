-- Stele initial database setup.
-- Idempotent; safe to re-run.

-- Database and developer role (created by post-create.sh as superuser before this runs).

\c stele

-- Schemas
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS pii;

-- Roles (per design doc §3.3)
--
-- In dev we don't enforce least-privilege strictly — the developer
-- needs to wear all hats — but we create the roles so that connection
-- strings, migrations, and dbt profiles match what will exist in prod.
-- The dev superuser inherits all of them.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_api') THEN
        CREATE ROLE stele_api LOGIN PASSWORD 'dev';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_etl') THEN
        CREATE ROLE stele_etl LOGIN PASSWORD 'dev';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_analyst') THEN
        CREATE ROLE stele_analyst LOGIN PASSWORD 'dev';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_pii_reviewer') THEN
        CREATE ROLE stele_pii_reviewer LOGIN PASSWORD 'dev';
    END IF;
END
$$;

-- Grants per design doc §3.3
-- API writes to app and pii.
GRANT USAGE, CREATE ON SCHEMA app, pii TO stele_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA app, pii
    GRANT SELECT, INSERT, UPDATE ON TABLES TO stele_api;
-- INSERTs into serial/bigserial tables call nextval(), which needs USAGE on
-- the backing sequence. Without this, least-privilege writes fail.
ALTER DEFAULT PRIVILEGES IN SCHEMA app, pii
    GRANT USAGE ON SEQUENCES TO stele_api;

-- ETL reads from app, writes to stg and marts.
--
-- Least-privilege on app (design doc §3.3): the ETL role does NOT get
-- schema-wide SELECT. dbt's sole source is app.raw_responses (invariant 1/4),
-- and the app schema also holds operator secrets — app.users.password_hash and
-- app.sessions.token (§3.10) — that the ETL role must never read. So stele_etl
-- gets table-level SELECT on each *declared ETL source* only, granted by the
-- migration that creates (or, for the pre-existing raw_responses, adopts) that
-- table. Adding a new ETL source = add its GRANT in that table's migration.
-- This makes default-deny the resting state: a new app table is invisible to
-- ETL until someone deliberately grants it, instead of leaking by inheritance.
GRANT USAGE ON SCHEMA app TO stele_etl;

GRANT USAGE, CREATE ON SCHEMA stg, marts TO stele_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA stg, marts
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO stele_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA stg, marts
    GRANT USAGE ON SEQUENCES TO stele_etl;

-- Analyst reads marts only. marts tables are created by stele_etl (dbt), not by
-- this init runner, so the same FOR ROLE hardening as app→stele_etl applies.
GRANT USAGE ON SCHEMA marts TO stele_analyst;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts
    GRANT SELECT ON TABLES TO stele_analyst;
ALTER DEFAULT PRIVILEGES FOR ROLE stele_etl IN SCHEMA marts
    GRANT SELECT ON TABLES TO stele_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO stele_analyst;

-- PII reviewer reads pii.
GRANT USAGE ON SCHEMA pii TO stele_pii_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA pii
    GRANT SELECT, UPDATE ON TABLES TO stele_pii_reviewer;

-- Dev container only: migrations run as the superuser stele_dev, so marts
-- tables it creates need the FOR ROLE default-privilege rule so the analyst can
-- read them. Guarded because stele_dev does not exist in CI (where postgres runs
-- migrations and the unqualified marts block above already applies). No-op
-- anywhere stele_dev is absent. No app rule here: stele_etl's app access is
-- table-level on declared ETL sources, granted in migrations (see above).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_dev') THEN
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE stele_dev IN SCHEMA marts '
            'GRANT SELECT ON TABLES TO stele_analyst';
    END IF;
END
$$;