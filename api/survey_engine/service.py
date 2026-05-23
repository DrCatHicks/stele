"""Survey authoring + publishing logic.

Drafts are mutable; publishing freezes a definition: it validates, computes a
canonical SHA-256 hash, and flips status to 'published'. Published rows are
immutable — a change means a new draft at the next version (design doc §3.6,
invariant 2).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.survey_engine.models import (
    FreeTextResponse,
    FreeTextReviewDecision,
    RawResponse,
    Response,
    ResponseItem,
    SurveyDefinition,
    Withdrawal,
)
from api.survey_engine.schemas import ResponseSubmit

# SurveyJS free-text element types. value goes to value_text downstream, routed
# by pii_risk (design doc §3.9). Other types resolve via options / numeric / date.
FREE_TEXT_TYPES = frozenset({"text", "comment"})


class SurveyNotFound(Exception):
    pass


class SurveyConflict(Exception):
    """Operation not allowed in the survey's current state."""


class InvalidDefinition(Exception):
    """Definition failed publish-time validation."""


class SubmissionRejected(Exception):
    """Submission could not be accepted (survey not published, or hash drift)."""


class FreeTextResponseNotFound(Exception):
    """No high-risk free-text answer with the given id (reviewer screening)."""


def canonical_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FreeTextQuestion:
    """A free-text question and its PII-risk tagging, read from the definition."""

    name: str
    pii_risk: str | None
    pii_risk_rationale: str | None

    @property
    def effective_risk(self) -> str:
        # Absent pii_risk defaults to 'high' — the safe path is the default
        # (design doc §3.9, CLAUDE.md §"silent defaults").
        return self.pii_risk or "high"


def _free_text_answer_to_text(answer: Any) -> str | None:
    if answer is None:
        return None
    if isinstance(answer, str):
        return answer
    # Keep non-string JSON payload values auditable and stable in storage.
    return json.dumps(answer, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _iter_elements(definition: dict[str, Any]) -> Iterator[dict[str, Any]]:
    # Both SurveyJS shapes: pages[].elements[] and top-level elements[]. Mirrors
    # the unnest in dbt's int_survey_elements so API and warehouse agree on what
    # counts as a question.
    pages = definition.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, dict):
                for element in page.get("elements", []) or []:
                    if isinstance(element, dict):
                        yield element
    for element in definition.get("elements", []) or []:
        if isinstance(element, dict):
            yield element


def extract_free_text_questions(definition: dict[str, Any]) -> list[FreeTextQuestion]:
    """Free-text questions (SurveyJS text/comment) with their pii_risk tagging."""
    questions: list[FreeTextQuestion] = []
    for element in _iter_elements(definition):
        if element.get("type") in FREE_TEXT_TYPES and element.get("name"):
            questions.append(
                FreeTextQuestion(
                    name=element["name"],
                    pii_risk=element.get("pii_risk"),
                    pii_risk_rationale=element.get("pii_risk_rationale"),
                )
            )
    return questions


def validate_definition(definition: dict[str, Any]) -> None:
    # Minimal gate for the slice. Full schema/lint/round-trip checks land in M3.
    if not definition:
        raise InvalidDefinition("definition must be a non-empty object")
    if "pages" not in definition and "elements" not in definition:
        raise InvalidDefinition("definition must contain 'pages' or 'elements'")
    # Free-text PII gate (invariant 6): pii_risk must be low/high if set, and a
    # downgrade to 'low' demands an explicit rationale at definition time. Never
    # silently downgrade — the default is 'high'.
    seen_free_text_names: set[str] = set()
    for question in extract_free_text_questions(definition):
        if question.name in seen_free_text_names:
            raise InvalidDefinition(
                f"duplicate free-text question name {question.name!r} is not allowed"
            )
        seen_free_text_names.add(question.name)
        if question.pii_risk is not None and question.pii_risk not in ("low", "high"):
            raise InvalidDefinition(
                f"question '{question.name}': pii_risk must be 'low' or 'high', "
                f"got {question.pii_risk!r}"
            )
        if question.effective_risk == "low" and not (question.pii_risk_rationale or "").strip():
            raise InvalidDefinition(
                f"question '{question.name}': downgrading pii_risk to 'low' requires a "
                "non-empty pii_risk_rationale"
            )


async def _get(
    session: AsyncSession, survey_id: uuid.UUID, version: int
) -> SurveyDefinition | None:
    result = await session.execute(
        select(SurveyDefinition).where(
            SurveyDefinition.survey_id == survey_id,
            SurveyDefinition.version == version,
        )
    )
    return result.scalar_one_or_none()


async def create_draft(session: AsyncSession, definition_json: dict[str, Any]) -> SurveyDefinition:
    survey = SurveyDefinition(
        survey_id=uuid.uuid4(),
        version=1,
        definition_json=definition_json,
        status="draft",
    )
    session.add(survey)
    await session.commit()
    await session.refresh(survey)
    return survey


async def create_draft_version(
    session: AsyncSession, survey_id: uuid.UUID, clone: bool
) -> SurveyDefinition:
    """Start a new draft at the next version, optionally cloning the latest definition."""
    max_version = (
        await session.execute(
            select(func.max(SurveyDefinition.version)).where(
                SurveyDefinition.survey_id == survey_id
            )
        )
    ).scalar_one_or_none()
    if max_version is None:
        raise SurveyNotFound(str(survey_id))

    definition: dict[str, Any] = {}
    if clone:
        latest = await _get(session, survey_id, max_version)
        if latest is not None:
            definition = dict(latest.definition_json)

    survey = SurveyDefinition(
        survey_id=survey_id,
        version=max_version + 1,
        definition_json=definition,
        status="draft",
    )
    session.add(survey)
    await session.commit()
    await session.refresh(survey)
    return survey


async def edit_draft(
    session: AsyncSession,
    survey_id: uuid.UUID,
    version: int,
    definition_json: dict[str, Any],
) -> SurveyDefinition:
    survey = await _get(session, survey_id, version)
    if survey is None:
        raise SurveyNotFound(f"{survey_id} v{version}")
    if survey.status != "draft":
        raise SurveyConflict("published surveys are immutable; create a new draft to make changes")
    survey.definition_json = definition_json
    await session.commit()
    await session.refresh(survey)
    return survey


async def publish(session: AsyncSession, survey_id: uuid.UUID, version: int) -> SurveyDefinition:
    survey = await _get(session, survey_id, version)
    if survey is None:
        raise SurveyNotFound(f"{survey_id} v{version}")
    if survey.status != "draft":
        raise SurveyConflict("survey is already published")
    validate_definition(survey.definition_json)
    survey.definition_hash = canonical_hash(survey.definition_json)
    survey.status = "published"
    survey.published_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(survey)
    return survey


async def get_definition(
    session: AsyncSession, survey_id: uuid.UUID, version: int
) -> SurveyDefinition | None:
    return await _get(session, survey_id, version)


async def list_definitions(session: AsyncSession) -> list[SurveyDefinition]:
    """All survey/version rows, newest first — backs the admin survey list.

    Returns every (survey_id, version) row, not just the latest per survey: the
    admin needs to see draft and published versions side by side to decide where
    to edit or publish. Definition JSON is omitted from the list view (callers use
    the detail endpoint); the ORM rows still carry it but the list schema drops it.
    """
    result = await session.execute(
        select(SurveyDefinition).order_by(
            SurveyDefinition.created_at.desc(), SurveyDefinition.version.desc()
        )
    )
    return list(result.scalars().all())


async def submit_response(
    session: AsyncSession,
    survey_id: uuid.UUID,
    version: int,
    submission: ResponseSubmit,
) -> Response:
    """Append the raw submission and derive the normalized read-model in one
    transaction. Both come from the same in-memory payload — the API never reads
    raw_responses back to build the read-model (invariant 1/4).
    """
    survey = await _get(session, survey_id, version)
    if survey is None:
        raise SurveyNotFound(f"{survey_id} v{version}")
    if survey.status != "published":
        raise SubmissionRejected("survey version is not published")
    if submission.definition_hash != survey.definition_hash:
        raise SubmissionRejected("definition hash mismatch; the survey has drifted")

    respondent_id = submission.respondent_id or uuid.uuid4()
    submitted_at = datetime.now(UTC)

    raw = RawResponse(
        respondent_id=respondent_id,
        survey_id=survey_id,
        survey_version=version,
        submitted_at=submitted_at,
        payload=submission.payload,
        shown_questions=submission.shown_questions,
        client_metadata=submission.client_metadata,
        # Freeze the definition this response was answered against, so the
        # warehouse can rebuild dimensions from raw_responses alone. The
        # published row is immutable, so this snapshot can never drift.
        definition_snapshot={
            "definition": survey.definition_json,
            "definition_hash": survey.definition_hash,
            "published_at": survey.published_at.isoformat() if survey.published_at else None,
        },
    )
    session.add(raw)
    await session.flush()

    response = Response(
        raw_response_id=raw.id,
        respondent_id=respondent_id,
        survey_id=survey_id,
        survey_version=version,
        submitted_at=submitted_at,
    )
    session.add(response)
    await session.flush()

    for question_name, value in submission.payload.items():
        session.add(ResponseItem(response_id=response.id, question_name=question_name, value=value))

    # Copy high-PII-risk free-text answers into the restricted pii store for the
    # reviewer (design doc §3.9). The operational read-model above stays a
    # faithful copy of the payload — same app-schema trust boundary as raw, so
    # redacting it gains nothing; the analyst boundary is the dbt marts.
    for question in extract_free_text_questions(survey.definition_json):
        if question.effective_risk == "high" and question.name in submission.payload:
            answer = submission.payload[question.name]
            session.add(
                FreeTextResponse(
                    raw_response_id=raw.id,
                    question_name=question.name,
                    value_text=_free_text_answer_to_text(answer),
                    pii_risk="high",
                )
            )

    await session.commit()
    await session.refresh(response)
    return response


@dataclass(frozen=True)
class FreeTextReviewItem:
    """One high-risk free-text answer in the reviewer queue, with its raw-row
    context and (when decided) screening status. status is None when pending."""

    id: int
    raw_response_id: int
    respondent_id: uuid.UUID
    survey_id: uuid.UUID
    survey_version: int
    question_name: str
    value_text: str | None
    created_at: datetime
    status: str | None


@dataclass(frozen=True)
class FreeTextDecisionResult:
    """Outcome of a recorded promote/reject decision."""

    free_text_id: int
    raw_response_id: int
    question_name: str
    status: str
    reviewed_at: datetime


@dataclass(frozen=True)
class WithdrawalResult:
    """Outcome of a withdrawal: the audit record plus what this call erased.

    On the idempotent path (respondent already withdrawn) the counts are zero
    and `already_withdrawn` is true — the original `requested_at` is preserved.
    """

    respondent_id: uuid.UUID
    requested_at: datetime
    already_withdrawn: bool
    raw_rows_tombstoned: int
    responses_purged: int
    pii_rows_deleted: int


async def withdraw_respondent(
    session: AsyncSession, respondent_id: uuid.UUID, reason: str | None = None
) -> WithdrawalResult:
    """Tombstone every trace of a respondent across all surveys, in one
    transaction (design doc §3.8 steps 1,2,3,5). Step 4 (marts) is automatic on
    the next dbt build: stg_raw_responses excludes the now-null-snapshot rows.

    Erasure is idempotent by nature: a repeat request for an already-withdrawn
    respondent returns the existing record with zero counts, without
    re-processing or overwriting the original timestamp. A respondent with no
    responses is still recorded as withdrawn (request honored, zero counts).
    """
    existing = (
        await session.execute(select(Withdrawal).where(Withdrawal.respondent_id == respondent_id))
    ).scalar_one_or_none()
    if existing is not None:
        return WithdrawalResult(
            respondent_id=respondent_id,
            requested_at=existing.requested_at,
            already_withdrawn=True,
            raw_rows_tombstoned=0,
            responses_purged=0,
            pii_rows_deleted=0,
        )

    # Step 1 — record the withdrawal. The unique constraint on respondent_id is
    # the real guard against a concurrent double-request; the check above just
    # makes the common repeat case a clean no-op.
    requested_at = datetime.now(UTC)
    session.add(Withdrawal(respondent_id=respondent_id, requested_at=requested_at, reason=reason))
    await session.flush()

    # Resolve the raw_response_ids up front. pii.free_text_responses cascades
    # only on a raw-row DELETE, and the tombstone NULLs (never deletes) raw
    # rows, so the PII deletion must be explicit (invariant 1 / design §3.8).
    raw_ids = (
        (
            await session.execute(
                select(RawResponse.id).where(RawResponse.respondent_id == respondent_id)
            )
        )
        .scalars()
        .all()
    )

    # Step 5 — delete the PII copy first (erase identifying data as early as
    # possible). No-op when the respondent had no high-risk free text.
    pii_rows_deleted = 0
    if raw_ids:
        pii_result = cast(
            CursorResult[Any],
            await session.execute(
                delete(FreeTextResponse).where(FreeTextResponse.raw_response_id.in_(raw_ids))
            ),
        )
        pii_rows_deleted = pii_result.rowcount or 0

    # Step 3 — purge the rebuildable read-model. response_items rows cascade via
    # the ON DELETE CASCADE FK on response_items.response_id.
    responses_result = cast(
        CursorResult[Any],
        await session.execute(delete(Response).where(Response.respondent_id == respondent_id)),
    )
    responses_purged = responses_result.rowcount or 0

    # Step 2 — tombstone raw: null the four content columns, keep the row so the
    # append-only audit log stays structurally complete. This is the sole
    # sanctioned UPDATE of raw_responses (CLAUDE.md / design §3.8); id,
    # respondent_id, survey_id, survey_version and submitted_at are preserved.
    raw_result = cast(
        CursorResult[Any],
        await session.execute(
            update(RawResponse)
            .where(RawResponse.respondent_id == respondent_id)
            .values(
                payload=None,
                shown_questions=None,
                client_metadata=None,
                definition_snapshot=None,
            )
        ),
    )
    raw_rows_tombstoned = raw_result.rowcount or 0

    await session.commit()
    return WithdrawalResult(
        respondent_id=respondent_id,
        requested_at=requested_at,
        already_withdrawn=False,
        raw_rows_tombstoned=raw_rows_tombstoned,
        responses_purged=responses_purged,
        pii_rows_deleted=pii_rows_deleted,
    )


async def list_withdrawals(
    session: AsyncSession, limit: int = 100, offset: int = 0
) -> list[Withdrawal]:
    """The pii.withdrawals erasure audit, newest first — backs the admin GDPR
    console. Read-only; the trigger path is withdraw_respondent."""
    result = await session.execute(
        select(Withdrawal).order_by(Withdrawal.requested_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


# Reviewer screening states. 'pending' = a high-risk free-text row with no
# decision yet (LEFT JOIN miss); the other two read the recorded decision.
ReviewStatus = Literal["pending", "promoted", "rejected"]


async def list_free_text_for_review(
    session: AsyncSession,
    status: ReviewStatus = "pending",
    limit: int = 100,
    offset: int = 0,
) -> list[FreeTextReviewItem]:
    """High-risk free-text answers in the reviewer queue (design §3.9).

    Joins pii.free_text_responses to its raw row (for respondent/survey context)
    and LEFT JOINs the decision table. 'pending' filters to answers with no
    decision; 'promoted'/'rejected' filter to the recorded outcome. Carries the
    screened value_text — gated to the reviewer role at the route.
    """
    decision = FreeTextReviewDecision
    query = (
        select(
            FreeTextResponse.id,
            FreeTextResponse.raw_response_id,
            RawResponse.respondent_id,
            RawResponse.survey_id,
            RawResponse.survey_version,
            FreeTextResponse.question_name,
            FreeTextResponse.value_text,
            FreeTextResponse.created_at,
            decision.status,
        )
        .join(RawResponse, RawResponse.id == FreeTextResponse.raw_response_id)
        .outerjoin(
            decision,
            (decision.raw_response_id == FreeTextResponse.raw_response_id)
            & (decision.question_name == FreeTextResponse.question_name),
        )
        .order_by(FreeTextResponse.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status == "pending":
        query = query.where(decision.status.is_(None))
    else:
        query = query.where(decision.status == status)

    rows = (await session.execute(query)).all()
    return [
        FreeTextReviewItem(
            id=r.id,
            raw_response_id=r.raw_response_id,
            respondent_id=r.respondent_id,
            survey_id=r.survey_id,
            survey_version=r.survey_version,
            question_name=r.question_name,
            value_text=r.value_text,
            created_at=r.created_at,
            status=r.status,
        )
        for r in rows
    ]


async def record_free_text_decision(
    session: AsyncSession,
    free_text_id: int,
    reviewer_id: int | None,
    status: Literal["promoted", "rejected"],
    note: str | None = None,
) -> FreeTextDecisionResult:
    """Record (or update) a reviewer's promote/reject decision for one high-risk
    free-text answer. Idempotent: re-deciding the same answer overwrites the prior
    decision (status, reviewer, timestamp, note) rather than inserting a duplicate
    — the (raw_response_id, question_name) unique constraint is the anchor.

    Raises FreeTextResponseNotFound if free_text_id is unknown.
    """
    free_text = (
        await session.execute(select(FreeTextResponse).where(FreeTextResponse.id == free_text_id))
    ).scalar_one_or_none()
    if free_text is None:
        raise FreeTextResponseNotFound

    decided_at = datetime.now(UTC)
    existing = (
        await session.execute(
            select(FreeTextReviewDecision).where(
                FreeTextReviewDecision.raw_response_id == free_text.raw_response_id,
                FreeTextReviewDecision.question_name == free_text.question_name,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            FreeTextReviewDecision(
                raw_response_id=free_text.raw_response_id,
                question_name=free_text.question_name,
                status=status,
                reviewed_by=reviewer_id,
                reviewed_at=decided_at,
                note=note,
            )
        )
    else:
        existing.status = status
        existing.reviewed_by = reviewer_id
        existing.reviewed_at = decided_at
        existing.note = note

    await session.commit()
    return FreeTextDecisionResult(
        free_text_id=free_text_id,
        raw_response_id=free_text.raw_response_id,
        question_name=free_text.question_name,
        status=status,
        reviewed_at=decided_at,
    )
