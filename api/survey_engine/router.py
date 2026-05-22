"""Survey authoring + publishing endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from api.db import SessionDep
from api.survey_engine import service
from api.survey_engine.schemas import (
    SurveyDefinitionDetail,
    SurveyDefinitionOut,
    SurveyDraftCreate,
)

router = APIRouter(prefix="/surveys", tags=["surveys"])


@router.post("", status_code=201, response_model=SurveyDefinitionOut)
async def create_survey(body: SurveyDraftCreate, session: SessionDep) -> SurveyDefinitionOut:
    survey = await service.create_draft(session, body.definition_json)
    return SurveyDefinitionOut.model_validate(survey)


@router.post("/{survey_id}/drafts", status_code=201, response_model=SurveyDefinitionOut)
async def create_draft_version(
    survey_id: uuid.UUID, session: SessionDep, clone: bool = True
) -> SurveyDefinitionOut:
    try:
        survey = await service.create_draft_version(session, survey_id, clone)
    except service.SurveyNotFound:
        raise HTTPException(status_code=404, detail="survey not found") from None
    return SurveyDefinitionOut.model_validate(survey)


@router.put("/{survey_id}/versions/{version}", response_model=SurveyDefinitionOut)
async def edit_survey(
    survey_id: uuid.UUID, version: int, body: SurveyDraftCreate, session: SessionDep
) -> SurveyDefinitionOut:
    try:
        survey = await service.edit_draft(session, survey_id, version, body.definition_json)
    except service.SurveyNotFound:
        raise HTTPException(status_code=404, detail="survey version not found") from None
    except service.SurveyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return SurveyDefinitionOut.model_validate(survey)


@router.post("/{survey_id}/versions/{version}/publish", response_model=SurveyDefinitionOut)
async def publish_survey(
    survey_id: uuid.UUID, version: int, session: SessionDep
) -> SurveyDefinitionOut:
    try:
        survey = await service.publish(session, survey_id, version)
    except service.SurveyNotFound:
        raise HTTPException(status_code=404, detail="survey version not found") from None
    except service.SurveyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except service.InvalidDefinition as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return SurveyDefinitionOut.model_validate(survey)


@router.get("/{survey_id}/versions/{version}", response_model=SurveyDefinitionDetail)
async def get_survey(
    survey_id: uuid.UUID, version: int, session: SessionDep
) -> SurveyDefinitionDetail:
    survey = await service.get_definition(session, survey_id, version)
    if survey is None:
        raise HTTPException(status_code=404, detail="survey version not found")
    return SurveyDefinitionDetail.model_validate(survey)
