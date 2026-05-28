"""Admin user-management API (M9.2): create and manage operator accounts.

Every route is gated to {admin} — operator administration is the narrowest
privilege (design doc §3.10). Until now the only way to mint a user was the
``scripts/bootstrap_admin.py`` CLI; this closes that gap with a CRUD surface the
admin UI (M9.3) drives.

The destructive/safety-bearing operations push their rules into the service
layer (``api.auth.service``), which raises typed errors this router maps to HTTP
status codes:

- ``DuplicateUser`` → 409 (email already registered)
- ``InvalidRole`` → 422 (unknown role, or an empty role set)
- ``UserNotFound`` → 404
- ``LastAdmin`` → 409 (would leave no enabled admin)

Last-admin protection and session revocation on reset live in the service so the
same guarantees hold no matter who calls them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import service
from api.auth.deps import require_role
from api.auth.schemas import CreateUserRequest, ResetPasswordRequest, SetRolesRequest, UserOut
from api.db import SessionDep

router = APIRouter(prefix="/admin/users", tags=["admin"])

# Operator administration is admin-only — the narrowest gate (design doc §3.10).
_admin_only = Depends(require_role("admin"))


@router.get("", response_model=list[UserOut], dependencies=[_admin_only])
async def list_users(session: SessionDep) -> list[UserOut]:
    return [UserOut.from_user(user, roles) for user, roles in await service.list_users(session)]


@router.post("", response_model=UserOut, status_code=201, dependencies=[_admin_only])
async def create_user(body: CreateUserRequest, session: SessionDep) -> UserOut:
    try:
        user = await service.create_user(session, body.email, body.password, body.roles)
    except service.DuplicateUser:
        raise HTTPException(status_code=409, detail="email already registered") from None
    except service.InvalidRole as exc:
        raise HTTPException(status_code=422, detail=f"invalid role(s): {exc}") from None
    roles = await service.get_roles(session, user.id)
    return UserOut.from_user(user, roles)


@router.put("/{user_id}/roles", response_model=UserOut, dependencies=[_admin_only])
async def set_roles(user_id: int, body: SetRolesRequest, session: SessionDep) -> UserOut:
    try:
        user, roles = await service.set_user_roles(session, user_id, body.roles)
    except service.UserNotFound:
        raise HTTPException(status_code=404, detail="user not found") from None
    except service.InvalidRole as exc:
        raise HTTPException(status_code=422, detail=f"invalid role(s): {exc}") from None
    except service.LastAdmin:
        raise HTTPException(
            status_code=409, detail="cannot remove the last enabled admin"
        ) from None
    return UserOut.from_user(user, roles)


@router.post("/{user_id}/disable", response_model=UserOut, dependencies=[_admin_only])
async def disable_user(user_id: int, session: SessionDep) -> UserOut:
    try:
        user, roles = await service.set_user_disabled(session, user_id, disabled=True)
    except service.UserNotFound:
        raise HTTPException(status_code=404, detail="user not found") from None
    except service.LastAdmin:
        raise HTTPException(
            status_code=409, detail="cannot disable the last enabled admin"
        ) from None
    return UserOut.from_user(user, roles)


@router.post("/{user_id}/enable", response_model=UserOut, dependencies=[_admin_only])
async def enable_user(user_id: int, session: SessionDep) -> UserOut:
    try:
        user, roles = await service.set_user_disabled(session, user_id, disabled=False)
    except service.UserNotFound:
        raise HTTPException(status_code=404, detail="user not found") from None
    return UserOut.from_user(user, roles)


@router.post("/{user_id}/reset-password", status_code=204, dependencies=[_admin_only])
async def reset_password(user_id: int, body: ResetPasswordRequest, session: SessionDep) -> None:
    try:
        await service.reset_password(session, user_id, body.password)
    except service.UserNotFound:
        raise HTTPException(status_code=404, detail="user not found") from None
