"""Authentication logic: user creation, login, sessions.

The API always connects as the single ``stele_api`` role; the {admin,
researcher, reviewer} roles carried on ``User.role`` are an application-layer
authorization concept (design doc §3.10), enforced by the dependencies in
``deps.py`` — never by Postgres grants.

Sessions are server-side and revocable: a row in app.sessions with an opaque
token and an expiry. Logout (or expiry) deletes the row, so a stolen cookie
stops working the moment the session is revoked — unlike a stateless token.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import config
from api.auth.hash import hash_password, verify_password
from api.auth.models import Session, User

VALID_ROLES = frozenset({"admin", "researcher", "reviewer"})


class AuthError(Exception):
    """Base for auth failures."""


class InvalidCredentials(AuthError):
    """Email/password did not match an active account."""


class DuplicateUser(AuthError):
    """An account with this email already exists."""


class InvalidRole(AuthError):
    """Role is not one of the application roles."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def create_user(session: AsyncSession, email: str, password: str, role: str) -> User:
    """Create an operator account. Used by the bootstrap CLI and (later) admin UI."""
    if role not in VALID_ROLES:
        raise InvalidRole(role)
    normalized = normalize_email(email)
    existing = (
        await session.execute(select(User).where(User.email == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateUser(normalized)
    user = User(email=normalized, password_hash=hash_password(password), role=role)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    """Resolve an active account from credentials, or raise InvalidCredentials.

    Failures are deliberately indistinguishable (no-such-user, wrong-password,
    disabled all raise the same error) so the endpoint can't be used to probe
    which emails are registered. A dummy verify on the no-user path keeps the
    timing from leaking account existence either.
    """
    normalized = normalize_email(email)
    user = (
        await session.execute(select(User).where(User.email == normalized))
    ).scalar_one_or_none()
    if user is None:
        # Equalize timing against the verify path so absence isn't observable.
        hash_password(password)
        raise InvalidCredentials(normalized)
    if not verify_password(user.password_hash, password):
        raise InvalidCredentials(normalized)
    if user.disabled:
        raise InvalidCredentials(normalized)
    return user


async def create_session(session: AsyncSession, user: User) -> Session:
    token = secrets.token_urlsafe(32)
    row = Session(
        token=token,
        user_id=user.id,
        expires_at=datetime.now(UTC) + config.SESSION_TTL,
    )
    session.add(row)
    await session.commit()
    return row


@dataclass(frozen=True)
class AuthenticatedUser:
    """A resolved, active session's user — what dependencies hand to endpoints."""

    id: int
    email: str
    role: str


async def resolve_session(session: AsyncSession, token: str) -> AuthenticatedUser | None:
    """Return the user behind a live session token, or None.

    None covers every not-authenticated case: unknown token, expired session, or
    a since-disabled account. Expired rows are deleted opportunistically so the
    table self-cleans on use.
    """
    row = (
        await session.execute(select(Session).where(Session.token == token))
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.expires_at <= datetime.now(UTC):
        await session.execute(delete(Session).where(Session.token == token))
        await session.commit()
        return None
    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None or user.disabled:
        return None
    return AuthenticatedUser(id=user.id, email=user.email, role=user.role)


async def delete_session(session: AsyncSession, token: str) -> None:
    """Revoke a session (logout). Idempotent — deleting a gone token is a no-op."""
    await session.execute(delete(Session).where(Session.token == token))
    await session.commit()
