-- Stele role creation — DEV / CI ONLY.
-- Idempotent; safe to re-run.
--
-- This file creates the four least-privilege roles with the throwaway dev
-- password. It is the ONLY environment-specific half of the bootstrap, so it is
-- kept apart from the load-bearing grant logic in 02-schemas-and-grants.sql,
-- which is shared verbatim with production.
--
-- Production does NOT run this file. There the roles are created from real
-- secrets by scripts/bootstrap_roles.py (the migrate entrypoint), which then
-- applies 02-schemas-and-grants.sql unchanged. Splitting it this way keeps the
-- dangerous part — schemas, grants, default privileges — single-sourced, so dev,
-- CI, and prod can never drift on who-can-touch-what (CLAUDE.md: grant changes
-- are silent until they bite under a non-superuser role).
--
-- Run as a superuser (dev container post-create / CI), connected to `stele`.

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
