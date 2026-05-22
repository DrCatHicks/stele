import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_response(session: AsyncSession) -> int:
    """Insert a published survey + raw response + read-model response header.

    Returns the new app.responses.id. Exercises the FK chain and (in CI, as the
    least-privileged stele_api role) the sequence-USAGE grants.
    """
    survey_id = (
        await session.execute(
            text(
                "INSERT INTO app.survey_definitions "
                "(survey_id, version, definition_json, definition_hash, status, published_at) "
                "VALUES (gen_random_uuid(), 1, '{}'::jsonb, 'hash', 'published', now()) "
                "RETURNING survey_id"
            )
        )
    ).scalar_one()
    raw_id = (
        await session.execute(
            text(
                "INSERT INTO app.raw_responses "
                "(respondent_id, survey_id, survey_version, payload, shown_questions) "
                "VALUES (gen_random_uuid(), :sid, 1, '{}'::jsonb, '[]'::jsonb) "
                "RETURNING id"
            ),
            {"sid": survey_id},
        )
    ).scalar_one()
    response_id = (
        await session.execute(
            text(
                "INSERT INTO app.responses "
                "(raw_response_id, respondent_id, survey_id, survey_version, submitted_at) "
                "VALUES (:raw, gen_random_uuid(), :sid, 1, now()) RETURNING id"
            ),
            {"raw": raw_id, "sid": survey_id},
        )
    ).scalar_one()
    return int(response_id)


async def test_insert_path_and_item_count(db_session: AsyncSession) -> None:
    response_id = await _seed_response(db_session)
    await db_session.execute(
        text(
            "INSERT INTO app.response_items (response_id, question_name, value) "
            "VALUES (:r, 'q1', '\"a\"'::jsonb), (:r, 'q2', '\"b\"'::jsonb)"
        ),
        {"r": response_id},
    )
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM app.response_items WHERE response_id = :r"),
            {"r": response_id},
        )
    ).scalar_one()
    assert count == 2


async def test_response_items_unique_per_question(db_session: AsyncSession) -> None:
    response_id = await _seed_response(db_session)
    await db_session.execute(
        text(
            "INSERT INTO app.response_items (response_id, question_name, value) "
            "VALUES (:r, 'q1', '\"a\"'::jsonb)"
        ),
        {"r": response_id},
    )
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO app.response_items (response_id, question_name, value) "
                    "VALUES (:r, 'q1', '\"b\"'::jsonb)"
                ),
                {"r": response_id},
            )


async def test_responses_one_to_one_with_raw(db_session: AsyncSession) -> None:
    """A submission maps to exactly one read-model response row."""
    response_id = await _seed_response(db_session)
    raw_id = (
        await db_session.execute(
            text("SELECT raw_response_id FROM app.responses WHERE id = :r"),
            {"r": response_id},
        )
    ).scalar_one()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO app.responses "
                    "(raw_response_id, respondent_id, survey_id, survey_version, submitted_at) "
                    "VALUES (:raw, gen_random_uuid(), gen_random_uuid(), 1, now())"
                ),
                {"raw": raw_id},
            )
