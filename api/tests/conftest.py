"""Shared test fixtures.

Each test runs inside a transaction that is rolled back at teardown, so tests
never persist data and stay isolated from one another. The FastAPI session
dependency is overridden to use that same transaction-bound session.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Tests round-trip cookies over plain HTTP (http://test), so a Secure cookie
# would never be sent back. Force it off for the suite; production keeps the
# default (Secure on). Set before the app/auth modules read it.
os.environ.setdefault("STELE_COOKIE_SECURE", "false")

from api.db import get_session
from api.main import app

TEST_DATABASE_URL = os.environ.get(
    "STELE_DATABASE_URL",
    "postgresql+psycopg://stele_dev:dev@localhost:5432/stele",
)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL)
    connection = await engine.connect()
    transaction = await connection.begin()
    # join_transaction_mode keeps any commit() inside the app from ending the
    # outer transaction, so the final rollback always undoes the test's writes.
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
