"""Respondent-scoped endpoints.

Withdrawal spans every survey/version a respondent answered, so it lives here
rather than under the survey-scoped /surveys router (design doc §3.8).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from api.db import SessionDep
from api.survey_engine import service
from api.survey_engine.schemas import WithdrawalOut, WithdrawalRequest

router = APIRouter(prefix="/respondents", tags=["respondents"])


@router.post("/{respondent_id}/withdrawal", response_model=WithdrawalOut)
async def withdraw(
    respondent_id: uuid.UUID, body: WithdrawalRequest, session: SessionDep
) -> WithdrawalOut:
    # Idempotent: a repeat request returns the existing record (already_withdrawn
    # true, zero counts). A respondent with no data is a valid request, not a 404.
    result = await service.withdraw_respondent(session, respondent_id, body.reason)
    return WithdrawalOut.model_validate(result)
