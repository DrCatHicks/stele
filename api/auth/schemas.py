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
