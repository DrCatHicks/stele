"""Authentication logic: user creation, login, sessions.

The API always connects as the single ``stele_api`` role; the {admin,
researcher, reviewer} roles a user holds (rows in ``app.user_roles``) are an
application-layer authorization concept (design doc §3.10), enforced by the
dependencies in ``deps.py`` — never by Postgres grants. Roles are multi-valued:
one account can be e.g. both researcher and reviewer.

Sessions are server-side and revocable: a row in app.sessions with an opaque
token and an expiry. Logout (or expiry) deletes the row, so a stolen cookie
stops working the moment the session is revoked — unlike a stateless token.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import config
from api.auth.hash import hash_password, verify_password
from api.auth.models import Session, User, UserRole

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


def normalize_roles(roles: Iterable[str]) -> list[str]:
    """De-duplicate and validate a set of application roles.

    Returns them sorted (a stable order for storage and output). Raises
    InvalidRole for an unknown role or an empty set — an account with no role
    could authenticate but reach nothing, so creation requires at least one.
    """
    if isinstance(roles, str):
        # A bare string satisfies Iterable[str] but would split into characters —
        # fail loudly rather than validate "admin" as {'a','d','m','i','n'}.
        raise TypeError("roles must be a collection of role strings, not a single str")
    unique = set(roles)
    unknown = unique - VALID_ROLES
    if unknown:
        raise InvalidRole(", ".join(sorted(unknown)))
    if not unique:
        raise InvalidRole("(at least one role required)")
    return sorted(unique)


async def create_user(
    session: AsyncSession, email: str, password: str, roles: Iterable[str]
) -> User:
    """Create an operator account with one or more roles. Used by the bootstrap
    CLI and the admin UI (M9.2)."""
    normalized_roles = normalize_roles(roles)
    normalized = normalize_email(email)
    existing = (
        await session.execute(select(User).where(User.email == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateUser(normalized)
    user = User(email=normalized, password_hash=hash_password(password))
    session.add(user)
    await session.flush()  # assign user.id before the role rows reference it
    for role in normalized_roles:
        session.add(UserRole(user_id=user.id, role=role))
    await session.commit()
    await session.refresh(user)
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def get_roles(session: AsyncSession, user_id: int) -> list[str]:
    """The application roles a user holds, sorted. Empty if the user is gone."""
    rows = (
        await session.execute(select(UserRole.role).where(UserRole.user_id == user_id))
    ).scalars()
    return sorted(rows)


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
    roles: frozenset[str]


async def resolve_session(session: AsyncSession, token: str) -> AuthenticatedUser | None:
    """Return the user behind a live session token, or None.

    None covers every not-authenticated case: unknown token, expired session, or
    a since-disabled account. Expired rows are deleted opportunistically so the
    table self-cleans on use. Roles are loaded fresh here, so a grant/revoke
    takes effect on the holder's next request.
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
    roles = frozenset(await get_roles(session, user.id))
    return AuthenticatedUser(id=user.id, email=user.email, roles=roles)


async def delete_session(session: AsyncSession, token: str) -> None:
    """Revoke a session (logout). Idempotent — deleting a gone token is a no-op."""
    await session.execute(delete(Session).where(Session.token == token))
    await session.commit()
