"""Rotate a deployment role's Postgres password (M7.6).

The deployment-secret counterpart to the M3.5 provisioning CLI. That tool rotates
*per-user* analyst/reviewer login roles tracked in ``app.db_credential_grants``;
this one rotates the four shared deployment roles the services authenticate with
(``stele_api`` / ``stele_etl`` / ``stele_analyst`` / ``stele_pii_reviewer``) and
the admin/owner role — the secrets the OpenTofu module generates.

Why a separate step at all. ``bootstrap_roles.py`` creates each role with its
password exactly once and never re-passwords an existing one (M7.3, so re-deploys
need no secrets). So regenerating the tofu secret alone would update the services'
*connection strings* without changing the live Postgres password — auth would
break. Rotation is therefore two moves, and this script is the first:

  1. ``ALTER ROLE <role> WITH PASSWORD <new>`` here (the live role changes now);
  2. feed the new password back to the deploy so the connection strings match —
     on Railway, set the matching ``*_password_override`` tofu variable and apply
     (see docs/verification/m7.6-demo-to-prod.md § Secret rotation).

The new password is shown ONCE on the controlling terminal (``/dev/tty``), never
on stdout, so a redirected/captured stdout (a wrapper, a CI job, ``… > file``)
gets only the non-secret confirmation. With no terminal the run fails closed
*before* the ALTER, so a role is never left with a password no one captured.

Connection. Uses the admin identity, ``STELE_ADMIN_DATABASE_URL`` if set else
``STELE_DATABASE_URL`` — the same precedence ``bootstrap_roles.py`` and Alembic
resolve, so the role you can bootstrap with is the role you can rotate with. The
var is required (a privileged tool must not guess where to connect); for local dev
against the container superuser, opt in with ``STELE_ALLOW_DEV_FALLBACK=1``.

Examples:
    STELE_ADMIN_DATABASE_URL=... uv run python scripts/rotate_role_password.py stele_analyst
    # supply a specific value instead of generating one (e.g. from a password manager):
    STELE_NEW_PASSWORD=... uv run python scripts/rotate_role_password.py stele_api
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import string
import sys
from typing import TextIO

import psycopg
from psycopg import sql

_DEV_FALLBACK_URL = "postgresql://stele_dev:dev@localhost:5432/stele"
_FALLBACK_FLAG = "STELE_ALLOW_DEV_FALLBACK"
_SECRET_SINK_ENV = "STELE_ROTATE_SECRET_SINK"
_NEW_PASSWORD_ENV = "STELE_NEW_PASSWORD"

# Admin connection precedence, matching bootstrap_roles.py / alembic env.py.
_ADMIN_URL_ENVS = ("STELE_ADMIN_DATABASE_URL", "STELE_DATABASE_URL")

# The deployment roles whose passwords the tofu module generates. The admin/owner
# role's name varies by platform (postgres on Railway), so it isn't listed; pass it
# by name and the existence check + identifier regex still apply.
DEPLOYMENT_ROLES = ("stele_api", "stele_etl", "stele_analyst", "stele_pii_reviewer")

# Postgres role names we will touch must be plain identifiers. sql.Identifier
# already neutralizes injection; this is a clarity/typo guard so a stray argument
# can't name something unexpected.
_ROLE_RE = re.compile(r"\A[a-z_][a-z0-9_]*\Z")

# Alphanumeric to match the OpenTofu module's random_password(special = false):
# the value lands in a connection string, so it must need no URL-escaping.
_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_PASSWORD_LENGTH = 32


class RotationError(Exception):
    """A precondition for rotating is missing (URL, role, terminal)."""


def generate_password() -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))


def _conninfo() -> str:
    """Resolve the admin libpq conninfo, stripping any SQLAlchemy driver tag.

    Prefers STELE_ADMIN_DATABASE_URL, then STELE_DATABASE_URL. Required: a
    privileged tool must not silently default to a hard-coded dev superuser, or a
    missing/misspelled env var would rotate a password in the wrong database. The
    dev superuser fallback is a deliberate opt-in (STELE_ALLOW_DEV_FALLBACK=1).
    """
    url = next((v for v in (os.environ.get(e) for e in _ADMIN_URL_ENVS) if v), None)
    if url is None:
        if os.environ.get(_FALLBACK_FLAG, "").strip().lower() not in {"1", "true", "yes"}:
            raise RotationError(
                "No admin connection set (STELE_ADMIN_DATABASE_URL or STELE_DATABASE_URL). "
                "Point one at the admin identity that owns the roles (CREATEROLE or "
                f"superuser). To use the local dev superuser fallback, opt in explicitly "
                f"with {_FALLBACK_FLAG}=1."
            )
        print(
            f"{_FALLBACK_FLAG} set; using the dev superuser fallback ({_DEV_FALLBACK_URL}). "
            "Never do this against a real database.",
            file=sys.stderr,
        )
        url = _DEV_FALLBACK_URL
    return url.replace("+psycopg", "", 1)


def _open_secret_sink() -> TextIO:
    """Open the destination for the one-time secret — the terminal, not stdout.

    Defaults to ``/dev/tty`` (the real terminal regardless of stdout redirection),
    so a captured stdout never receives the new password and a non-interactive run
    fails closed before the ALTER. Override with ``STELE_ROTATE_SECRET_SINK`` for
    non-interactive use (tests). Opened up front so we never change a password we
    then can't show.
    """
    sink = os.environ.get(_SECRET_SINK_ENV, "/dev/tty")
    try:
        return open(sink, "w")  # caller closes; terminal/device path, not a project file
    except OSError as exc:
        raise RotationError(
            f"cannot open {sink!r} to show the new secret: {exc}. Run in an interactive "
            f"terminal, or set {_SECRET_SINK_ENV} to a writable path."
        ) from exc


def _role_exists(conn: psycopg.Connection, role: str) -> bool:
    row = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    return row is not None


def rotate(role: str, password: str, conninfo: str) -> None:
    """ALTER the role's password. Raises RotationError if the role doesn't exist."""
    with psycopg.connect(conninfo) as conn:
        if not _role_exists(conn, role):
            raise RotationError(
                f"role {role!r} does not exist on this database. Rotatable deployment "
                f"roles: {', '.join(DEPLOYMENT_ROLES)} (plus the admin/owner role by name)."
            )
        # ALTER ROLE PASSWORD is its own statement (no enclosing BEGIN needed); the
        # identifier is quoted and the password passed as a literal, like the
        # provisioning CLI's rotate.
        conn.execute(
            sql.SQL("ALTER ROLE {role} WITH PASSWORD {pw}").format(
                role=sql.Identifier(role), pw=sql.Literal(password)
            )
        )


def cmd_rotate(args: argparse.Namespace) -> int:
    role: str = args.role
    if not _ROLE_RE.match(role):
        raise RotationError(
            f"{role!r} is not a plain role identifier (expected lowercase letters, digits, "
            "underscore)."
        )
    if role not in DEPLOYMENT_ROLES:
        # Not fatal — the admin/owner role is rotatable too and its name varies —
        # but worth flagging so a typo'd stele_* role doesn't sail through.
        print(
            f"note: {role!r} is not one of the standard deployment roles "
            f"({', '.join(DEPLOYMENT_ROLES)}); rotating it anyway if it exists.",
            file=sys.stderr,
        )

    supplied = os.environ.get(_NEW_PASSWORD_ENV)
    password = supplied if supplied else generate_password()

    # Fail closed before the ALTER if there's nowhere safe to show the secret.
    secret_out = _open_secret_sink()
    try:
        rotate(role, password, _conninfo())
        # Terminal sink (/dev/tty), not storage — CodeQL clear-text-storage here is
        # a false positive, same as the provisioning CLI (dismissed on the PR). The
        # operator must see the one-time password; the terminal is the safest channel.
        origin = "supplied" if supplied else "generated"
        secret_out.write(f"new password for {role} ({origin}): {password}\n")
    finally:
        secret_out.close()

    print(f"Rotated the live Postgres password for {role}.")
    print("  new password written to your terminal (not stdout); record it now — not stored.")
    print("  next: set the matching *_password_override tofu variable to this value and apply,")
    print("        so the services' connection strings track the live role, then redeploy.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "role",
        help=(
            "the role to rotate: one of "
            f"{', '.join(DEPLOYMENT_ROLES)}, or the admin/owner role by name"
        ),
    )
    parser.set_defaults(func=cmd_rotate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except RotationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
