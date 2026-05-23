"""Admin GDPR console — read side of the erasure workflow (design doc §3.8/§3.10).

The withdrawal *trigger* is POST /respondents/{id}/withdrawal (respondents_router);
this exposes the retained pii.withdrawals audit so the admin UI can show what has
been erased. Admin-only: the audit keys on respondent_id (identifying) and erasure
is the admin's responsibility, the narrowest gate.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth.deps import require_role
from api.db import SessionDep
from api.survey_engine import service
from api.survey_engine.schemas import WithdrawalAuditOut

router = APIRouter(prefix="/admin/withdrawals", tags=["admin"])

_admin_only = Depends(require_role("admin"))


@router.get("", response_model=list[WithdrawalAuditOut], dependencies=[_admin_only])
async def list_withdrawals(session: SessionDep) -> list[WithdrawalAuditOut]:
    rows = await service.list_withdrawals(session)
    return [WithdrawalAuditOut.model_validate(w) for w in rows]
