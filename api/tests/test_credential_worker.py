"""Integration tests for the provisioning worker (api.credential_worker).

The worker drains app.provision_requests over an elevated connection and performs
the real role DDL, so these exercise the privileged path end-to-end against a real
Postgres — like test_provision_cli.py. They need a CREATEROLE/superuser connection
(the same STELE_PROVISION_DATABASE_URL the worker reads) and skip cleanly without
one; CI provides it. Roles/users are created outside the per-test rollback
transaction, so each test cleans up after itself.

One test (missing required fields) fails *before* any DDL, so it needs only a
plain connection and runs unconditionally.
"""

from __future__ import annotations

import os
import secrets

import psycopg
import pytest
from psycopg import sql

from api.auth import provisioning, secret_delivery

# Opt into the dev-superuser fallback for the local case (CI sets the URL).
os.environ.setdefault("STELE_ALLOW_DEV_FALLBACK", "1")

from api import credential_worker


def _conninfo() -> str:
    return provisioning.provision_conninfo()


def _can_create_roles() -> bool:
    try:
        conninfo = _conninfo()
    except provisioning.ProvisioningError:
        return False
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    return bool(row and row[0])


_needs_elevated = pytest.mark.skipif(
    not _can_create_roles(),
    reason="provisioning worker integration test needs a CREATEROLE/superuser connection",
)


def _insert_user(conn: psycopg.Connection, email: str) -> int:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO app.users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (email, "x"),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _enqueue(
    conn: psycopg.Connection,
    *,
    action: str,
    access: str | None = None,
    subject_label: str | None = None,
    target_user_id: int | None = None,
    requested_by: int | None = None,
    login_role: str | None = None,
) -> int:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO app.provision_requests "
            "(action, access, subject_label, target_user_id, requested_by, login_role) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (action, access, subject_label, target_user_id, requested_by, login_role),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _cleanup(conn: psycopg.Connection, *, subject: str | None, user_id: int | None) -> None:
    with conn.transaction():
        if subject is not None:
            for (login_role,) in conn.execute(
                "SELECT login_role FROM app.db_credential_grants WHERE subject_label = %s",
                (subject,),
            ).fetchall():
                if provisioning.role_exists(conn, login_role):
                    conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login_role)))
            conn.execute(
                "DELETE FROM app.db_credential_grants WHERE subject_label = %s", (subject,)
            )
        conn.execute(
            "DELETE FROM app.provision_requests WHERE subject_label = %s OR target_user_id = %s",
            (subject, user_id),
        )
        if user_id is not None:
            # CASCADE drops the user's secret_deliveries.
            conn.execute("DELETE FROM app.users WHERE id = %s", (user_id,))


def _request_row(conn: psycopg.Connection, request_id: int) -> tuple[str, str | None, str | None]:
    row = conn.execute(
        "SELECT status, login_role, error_detail FROM app.provision_requests WHERE id = %s",
        (request_id,),
    ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def _scalar(conn: psycopg.Connection, query: str, params: tuple[object, ...]) -> object:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


@_needs_elevated
def test_provision_request_mints_role_grant_and_secret() -> None:
    subject = f"workertest_{secrets.token_hex(4)}@example.com"
    with psycopg.connect(_conninfo(), autocommit=True) as conn:
        user_id = _insert_user(conn, subject)
        req_id = _enqueue(
            conn,
            action="provision",
            access="analyst",
            subject_label=subject,
            target_user_id=user_id,
            requested_by=user_id,
        )
        try:
            assert credential_worker.run_once(conn) is True

            status, login_role, error = _request_row(conn, req_id)
            assert (status, error) == ("done", None)
            assert login_role is not None
            assert login_role.startswith("stele_analyst_")

            # Registry row: active, attributed to the requester.
            grant = conn.execute(
                "SELECT status, provisioned_by FROM app.db_credential_grants WHERE login_role = %s",
                (login_role,),
            ).fetchone()
            assert grant == ("active", user_id)

            # The login role exists, is NOINHERIT, and is a member of the group role.
            attrs = conn.execute(
                "SELECT rolcanlogin, rolinherit FROM pg_roles WHERE rolname = %s", (login_role,)
            ).fetchone()
            assert attrs == (True, False)
            member = conn.execute(
                "SELECT 1 FROM pg_auth_members m JOIN pg_roles u ON u.oid = m.member "
                "JOIN pg_roles g ON g.oid = m.roleid WHERE u.rolname = %s AND g.rolname = %s",
                (login_role, "stele_analyst"),
            ).fetchone()
            assert member is not None

            # A one-time encrypted delivery exists; decrypting it yields a password
            # that actually authenticates as the new role.
            row = conn.execute(
                "SELECT ciphertext FROM app.secret_deliveries "
                "WHERE target_user_id = %s AND login_role = %s",
                (user_id, login_role),
            ).fetchone()
            assert row is not None
            assert row[0] is not None
            password = secret_delivery.decrypt(row[0])
            info = conn.info
            with psycopg.connect(
                host=info.host,
                port=info.port,
                dbname=info.dbname,
                user=login_role,
                password=password,
            ) as as_role:
                assert as_role.execute("SELECT 1").fetchone() == (1,)
        finally:
            _cleanup(conn, subject=subject, user_id=user_id)


@_needs_elevated
def test_duplicate_active_request_marks_failed() -> None:
    subject = f"workertest_{secrets.token_hex(4)}@example.com"
    with psycopg.connect(_conninfo(), autocommit=True) as conn:
        user_id = _insert_user(conn, subject)
        try:
            first = _enqueue(
                conn,
                action="provision",
                access="analyst",
                subject_label=subject,
                target_user_id=user_id,
            )
            second = _enqueue(
                conn,
                action="provision",
                access="analyst",
                subject_label=subject,
                target_user_id=user_id,
            )
            assert credential_worker.run_once(conn) is True  # first → done
            assert credential_worker.run_once(conn) is True  # second → failed

            assert _request_row(conn, first)[0] == "done"
            status, _, error = _request_row(conn, second)
            assert status == "failed"
            assert error is not None
            assert "already has an active" in error
        finally:
            _cleanup(conn, subject=subject, user_id=user_id)


@_needs_elevated
def test_rotate_then_revoke_requests() -> None:
    subject = f"workertest_{secrets.token_hex(4)}@example.com"
    with psycopg.connect(_conninfo(), autocommit=True) as conn:
        user_id = _insert_user(conn, subject)
        try:
            prov = _enqueue(
                conn,
                action="provision",
                access="analyst",
                subject_label=subject,
                target_user_id=user_id,
            )
            assert credential_worker.run_once(conn) is True
            login_role = _request_row(conn, prov)[1]
            assert login_role is not None

            # Rotate: new delivery, rotated_at stamped, the new password logs in.
            rot = _enqueue(conn, action="rotate", target_user_id=user_id, login_role=login_role)
            assert credential_worker.run_once(conn) is True
            assert _request_row(conn, rot)[0] == "done"
            assert (
                _scalar(
                    conn,
                    "SELECT rotated_at FROM app.db_credential_grants WHERE login_role = %s",
                    (login_role,),
                )
                is not None
            )
            # The un-revealed provision delivery is superseded by the rotate's fresh
            # one: at most one revealable delivery per role, matching the live password.
            assert (
                _scalar(
                    conn,
                    "SELECT count(*) FROM app.secret_deliveries WHERE login_role = %s "
                    "AND ciphertext IS NOT NULL AND consumed_at IS NULL",
                    (login_role,),
                )
                == 1
            )
            ciphertext = _scalar(
                conn,
                "SELECT ciphertext FROM app.secret_deliveries "
                "WHERE target_user_id = %s AND login_role = %s ORDER BY id DESC LIMIT 1",
                (user_id, login_role),
            )
            new_password = secret_delivery.decrypt(str(ciphertext))
            info = conn.info
            with psycopg.connect(
                host=info.host,
                port=info.port,
                dbname=info.dbname,
                user=login_role,
                password=new_password,
            ) as as_role:
                assert as_role.execute("SELECT 1").fetchone() == (1,)

            # Revoke: role dropped, registry marked revoked.
            rev = _enqueue(conn, action="revoke", login_role=login_role)
            assert credential_worker.run_once(conn) is True
            assert _request_row(conn, rev)[0] == "done"
            assert provisioning.role_exists(conn, login_role) is False
            assert (
                _scalar(
                    conn,
                    "SELECT status FROM app.db_credential_grants WHERE login_role = %s",
                    (login_role,),
                )
                == "revoked"
            )
        finally:
            _cleanup(conn, subject=subject, user_id=user_id)


def test_provision_missing_fields_marks_failed() -> None:
    # access/subject/target null is allowed by the table CHECKs but invalid for a
    # provision; the worker must record the failure, not crash — and this path
    # fails before any DDL, so it needs no elevated connection.
    with psycopg.connect(_conninfo(), autocommit=True) as conn:
        req_id = _enqueue(conn, action="provision")
        try:
            assert credential_worker.run_once(conn) is True
            status, _, error = _request_row(conn, req_id)
            assert status == "failed"
            assert error is not None
            assert "needs access" in error
        finally:
            _cleanup(conn, subject=None, user_id=None)
            with conn.transaction():
                conn.execute("DELETE FROM app.provision_requests WHERE id = %s", (req_id,))
