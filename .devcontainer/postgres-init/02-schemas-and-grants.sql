-- Stele schemas, grants, and default privileges.
-- Idempotent; safe to re-run.
--
-- SHARED VERBATIM across dev, CI, and production. Dev/CI apply it with psql
-- (post-create.sh / ci.yml), connected to `stele`; production applies it through
-- psycopg from scripts/bootstrap_roles.py, after that script has created the four
-- roles from real secrets. Keep this file free of psql meta-commands (no \c, no
-- \set) so psycopg can execute it unchanged — the caller is already connected to
-- the target database.
--
-- This is the load-bearing, single-sourced half of the bootstrap: who can touch
-- what. The roles it references are created beforehand — by 01-roles.sql in
-- dev/CI, by bootstrap_roles.py in prod (CLAUDE.md: grant changes are silent
-- until they bite under a non-superuser role, so they live in exactly one place).
--
-- The executing identity MUST also be the migrator (`alembic upgrade head`): the
-- ALTER DEFAULT PRIVILEGES below are grantor-specific, so they only reach the
-- tables a migration creates if the same role created the defaults and the tables.

-- Schemas
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS pii;
-- Operational metadata (ETL run log, §3.7). Deliberately outside marts: run
-- history is operational, not analytical. Table created + granted by Alembic.
CREATE SCHEMA IF NOT EXISTS ops;

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

-- ETL run log lives in ops (§3.7). Same model-C least-privilege as app: schema
-- USAGE only here; the migration that creates ops.etl_runs grants the table-level
-- SELECT/INSERT/UPDATE to stele_etl (the runner) and SELECT to stele_analyst.
-- No schema-wide default privileges, so a future ops table is invisible until its
-- own migration grants it.
GRANT USAGE ON SCHEMA ops TO stele_etl;
GRANT USAGE ON SCHEMA ops TO stele_analyst;

-- Analyst reads marts only. marts tables are created by stele_etl (dbt), not by
-- the migrator, so a FOR ROLE stele_etl default-privilege rule is needed for the
-- analyst to read what dbt creates.
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
-- migrations and the unqualified marts block above already applies) or in prod.
-- No-op anywhere stele_dev is absent. No app rule here: stele_etl's app access is
-- table-level on declared ETL sources, granted in migrations (see above).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_dev') THEN
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE stele_dev IN SCHEMA marts '
            'GRANT SELECT ON TABLES TO stele_analyst';
    END IF;
END
$$;
