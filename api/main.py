"""FastAPI application entrypoint.

Minimal skeleton for now: a health check and a read-only count endpoint that
exercises the database session. Real survey endpoints arrive in M1.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session

app = FastAPI(title="Survey Engine API")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/surveys/count")
async def surveys_count(session: SessionDep) -> dict[str, int]:
    result = await session.execute(text("SELECT count(*) FROM app.survey_definitions"))
    return {"count": int(result.scalar_one())}
