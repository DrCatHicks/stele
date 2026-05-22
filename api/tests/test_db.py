from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_surveys_count_endpoint(client: AsyncClient) -> None:
    """The DB-backed endpoint resolves through the transactional session."""
    response = await client.get("/surveys/count")
    assert response.status_code == 200
    assert response.json()["count"] >= 0


async def test_session_fixture_queries_app_schema(db_session: AsyncSession) -> None:
    """The transactional fixture can read the migrated app schema."""
    result = await db_session.execute(text("SELECT count(*) FROM app.survey_definitions"))
    assert result.scalar_one() >= 0
