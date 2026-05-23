"""FastAPI dependencies for resolving and requiring the current user.

The cookie carries a *signed* opaque token (itsdangerous). The signature is
tamper-evidence only — authority comes from the server-side session row, not the
cookie — but it lets a forged/corrupted cookie be rejected before any DB lookup
and namespaces the secret to this use (design doc §3.10).

``current_user`` resolves cookie → session → active user or raises 401.
RBAC (``require_role``) lands in M3.2 on top of this.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer

from api.auth import config
from api.auth.service import AuthenticatedUser, resolve_session
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
