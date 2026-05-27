"""Field-level free-text scrub (design doc §3.8).

The surgical sibling of withdrawal: a reviewer destroys one high-risk free-text
answer's PII across all three durable copies — the append-only raw_responses
payload value, the operational read-model item, and pii.free_text_responses —
while the rest of the response survives. Setup uses the service write path on the
session (publish is author-gated, submit is public), then the scrub endpoint is
driven over HTTP as a logged-in reviewer.

The warehouse-side invariance is preserved by construction and needs no new dbt
test: stele_etl has no SELECT on pii.free_text_scrubs (it isn't a declared ETL
source, and pii default privileges grant stele_etl nothing), so dbt cannot even
see the scrub, and a scrubbed answer is deliberately indistinguishable in the
marts from any other
redacted high-risk answer (null value_text, value_text_redacted=true) — already
covered by free_text_redaction_parity, shown_set_integrity, and row-count parity.
The one scrub-specific guarantee that *could* regress — that nulling the value in
place keeps the payload key, so the answer stays shown+answered rather than
collapsing into "skipped" downstream (int_response_answers derives `answered`
from jsonb_exists on the key) — is pinned here by asserting the key survives.
"""

from __future__ import annotations

import uuid
from typing import Any

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
                # A second, low-risk answer that must survive the scrub untouched.
                {
                    "type": "comment",
                    "name": "ft_low",
                    "pii_risk": "low",
                    "pii_risk_rationale": "free of identifying content by design",
                },
            ],
        }
    ]
}

PANEL_DEFINITION: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [
                {
                    "type": "paneldynamic",
                    "name": "devices",
                    "templateElements": [
                        {"type": "dropdown", "name": "kind", "choices": ["phone", "laptop"]},
                        {"type": "comment", "name": "nickname"},  # high-risk default
                    ],
                }
            ],
        }
    ]
}


async def _login_reviewer(client: AsyncClient, db_session: AsyncSession) -> None:
    await auth_service.create_user(db_session, "rev@scrub.example.com", PASSWORD, "reviewer")
    resp = await client.post(
        "/auth/login", json={"email": "rev@scrub.example.com", "password": PASSWORD}
    )
    assert resp.status_code == 200


async def _seed_high_risk_answer(
    db_session: AsyncSession, high: str, low: str
) -> tuple[int, int, str]:
    """Publish the two-question survey and submit one response. Returns
    (pii free_text id of the high-risk answer, raw_response_id, unique high text)."""
    token = uuid.uuid4()
    high_unique = f"{high} [{token}]"
    survey = await service.create_draft(db_session, HIGH_RISK_DEFINITION)
    published = await service.publish(db_session, survey.survey_id, survey.version)
    assert published.definition_hash is not None
    await service.submit_response(
        db_session,
        published.survey_id,
        published.version,
        ResponseSubmit(
            definition_hash=published.definition_hash,
            payload={"ft_high": high_unique, "ft_low": low},
            shown_questions=["ft_high", "ft_low"],
            respondent_id=uuid.uuid4(),
        ),
    )
    row = (
        await db_session.execute(
            text(
                "SELECT id, raw_response_id FROM pii.free_text_responses "
                "WHERE question_name = 'ft_high' AND value_text = :v"
            ),
            {"v": high_unique},
        )
    ).one()
    return int(row.id), int(row.raw_response_id), high_unique


async def test_scrub_nulls_value_in_place_keeping_response(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A scrub nulls the high-risk answer in the raw payload (key kept, value
    JSON-null), clears the read-model item and the PII copy, and writes an audit
    row — while shown_questions and the other answer stay intact."""
    ft_id, raw_id, _ = await _seed_high_risk_answer(
        db_session, "I lead the platform team", "no PII here"
    )
    await _login_reviewer(client, db_session)

    resp = await client.post(f"/admin/pii/free-text/{ft_id}/scrub", json={"reason": "ticket-7"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["already_scrubbed"] is False
    assert body["raw_payload_scrubbed"] is True
    assert body["read_model_items_scrubbed"] == 1
    assert body["pii_value_cleared"] is True

    # Raw payload: ft_high key present but value gone; ft_low and shown_questions intact.
    raw = (
        await db_session.execute(
            text(
                "SELECT payload ? 'ft_high' AS has_key, "
                "payload ->> 'ft_high' AS high_val, "
                "payload ->> 'ft_low' AS low_val, "
                "shown_questions FROM app.raw_responses WHERE id = :id"
            ),
            {"id": raw_id},
        )
    ).one()
    assert raw.has_key is True
    assert raw.high_val is None
    assert raw.low_val == "no PII here"
    assert raw.shown_questions == ["ft_high", "ft_low"]

    # Read-model item nulled; PII copy cleared; audit row written with the actor.
    item_val = (
        await db_session.execute(
            text(
                "SELECT value FROM app.response_items WHERE question_name = 'ft_high' "
                "AND response_id IN (SELECT id FROM app.responses WHERE raw_response_id = :id)"
            ),
            {"id": raw_id},
        )
    ).scalar_one()
    assert item_val is None
    pii_val = (
        await db_session.execute(
            text("SELECT value_text FROM pii.free_text_responses WHERE id = :id"),
            {"id": ft_id},
        )
    ).scalar_one()
    assert pii_val is None
    scrub_by = (
        await db_session.execute(
            text(
                "SELECT scrubbed_by FROM pii.free_text_scrubs "
                "WHERE raw_response_id = :id AND question_name = 'ft_high'"
            ),
            {"id": raw_id},
        )
    ).scalar_one()
    assert scrub_by is not None


async def test_scrub_is_idempotent(client: AsyncClient, db_session: AsyncSession) -> None:
    """A repeat scrub is a no-op returning the original record; exactly one audit row."""
    ft_id, raw_id, _ = await _seed_high_risk_answer(db_session, "Senior eng at Acme", "ok")
    await _login_reviewer(client, db_session)

    first = await client.post(f"/admin/pii/free-text/{ft_id}/scrub", json={})
    assert first.status_code == 200
    assert first.json()["already_scrubbed"] is False

    again = await client.post(f"/admin/pii/free-text/{ft_id}/scrub", json={})
    assert again.status_code == 200
    second = again.json()
    assert second["already_scrubbed"] is True
    assert second["raw_payload_scrubbed"] is False
    assert second["scrubbed_at"] == first.json()["scrubbed_at"]

    count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM pii.free_text_scrubs "
                "WHERE raw_response_id = :id AND question_name = 'ft_high'"
            ),
            {"id": raw_id},
        )
    ).scalar_one()
    assert count == 1


async def test_scrubbed_answer_leaves_other_queues(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Once scrubbed, an answer shows only under 'scrubbed' (with no value_text),
    and is gone from pending — even though it had been promoted first."""
    ft_id, _, _ = await _seed_high_risk_answer(db_session, "Works at a startup", "fine")
    await _login_reviewer(client, db_session)

    await client.post(f"/admin/pii/free-text/{ft_id}/promote", json={})
    await client.post(f"/admin/pii/free-text/{ft_id}/scrub", json={})

    scrubbed = await client.get("/admin/pii/free-text", params={"status": "scrubbed"})
    item = next(r for r in scrubbed.json() if r["id"] == ft_id)
    assert item["status"] == "scrubbed"
    assert item["value_text"] is None

    for queue in ("pending", "promoted", "rejected"):
        listing = await client.get("/admin/pii/free-text", params={"status": queue})
        assert ft_id not in {r["id"] for r in listing.json()}


async def test_scrub_missing_answer_404(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_reviewer(client, db_session)
    resp = await client.post("/admin/pii/free-text/999999/scrub", json={})
    assert resp.status_code == 404


async def test_scrub_aborts_when_raw_value_not_nulled(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """If the resolved payload path doesn't match the stored value (here forced by
    corrupting the PII row's question_name so _scrub_target points at a key the
    payload lacks), the scrub must NOT report success: it aborts with 409, writes
    no audit row, and leaves the PII copy intact — so the answer stays retryable
    rather than being marked scrubbed while PII survives in raw_responses."""
    ft_id, raw_id, high_text = await _seed_high_risk_answer(
        db_session, "I lead the platform team", "no PII"
    )
    # Point the scrub at a non-existent payload key (simulates an unresolved path).
    await db_session.execute(
        text("UPDATE pii.free_text_responses SET question_name = 'ghost' WHERE id = :id"),
        {"id": ft_id},
    )
    await _login_reviewer(client, db_session)

    resp = await client.post(f"/admin/pii/free-text/{ft_id}/scrub", json={})
    assert resp.status_code == 409

    # Nothing committed: no audit row, the PII copy still holds its value, and the
    # original answer still sits in the raw payload.
    audit_count = (
        await db_session.execute(
            text("SELECT count(*) FROM pii.free_text_scrubs WHERE raw_response_id = :id"),
            {"id": raw_id},
        )
    ).scalar_one()
    assert audit_count == 0
    pii_val = (
        await db_session.execute(
            text("SELECT value_text FROM pii.free_text_responses WHERE id = :id"),
            {"id": ft_id},
        )
    ).scalar_one()
    assert pii_val == high_text
    raw_val = (
        await db_session.execute(
            text("SELECT payload ->> 'ft_high' FROM app.raw_responses WHERE id = :id"),
            {"id": raw_id},
        )
    ).scalar_one()
    assert raw_val == high_text


async def test_scrub_panel_cell_targets_one_occurrence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Scrubbing one occurrence of a paneldynamic free-text cell nulls only that
    cell in the payload array; the other occurrence and the sibling 'kind' cell
    stay intact."""
    token = uuid.uuid4()
    nick = [f"work phone [{token}]", f"the big one [{token}]"]
    survey = await service.create_draft(db_session, PANEL_DEFINITION)
    published = await service.publish(db_session, survey.survey_id, survey.version)
    assert published.definition_hash is not None
    await service.submit_response(
        db_session,
        published.survey_id,
        published.version,
        ResponseSubmit(
            definition_hash=published.definition_hash,
            payload={
                "devices": [
                    {"kind": "phone", "nickname": nick[0]},
                    {"kind": "laptop", "nickname": nick[1]},
                ]
            },
            shown_questions=["devices"],
            respondent_id=uuid.uuid4(),
        ),
    )
    occ1 = (
        await db_session.execute(
            text(
                "SELECT id, raw_response_id FROM pii.free_text_responses "
                "WHERE question_name = 'devices.nickname' AND value_text = :v"
            ),
            {"v": nick[0]},
        )
    ).one()
    await _login_reviewer(client, db_session)

    resp = await client.post(f"/admin/pii/free-text/{int(occ1.id)}/scrub", json={})
    assert resp.status_code == 200

    raw = (
        await db_session.execute(
            text(
                "SELECT payload -> 'devices' -> 0 ->> 'nickname' AS n0, "
                "payload -> 'devices' -> 1 ->> 'nickname' AS n1, "
                "payload -> 'devices' -> 0 ->> 'kind' AS k0 "
                "FROM app.raw_responses WHERE id = :id"
            ),
            {"id": int(occ1.raw_response_id)},
        )
    ).one()
    assert raw.n0 is None  # scrubbed occurrence
    assert raw.n1 == nick[1]  # sibling occurrence untouched
    assert raw.k0 == "phone"  # sibling cell untouched

    # The PII copy for occurrence 2 still carries its value; occurrence 1 cleared.
    vals = (
        await db_session.execute(
            text(
                "SELECT occurrence, value_text FROM pii.free_text_responses "
                "WHERE question_name = 'devices.nickname' "
                "AND raw_response_id = :id ORDER BY occurrence"
            ),
            {"id": int(occ1.raw_response_id)},
        )
    ).all()
    assert [(v.occurrence, v.value_text) for v in vals] == [(1, None), (2, nick[1])]
