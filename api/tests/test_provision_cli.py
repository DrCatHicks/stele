"""Integration test for the provisioning CLI (M3.5).

Exercises the privileged path end-to-end against a real Postgres: provision
creates a NOINHERIT login role that's a member of the group role and writes an
active registry row; revoke drops the role and marks the row revoked — both in
one transaction.

Requires an elevated connection (CREATEROLE/superuser) via the same
STELE_PROVISION_DATABASE_URL the CLI reads. Skips cleanly when the connection
can't create roles — e.g. a pytest run pointed only at the least-privileged
stele_api role. CI sets the elevated URL so this runs there. The test creates
real roles outside the per-test rollback transaction, so it cleans up after itself.
"""

from __future__ import annotations

import importlib.util
import os
import secrets
from pathlib import Path
from types import ModuleType

import psycopg
import pytest
from psycopg import sql

# This is a dev/CI integration test, so opt into the CLI's dev-superuser fallback
# for the local case where STELE_PROVISION_DATABASE_URL is unset. When CI sets the
# URL explicitly the flag is moot (the URL wins). Set before the CLI reads it.
os.environ.setdefault("STELE_ALLOW_DEV_FALLBACK", "1")

# Load the standalone CLI script (scripts/ is not a package) by path.
_CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "provision_db_credential.py"
_spec = importlib.util.spec_from_file_location("provision_db_credential", _CLI_PATH)
assert _spec is not None
assert _spec.loader is not None
cli: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _elevated_conninfo() -> str:
    return str(cli._conninfo())


def _can_create_roles() -> bool:
    """True only if we can resolve an elevated conn that can create roles."""
    try:
        conninfo = _elevated_conninfo()
    except cli.provisioning.ProvisioningError:
        return False
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    return bool(row and row[0])


# Only the DB-touching tests need an elevated connection; the _conninfo unit
# tests below are env-only and must always run, so this gates per-test rather
# than the whole module.
_needs_elevated = pytest.mark.skipif(
    not _can_create_roles(),
    reason="provisioning CLI needs a CREATEROLE/superuser connection",
)


def test_conninfo_requires_url_without_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    # The privileged tool must not guess where to connect: no URL, no opt-in → fail.
    monkeypatch.delenv("STELE_PROVISION_DATABASE_URL", raising=False)
    monkeypatch.delenv("STELE_ALLOW_DEV_FALLBACK", raising=False)
    with pytest.raises(cli.provisioning.ProvisioningError):
        cli._conninfo()


def test_conninfo_dev_fallback_is_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STELE_PROVISION_DATABASE_URL", raising=False)
    monkeypatch.setenv("STELE_ALLOW_DEV_FALLBACK", "1")
    assert cli._conninfo() == cli._DEV_FALLBACK_URL


def test_conninfo_strips_sqlalchemy_driver_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STELE_PROVISION_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    assert cli._conninfo() == "postgresql://u:p@h:5432/db"


@_needs_elevated
def test_provision_then_revoke_roundtrip() -> None:
    conninfo = _elevated_conninfo()
    subject = f"clitest_{secrets.token_hex(4)}@example.com"
    login_role: str | None = None
    try:
        rc = cli.main(["provision", "--access", "analyst", "--subject", subject])
        assert rc == 0

        with psycopg.connect(conninfo) as conn:
            row = conn.execute(
                "SELECT login_role FROM app.db_credential_grants "
                "WHERE subject_label = %s AND status = 'active'",
                (subject,),
            ).fetchone()
            assert row is not None
            login_role = row[0]
            # Role exists, is NOINHERIT, and is a member of the analyst group role.
            attrs = conn.execute(
                "SELECT rolcanlogin, rolinherit FROM pg_roles WHERE rolname = %s",
                (login_role,),
            ).fetchone()
            assert attrs == (True, False)
            member = conn.execute(
                "SELECT 1 FROM pg_auth_members m "
                "JOIN pg_roles u ON u.oid = m.member "
                "JOIN pg_roles g ON g.oid = m.roleid "
                "WHERE u.rolname = %s AND g.rolname = 'stele_analyst'",
                (login_role,),
            ).fetchone()
            assert member is not None

        rc = cli.main(["revoke", login_role])
        assert rc == 0

        with psycopg.connect(conninfo) as conn:
            assert not cli._role_exists(conn, login_role)
            status_row = conn.execute(
                "SELECT status FROM app.db_credential_grants WHERE login_role = %s",
                (login_role,),
            ).fetchone()
            assert status_row is not None
            assert status_row[0] == "revoked"
    finally:
        # The CLI commits real changes outside any test transaction; tidy up.
        with psycopg.connect(conninfo) as conn:
            if login_role and cli._role_exists(conn, login_role):
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login_role)))
            conn.execute(
                "DELETE FROM app.db_credential_grants WHERE subject_label = %s", (subject,)
            )


@_needs_elevated
def test_provision_rejects_duplicate_active_subject() -> None:
    conninfo = _elevated_conninfo()
    subject = f"clitest_{secrets.token_hex(4)}@example.com"
    try:
        assert cli.main(["provision", "--access", "analyst", "--subject", subject]) == 0
        # A second active analyst credential for the same subject must be refused.
        assert cli.main(["provision", "--access", "analyst", "--subject", subject]) == 1
    finally:
        with psycopg.connect(conninfo) as conn:
            rows = conn.execute(
                "SELECT login_role FROM app.db_credential_grants WHERE subject_label = %s",
                (subject,),
            ).fetchall()
            for (login_role,) in rows:
                if cli._role_exists(conn, login_role):
                    conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login_role)))
            conn.execute(
                "DELETE FROM app.db_credential_grants WHERE subject_label = %s", (subject,)
            )
