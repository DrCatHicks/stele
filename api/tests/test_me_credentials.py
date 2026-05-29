"""Self-service DB credentials: GET /me/db-credentials, reveal, regenerate.

Tests that need a real app.secret_deliveries row are committed integration tests:
stele_api may not INSERT that table (§3.10), so they seed it (and its FK user +
grant) over the elevated connection and drive the endpoints through ``live_client``
(a real, committing session). Mixing committed rows with the transactional session
deadlocks at cleanup, so those tests own their cleanup in a finally. Tests that only
need a grant or a queued request use the ordinary transactional session.
"""

from __future__ import annotations

import secrets

import psycopg
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import secret_delivery, service
from api.auth.hash import hash_password
from api.auth.models import DbCredentialGrant, ProvisionRequest, User

PASSWORD = "correct-horse-battery-staple"
LOGIN_ROLE = "stele_analyst_alice_a1b2"


# --- transactional helpers (rollback fixture) -------------------------------


async def _login_as(
    client: AsyncClient, session: AsyncSession, email: str, roles: list[str]
) -> User:
    await service.create_user(session, email, PASSWORD, roles)
    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def _grant(session: AsyncSession, subject: str, login_role: str = LOGIN_ROLE) -> None:
    session.add(
        DbCredentialGrant(
            subject_label=subject, access="analyst", login_role=login_role, status="active"
        )
    )
    await session.commit()


# --- committed-integration helpers (live_client + elevated_conn) ------------


def _commit_user(conn: psycopg.Connection, email: str) -> int:
    row = conn.execute(
        "INSERT INTO app.users (email, password_hash) VALUES (%s, %s) RETURNING id",
        (email, hash_password(PASSWORD)),
    ).fetchone()
    assert row is not None
    user_id = int(row[0])
    conn.execute("INSERT INTO app.user_roles (user_id, role) VALUES (%s, 'analyst')", (user_id,))
    return user_id


def _commit_grant(conn: psycopg.Connection, subject: str, login_role: str = LOGIN_ROLE) -> None:
    conn.execute(
        "INSERT INTO app.db_credential_grants (subject_label, access, login_role, status) "
        "VALUES (%s, 'analyst', %s, 'active')",
        (subject, login_role),
    )


def _commit_delivery(
    conn: psycopg.Connection, user_id: int, login_role: str, password: str
) -> None:
    conn.execute(
        "INSERT INTO app.secret_deliveries (target_user_id, login_role, ciphertext, expires_at) "
        "VALUES (%s, %s, %s, now() + make_interval(secs => 3600))",
        (user_id, login_role, secret_delivery.encrypt(password)),
    )


def _cleanup(conn: psycopg.Connection, user_id: int, login_role: str = LOGIN_ROLE) -> None:
    conn.execute("DELETE FROM app.db_credential_grants WHERE login_role = %s", (login_role,))
    conn.execute("DELETE FROM app.users WHERE id = %s", (user_id,))  # cascades delivery + sessions


# --- tests ------------------------------------------------------------------


async def test_my_credentials_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/me/db-credentials")).status_code == 401


async def test_my_credentials_lists_own_with_pending_flag(
    live_client: AsyncClient, elevated_conn: psycopg.Connection
) -> None:
    suffix = secrets.token_hex(4)
    email = f"alice_{suffix}@example.com"
    login_role = f"stele_analyst_alice_{suffix}"
    user_id = _commit_user(elevated_conn, email)
    try:
        resp = await live_client.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert resp.status_code == 200
        _commit_grant(elevated_conn, email, login_role)
        _commit_delivery(elevated_conn, user_id, login_role, "pw")

        resp = await live_client.get("/me/db-credentials")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["login_role"] == login_role
        assert body[0]["access"] == "analyst"
        assert body[0]["has_pending_secret"] is True
    finally:
        _cleanup(elevated_conn, user_id, login_role)


async def test_my_credentials_excludes_other_subjects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login_as(client, db_session, "alice@example.com", ["analyst"])
    await _grant(db_session, "someone-else@example.com", "stele_analyst_other_x")
    resp = await client.get("/me/db-credentials")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_reveal_returns_password_once(
    live_client: AsyncClient, elevated_conn: psycopg.Connection
) -> None:
    suffix = secrets.token_hex(4)
    email = f"alice_{suffix}@example.com"
    login_role = f"stele_analyst_alice_{suffix}"
    user_id = _commit_user(elevated_conn, email)
    try:
        resp = await live_client.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert resp.status_code == 200
        _commit_grant(elevated_conn, email, login_role)
        _commit_delivery(elevated_conn, user_id, login_role, "live-password")

        resp = await live_client.post(f"/me/db-credentials/{login_role}/reveal")
        assert resp.status_code == 200
        body = resp.json()
        assert body["password"] == "live-password"
        assert body["group_role"] == "stele_analyst"
        assert body["set_role_sql"] == "SET ROLE stele_analyst;"

        # Single-use: the password is gone now.
        resp2 = await live_client.post(f"/me/db-credentials/{login_role}/reveal")
        assert resp2.status_code == 410
    finally:
        _cleanup(elevated_conn, user_id, login_role)


async def test_reveal_rejects_credential_not_owned(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The ownership check (grant.subject_label != user.email) 404s before any
    # delivery lookup, so no secret row is needed.
    await _grant(db_session, "owner@example.com")
    await _login_as(client, db_session, "intruder@example.com", ["analyst"])
    assert (await client.post(f"/me/db-credentials/{LOGIN_ROLE}/reveal")).status_code == 404


async def test_regenerate_enqueues_rotate(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_as(client, db_session, "alice@example.com", ["analyst"])
    await _grant(db_session, "alice@example.com")

    resp = await client.post(f"/me/db-credentials/{LOGIN_ROLE}/regenerate")
    assert resp.status_code == 202
    body = resp.json()
    assert body["action"] == "rotate"
    assert body["login_role"] == LOGIN_ROLE
    assert body["status"] == "pending"

    queued = (
        await db_session.execute(
            select(ProvisionRequest).where(
                ProvisionRequest.action == "rotate", ProvisionRequest.login_role == LOGIN_ROLE
            )
        )
    ).scalar_one()
    assert queued.target_user_id is not None


async def test_regenerate_rejects_not_owned(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_as(client, db_session, "alice@example.com", ["analyst"])
    await _grant(db_session, "someone-else@example.com", "stele_analyst_other_x")
    assert (
        await client.post("/me/db-credentials/stele_analyst_other_x/regenerate")
    ).status_code == 404
