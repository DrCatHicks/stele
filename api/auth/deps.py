"""FastAPI dependencies for resolving and requiring the current user.

The cookie carries a *signed* opaque token (itsdangerous). The signature is
tamper-evidence only — authority comes from the server-side session row, not the
cookie — but it lets a forged/corrupted cookie be rejected before any DB lookup
and namespaces the secret to this use (design doc §3.10).

``current_user`` resolves cookie → session → active user or raises 401.
``require_role`` builds on it: 401 if unauthenticated, 403 if none of the user's
roles are permitted (design doc §3.10). App roles {admin, researcher, reviewer,
analyst} are authorization carried in app.user_roles, never Postgres grants.
('analyst' is minimal — it only lets its holder reveal their own DB credential.)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer

from api.auth import config
from api.auth.service import VALID_ROLES, AuthenticatedUser, resolve_session
from api.db import SessionDep


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(config.session_secret(), salt=config.SIGNER_SALT)


def sign_token(token: str) -> str:
    """Cookie value for a session token."""
    return _serializer().dumps(token)


def unsign_token(signed: str) -> str | None:
    """Recover the token from a cookie value, or None if the signature is bad."""
    try:
        value = _serializer().loads(signed)
    except BadSignature:
        return None
    return value if isinstance(value, str) else None


async def current_user(request: Request, session: SessionDep) -> AuthenticatedUser:
    """Require an authenticated operator. 401 if no valid session."""
    signed = request.cookies.get(config.COOKIE_NAME)
    if signed is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    token = unsign_token(signed)
    if token is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = await resolve_session(session, token)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]


def require_role(*roles: str) -> Callable[[AuthenticatedUser], Awaitable[AuthenticatedUser]]:
    """Dependency factory requiring an authenticated user in one of ``roles``.

    Authentication (401) is delegated to ``current_user``; this layer adds
    authorization, raising 403 when the session is valid but its role isn't
    permitted. Returns the user so a gated endpoint that needs the actor can
    depend on the result directly rather than re-resolving it.

    Validated at factory-creation time so misconfiguration fails loudly at
    import (when routes declare their gates) rather than as a silent 403 on
    every request: an empty ``roles`` would reject everyone, and a typo'd role
    would never match a real user's role.
    """
    if not roles:
        raise ValueError("require_role needs at least one role")
    unknown = set(roles) - VALID_ROLES
    if unknown:
        raise ValueError(
            f"require_role got unknown role(s) {sorted(unknown)}; "
            f"valid roles are {sorted(VALID_ROLES)}"
        )
    allowed = frozenset(roles)

    async def _require_role(user: CurrentUser) -> AuthenticatedUser:
        if allowed.isdisjoint(user.roles):
            raise HTTPException(status_code=403, detail="insufficient role")
        return user

    return _require_role
