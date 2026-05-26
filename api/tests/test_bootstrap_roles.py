"""Tests for the managed-Postgres role/grant bootstrap runner (M7.3).

The runner reproduces, over an ordinary connection, the schema + role + grant
bootstrap that dev/CI get from a superuser applying the postgres-init SQL — so a
managed Postgres (no init hook, non-superuser owner) lands in the same place.

Unit tests (env / file only) always run. The DB-touching test exercises the real
idempotent path and needs an elevated connection via STELE_DATABASE_URL (or the
explicit dev-superuser opt-in); it skips cleanly when pointed only at a
least-privileged role. The full NON-superuser end-to-end — admin bootstraps +
migrates, then the least-privilege roles do real work — is the `prod-bootstrap-sim`
CI job, which a single-process pytest can't faithfully reproduce.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import psycopg
import pytest

# Mirror the provision-CLI integration test: opt into the dev-superuser fallback
# for the local case where STELE_DATABASE_URL is unset (CI sets it explicitly, so
# the flag is moot there). Set before the module reads the environment.
os.environ.setdefault("STELE_ALLOW_DEV_FALLBACK", "1")

# Load the standalone runner (scripts/ is not a package) by path.
_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_roles.py"
_spec = importlib.util.spec_from_file_location("bootstrap_roles", _PATH)
assert _spec is not None
assert _spec.loader is not None
br: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)


# --- unit: connection resolution (mirrors the provisioning CLI's contract) -------


def test_conninfo_requires_url_without_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STELE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("STELE_DATABASE_URL", raising=False)
    monkeypatch.delenv("STELE_ALLOW_DEV_FALLBACK", raising=False)
    with pytest.raises(br.BootstrapError):
        br._conninfo()


def test_conninfo_dev_fallback_is_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STELE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("STELE_DATABASE_URL", raising=False)
    monkeypatch.setenv("STELE_ALLOW_DEV_FALLBACK", "1")
    assert br._conninfo() == br._DEV_FALLBACK_URL


def test_conninfo_strips_sqlalchemy_driver_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STELE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("STELE_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    assert br._conninfo() == "postgresql://u:p@h:5432/db"


def test_conninfo_prefers_admin_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # On a Railway-style deploy the web service carries both: STELE_DATABASE_URL is
    # the least-privilege stele_api connection the web process uses, and the
    # pre-deploy migrate must instead reach the admin identity. The admin var wins.
    monkeypatch.setenv("STELE_DATABASE_URL", "postgresql://stele_api:p@h:5432/db")
    monkeypatch.setenv("STELE_ADMIN_DATABASE_URL", "postgresql://admin:s@h:5432/db")
    assert br._conninfo() == "postgresql://admin:s@h:5432/db"


def test_conninfo_falls_back_to_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Dev/CI set only STELE_DATABASE_URL (one admin identity); the fallback keeps
    # bootstrap-er == migrator there without any new env.
    monkeypatch.delenv("STELE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("STELE_DATABASE_URL", "postgresql://admin:s@h:5432/db")
    assert br._conninfo() == "postgresql://admin:s@h:5432/db"


# --- unit: fail-closed when a role must be created but its password is absent ----


class _FakeConn:
    """Stands in for a connection; create_roles only calls execute after the
    password check, so the missing-password branch never touches it."""

    def execute(self, *_args: object, **_kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("execute must not run when a password is missing")


def test_create_roles_fails_closed_on_missing_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pretend every managed role is absent, and clear every password env: the
    # first missing role must abort before any CREATE ROLE, never minting a role
    # with an empty/guessable password.
    monkeypatch.setattr(br, "_role_exists", lambda _conn, _role: False)
    for env in br.ROLE_PASSWORD_ENV.values():
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(br.BootstrapError, match="does not exist"):
        br.create_roles(_FakeConn())


# --- unit: the shared grant SQL must stay psql-meta-free so psycopg can run it ---


def test_shared_grant_sql_has_no_psql_meta_commands() -> None:
    # psql meta-commands (\c, \set, ...) aren't valid over psycopg; the prod runner
    # executes 02-schemas-and-grants.sql verbatim, so a stray meta-command would
    # only surface in prod. Pin the constraint here.
    lines = br._GRANTS_SQL.read_text().splitlines()
    offenders = [ln for ln in lines if ln.lstrip().startswith("\\")]
    assert offenders == [], f"psql meta-commands in shared grant SQL: {offenders}"


# --- integration: idempotent bootstrap against a real DB --------------------------


def _can_bootstrap() -> bool:
    try:
        conninfo = br._conninfo()
    except br.BootstrapError:
        return False
    try:
        with psycopg.connect(conninfo) as conn:
            row = conn.execute(
                "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
    except psycopg.OperationalError:
        return False
    return bool(row and row[0])


_needs_admin = pytest.mark.skipif(
    not _can_bootstrap(),
    reason="bootstrap needs a superuser/CREATEROLE connection",
)


@_needs_admin
def test_bootstrap_is_idempotent_and_converges_grants(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The four roles already exist in dev/CI, so this run creates none and just
    # re-applies the (idempotent) schemas + grants. Running twice must stay green.
    assert br.bootstrap() == 0
    first = capsys.readouterr().out
    assert "applied 02-schemas-and-grants.sql" in first
    assert "no new roles" in first
    assert br.bootstrap() == 0
