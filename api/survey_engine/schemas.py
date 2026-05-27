"""Pydantic request/response models for the survey API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SurveyDraftCreate(BaseModel):
    definition_json: dict[str, Any]
    # Whether publishing runs the headless round-trip gate (design doc §3.6).
    # Omitted (None): on create defaults to True (gate by default); on edit
    # preserves the draft's current value, so a UI that PUTs only the definition
    # never silently flips a sandbox survey back to gated.
    for_real_respondents: bool | None = None


class SurveyDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    survey_id: uuid.UUID
    version: int
    status: str
    definition_hash: str | None
    for_real_respondents: bool
    published_at: datetime | None
    created_at: datetime


class SurveyDefinitionDetail(SurveyDefinitionOut):
    definition_json: dict[str, Any]


class SurveyListItem(SurveyDefinitionOut):
    # Live (non-tombstoned) response count for this version; backs the dashboard.
    response_count: int
    # The survey's short code, if an operator set one. Survey-level, so it repeats
    # across every version row of the same survey; None when unset.
    short_code: str | None = None


class ShortCodeSet(BaseModel):
    # Raw operator input; normalised + validated server-side (lowercase, link-safe).
    short_code: str


class ShortCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    survey_id: uuid.UUID
    short_code: str


class ShortCodeResolved(BaseModel):
    """What a /s/<code> link resolves to: a survey + its latest published version."""

    survey_id: uuid.UUID
    version: int


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
    # 1-based panel occurrence (paneldynamic cell); 1 for a plain free-text question.
    occurrence: int
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


class FreeTextScrubRequest(BaseModel):
    # Optional note (e.g. ticket reference). Not for the scrubbed content / PII.
    reason: str | None = None


class FreeTextScrubOut(BaseModel):
    """Outcome of a field-level free-text scrub (design §3.8).

    Counts/flags report what *this* call changed; on the idempotent path (the
    answer was already scrubbed) `already_scrubbed` is true and they are zero/false.
    """

    model_config = ConfigDict(from_attributes=True)

    free_text_id: int
    raw_response_id: int
    question_name: str
    occurrence: int
    scrubbed_at: datetime
    already_scrubbed: bool
    raw_payload_scrubbed: bool
    read_model_items_scrubbed: int
    pii_value_cleared: bool
