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


class WithdrawalAuditOut(BaseModel):
    """A row from the pii.withdrawals erasure audit (admin GDPR console)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    respondent_id: uuid.UUID
    requested_at: datetime
    reason: str | None


class FreeTextReviewItemOut(BaseModel):
    """A high-risk free-text answer in the reviewer screening queue.

    Carries the screened value_text — the reviewer is the PII-cleared role, so
    the endpoint is gated to it. status is None when the answer is still pending
    (no decision recorded yet).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_response_id: int
    respondent_id: uuid.UUID
    survey_id: uuid.UUID
    survey_version: int
    question_name: str
    value_text: str | None
    created_at: datetime
    status: str | None


class FreeTextDecisionRequest(BaseModel):
    # Optional rationale. Not for the screened content / PII.
    note: str | None = None


class FreeTextDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    free_text_id: int
    raw_response_id: int
    question_name: str
    status: str
    reviewed_at: datetime
