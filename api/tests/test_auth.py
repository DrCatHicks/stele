"""Auth foundation: login, sessions, logout (M3.1).

Covers the acceptance criteria: login success/failure, logout, and the
not-authenticated paths — no cookie, tampered signature, expired session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import config, service
from api.auth.deps import sign_token
from api.auth.models import Session

PASSWORD = "correct-horse-battery-staple"


async def _make_user(
    session: AsyncSession,
    email: str = "admin@example.com",
    role: str = "admin",
    disabled: bool = False,
) -> int:
    user = await service.create_user(session, email, PASSWORD, role)
    if disabled:
        user.disabled = True
        await session.commit()
    return user.id


async def test_login_success_sets_httponly_cookie(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _make_user(db_session)

    resp = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@example.com"
    assert body["role"] == "admin"
    set_cookie = resp.headers["set-cookie"]
    assert "httponly" in set_cookie.lower()
    assert client.cookies.get(config.COOKIE_NAME) is not None


async def test_login_is_case_insensitive_on_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _make_user(db_session, email="Admin@Example.com")

    resp = await client.post(
        "/auth/login", json={"email": "ADMIN@example.com", "password": PASSWORD}
    )

    assert resp.status_code == 200


async def test_login_wrong_password_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    await _make_user(db_session)

    resp = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )

    assert resp.status_code == 401
    assert client.cookies.get(config.COOKIE_NAME) is None


async def test_login_unknown_email_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert resp.status_code == 401


async def test_login_disabled_account_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _make_user(db_session, disabled=True)

    resp = await client.post(
        "/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
    )

    assert resp.status_code == 401


async def test_me_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


async def test_me_returns_current_user_after_login(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _make_user(db_session)
    await client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD})

    resp = await client.get("/auth/me")

    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@example.com"


async def test_logout_revokes_session(client: AsyncClient, db_session: AsyncSession) -> None:
    await _make_user(db_session)
    await client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    # A session row exists before logout.
    assert (await db_session.execute(select(Session))).scalars().all()

    logout = await client.post("/auth/logout")

    assert logout.status_code == 204
    # Server-side session deleted, and the cookie no longer authenticates.
    assert not (await db_session.execute(select(Session))).scalars().all()
    assert (await client.get("/auth/me")).status_code == 401


async def test_logout_without_session_is_idempotent(client: AsyncClient) -> None:
    resp = await client.post("/auth/logout")
    assert resp.status_code == 204


async def test_disabling_user_revokes_live_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The differentiator of server-side sessions: disabling an account after a
    # session is issued must immediately stop that session from authenticating.
    user_id = await _make_user(db_session)
    await client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert (await client.get("/auth/me")).status_code == 200

    user = await service.get_user(db_session, user_id)
    assert user is not None
    user.disabled = True
    await db_session.commit()

    assert (await client.get("/auth/me")).status_code == 401


def _cookie_header(value: str) -> dict[str, str]:
    # Send exactly one cookie via an explicit header. Cookie-injection tests must
    # not go through the client jar: setting a same-named cookie there leaves the
    # login cookie in place too, and which duplicate the HTTP stack forwards/parses
    # last varies across environments (Python/httpx versions) — a CI flake.
    return {"Cookie": f"{config.COOKIE_NAME}={value}"}


async def test_tampered_cookie_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    # Tampering one character of a validly-signed cookie must 401 even when the
    # underlying token is a live session: the signature is verified before (and
    # independently of) the DB lookup, with no fallback to the raw value.
    user_id = await _make_user(db_session)
    token = "live-session-token"
    db_session.add(
        Session(token=token, user_id=user_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
    )
    await db_session.commit()
    signed = sign_token(token)
    tampered = signed[:-1] + ("A" if signed[-1] != "A" else "B")

    # The untampered cookie authenticates, so the 401 below is due to tampering.
    ok = await client.get("/auth/me", headers=_cookie_header(signed))
    assert ok.status_code == 200

    bad = await client.get("/auth/me", headers=_cookie_header(tampered))
    assert bad.status_code == 401


async def test_expired_session_rejected_and_cleaned(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await _make_user(db_session)
    # Plant an already-expired session and present its signed token.
    token = "expired-token-fixture"
    db_session.add(
        Session(
            token=token,
            user_id=user_id,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await db_session.commit()

    resp = await client.get("/auth/me", headers=_cookie_header(sign_token(token)))

    assert resp.status_code == 401
    # resolve_session deletes the expired row opportunistically.
    remaining = (
        await db_session.execute(select(Session).where(Session.token == token))
    ).scalar_one_or_none()
    assert remaining is None
