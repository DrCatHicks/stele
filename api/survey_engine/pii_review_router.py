"""Reviewer PII-screening console (design doc §3.9 / §3.10).

The reviewer is the PII-cleared role: it screens high-PII-risk free text and
promotes individually safe answers to the analyst marts, or rejects them. These
endpoints are gated to the reviewer role only — authors don't read PII, and the
admin's destructive/operational gates are elsewhere. The screened value_text is
returned in the queue because reading it is the whole point of the screening pass.

Promotion is recorded as a per-response decision (pii.free_text_review_decisions);
the next dbt build surfaces value_text in marts.fact_response_item for promoted
rows. This endpoint never touches the warehouse directly.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException

from api.auth.deps import require_role
from api.auth.service import AuthenticatedUser
from api.db import SessionDep
from api.survey_engine import service
from api.survey_engine.schemas import (
    FreeTextDecisionOut,
    FreeTextDecisionRequest,
    FreeTextReviewItemOut,
    FreeTextScrubOut,
    FreeTextScrubRequest,
)

router = APIRouter(prefix="/admin/pii/free-text", tags=["admin"])

# Screening and promotion are the reviewer's job alone (design doc §3.10). The
# dependency returns the user so the decision endpoints can record who decided.
Reviewer = Annotated[AuthenticatedUser, Depends(require_role("reviewer"))]


@router.get("", response_model=list[FreeTextReviewItemOut])
async def list_for_review(
    session: SessionDep,
    reviewer: Reviewer,
    status: Literal["pending", "promoted", "rejected", "scrubbed"] = "pending",
) -> list[FreeTextReviewItemOut]:
    items = await service.list_free_text_for_review(session, status=status)
    return [FreeTextReviewItemOut.model_validate(i) for i in items]


async def _decide(
    session: SessionDep,
    free_text_id: int,
    reviewer: AuthenticatedUser,
    status: Literal["promoted", "rejected"],
    note: str | None,
) -> FreeTextDecisionOut:
    try:
        result = await service.record_free_text_decision(
            session, free_text_id, reviewer.id, status, note
        )
    except service.FreeTextResponseNotFound:
        raise HTTPException(status_code=404, detail="free-text answer not found") from None
    return FreeTextDecisionOut.model_validate(result)


@router.post("/{free_text_id}/promote", response_model=FreeTextDecisionOut)
async def promote(
    free_text_id: int,
    body: FreeTextDecisionRequest,
    session: SessionDep,
    reviewer: Reviewer,
) -> FreeTextDecisionOut:
    # Idempotent: re-promoting overwrites the prior decision, no duplicate row.
    return await _decide(session, free_text_id, reviewer, "promoted", body.note)


@router.post("/{free_text_id}/reject", response_model=FreeTextDecisionOut)
async def reject(
    free_text_id: int,
    body: FreeTextDecisionRequest,
    session: SessionDep,
    reviewer: Reviewer,
) -> FreeTextDecisionOut:
    return await _decide(session, free_text_id, reviewer, "rejected", body.note)


@router.post("/{free_text_id}/scrub", response_model=FreeTextScrubOut)
async def scrub(
    free_text_id: int,
    body: FreeTextScrubRequest,
    session: SessionDep,
    reviewer: Reviewer,
) -> FreeTextScrubOut:
    """Destroy this answer's PII in place across raw payload, read-model, and the
    PII copy (field-level scrub, design §3.8). Destructive and reviewer-only;
    idempotent (a repeat scrub returns the original record)."""
    try:
        result = await service.scrub_free_text(session, free_text_id, reviewer.id, body.reason)
    except service.FreeTextResponseNotFound:
        raise HTTPException(status_code=404, detail="free-text answer not found") from None
    except service.ScrubIncomplete as exc:
        # The raw value wasn't verifiably nulled; nothing was committed. 409 so the
        # operator knows the answer is NOT scrubbed and can retry (vs a silent 200).
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return FreeTextScrubOut.model_validate(result)
