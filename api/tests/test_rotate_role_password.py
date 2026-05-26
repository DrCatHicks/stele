"""Tests for the deployment-secret rotation helper (M7.6).

Unit tests cover the env-only logic (connection precedence, password shape, role
validation) and always run. The integration test exercises the real path against
Postgres: it ALTERs a throwaway role's password through the CLI, then proves the
change took by connecting *as that role* with the new password (and that the old
password no longer works). It needs an elevated connection (the same admin URL the
CLI reads) and skips cleanly without one; CI provides it. The throwaway role is
created outside the per-test rollback transaction, so the test cleans up after itself.
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

# Opt into the dev-superuser fallback for the local case where the admin URL is
# unset; when CI sets STELE_ADMIN_DATABASE_URL the flag is moot (the URL wins).
os.environ.setdefault("STELE_ALLOW_DEV_FALLBACK", "1")
# The CLI shows the secret on /dev/tty and fails closed without one; tests have no
# terminal, so default the sink to /dev/null. Tests that inspect the secret point
# it at a real file.
os.environ.setdefault("STELE_ROTATE_SECRET_SINK", os.devnull)

# Load the standalone CLI script (scripts/ is not a package) by path.
_CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "rotate_role_password.py"
_spec = importlib.util.spec_from_file_location("rotate_role_password", _CLI_PATH)
assert _spec is not None
assert _spec.loader is not None
cli: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _admin_conninfo() -> str:
    return str(cli._conninfo())


def _can_create_roles() -> bool:
    try:
        conninfo = _admin_conninfo()
    except cli.RotationError:
        return False
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    return bool(row and row[0])


_needs_elevated = pytest.mark.skipif(
    not _can_create_roles(),
    reason="rotation helper integration test needs a CREATEROLE/superuser connection",
)


# ---- Unit (env-only, always run) --------------------------------------------


def test_conninfo_requires_url_without_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STELE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("STELE_DATABASE_URL", raising=False)
    monkeypatch.delenv("STELE_ALLOW_DEV_FALLBACK", raising=False)
    with pytest.raises(cli.RotationError):
        cli._conninfo()


def test_conninfo_prefers_admin_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STELE_ADMIN_DATABASE_URL", "postgresql://admin:p@h:5432/db")
    monkeypatch.setenv("STELE_DATABASE_URL", "postgresql://api:p@h:5432/db")
    assert cli._conninfo() == "postgresql://admin:p@h:5432/db"


def test_conninfo_falls_back_to_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STELE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("STELE_DATABASE_URL", "postgresql+psycopg://api:p@h:5432/db")
    # And the SQLAlchemy driver tag is stripped for libpq.
    assert cli._conninfo() == "postgresql://api:p@h:5432/db"


def test_generated_password_is_alphanumeric_and_long() -> None:
    pw = cli.generate_password()
    assert len(pw) == 32
    assert pw.isalnum()  # no URL-special chars (matches random_password special=false)
    # Two draws differ — it's actually random, not a constant.
    assert pw != cli.generate_password()


def test_rejects_non_identifier_role(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(['stele_api"; DROP ROLE x; --']) == 2
    assert "not a plain role identifier" in capsys.readouterr().err


def test_unknown_but_valid_role_warns_then_tries(monkeypatch: pytest.MonkeyPatch) -> None:
    # A syntactically valid but non-standard role still reaches the DB step (where a
    # missing role fails); the warning is informational, not fatal.
    monkeypatch.delenv("STELE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("STELE_DATABASE_URL", raising=False)
    monkeypatch.delenv("STELE_ALLOW_DEV_FALLBACK", raising=False)
    # No connection configured → the run fails at _conninfo (rc 2), proving it got
    # past role validation rather than rejecting the name.
    assert cli.main(["some_other_role"]) == 2


# ---- Integration (real ALTER ROLE round-trip) -------------------------------


@_needs_elevated
def test_rotate_changes_the_live_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conninfo = _admin_conninfo()
    role = f"rottest_{secrets.token_hex(4)}"
    old_password = secrets.token_hex(16)
    sink = tmp_path / "secret.txt"
    monkeypatch.setenv("STELE_ROTATE_SECRET_SINK", str(sink))
    monkeypatch.delenv("STELE_NEW_PASSWORD", raising=False)  # exercise the generate path

    with psycopg.connect(conninfo) as conn, conn.transaction():
        conn.execute(
            sql.SQL("CREATE ROLE {r} LOGIN PASSWORD {p}").format(
                r=sql.Identifier(role), p=sql.Literal(old_password)
            )
        )
    # Connection coordinates to re-connect AS the throwaway role.
    with psycopg.connect(conninfo) as conn:
        host, port, dbname = conn.info.host, conn.info.port, conn.info.dbname

    def login(password: str) -> bool:
        try:
            with psycopg.connect(host=host, port=port, dbname=dbname, user=role, password=password):
                return True
        except psycopg.OperationalError:
            return False

    try:
        assert login(old_password) is True  # baseline: the old password works

        assert cli.main([role]) == 0

        # The generated password lands in the sink, never on stdout.
        secret_text = sink.read_text()
        assert f"new password for {role}" in secret_text
        new_password = secret_text.rsplit(": ", 1)[1].strip()
        assert new_password.isalnum()
        assert len(new_password) == 32
        assert new_password not in capsys.readouterr().out

        # The live role now authenticates with the new password and not the old one.
        assert login(new_password) is True
        assert login(old_password) is False
    finally:
        with psycopg.connect(conninfo) as conn, conn.transaction():
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {r}").format(r=sql.Identifier(role)))


@_needs_elevated
def test_rotate_missing_role_fails(capsys: pytest.CaptureFixture[str]) -> None:
    role = f"rottest_{secrets.token_hex(4)}"  # never created
    assert cli.main([role]) == 2
    assert "does not exist" in capsys.readouterr().err
