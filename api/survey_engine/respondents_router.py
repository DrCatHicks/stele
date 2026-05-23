"""Respondent-scoped endpoints.

Withdrawal spans every survey/version a respondent answered, so it lives here
rather than under the survey-scoped /surveys router (design doc §3.8).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from api.auth.deps import require_role
from api.db import SessionDep
from api.survey_engine import service
from api.survey_engine.schemas import WithdrawalOut, WithdrawalRequest

router = APIRouter(prefix="/respondents", tags=["respondents"])

# Erasure is irreversible and destroys PII across every survey a respondent
# answered — admin-only, the narrowest gate (design doc §3.10; resolves the
# M2.2 review follow-up that flagged this endpoint as unauthenticated).
_admin_only = Depends(require_role("admin"))


@router.post(
    "/{respondent_id}/withdrawal", response_model=WithdrawalOut, dependencies=[_admin_only]
)
async def withdraw(
    respondent_id: uuid.UUID, body: WithdrawalRequest, session: SessionDep
) -> WithdrawalOut:
    # Idempotent: a repeat request returns the existing record (already_withdrawn
    # true, zero counts). A respondent with no data is a valid request, not a 404.
    result = await service.withdraw_respondent(session, respondent_id, body.reason)
    return WithdrawalOut.model_validate(result)
