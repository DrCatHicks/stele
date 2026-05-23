"""Provision, rotate, and revoke analyst/reviewer database credentials.

The out-of-band, privileged half of design doc §3.10. Analysts and reviewers
query Postgres *directly* (marts / pii); they don't go through the API. An admin
gives each of them their own login role — a member of the shared ``stele_analyst``
/ ``stele_pii_reviewer`` group role (§3.3) — so access is per-person, revocable
per-person, and audited. The ``CREATE ROLE`` / ``GRANT`` lives here, in a tool an
operator runs deliberately, never behind the public ``stele_api`` connection
(which has no CREATEROLE and no business minting Postgres logins).

Each action records itself in ``app.db_credential_grants`` (the registry the
admin-only GET /admin/db-credentials endpoint reads) in the *same transaction* as
the DDL, so role and audit row commit together or not at all. Passwords are shown
once on the controlling terminal (``/dev/tty``, never stdout — see
``_open_secret_sink``) and never stored.

The login role is created NOINHERIT: connecting with it grants nothing until the
user runs ``SET ROLE <group>``, so an idle or mis-configured connection is
privilege-less by default. The printed instructions spell this out.

Connection. Uses STELE_PROVISION_DATABASE_URL — a role with CREATEROLE and ADMIN
OPTION on the group roles (or a superuser). This is *not* the app's stele_api URL;
keeping role-DDL privilege out of the request path is the whole point. The var is
required: rather than guess, an unset URL fails the run, so a missing/misspelled
var can't silently provision into the wrong database. For local dev against the
container superuser, opt into the fallback explicitly with STELE_ALLOW_DEV_FALLBACK=1.

Examples:
    uv run python scripts/provision_db_credential.py provision \
        --access analyst --subject jdoe@example.com
    uv run python scripts/provision_db_credential.py list
    uv run python scripts/provision_db_credential.py rotate stele_analyst_jdoe_a1b2
    uv run python scripts/provision_db_credential.py revoke stele_analyst_jdoe_a1b2
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import TextIO

import psycopg
from psycopg import sql

from api.auth import provisioning

_DEV_FALLBACK_URL = "postgresql://stele_dev:dev@localhost:5432/stele"
_FALLBACK_FLAG = "STELE_ALLOW_DEV_FALLBACK"
_SECRET_SINK_ENV = "STELE_PROVISION_SECRET_SINK"


def _open_secret_sink() -> TextIO:
    """Open the destination for one-time secrets — the controlling terminal, not stdout.

    Defaults to ``/dev/tty``, which refers to the real terminal regardless of how
    stdout is redirected. So a redirected or captured stdout (a wrapper script, a CI
    job, ``provision … > file``) receives only the non-secret confirmation, never the
    password — and with no terminal the command fails closed before any role is
    created, rather than emitting a credential into a capturable stream. Override the
    destination with ``STELE_PROVISION_SECRET_SINK`` for non-interactive use (tests).

    Opened up front so provisioning aborts *before* the DDL when there's nowhere safe
    to show the secret, never leaving a role whose password no one saw.
    """
    sink = os.environ.get(_SECRET_SINK_ENV, "/dev/tty")
    try:
        return open(sink, "w")  # caller closes; device/terminal path, not a project file
    except OSError as exc:
        raise provisioning.ProvisioningError(
            f"cannot open {sink!r} to show the generated secret: {exc}. Run in an "
            f"interactive terminal, or set {_SECRET_SINK_ENV} to a writable path."
        ) from exc


def _conninfo() -> str:
    """Resolve the elevated libpq conninfo, stripping any SQLAlchemy driver tag.

    This is a privileged tool that mints Postgres roles, so it refuses to *guess*
    where to connect: ``STELE_PROVISION_DATABASE_URL`` must be set, or the run
    fails. Silently defaulting to a hard-coded dev superuser would make a missing
    or misspelled env var provision into the wrong database. The dev convenience
    is still available, but only as a deliberate opt-in (``STELE_ALLOW_DEV_FALLBACK=1``).
    """
    url = os.environ.get("STELE_PROVISION_DATABASE_URL")
    if url is None:
        if os.environ.get(_FALLBACK_FLAG, "").strip().lower() not in {"1", "true", "yes"}:
            raise provisioning.ProvisioningError(
                "STELE_PROVISION_DATABASE_URL is not set. Point it at a role with CREATEROLE "
                "and ADMIN OPTION on the group roles (a superuser in dev). To use the local "
                f"dev superuser fallback, opt in explicitly with {_FALLBACK_FLAG}=1."
            )
        print(
            f"{_FALLBACK_FLAG} set; using the dev superuser fallback "
            f"({_DEV_FALLBACK_URL}). Never do this against a real database.",
            file=sys.stderr,
        )
        url = _DEV_FALLBACK_URL
    # SQLAlchemy-style "+psycopg" suffix isn't valid libpq; drop it if present.
    return url.replace("+psycopg", "", 1)


def _role_exists(conn: psycopg.Connection, role: str) -> bool:
    row = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    return row is not None


def _active_credential(conn: psycopg.Connection, subject: str, access: str) -> str | None:
    """login_role of an existing active credential for this subject+access, if any."""
    row = conn.execute(
        "SELECT login_role FROM app.db_credential_grants "
        "WHERE subject_label = %s AND access = %s AND status = 'active'",
        (subject, access),
    ).fetchone()
    return row[0] if row else None


def cmd_provision(args: argparse.Namespace) -> int:
    access: str = args.access
    subject = provisioning.normalize_subject(args.subject)
    group_role = provisioning.group_role_for(access)
    login_role = provisioning.derive_login_role(access, subject, provisioning.random_suffix())
    password = provisioning.generate_password()

    # Fail closed before any DDL if there's nowhere safe to show the password.
    secret_out = _open_secret_sink()
    try:
        with psycopg.connect(_conninfo()) as conn:
            if not _role_exists(conn, group_role):
                print(
                    f"Group role {group_role!r} does not exist; run the init SQL first.",
                    file=sys.stderr,
                )
                return 1
            existing = _active_credential(conn, subject, access)
            if existing is not None:
                print(
                    f"{subject!r} already has an active {access} credential ({existing}). "
                    "Revoke it before provisioning another.",
                    file=sys.stderr,
                )
                return 1
            # Role DDL and the audit row commit together (Postgres role DDL is
            # transactional), so we never leave a role without its registry record.
            with conn.transaction():
                conn.execute(
                    sql.SQL("CREATE ROLE {login} LOGIN PASSWORD {pw} NOINHERIT").format(
                        login=sql.Identifier(login_role), pw=sql.Literal(password)
                    )
                )
                conn.execute(
                    sql.SQL("GRANT {group} TO {login}").format(
                        group=sql.Identifier(group_role), login=sql.Identifier(login_role)
                    )
                )
                conn.execute(
                    "INSERT INTO app.db_credential_grants "
                    "(subject_label, access, login_role, status) VALUES (%s, %s, %s, 'active')",
                    (subject, access, login_role),
                )
        # Success: deliver the password to the terminal only.
        secret_out.write(f"password for {login_role}: {password}\n")
    finally:
        secret_out.close()

    print(f"Provisioned {access} credential for {subject}.")
    print(f"  login role : {login_role}")
    print(f"  group role : {group_role}")
    print("  password written to your terminal (not stdout); record it now — not stored.")
    print(f"  the user connects, then runs:  SET ROLE {group_role};")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    login_role: str = args.login_role
    with psycopg.connect(_conninfo()) as conn:
        row = conn.execute(
            "SELECT status FROM app.db_credential_grants WHERE login_role = %s",
            (login_role,),
        ).fetchone()
        if row is None:
            print(f"No registry entry for login role {login_role!r}.", file=sys.stderr)
            return 1
        if row[0] == "revoked":
            print(f"{login_role} is already revoked; nothing to do.")
            return 0
        with conn.transaction():
            # DROP ROLE removes membership implicitly, but revoking first keeps the
            # intent explicit and is harmless if the role was already dropped by hand.
            if _role_exists(conn, login_role):
                conn.execute(sql.SQL("DROP ROLE {login}").format(login=sql.Identifier(login_role)))
            conn.execute(
                "UPDATE app.db_credential_grants "
                "SET status = 'revoked', revoked_at = now() WHERE login_role = %s",
                (login_role,),
            )
    print(f"Revoked {login_role}: role dropped, registry marked revoked.")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    login_role: str = args.login_role
    password = provisioning.generate_password()
    # Fail closed before changing the password if there's nowhere safe to show it.
    secret_out = _open_secret_sink()
    try:
        with psycopg.connect(_conninfo()) as conn:
            row = conn.execute(
                "SELECT status FROM app.db_credential_grants WHERE login_role = %s",
                (login_role,),
            ).fetchone()
            if row is None or row[0] != "active":
                print(f"No active credential for login role {login_role!r}.", file=sys.stderr)
                return 1
            with conn.transaction():
                conn.execute(
                    sql.SQL("ALTER ROLE {login} PASSWORD {pw}").format(
                        login=sql.Identifier(login_role), pw=sql.Literal(password)
                    )
                )
                conn.execute(
                    "UPDATE app.db_credential_grants SET rotated_at = now() WHERE login_role = %s",
                    (login_role,),
                )
        secret_out.write(f"new password for {login_role}: {password}\n")
    finally:
        secret_out.close()
    print(f"Rotated password for {login_role}.")
    print("  new password written to your terminal (not stdout); record it now — not stored.")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    with psycopg.connect(_conninfo()) as conn:
        rows = conn.execute(
            "SELECT id, subject_label, access, login_role, status, created_at, revoked_at "
            "FROM app.db_credential_grants ORDER BY created_at DESC"
        ).fetchall()
    if not rows:
        print("No credentials provisioned.")
        return 0
    for r in rows:
        id_, subject, access, login_role, status, created_at, revoked_at = r
        when = f"revoked {revoked_at:%Y-%m-%d}" if revoked_at else f"since {created_at:%Y-%m-%d}"
        print(f"  [{id_}] {status:<7} {access:<8} {login_role:<32} {subject} ({when})")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prov = sub.add_parser("provision", help="create a per-person analyst/reviewer credential")
    p_prov.add_argument("--access", required=True, choices=sorted(provisioning.VALID_ACCESS))
    p_prov.add_argument(
        "--subject", required=True, help="who the credential is for (e.g. an email)"
    )
    p_prov.set_defaults(func=cmd_provision)

    p_rev = sub.add_parser("revoke", help="drop a credential's role and mark it revoked")
    p_rev.add_argument("login_role", help="the login role to revoke")
    p_rev.set_defaults(func=cmd_revoke)

    p_rot = sub.add_parser("rotate", help="set a new password on an active credential")
    p_rot.add_argument("login_role", help="the login role to rotate")
    p_rot.set_defaults(func=cmd_rotate)

    p_list = sub.add_parser("list", help="show the credential registry")
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except provisioning.ProvisioningError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
