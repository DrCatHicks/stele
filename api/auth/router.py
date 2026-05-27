"""Authentication endpoints: login, logout, current-user.

Login sets a signed, httpOnly, secure cookie carrying the session token; logout
deletes the server-side session and clears the cookie. ``GET /auth/me`` returns
the resolved user (or 401), which the admin UI uses to reflect login state.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from api.auth import config, service
from api.auth.deps import CurrentUser, sign_token, unsign_token
from api.auth.schemas import LoginRequest, UserOut
from api.db import SessionDep

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=sign_token(token),
        httponly=True,
        secure=config.cookie_secure(),
        samesite="lax",
        max_age=int(config.SESSION_TTL.total_seconds()),
    )


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, response: Response, session: SessionDep) -> UserOut:
    try:
        user = await service.authenticate(session, body.email, body.password)
    except service.InvalidCredentials:
        # Uniform 401 — never reveal whether the email exists or the account is
        # disabled (design doc §3.10; see service.authenticate).
        raise HTTPException(status_code=401, detail="invalid email or password") from None
    row = await service.create_session(session, user)
    _set_session_cookie(response, row.token)
    roles = await service.get_roles(session, user.id)
    return UserOut.from_user(user, roles)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: SessionDep) -> Response:
    # Revoke the server-side session if the cookie carries a valid token, then
    # clear the cookie regardless. Logout is idempotent and never errors.
    signed = request.cookies.get(config.COOKIE_NAME)
    if signed is not None:
        token = unsign_token(signed)
        if token is not None:
            await service.delete_session(session, token)
    response.delete_cookie(key=config.COOKIE_NAME, httponly=True, samesite="lax")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser, session: SessionDep) -> UserOut:
    # current_user already validated the session and resolved the roles; re-read
    # the full row for the remaining output fields.
    full = await service.get_user(session, user.id)
    if full is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return UserOut.from_user(full, sorted(user.roles))
