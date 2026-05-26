"""ETL least-privilege on app: SELECT on declared sources only

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-05-23 15:00:00.000000

Flips the stele_etl → app grant model from "schema-wide SELECT by inheritance"
to "table-level SELECT on declared ETL sources only" (design doc §3.3, model C).

Why: the old init SQL gave stele_etl SELECT on every app table (via ALTER
DEFAULT PRIVILEGES + a catch-all GRANT). That made *default-allow* the resting
state — any new app table leaked to the ETL role by inheritance. With operator
auth (M3.1) the app schema now holds secrets (app.users.password_hash,
app.sessions.token) that role must never read, and the M3.1 migration's
per-table REVOKE was fragile: a re-run of the idempotent init SQL re-granted it.

The durable fix is to make *default-deny* the resting state. The init SQL no
longer grants stele_etl any app-table SELECT; instead each declared ETL source
gets an explicit grant in its own migration. dbt's only app source is
raw_responses (invariant 1/4, enforced by scripts/check_invariants.py), so that
is the sole grant here. A future ETL source adds its GRANT in its own migration.

This migration converges already-provisioned databases (where the old broad
grants persist as server state Alembic can't see): it revokes the object-level
grant on existing tables and reverses the default-privilege entries the old init
SQL created — one per grantor (postgres ran init / CI migrations; stele_api in
prod; stele_dev in the dev container), since ALTER DEFAULT PRIVILEGES is keyed by
the role whose created tables it covers. On a fresh build the init SQL creates
none of these entries, so every reversal is a harmless no-op.

Each FOR ROLE reversal is guarded on the grantor *existing* and the migrator
holding its inherited privileges (pg_has_role ... 'USAGE'). A superuser passes
both for every role, so dev/CI behavior is unchanged; a managed-Postgres
non-superuser admin (M7.3) — which is not a member of `postgres` (a role that may
not even exist there) and has only INHERIT-FALSE membership of the roles it
created — skips them, which is correct: a fresh prod DB has nothing to reverse.
Without the guard, the bare `ALTER DEFAULT PRIVILEGES FOR ROLE postgres` would
fail the whole migration on managed Postgres.

The M3.1 migration's targeted `REVOKE ... FROM stele_etl` on users/sessions is
subsumed by this default-deny model and becomes a no-op on fresh builds; it is
left in place rather than editing an already-applied migration.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Roles that may own a default-privilege entry the old init SQL set for stele_etl,
# keyed by the grantor whose created tables it covers. postgres: init runner / CI
# migration runner. stele_api: prod migration runner (legacy). stele_dev:
# dev-container migration runner. Each is reversed only if it exists AND the
# migrator can target it (see _reverse_app_default_privileges) — so a managed-PG
# non-superuser admin, a member of none of them with inherited privilege, skips
# all three, which is correct on a fresh DB that has no such entry to reverse.
_GRANTOR_ROLES = ("postgres", "stele_api", "stele_dev")


def _reverse_app_default_privileges(action: str) -> None:
    """Emit a guarded ALTER DEFAULT PRIVILEGES per grantor (action: REVOKE/GRANT).

    Runs the statement only when the grantor role exists and the current role
    holds its inherited privileges (pg_has_role 'USAGE'); ALTER DEFAULT PRIVILEGES
    FOR ROLE requires the latter, and a bare statement for an unprivileged or
    absent grantor (e.g. `postgres` on managed Postgres) would abort the migration.
    """
    verb = (
        "REVOKE SELECT ON TABLES FROM stele_etl"
        if action == "REVOKE"
        else ("GRANT SELECT ON TABLES TO stele_etl")
    )
    # _GRANTOR_ROLES is a hardcoded constant, not user input — no injection vector.
    roles = ", ".join(f"'{r}'" for r in _GRANTOR_ROLES)
    op.execute(
        f"""
        DO $$
        DECLARE grantor text;
        BEGIN
            FOREACH grantor IN ARRAY ARRAY[{roles}] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = grantor)
                   AND pg_has_role(current_user, grantor, 'USAGE') THEN
                    EXECUTE format(
                        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA app {verb}',
                        grantor);
                END IF;
            END LOOP;
        END
        $$;
        """  # noqa: S608
    )


def upgrade() -> None:
    # Existing app tables: drop the broad object-level grant.
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA app FROM stele_etl")

    # Future app tables: reverse the inherited default-privilege grants so a new
    # table is no longer auto-readable by the ETL role. One per grantor, guarded.
    _reverse_app_default_privileges("REVOKE")

    # The one declared ETL source. raw_responses already exists (initial
    # migration); this adopts it under the new explicit-grant model.
    op.execute("GRANT SELECT ON app.raw_responses TO stele_etl")


def downgrade() -> None:
    # Restore the prior schema-wide SELECT model.
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA app TO stele_etl")
    _reverse_app_default_privileges("GRANT")
