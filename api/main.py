"""FastAPI application entrypoint.

Minimal skeleton for now: a health check and a read-only count endpoint that
exercises the database session. Real survey endpoints arrive in M1.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from api.auth.provisioning_router import router as db_credentials_router
from api.auth.router import router as auth_router
from api.db import SessionDep
from api.survey_engine.gdpr_router import router as gdpr_router
from api.survey_engine.pii_review_router import router as pii_review_router
from api.survey_engine.respondents_router import router as respondents_router
from api.survey_engine.router import router as surveys_router

app = FastAPI(title="Survey Engine API")
app.include_router(auth_router)
app.include_router(db_credentials_router)
app.include_router(surveys_router)
app.include_router(respondents_router)
app.include_router(gdpr_router)
app.include_router(pii_review_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/surveys/count")
async def surveys_count(session: SessionDep) -> dict[str, int]:
    result = await session.execute(text("SELECT count(*) FROM app.survey_definitions"))
    return {"count": int(result.scalar_one())}
