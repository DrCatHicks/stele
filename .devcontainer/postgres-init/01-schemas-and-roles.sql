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

-- ETL reads from app, writes to stg and marts.
GRANT USAGE ON SCHEMA app TO stele_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT SELECT ON TABLES TO stele_etl;
GRANT USAGE, CREATE ON SCHEMA stg, marts TO stele_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA stg, marts
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO stele_etl;

-- Analyst reads marts only.
GRANT USAGE ON SCHEMA marts TO stele_analyst;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts
    GRANT SELECT ON TABLES TO stele_analyst;

-- PII reviewer reads pii.
GRANT USAGE ON SCHEMA pii TO stele_pii_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA pii
    GRANT SELECT, UPDATE ON TABLES TO stele_pii_reviewer;