"""Admin user-management API (M9.2): /admin/users.

Covers the CRUD surface (list, create, set-roles, disable/enable, reset-password),
its admin-only gating, and the two safety guarantees the service enforces:
last-admin protection (can't disable or de-admin the only enabled admin) and
session revocation (disable and reset both immediately invalidate live sessions).

The ``authed_client`` fixture logs in as ``rbac-admin@example.com``; the
last-admin guard tests first disable any other (e.g. bootstrap) admins inside
the rolled-back transaction via ``_isolate_as_sole_admin`` so that account is
provably the last one standing, rather than assuming a pristine DB.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import service
from api.auth.models import User, UserRole
from api.main import api_app
from api.tests.conftest import ADMIN_EMAIL

PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "a-different-correct-horse"


@asynccontextmanager
async def _second_client() -> AsyncIterator[AsyncClient]:
    """A second client over the same app (and the same overridden, transactional
    session the ``client`` fixture installed) — for a target user's own session,
    distinct from the admin's cookie jar."""
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client: AsyncClient, email: str, password: str) -> int:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return int(resp.json()["id"])


async def _admin_id(admin: AsyncClient) -> int:
    listing = await admin.get("/admin/users")
    assert listing.status_code == 200
    return int(next(u["id"] for u in listing.json() if u["email"] == ADMIN_EMAIL))


async def _isolate_as_sole_admin(session: AsyncSession, keep_id: int) -> None:
    """Disable every enabled admin except ``keep_id``, inside the test's
    transaction (rolled back at teardown). The dev/CI DB may carry a committed
    bootstrap admin, so the last-admin guard tests can't assume the fixture's
    admin is the only one — this makes ``keep_id`` provably the last standing."""
    other_ids = [
        uid
        for uid in (await session.execute(select(UserRole.user_id).where(UserRole.role == "admin")))
        .scalars()
        .all()
        if uid != keep_id
    ]
    if other_ids:
        await session.execute(update(User).where(User.id.in_(other_ids)).values(disabled=True))
        await session.commit()


# --- Gating: every route is admin-only -------------------------------------

# (method, path, body) for each route; ids are arbitrary — the gate runs first.
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("get", "/admin/users", None),
    ("post", "/admin/users", {"email": "x@example.com", "password": PASSWORD, "roles": ["admin"]}),
    ("put", "/admin/users/1/roles", {"roles": ["admin"]}),
    ("post", "/admin/users/1/disable", None),
    ("post", "/admin/users/1/enable", None),
    ("post", "/admin/users/1/reset-password", {"password": PASSWORD}),
]


async def _call(client: AsyncClient, method: str, path: str, body: dict[str, Any] | None) -> Any:
    return (
        await getattr(client, method)(path)
        if body is None
        else await getattr(client, method)(path, json=body)
    )


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_routes_require_authentication(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    assert (await _call(client, method, path, body)).status_code == 401


@pytest.mark.parametrize("role", ["researcher", "reviewer"])
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_routes_forbidden_for_non_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    role: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    await service.create_user(db_session, f"{role}@example.com", PASSWORD, [role])
    await _login(client, f"{role}@example.com", PASSWORD)
    assert (await _call(client, method, path, body)).status_code == 403


# --- List ------------------------------------------------------------------


async def test_list_returns_users_with_roles(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    await service.create_user(db_session, "multi@example.com", PASSWORD, ["researcher", "reviewer"])

    resp = await authed_client.get("/admin/users")

    assert resp.status_code == 200
    by_email = {u["email"]: u for u in resp.json()}
    assert by_email[ADMIN_EMAIL]["roles"] == ["admin"]
    assert by_email["multi@example.com"]["roles"] == ["researcher", "reviewer"]
    assert "password" not in by_email["multi@example.com"]
    assert "password_hash" not in by_email["multi@example.com"]


# --- Create ----------------------------------------------------------------


async def test_create_user_succeeds(authed_client: AsyncClient) -> None:
    resp = await authed_client.post(
        "/admin/users",
        json={"email": "New@Example.com", "password": PASSWORD, "roles": ["reviewer", "admin"]},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new@example.com"  # normalized
    assert body["roles"] == ["admin", "reviewer"]  # sorted
    assert body["disabled"] is False
    # The created account can actually log in.
    async with _second_client() as other:
        assert (
            await other.post("/auth/login", json={"email": "new@example.com", "password": PASSWORD})
        ).status_code == 200


async def test_create_duplicate_email_conflicts(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    await service.create_user(db_session, "dupe@example.com", PASSWORD, ["researcher"])
    resp = await authed_client.post(
        "/admin/users",
        json={"email": "dupe@example.com", "password": PASSWORD, "roles": ["researcher"]},
    )
    assert resp.status_code == 409


@pytest.mark.parametrize("roles", [["wizard"], []])
async def test_create_invalid_roles_unprocessable(
    authed_client: AsyncClient, roles: list[str]
) -> None:
    resp = await authed_client.post(
        "/admin/users",
        json={"email": "bad@example.com", "password": PASSWORD, "roles": roles},
    )
    assert resp.status_code == 422


# --- Set roles -------------------------------------------------------------


async def test_set_roles_replaces_wholesale(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await service.create_user(db_session, "r@example.com", PASSWORD, ["researcher"])

    resp = await authed_client.put(f"/admin/users/{user.id}/roles", json={"roles": ["reviewer"]})

    assert resp.status_code == 200
    assert resp.json()["roles"] == ["reviewer"]
    assert await service.get_roles(db_session, user.id) == ["reviewer"]


async def test_set_roles_unknown_user_not_found(authed_client: AsyncClient) -> None:
    resp = await authed_client.put("/admin/users/999999/roles", json={"roles": ["reviewer"]})
    assert resp.status_code == 404


async def test_set_roles_unknown_user_beats_invalid_role(authed_client: AsyncClient) -> None:
    # Existence is checked before role validity: a missing user is 404, not 422,
    # even when the body's roles are also bad.
    resp = await authed_client.put("/admin/users/999999/roles", json={"roles": ["wizard"]})
    assert resp.status_code == 404


async def test_set_roles_invalid_role_unprocessable(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await service.create_user(db_session, "r2@example.com", PASSWORD, ["researcher"])
    resp = await authed_client.put(f"/admin/users/{user.id}/roles", json={"roles": ["wizard"]})
    assert resp.status_code == 422


# --- Disable / enable ------------------------------------------------------


async def test_disable_then_enable_roundtrip(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await service.create_user(db_session, "tgt@example.com", PASSWORD, ["researcher"])

    disabled = await authed_client.post(f"/admin/users/{user.id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["disabled"] is True
    # A disabled account can't log in.
    async with _second_client() as other:
        assert (
            await other.post("/auth/login", json={"email": "tgt@example.com", "password": PASSWORD})
        ).status_code == 401

    enabled = await authed_client.post(f"/admin/users/{user.id}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["disabled"] is False
    async with _second_client() as other:
        assert (
            await other.post("/auth/login", json={"email": "tgt@example.com", "password": PASSWORD})
        ).status_code == 200


async def test_disable_revokes_live_session(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await service.create_user(db_session, "live@example.com", PASSWORD, ["researcher"])
    async with _second_client() as other:
        await _login(other, "live@example.com", PASSWORD)
        assert (await other.get("/auth/me")).status_code == 200

        assert (await authed_client.post(f"/admin/users/{user.id}/disable")).status_code == 200

        # The already-issued cookie stops authenticating immediately.
        assert (await other.get("/auth/me")).status_code == 401


async def test_disable_unknown_user_not_found(authed_client: AsyncClient) -> None:
    assert (await authed_client.post("/admin/users/999999/disable")).status_code == 404
    assert (await authed_client.post("/admin/users/999999/enable")).status_code == 404


# --- Reset password --------------------------------------------------------


async def test_reset_password_revokes_sessions_and_changes_credential(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await service.create_user(db_session, "pw@example.com", PASSWORD, ["researcher"])
    async with _second_client() as other:
        await _login(other, "pw@example.com", PASSWORD)
        assert (await other.get("/auth/me")).status_code == 200

        resp = await authed_client.post(
            f"/admin/users/{user.id}/reset-password", json={"password": NEW_PASSWORD}
        )
        assert resp.status_code == 204

        # The old session is dead …
        assert (await other.get("/auth/me")).status_code == 401
    # … the old password no longer works, and the new one does.
    async with _second_client() as fresh:
        assert (
            await fresh.post("/auth/login", json={"email": "pw@example.com", "password": PASSWORD})
        ).status_code == 401
        assert (
            await fresh.post(
                "/auth/login", json={"email": "pw@example.com", "password": NEW_PASSWORD}
            )
        ).status_code == 200


async def test_reset_password_unknown_user_not_found(authed_client: AsyncClient) -> None:
    resp = await authed_client.post(
        "/admin/users/999999/reset-password", json={"password": NEW_PASSWORD}
    )
    assert resp.status_code == 404


# --- Last-admin protection -------------------------------------------------


async def test_cannot_disable_last_admin(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin_id = await _admin_id(authed_client)
    await _isolate_as_sole_admin(db_session, admin_id)
    resp = await authed_client.post(f"/admin/users/{admin_id}/disable")
    assert resp.status_code == 409


async def test_cannot_strip_admin_role_from_last_admin(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin_id = await _admin_id(authed_client)
    await _isolate_as_sole_admin(db_session, admin_id)
    resp = await authed_client.put(f"/admin/users/{admin_id}/roles", json={"roles": ["researcher"]})
    assert resp.status_code == 409


async def test_can_disable_admin_when_another_admin_exists(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    # A second enabled admin means the first is no longer the last one.
    admin_id = await _admin_id(authed_client)
    await _isolate_as_sole_admin(db_session, admin_id)
    await service.create_user(db_session, "admin2@example.com", PASSWORD, ["admin"])
    assert (await authed_client.post(f"/admin/users/{admin_id}/disable")).status_code == 200


async def test_can_strip_admin_when_another_admin_exists(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin_id = await _admin_id(authed_client)
    await _isolate_as_sole_admin(db_session, admin_id)
    await service.create_user(db_session, "admin2@example.com", PASSWORD, ["admin"])
    resp = await authed_client.put(f"/admin/users/{admin_id}/roles", json={"roles": ["researcher"]})
    assert resp.status_code == 200
    assert resp.json()["roles"] == ["researcher"]


async def test_last_admin_guard_ignores_disabled_admins(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    # A *disabled* second admin doesn't count — the live admin is still the last
    # one standing, so disabling it must still be refused.
    admin_id = await _admin_id(authed_client)
    await _isolate_as_sole_admin(db_session, admin_id)
    await service.create_user(db_session, "ghost@example.com", PASSWORD, ["admin"])
    ghost = await authed_client.get("/admin/users")
    ghost_id = next(u["id"] for u in ghost.json() if u["email"] == "ghost@example.com")
    assert (await authed_client.post(f"/admin/users/{ghost_id}/disable")).status_code == 200

    assert (await authed_client.post(f"/admin/users/{admin_id}/disable")).status_code == 409
