"""Admin DB-credential granting: POST /admin/db-credentials/grant, revoke, polling.

These cover the request path only (no role DDL): granting ensures the recipient's
app account + role and enqueues a provision request; the privileged worker (tested
in test_credential_worker.py) does the actual CREATE ROLE. The reviewer (PII) tier
requires the admin to re-confirm their password.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import service
from api.auth.models import DbCredentialGrant, ProvisionRequest, User

PASSWORD = "correct-horse-battery-staple"  # == conftest ADMIN_PASSWORD


async def _login(client: AsyncClient, session: AsyncSession, role: str) -> None:
    await service.create_user(session, f"{role}@example.com", PASSWORD, [role])
    resp = await client.post(
        "/auth/login", json={"email": f"{role}@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 200


async def _roles(session: AsyncSession, email: str) -> list[str]:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one()
    return await service.get_roles(session, user.id)


# --- authorization ----------------------------------------------------------


async def test_grant_requires_authentication(client: AsyncClient) -> None:
    resp = await client.post(
        "/admin/db-credentials/grant", json={"email": "a@example.com", "access": "analyst"}
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("role", ["researcher", "reviewer", "analyst"])
async def test_grant_forbidden_for_non_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
) -> None:
    await _login(client, db_session, role)
    resp = await client.post(
        "/admin/db-credentials/grant",
        json={"email": "a@example.com", "access": "analyst", "initial_password": PASSWORD},
    )
    assert resp.status_code == 403


# --- granting ---------------------------------------------------------------


async def test_grant_analyst_creates_account_role_and_request(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await authed_client.post(
        "/admin/db-credentials/grant",
        json={"email": "NewAnalyst@example.com", "access": "analyst", "initial_password": PASSWORD},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["action"] == "provision"
    assert body["access"] == "analyst"
    assert body["subject_label"] == "newanalyst@example.com"
    assert body["status"] == "pending"

    # The recipient now has a minimal app account with the analyst role.
    assert await _roles(db_session, "newanalyst@example.com") == ["analyst"]
    # And a provision request is queued.
    queued = (
        await db_session.execute(
            select(ProvisionRequest).where(
                ProvisionRequest.subject_label == "newanalyst@example.com"
            )
        )
    ).scalar_one()
    assert queued.action == "provision"


async def test_grant_adds_role_to_existing_account(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    await service.create_user(db_session, "researcher2@example.com", PASSWORD, ["researcher"])
    resp = await authed_client.post(
        "/admin/db-credentials/grant",
        json={"email": "researcher2@example.com", "access": "analyst"},
    )
    assert resp.status_code == 202
    assert await _roles(db_session, "researcher2@example.com") == ["analyst", "researcher"]


async def test_grant_new_account_requires_initial_password(
    authed_client: AsyncClient,
) -> None:
    resp = await authed_client.post(
        "/admin/db-credentials/grant",
        json={"email": "nopassword@example.com", "access": "analyst"},
    )
    assert resp.status_code == 422


async def test_grant_rejects_unknown_access(authed_client: AsyncClient) -> None:
    resp = await authed_client.post(
        "/admin/db-credentials/grant",
        json={"email": "x@example.com", "access": "superuser", "initial_password": PASSWORD},
    )
    assert resp.status_code == 422


async def test_duplicate_grant_conflicts(authed_client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "access": "analyst", "initial_password": PASSWORD}
    assert (
        await authed_client.post("/admin/db-credentials/grant", json=payload)
    ).status_code == 202
    # A second provision for the same subject+tier is already queued → 409.
    resp = await authed_client.post("/admin/db-credentials/grant", json=payload)
    assert resp.status_code == 409


# --- reviewer-tier step-up --------------------------------------------------


async def test_reviewer_grant_without_confirmation_is_forbidden(
    authed_client: AsyncClient,
) -> None:
    resp = await authed_client.post(
        "/admin/db-credentials/grant",
        json={"email": "rev@example.com", "access": "reviewer", "initial_password": PASSWORD},
    )
    assert resp.status_code == 403


async def test_reviewer_grant_with_wrong_confirmation_is_forbidden(
    authed_client: AsyncClient,
) -> None:
    resp = await authed_client.post(
        "/admin/db-credentials/grant",
        json={
            "email": "rev@example.com",
            "access": "reviewer",
            "initial_password": PASSWORD,
            "confirm_password": "wrong-password",
        },
    )
    assert resp.status_code == 403


async def test_reviewer_grant_with_confirmation_succeeds(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await authed_client.post(
        "/admin/db-credentials/grant",
        json={
            "email": "rev@example.com",
            "access": "reviewer",
            "initial_password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )
    assert resp.status_code == 202
    assert await _roles(db_session, "rev@example.com") == ["reviewer"]


# --- revoke + polling -------------------------------------------------------


async def _seed_active_grant(session: AsyncSession, login_role: str = "stele_analyst_x_1") -> None:
    session.add(
        DbCredentialGrant(
            subject_label="x@example.com", access="analyst", login_role=login_role, status="active"
        )
    )
    await session.commit()


async def test_revoke_enqueues_request(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_active_grant(db_session)
    resp = await authed_client.post("/admin/db-credentials/stele_analyst_x_1/revoke")
    assert resp.status_code == 202
    body = resp.json()
    assert body["action"] == "revoke"
    assert body["login_role"] == "stele_analyst_x_1"


async def test_revoke_unknown_role_is_404(authed_client: AsyncClient) -> None:
    resp = await authed_client.post("/admin/db-credentials/stele_analyst_ghost/revoke")
    assert resp.status_code == 404


async def test_revoke_inactive_is_409(authed_client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(
        DbCredentialGrant(
            subject_label="y@example.com",
            access="analyst",
            login_role="stele_analyst_y_1",
            status="revoked",
        )
    )
    await db_session.commit()
    resp = await authed_client.post("/admin/db-credentials/stele_analyst_y_1/revoke")
    assert resp.status_code == 409


async def test_request_status_poll(authed_client: AsyncClient) -> None:
    grant = await authed_client.post(
        "/admin/db-credentials/grant",
        json={"email": "poll@example.com", "access": "analyst", "initial_password": PASSWORD},
    )
    request_id = grant.json()["id"]
    resp = await authed_client.get(f"/admin/db-credentials/requests/{request_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert (await authed_client.get("/admin/db-credentials/requests/999999")).status_code == 404
