"""Pydantic request/response models for the survey API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SurveyDraftCreate(BaseModel):
    definition_json: dict[str, Any]


class SurveyDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    survey_id: uuid.UUID
    version: int
    status: str
    definition_hash: str | None
    published_at: datetime | None
    created_at: datetime


class SurveyDefinitionDetail(SurveyDefinitionOut):
    definition_json: dict[str, Any]


class ResponseSubmit(BaseModel):
    # The hash the respondent rendered against; used to reject submissions to a
    # definition that has since drifted (design doc §6 risk table).
    definition_hash: str
    payload: dict[str, Any]
    shown_questions: list[str]
    respondent_id: uuid.UUID | None = None
    client_metadata: dict[str, Any] | None = None


class ResponseSubmitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    raw_response_id: int
    respondent_id: uuid.UUID
    submitted_at: datetime


class WithdrawalRequest(BaseModel):
    # Optional note (e.g. ticket reference). Not for PII.
    reason: str | None = None


class WithdrawalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    respondent_id: uuid.UUID
    requested_at: datetime
    already_withdrawn: bool
    raw_rows_tombstoned: int
    responses_purged: int
    pii_rows_deleted: int
