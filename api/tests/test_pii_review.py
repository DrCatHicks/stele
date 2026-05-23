"""Reviewer PII-screening console (M3.4, design doc §3.9/§3.10).

The reviewer lists high-risk free-text answers, promotes safe ones (which the
next dbt build surfaces in the marts) or rejects them. Setup uses the service
write path directly on the session — publishing is author-gated and the response
submission is public — then the reviewer endpoints are driven over HTTP as a
logged-in reviewer. The marts-side effect of promotion is covered by the dbt
singular test promoted_free_text_in_marts; here we cover the API behaviour.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import service as auth_service
from api.survey_engine import service
from api.survey_engine.schemas import ResponseSubmit

PASSWORD = "correct-horse-battery-staple"

HIGH_RISK_DEFINITION: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [
                # pii_risk defaults to 'high' when absent.
                {"type": "comment", "name": "ft_high", "title": "Describe your role"},
            ],
        }
    ]
}


async def _seed_high_risk_answer(db_session: AsyncSession, answer: str) -> tuple[int, str]:
    """Publish a high-risk free-text survey and submit one answer via the write
    path. Returns (pii.free_text_responses id, the stored answer text).

    The answer is suffixed with a unique token so the lookup is unambiguous even
    when the database already holds committed rows (e.g. from seed runs) — these
    tests run in a rolled-back transaction but share a DB with such fixtures.
    """
    unique_answer = f"{answer} [{uuid.uuid4()}]"
    survey = await service.create_draft(db_session, HIGH_RISK_DEFINITION)
    published = await service.publish(db_session, survey.survey_id, survey.version)
    assert published.definition_hash is not None
    await service.submit_response(
        db_session,
        published.survey_id,
        published.version,
        ResponseSubmit(
            definition_hash=published.definition_hash,
            payload={"ft_high": unique_answer},
            shown_questions=["ft_high"],
            respondent_id=uuid.uuid4(),
        ),
    )
    ft_id = int(
        (
            await db_session.execute(
                text(
                    "SELECT id FROM pii.free_text_responses "
                    "WHERE question_name = 'ft_high' AND value_text = :v"
                ),
                {"v": unique_answer},
            )
        ).scalar_one()
    )
    return ft_id, unique_answer


async def _login_reviewer(client: AsyncClient, db_session: AsyncSession) -> None:
    await auth_service.create_user(db_session, "rev@pii.example.com", PASSWORD, "reviewer")
    resp = await client.post(
        "/auth/login", json={"email": "rev@pii.example.com", "password": PASSWORD}
    )
    assert resp.status_code == 200


async def test_pending_lists_unreviewed_high_risk_text(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A high-risk answer with no decision appears in the pending queue, carrying
    its value_text (the reviewer is PII-cleared) and a null status."""
    ft_id, answer = await _seed_high_risk_answer(db_session, "I lead the platform team")
    await _login_reviewer(client, db_session)

    resp = await client.get("/admin/pii/free-text")
    assert resp.status_code == 200
    item = next(r for r in resp.json() if r["id"] == ft_id)
    assert item["question_name"] == "ft_high"
    assert item["value_text"] == answer
    assert item["status"] is None


async def test_promote_records_decision_and_is_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ft_id, _ = await _seed_high_risk_answer(db_session, "Senior engineer at Acme")
    await _login_reviewer(client, db_session)

    promoted = await client.post(f"/admin/pii/free-text/{ft_id}/promote", json={"note": "ok"})
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "promoted"

    # Re-promoting overwrites, never duplicates: still one decision row for this
    # answer (scoped by raw_response_id so committed seed data can't inflate it).
    again = await client.post(f"/admin/pii/free-text/{ft_id}/promote", json={})
    assert again.status_code == 200
    count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM pii.free_text_review_decisions WHERE raw_response_id IN "
                "(SELECT raw_response_id FROM pii.free_text_responses WHERE id = :id)"
            ),
            {"id": ft_id},
        )
    ).scalar_one()
    assert count == 1

    # It now shows under 'promoted', and is gone from 'pending'.
    promoted_list = await client.get("/admin/pii/free-text", params={"status": "promoted"})
    assert ft_id in {r["id"] for r in promoted_list.json()}
    pending_list = await client.get("/admin/pii/free-text", params={"status": "pending"})
    assert ft_id not in {r["id"] for r in pending_list.json()}


async def test_reject_then_repromote_flips_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ft_id, _ = await _seed_high_risk_answer(db_session, "Works at a startup")
    await _login_reviewer(client, db_session)

    rejected = await client.post(f"/admin/pii/free-text/{ft_id}/reject", json={})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    repromoted = await client.post(f"/admin/pii/free-text/{ft_id}/promote", json={})
    assert repromoted.json()["status"] == "promoted"
    status = (
        await db_session.execute(
            text(
                "SELECT status FROM pii.free_text_review_decisions WHERE raw_response_id IN "
                "(SELECT raw_response_id FROM pii.free_text_responses WHERE id = :id)"
            ),
            {"id": ft_id},
        )
    ).scalar_one()
    assert status == "promoted"


@pytest.mark.parametrize("action", ["promote", "reject"])
async def test_decision_on_missing_answer_404(
    client: AsyncClient, db_session: AsyncSession, action: str
) -> None:
    await _login_reviewer(client, db_session)
    resp = await client.post(f"/admin/pii/free-text/999999/{action}", json={})
    assert resp.status_code == 404
