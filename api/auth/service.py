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

from api.auth import config, provisioning
from api.auth.hash import hash_password, verify_password
from api.auth.models import ProvisionRequest, Session, User, UserRole

# Application roles. 'analyst' is a minimal role: an analyst account exists only so
# its holder can sign in and reveal/regenerate their own DB credential (design doc
# §3.10 revision). It gates no other capability — require_role allow-lists never
# include it.
VALID_ROLES = frozenset({"admin", "researcher", "reviewer", "analyst"})


class AuthError(Exception):
    """Base for auth failures."""


class InvalidCredentials(AuthError):
    """Email/password did not match an active account."""


class DuplicateUser(AuthError):
    """An account with this email already exists."""


class InvalidRole(AuthError):
    """Role is not one of the application roles."""


class UserNotFound(AuthError):
    """No operator account with the given id."""


class LastAdmin(AuthError):
    """The operation would leave no enabled admin (last-admin protection).

    Raised when disabling, or removing the admin role from, the only remaining
    enabled admin. The system must always retain at least one operator who can
    administer accounts, so the change is refused rather than silently locking
    everyone out of user management.
    """


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


async def delete_user_sessions(session: AsyncSession, user_id: int) -> None:
    """Revoke every session for a user (used on password reset).

    Unlike ``delete_session`` (logout), this deliberately does NOT commit: it's
    meant to be one statement inside a larger transaction — ``reset_password``
    changes the hash and revokes sessions atomically, then commits once. Call it
    only where the caller owns the commit.
    """
    await session.execute(delete(Session).where(Session.user_id == user_id))


# --- Admin user management (M9.2) -------------------------------------------


async def list_users(session: AsyncSession) -> list[tuple[User, list[str]]]:
    """Every operator account paired with its sorted roles, oldest first.

    Roles are fetched in one query and grouped in Python rather than per-user, so
    listing N accounts is two queries, not N+1.
    """
    users = (await session.execute(select(User).order_by(User.created_at, User.id))).scalars().all()
    role_rows = (await session.execute(select(UserRole.user_id, UserRole.role))).all()
    by_user: dict[int, list[str]] = {}
    for user_id, role in role_rows:
        by_user.setdefault(user_id, []).append(role)
    return [(user, sorted(by_user.get(user.id, []))) for user in users]


async def _other_enabled_admin_exists(session: AsyncSession, exclude_user_id: int) -> bool:
    """True if some enabled account other than ``exclude_user_id`` holds admin."""
    row = (
        await session.execute(
            select(UserRole.user_id)
            .join(User, User.id == UserRole.user_id)
            .where(
                UserRole.role == "admin",
                User.disabled.is_(False),
                UserRole.user_id != exclude_user_id,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def set_user_roles(
    session: AsyncSession, user_id: int, roles: Iterable[str]
) -> tuple[User, list[str]]:
    """Replace a user's roles wholesale. Returns the user and its new sorted roles.

    Validates the new set (InvalidRole on an unknown/empty set, so an account can
    never be stripped to zero roles). Enforces last-admin protection: stripping
    admin from the only remaining enabled admin raises LastAdmin.
    """
    user = await get_user(session, user_id)
    if user is None:
        raise UserNotFound(str(user_id))
    normalized = normalize_roles(roles)
    if "admin" not in normalized and not user.disabled:
        current = await get_roles(session, user_id)
        if "admin" in current and not await _other_enabled_admin_exists(session, user_id):
            raise LastAdmin(str(user_id))
    await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
    for role in normalized:
        session.add(UserRole(user_id=user_id, role=role))
    await session.commit()
    return user, normalized


async def set_user_disabled(
    session: AsyncSession, user_id: int, disabled: bool
) -> tuple[User, list[str]]:
    """Disable or re-enable an account. Returns the user and its sorted roles.

    Disabling takes effect immediately — resolve_session already rejects a
    disabled user, so live sessions stop working without deleting their rows.
    Enforces last-admin protection: disabling the only remaining enabled admin
    raises LastAdmin.
    """
    user = await get_user(session, user_id)
    if user is None:
        raise UserNotFound(str(user_id))
    if disabled and not user.disabled:
        current = await get_roles(session, user_id)
        if "admin" in current and not await _other_enabled_admin_exists(session, user_id):
            raise LastAdmin(str(user_id))
    user.disabled = disabled
    await session.commit()
    roles = await get_roles(session, user_id)
    return user, roles


async def reset_password(session: AsyncSession, user_id: int, password: str) -> User:
    """Set a new password and revoke the user's live sessions in one transaction.

    Revoking sessions is the point: a password reset should invalidate any cookie
    minted under the old credentials, so the holder must log in again.
    """
    user = await get_user(session, user_id)
    if user is None:
        raise UserNotFound(str(user_id))
    user.password_hash = hash_password(password)
    await delete_user_sessions(session, user_id)
    await session.commit()
    return user


# --- DB-credential access granting (UI-driven provisioning, §3.10 revision) --


class InvalidAccess(AuthError):
    """Access tier is not one of the DB-credential tiers (analyst, reviewer)."""


class MissingInitialPassword(AuthError):
    """Granting DB access to a brand-new account needs an initial login password."""


class DuplicateGrant(AuthError):
    """The subject already has an active or pending credential for this tier."""


async def verify_user_password(session: AsyncSession, user_id: int, password: str) -> bool:
    """True if ``password`` matches the user's hash. For the reviewer-tier step-up."""
    user = await get_user(session, user_id)
    if user is None:
        return False
    return verify_password(user.password_hash, password)


async def grant_db_access(
    session: AsyncSession,
    *,
    email: str,
    access: str,
    actor_id: int,
    initial_password: str | None = None,
) -> tuple[ProvisionRequest, int]:
    """Ensure a recipient app account+role exists, then enqueue a provision request.

    Couples the DB-credential holder to an app login (§3.10 revision): the
    recipient signs in and reveals their own credential once. A new account is
    created with the ``initial_password`` and the tier's application role; an
    existing account just gains the role if it lacks it. Raises DuplicateGrant if
    the subject already holds (or has queued) a credential for this tier. Returns
    the queued request and the recipient's user id. The actual CREATE ROLE is the
    worker's job — this never touches role DDL.
    """
    if access not in provisioning.VALID_ACCESS:
        raise InvalidAccess(access)
    app_role = access  # the application role mirrors the access tier
    normalized = normalize_email(email)
    # Reject a duplicate before mutating anything, so a 409 leaves the recipient's
    # account untouched.
    if await provisioning.active_grant_exists(
        session, normalized, access
    ) or await provisioning.pending_request_exists(session, normalized, access):
        raise DuplicateGrant(normalized)
    # The account/role change and the queued request commit together (a single
    # commit at the end), so the grant is all-or-nothing: a failed enqueue never
    # leaves an orphaned account or a dangling role with no request behind it.
    user = (
        await session.execute(select(User).where(User.email == normalized))
    ).scalar_one_or_none()
    if user is None:
        if not initial_password:
            raise MissingInitialPassword(normalized)
        user = User(email=normalized, password_hash=hash_password(initial_password))
        session.add(user)
        await session.flush()  # assign user.id before the role + request reference it
        session.add(UserRole(user_id=user.id, role=app_role))
    elif app_role not in await get_roles(session, user.id):
        session.add(UserRole(user_id=user.id, role=app_role))
    request = ProvisionRequest(
        action="provision",
        access=access,
        subject_label=normalized,
        target_user_id=user.id,
        requested_by=actor_id,
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request, user.id
