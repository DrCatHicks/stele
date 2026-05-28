"""Self-service DB credentials: GET /me/db-credentials, reveal, regenerate.

The recipient signs in and reveals their freshly-minted password exactly once
(scoped to their own session and email) or regenerates it. No role DDL here — the
endpoints read the registry and enqueue rotate requests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import secret_delivery, service
from api.auth.models import DbCredentialGrant, ProvisionRequest, SecretDelivery, User

PASSWORD = "correct-horse-battery-staple"
LOGIN_ROLE = "stele_analyst_alice_a1b2"


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


async def _deliver(session: AsyncSession, user_id: int, login_role: str, password: str) -> None:
    session.add(
        SecretDelivery(
            target_user_id=user_id,
            login_role=login_role,
            ciphertext=secret_delivery.encrypt(password),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await session.commit()


async def test_my_credentials_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/me/db-credentials")).status_code == 401


async def test_my_credentials_lists_own_with_pending_flag(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _login_as(client, db_session, "alice@example.com", ["analyst"])
    await _grant(db_session, "alice@example.com")
    await _deliver(db_session, user.id, LOGIN_ROLE, "pw")

    resp = await client.get("/me/db-credentials")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["login_role"] == LOGIN_ROLE
    assert body[0]["access"] == "analyst"
    assert body[0]["has_pending_secret"] is True


async def test_my_credentials_excludes_other_subjects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login_as(client, db_session, "alice@example.com", ["analyst"])
    await _grant(db_session, "someone-else@example.com", "stele_analyst_other_x")
    resp = await client.get("/me/db-credentials")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_reveal_returns_password_once(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _login_as(client, db_session, "alice@example.com", ["analyst"])
    await _grant(db_session, "alice@example.com")
    await _deliver(db_session, user.id, LOGIN_ROLE, "live-password")

    resp = await client.post(f"/me/db-credentials/{LOGIN_ROLE}/reveal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["password"] == "live-password"
    assert body["group_role"] == "stele_analyst"
    assert body["set_role_sql"] == "SET ROLE stele_analyst;"

    # Single-use: the password is gone now.
    assert (await client.post(f"/me/db-credentials/{LOGIN_ROLE}/reveal")).status_code == 410


async def test_reveal_rejects_credential_not_owned(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _login_as(client, db_session, "owner@example.com", ["analyst"])
    await _grant(db_session, "owner@example.com")
    await _deliver(db_session, owner.id, LOGIN_ROLE, "secret")

    # A different user logs in and tries to reveal the owner's credential.
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
