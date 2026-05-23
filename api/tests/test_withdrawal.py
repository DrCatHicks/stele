from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RESPONDENT = "11111111-1111-1111-1111-111111111111"
OTHER_RESPONDENT = "22222222-2222-2222-2222-222222222222"

VALID_DEFINITION: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [{"type": "radiogroup", "name": "q1", "choices": ["a", "b"]}],
        }
    ]
}


def _free_text_definition(pii_risk: str | None) -> dict[str, Any]:
    element: dict[str, Any] = {"type": "comment", "name": "ft1"}
    if pii_risk is not None:
        element["pii_risk"] = pii_risk
    return {"pages": [{"name": "p1", "elements": [element]}]}


async def _publish_survey(
    authed_client: AsyncClient, definition: dict[str, Any] | None = None
) -> tuple[str, str]:
    created = await authed_client.post(
        "/surveys", json={"definition_json": definition or VALID_DEFINITION}
    )
    survey_id = created.json()["survey_id"]
    published = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    return survey_id, published.json()["definition_hash"]


async def _submit(
    authed_client: AsyncClient,
    survey_id: str,
    definition_hash: str,
    respondent_id: str,
    payload: dict[str, Any],
) -> int:
    """Submit a response for a fixed respondent; return its raw_response_id."""
    response = await authed_client.post(
        f"/surveys/{survey_id}/versions/1/responses",
        json={
            "definition_hash": definition_hash,
            "payload": payload,
            "shown_questions": list(payload.keys()),
            "respondent_id": respondent_id,
        },
    )
    assert response.status_code == 201
    return int(response.json()["raw_response_id"])


async def _scalar(db_session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await db_session.execute(text(sql), params)).scalar_one()


async def test_withdrawal_nulls_raw_content_keeps_row(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The raw row survives (audit log stays structurally complete) but every
    content column is nulled; the identifying/structural columns are preserved."""
    survey_id, definition_hash = await _publish_survey(authed_client)
    await _submit(authed_client, survey_id, definition_hash, RESPONDENT, {"q1": "a"})

    result = await authed_client.post(f"/respondents/{RESPONDENT}/withdrawal", json={})
    assert result.status_code == 200

    # Assert SQL NULL, not a JSON 'null' scalar: a JSONB column set to JSON null
    # reads back as Python None but is NOT `IS NULL`, and would slip past dbt's
    # `definition_snapshot is not null` staging filter, leaking the withdrawn
    # respondent into the warehouse. jsonb_typeof distinguishes the two.
    row = (
        await db_session.execute(
            text(
                "SELECT (payload IS NULL) AS payload_null, "
                "(shown_questions IS NULL) AS shown_null, "
                "(client_metadata IS NULL) AS meta_null, "
                "(definition_snapshot IS NULL) AS snapshot_null, "
                "jsonb_typeof(payload) AS payload_jtype, "
                "survey_id, survey_version, submitted_at "
                "FROM app.raw_responses WHERE respondent_id = :rid"
            ),
            {"rid": RESPONDENT},
        )
    ).one()
    assert row.payload_null is True
    assert row.shown_null is True
    assert row.meta_null is True
    assert row.snapshot_null is True
    assert row.payload_jtype is None  # not 'null' (a JSON-null scalar)
    # Row kept, structural columns intact.
    assert str(row.survey_id) == survey_id
    assert row.survey_version == 1
    assert row.submitted_at is not None


async def test_withdrawal_purges_read_model(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    survey_id, definition_hash = await _publish_survey(authed_client)
    await _submit(authed_client, survey_id, definition_hash, RESPONDENT, {"q1": "a"})

    await authed_client.post(f"/respondents/{RESPONDENT}/withdrawal", json={})

    responses = await _scalar(
        db_session,
        "SELECT count(*) FROM app.responses WHERE respondent_id = :rid",
        rid=RESPONDENT,
    )
    assert responses == 0
    # response_items cascade-deleted with their parent response.
    items = await _scalar(
        db_session,
        "SELECT count(*) FROM app.response_items ri "
        "JOIN app.responses r ON r.id = ri.response_id WHERE r.respondent_id = :rid",
        rid=RESPONDENT,
    )
    assert items == 0


async def test_withdrawal_deletes_pii(authed_client: AsyncClient, db_session: AsyncSession) -> None:
    """The PII copy must be explicitly deleted: the raw row is NULL-tombstoned,
    not deleted, so the ON DELETE CASCADE on pii.free_text_responses never fires."""
    survey_id, definition_hash = await _publish_survey(authed_client, _free_text_definition("high"))
    raw_id = await _submit(
        authed_client, survey_id, definition_hash, RESPONDENT, {"ft1": "my secret note"}
    )
    # Precondition: the PII row exists before withdrawal.
    assert (
        await _scalar(
            db_session,
            "SELECT count(*) FROM pii.free_text_responses WHERE raw_response_id = :id",
            id=raw_id,
        )
        == 1
    )

    await authed_client.post(f"/respondents/{RESPONDENT}/withdrawal", json={})

    assert (
        await _scalar(
            db_session,
            "SELECT count(*) FROM pii.free_text_responses WHERE raw_response_id = :id",
            id=raw_id,
        )
        == 0
    )


async def test_withdrawal_records_withdrawal_row(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    survey_id, definition_hash = await _publish_survey(authed_client, _free_text_definition("high"))
    await _submit(authed_client, survey_id, definition_hash, RESPONDENT, {"ft1": "secret"})

    result = await authed_client.post(
        f"/respondents/{RESPONDENT}/withdrawal", json={"reason": "ticket-123"}
    )
    body = result.json()
    assert body["already_withdrawn"] is False
    assert body["raw_rows_tombstoned"] == 1
    assert body["responses_purged"] == 1
    assert body["pii_rows_deleted"] == 1

    row = (
        await db_session.execute(
            text(
                "SELECT respondent_id, requested_at, reason "
                "FROM pii.withdrawals WHERE respondent_id = :rid"
            ),
            {"rid": RESPONDENT},
        )
    ).one()
    assert str(row.respondent_id) == RESPONDENT
    assert row.requested_at is not None
    assert row.reason == "ticket-123"


async def test_withdrawal_idempotent_already_withdrawn(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A repeat request returns 200 with already_withdrawn + zero counts, leaves
    a single withdrawal record, and does not touch the original timestamp."""
    survey_id, definition_hash = await _publish_survey(authed_client)
    await _submit(authed_client, survey_id, definition_hash, RESPONDENT, {"q1": "a"})

    first = await authed_client.post(f"/respondents/{RESPONDENT}/withdrawal", json={})
    first_requested_at = first.json()["requested_at"]

    second = await authed_client.post(f"/respondents/{RESPONDENT}/withdrawal", json={})
    assert second.status_code == 200
    body = second.json()
    assert body["already_withdrawn"] is True
    assert body["raw_rows_tombstoned"] == 0
    assert body["responses_purged"] == 0
    assert body["pii_rows_deleted"] == 0
    assert body["requested_at"] == first_requested_at

    count = await _scalar(
        db_session,
        "SELECT count(*) FROM pii.withdrawals WHERE respondent_id = :rid",
        rid=RESPONDENT,
    )
    assert count == 1


async def test_withdrawal_respondent_with_no_data(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A withdrawal for a respondent who never submitted is honored and recorded
    with zero counts (the request is satisfied; there's simply nothing to erase)."""
    result = await authed_client.post(f"/respondents/{RESPONDENT}/withdrawal", json={})
    assert result.status_code == 200
    body = result.json()
    assert body["already_withdrawn"] is False
    assert body["raw_rows_tombstoned"] == 0
    assert body["responses_purged"] == 0
    assert body["pii_rows_deleted"] == 0

    count = await _scalar(
        db_session,
        "SELECT count(*) FROM pii.withdrawals WHERE respondent_id = :rid",
        rid=RESPONDENT,
    )
    assert count == 1


async def test_withdrawal_multi_survey_respondent(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    """One withdrawal tombstones the respondent across every survey they answered."""
    survey_a, hash_a = await _publish_survey(authed_client)
    survey_b, hash_b = await _publish_survey(authed_client)
    await _submit(authed_client, survey_a, hash_a, RESPONDENT, {"q1": "a"})
    await _submit(authed_client, survey_b, hash_b, RESPONDENT, {"q1": "b"})

    result = await authed_client.post(f"/respondents/{RESPONDENT}/withdrawal", json={})
    assert result.json()["raw_rows_tombstoned"] == 2

    # No non-tombstoned raw row remains for the respondent.
    remaining = await _scalar(
        db_session,
        "SELECT count(*) FROM app.raw_responses "
        "WHERE respondent_id = :rid AND definition_snapshot IS NOT NULL",
        rid=RESPONDENT,
    )
    assert remaining == 0
    responses = await _scalar(
        db_session,
        "SELECT count(*) FROM app.responses WHERE respondent_id = :rid",
        rid=RESPONDENT,
    )
    assert responses == 0


async def test_withdrawal_does_not_affect_other_respondents(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Withdrawal is scoped strictly to the named respondent."""
    survey_id, definition_hash = await _publish_survey(authed_client, _free_text_definition("high"))
    await _submit(authed_client, survey_id, definition_hash, RESPONDENT, {"ft1": "mine"})
    other_raw = await _submit(
        authed_client, survey_id, definition_hash, OTHER_RESPONDENT, {"ft1": "theirs"}
    )

    await authed_client.post(f"/respondents/{RESPONDENT}/withdrawal", json={})

    # The other respondent's raw content, read-model, and PII are untouched.
    other_payload = await _scalar(
        db_session,
        "SELECT payload FROM app.raw_responses WHERE respondent_id = :rid",
        rid=OTHER_RESPONDENT,
    )
    assert other_payload == {"ft1": "theirs"}
    other_responses = await _scalar(
        db_session,
        "SELECT count(*) FROM app.responses WHERE respondent_id = :rid",
        rid=OTHER_RESPONDENT,
    )
    assert other_responses == 1
    other_pii = await _scalar(
        db_session,
        "SELECT count(*) FROM pii.free_text_responses WHERE raw_response_id = :id",
        id=other_raw,
    )
    assert other_pii == 1
