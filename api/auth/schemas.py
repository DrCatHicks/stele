"""Pydantic request/response models for the auth API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from api.auth.models import User


class LoginRequest(BaseModel):
    # Normalized (trimmed + lowercased) by the service; matched case-insensitively.
    email: str
    password: str


class CreateUserRequest(BaseModel):
    # Normalized + validated by the service (email lowercased, roles checked).
    email: str
    password: str
    roles: list[str]


class SetRolesRequest(BaseModel):
    # Wholesale replacement of the user's roles; must be non-empty (service rejects).
    roles: list[str]


class ResetPasswordRequest(BaseModel):
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


class GrantDbAccessRequest(BaseModel):
    """Admin request to grant a person DB access at a tier (§3.10 revision).

    ``initial_password`` is required only when the recipient has no app account
    yet (the service raises if it's missing). ``confirm_password`` is the acting
    admin's own password, required for the reviewer (PII) tier as a step-up.
    """

    email: str
    access: str  # 'analyst' | 'reviewer'; validated by the service
    initial_password: str | None = None
    confirm_password: str | None = None


class ProvisionRequestOut(BaseModel):
    """Status of a queued provision/rotate/revoke request, for polling."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    access: str | None
    subject_label: str | None
    login_role: str | None
    status: str
    error_detail: str | None
    created_at: datetime
    processed_at: datetime | None


class MyCredentialOut(BaseModel):
    """A credential the signed-in recipient holds, plus whether its one-time
    password is still waiting to be revealed."""

    login_role: str
    access: str
    status: str
    created_at: datetime
    has_pending_secret: bool


class RevealedSecretOut(BaseModel):
    """The one-time reveal of a freshly-minted password. Returned once, then the
    stored ciphertext is wiped; the client must capture it now."""

    login_role: str
    access: str
    group_role: str
    password: str
    set_role_sql: str
