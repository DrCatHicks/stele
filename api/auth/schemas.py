"""Pydantic request/response models for the auth API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    # Normalized (trimmed + lowercased) by the service; matched case-insensitively.
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    disabled: bool
    created_at: datetime


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
