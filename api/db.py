"""Async database engine and session wiring."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Local dev defaults to the dev-container superuser; CI and prod set this to the
# least-privileged stele_api role so missing grants fail loudly (see CLAUDE.md).
DEFAULT_DATABASE_URL = "postgresql+psycopg://stele_dev:dev@localhost:5432/stele"


def get_database_url() -> str:
    return os.environ.get("STELE_DATABASE_URL", DEFAULT_DATABASE_URL)


engine = create_async_engine(get_database_url(), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a database session."""
    async with SessionLocal() as session:
        yield session
