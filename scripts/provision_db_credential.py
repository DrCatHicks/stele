"""Provision, rotate, and revoke analyst/reviewer database credentials.

The out-of-band, privileged half of design doc §3.10. Analysts and reviewers
query Postgres *directly* (marts / pii); they don't go through the API. An admin
gives each of them their own login role — a member of the shared ``stele_analyst``
/ ``stele_pii_reviewer`` group role (§3.3) — so access is per-person, revocable
per-person, and audited. The ``CREATE ROLE`` / ``GRANT`` lives in
``api.auth.provisioning`` (shared verbatim with the UI-driven worker,
``api.provisioning.worker``), never behind the public ``stele_api`` connection
(which has no CREATEROLE and no business minting Postgres logins).

Each action records itself in ``app.db_credential_grants`` (the registry the
admin-only GET /admin/db-credentials endpoint reads) in the *same transaction* as
the DDL, so role and audit row commit together or not at all. Passwords are shown
once on the controlling terminal (``/dev/tty``, never stdout — see
``_open_secret_sink``) and never stored. (The UI flow instead delivers the
password encrypted, once, to the recipient's own session; this CLI is the
break-glass path and the operator tool for the analyst tier.)

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

from api.auth import provisioning

# Re-exported from the shared module so the connection/fallback logic lives in one
# place (api.auth.provisioning, shared with the worker); the CLI keeps the names.
_DEV_FALLBACK_URL = provisioning._DEV_FALLBACK_URL
_FALLBACK_FLAG = provisioning._FALLBACK_FLAG
_role_exists = provisioning.role_exists
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
    """Resolve the elevated libpq conninfo (delegates to the shared resolver).

    Warns on stderr when the dev-superuser fallback is in play, so an operator
    can't mistake it for a real connection. Resolution + driver-tag stripping live
    in ``provisioning.provision_conninfo``.
    """
    if os.environ.get(provisioning._PROVISION_URL_ENV) is None and os.environ.get(
        _FALLBACK_FLAG, ""
    ).strip().lower() in {"1", "true", "yes"}:
        print(
            f"{_FALLBACK_FLAG} set; using the dev superuser fallback "
            f"({_DEV_FALLBACK_URL}). Never do this against a real database.",
            file=sys.stderr,
        )
    return provisioning.provision_conninfo()


def cmd_provision(args: argparse.Namespace) -> int:
    access: str = args.access
    subject = provisioning.normalize_subject(args.subject)
    group_role = provisioning.group_role_for(access)

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
            existing = provisioning.active_credential(conn, subject, access)
            if existing is not None:
                print(
                    f"{subject!r} already has an active {access} credential ({existing}). "
                    "Revoke it before provisioning another.",
                    file=sys.stderr,
                )
                return 1
            with conn.transaction():
                login_role, password = provisioning.provision_in_tx(conn, access, subject)
        # Success: deliver the password to the terminal sink only. The sink is
        # /dev/tty by default (see _open_secret_sink) — a terminal device, not
        # persistent storage — so CodeQL's clear-text-storage alert here is a false
        # positive (dismissed on the PR). The operator must see the one-time password
        # somehow; the terminal is the most ephemeral safe channel.
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
            provisioning.revoke_in_tx(conn, login_role)
    print(f"Revoked {login_role}: role dropped, registry marked revoked.")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    login_role: str = args.login_role
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
                password = provisioning.rotate_in_tx(conn, login_role)
        # Terminal sink (/dev/tty), not storage — clear-text-storage is a false
        # positive here, same as in cmd_provision (dismissed on the PR).
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
