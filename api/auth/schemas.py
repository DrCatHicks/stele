"""Pydantic request/response models for the auth API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from api.auth.models import User


class LoginRequest(BaseModel):
    # Normalized (trimmed + lowercased) by the service; matched case-insensitively.
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    roles: list[str]
    disabled: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User, roles: list[str]) -> UserOut:
        # Roles live in app.user_roles, not on the User row, so the caller (which
        # has already loaded them) passes them in rather than reading off the ORM.
        return cls(
            id=user.id,
            email=user.email,
            roles=roles,
            disabled=user.disabled,
            created_at=user.created_at,
        )


class DbCredentialOut(BaseModel):
    """A row of the analyst/reviewer credential registry. Metadata only — there is
    no password field because passwords are never stored (design doc §3.10)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_label: str
    access: str
    login_role: str
    status: str
    provisioned_by: int | None
    created_at: datetime
    revoked_at: datetime | None
    rotated_at: datetime | None
