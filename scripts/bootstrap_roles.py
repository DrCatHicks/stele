"""Bootstrap the least-privilege roles, schemas, and grants on a managed Postgres.

Production half of the role/grant bootstrap (M7.3). In dev and CI a superuser
applies ``.devcontainer/postgres-init/01-roles.sql`` then
``02-schemas-and-grants.sql`` (post-create.sh / ci.yml). A managed Postgres
(Railway, RDS, Cloud SQL) has no init-script hook and hands you a *non-superuser*
owner role, so this script reproduces that bootstrap over an ordinary connection:

  1. create ``stele_api`` / ``stele_etl`` / ``stele_analyst`` /
     ``stele_pii_reviewer`` from secrets — each role's password from
     ``STELE_<ROLE>_PASSWORD`` — idempotently. An existing role is left
     untouched, never silently re-passworded; so re-deploys need no secrets.
  2. apply ``02-schemas-and-grants.sql`` VERBATIM (the single-sourced grant
     logic), so dev, CI, and prod can never drift on who-can-touch-what.

Run as the SAME admin identity that then runs ``alembic upgrade head``: ALTER
DEFAULT PRIVILEGES is grantor-specific, so the grants only reach migration-created
tables if bootstrap-er == migrator. The ``migrate`` verb of
scripts/docker-entrypoint.sh chains the two for this reason.

Connection precedence: ``STELE_ADMIN_DATABASE_URL`` if set, else
``STELE_DATABASE_URL``. The two split apart in a Railway-style deploy where one
service runs both the web process (least-privilege ``stele_api`` on
``STELE_DATABASE_URL``) and a pre-deploy ``migrate`` (the admin identity on
``STELE_ADMIN_DATABASE_URL``); Alembic's env.py resolves the same precedence so
bootstrap-er still equals migrator. Dev/CI set only ``STELE_DATABASE_URL`` (one
admin identity), so the fallback keeps them unchanged.

Under a non-superuser admin, the grant SQL's ``ALTER DEFAULT PRIVILEGES FOR ROLE
stele_etl`` (so the analyst can read what dbt creates) requires the admin to hold
*inherited* privileges of ``stele_etl`` — bare membership is not enough. PG16
auto-grants the creator membership but WITH INHERIT FALSE, which does not satisfy
the check, so this script re-grants WITH INHERIT TRUE before applying the grants.
Inheriting ETL's rights is no escalation: the admin owns these objects and runs
migrations anyway. A superuser bypasses the check, so the step is a no-op in dev/CI.

Connection. One of ``STELE_ADMIN_DATABASE_URL`` (preferred) or
``STELE_DATABASE_URL`` must be set, or the run fails — a privileged bootstrap must
not guess where to connect. For local dev against the container superuser, opt into
the fallback explicitly with ``STELE_ALLOW_DEV_FALLBACK=1``.

Run:  STELE_ADMIN_DATABASE_URL=... STELE_API_PASSWORD=... STELE_ETL_PASSWORD=... \
        STELE_ANALYST_PASSWORD=... STELE_PII_REVIEWER_PASSWORD=... \
        uv run python scripts/bootstrap_roles.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

# Each managed role and the env var carrying its password. Order is the creation
# order; it doesn't matter functionally (grants come later) but mirrors 01-roles.sql.
ROLE_PASSWORD_ENV: dict[str, str] = {
    "stele_api": "STELE_API_PASSWORD",
    "stele_etl": "STELE_ETL_PASSWORD",
    "stele_analyst": "STELE_ANALYST_PASSWORD",
    "stele_pii_reviewer": "STELE_PII_REVIEWER_PASSWORD",
}

# The grant logic shared verbatim with dev/CI. Applied through psycopg, so the
# file must stay free of psql meta-commands (enforced by reading + executing here).
_GRANTS_SQL = (
    Path(__file__).resolve().parents[1]
    / ".devcontainer"
    / "postgres-init"
    / "02-schemas-and-grants.sql"
)

_DEV_FALLBACK_URL = "postgresql://stele_dev:dev@localhost:5432/stele"
_FALLBACK_FLAG = "STELE_ALLOW_DEV_FALLBACK"

# Admin connection, in precedence order. STELE_ADMIN_DATABASE_URL lets a deploy
# that runs migrate as a pre-deploy step (same service as the web process) carry a
# distinct admin identity, while STELE_DATABASE_URL stays the least-privilege
# stele_api connection the web process uses at runtime. Falls back to
# STELE_DATABASE_URL so dev/CI (one admin identity) need no new env.
_ADMIN_URL_ENVS = ("STELE_ADMIN_DATABASE_URL", "STELE_DATABASE_URL")


class BootstrapError(Exception):
    """A precondition for bootstrapping is missing (URL, secret, privilege)."""


def _conninfo() -> str:
    """Resolve the admin libpq conninfo, stripping any SQLAlchemy driver tag.

    Prefers STELE_ADMIN_DATABASE_URL, then STELE_DATABASE_URL (see _ADMIN_URL_ENVS).
    Required, with the same explicit dev opt-in as the provisioning CLI: a
    privileged bootstrap must not silently default to a hard-coded dev superuser,
    or a missing/misspelled env var would bootstrap into the wrong database.
    """
    url = next((v for v in (os.environ.get(e) for e in _ADMIN_URL_ENVS) if v), None)
    if url is None:
        if os.environ.get(_FALLBACK_FLAG, "").strip().lower() not in {"1", "true", "yes"}:
            raise BootstrapError(
                "No admin connection set (STELE_ADMIN_DATABASE_URL or STELE_DATABASE_URL). "
                "Point one at the admin identity that also runs migrations (CREATEROLE + "
                "owner of the target database). To use the local dev superuser fallback, "
                f"opt in explicitly with {_FALLBACK_FLAG}=1."
            )
        print(
            f"{_FALLBACK_FLAG} set; using the dev superuser fallback ({_DEV_FALLBACK_URL}). "
            "Never do this against a real database.",
            file=sys.stderr,
        )
        url = _DEV_FALLBACK_URL
    # SQLAlchemy-style "+psycopg" suffix isn't valid libpq; drop it if present.
    return url.replace("+psycopg", "", 1)


def _role_exists(conn: psycopg.Connection, role: str) -> bool:
    row = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    return row is not None


def _is_superuser(conn: psycopg.Connection) -> bool:
    row = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    return bool(row and row[0])


def create_roles(conn: psycopg.Connection) -> list[str]:
    """Create any missing roles from their password env. Returns the ones created.

    An existing role is left as-is — never re-passworded — so a re-deploy needs no
    secrets. A role that must be created but whose password env is unset fails the
    run closed, rather than minting a role with a guessable or empty password.
    """
    created: list[str] = []
    for role, env in ROLE_PASSWORD_ENV.items():
        if _role_exists(conn, role):
            print(f"  role {role} already exists; left untouched.")
            continue
        password = os.environ.get(env)
        if not password:
            raise BootstrapError(
                f"role {role!r} does not exist and {env} is not set. Provide every "
                "role's password on first bootstrap; re-deploys (roles already present) "
                "need none."
            )
        conn.execute(
            sql.SQL("CREATE ROLE {role} LOGIN PASSWORD {pw}").format(
                role=sql.Identifier(role), pw=sql.Literal(password)
            )
        )
        created.append(role)
        print(f"  created role {role}.")
    return created


def ensure_default_privilege_membership(conn: psycopg.Connection) -> None:
    """Let a non-superuser admin run the grant SQL's ALTER DEFAULT PRIVILEGES FOR ROLE.

    Setting default privileges *for* stele_etl requires the executing role to hold
    inherited privileges of stele_etl (has_privs_of_role), not bare membership: PG16
    auto-grants the creator membership but WITH INHERIT FALSE, which fails the check.
    So we re-grant WITH INHERIT TRUE when the inherited-privilege test (USAGE) is
    missing. A superuser bypasses this, so it's a no-op in dev/CI; in prod the admin
    created stele_etl and so can grant itself the membership.
    """
    if _is_superuser(conn):
        return
    has_usage = conn.execute("SELECT pg_has_role(current_user, 'stele_etl', 'USAGE')").fetchone()
    if has_usage and has_usage[0]:
        return
    conn.execute(
        sql.SQL("GRANT stele_etl TO {admin} WITH INHERIT TRUE").format(
            admin=sql.Identifier(conn.info.user)
        )
    )
    print(f"  granted stele_etl membership to {conn.info.user} (INHERIT TRUE).")


def apply_grants(conn: psycopg.Connection) -> None:
    """Apply the shared schemas + grants SQL verbatim."""
    conn.execute(_GRANTS_SQL.read_text())
    print(f"  applied {_GRANTS_SQL.name}.")


def bootstrap() -> int:
    conninfo = _conninfo()
    print("Bootstrapping roles, schemas, and grants...")
    # One transaction: roles, the self-membership, and the grants commit together
    # or not at all, so a half-applied bootstrap never leaves the DB in between.
    with psycopg.connect(conninfo) as conn, conn.transaction():
        created = create_roles(conn)
        ensure_default_privilege_membership(conn)
        apply_grants(conn)
    summary = f"{len(created)} role(s) created" if created else "no new roles"
    print(f"Bootstrap complete ({summary}); schemas and grants converged.")
    return 0


def main() -> int:
    try:
        return bootstrap()
    except BootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
