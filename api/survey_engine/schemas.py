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
