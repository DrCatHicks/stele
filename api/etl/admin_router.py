"""Admin ETL console — trigger a run and read its status (design doc § 3.7 / § 5).

The respondent-facing API never touches this; it's the operator's manual ETL
button. Admin-only: a full-refresh rebuild is an operational action, the narrowest
gate (mirrors the GDPR console). The work runs as ``stele_etl`` in
``api.etl.admin_service`` — see that module for why the web service can run ETL
without a new runtime component or a new grant.

Endpoints (all under ``/admin/etl``):
- ``POST /runs``   start a run; 202 + the ``running`` row, or 409 if one's active.
- ``GET  /runs``   recent runs, newest first (the history panel).
- ``GET  /runs/{run_id}``  one run (the console polls this to watch a live run).

The blocking psycopg work is pushed off the event loop with ``asyncio.to_thread``;
the minutes-long ``dbt build`` itself runs detached as a background task.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from api.auth.deps import require_role
from api.etl import admin_service

router = APIRouter(prefix="/admin/etl", tags=["admin"])

_admin_only = Depends(require_role("admin"))


class EtlNodeFailure(BaseModel):
    """A dbt node that didn't pass, for explaining a failed run."""

    unique_id: str | None
    status: str | None
    message: str | None


class EtlRunOut(BaseModel):
    """One ``ops.etl_runs`` row, shaped for the admin console."""

    run_id: uuid.UUID
    status: str
    started_at: datetime
    completed_at: datetime | None
    source_row_counts: dict[str, int | None] | None
    mart_row_counts: dict[str, int] | None
    dbt_version: str | None
    git_sha: str | None
    # A 'running' row past the stale window — almost certainly orphaned by a
    # container restart. The console surfaces these and offers to clear them.
    interrupted: bool
    # Derived from the row's dbt_run_results so the UI needn't parse it: empty
    # unless the run failed (or a node errored mid-build).
    failures: list[EtlNodeFailure]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> EtlRunOut:
        return cls(
            run_id=row["run_id"],
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            source_row_counts=row["source_row_counts"],
            mart_row_counts=row["mart_row_counts"],
            dbt_version=row["dbt_version"],
            git_sha=row["git_sha"],
            interrupted=admin_service.is_interrupted(row),
            failures=[
                EtlNodeFailure(**f) for f in admin_service.failures_from(row["dbt_run_results"])
            ],
        )


@router.get("/runs", response_model=list[EtlRunOut], dependencies=[_admin_only])
async def list_runs(limit: int = 20) -> list[EtlRunOut]:
    limit = max(1, min(limit, 100))
    rows = await asyncio.to_thread(admin_service.list_runs, limit)
    return [EtlRunOut.from_row(r) for r in rows]


@router.post("/runs", status_code=202, response_model=EtlRunOut, dependencies=[_admin_only])
async def trigger_run(background_tasks: BackgroundTasks) -> EtlRunOut:
    try:
        row = await asyncio.to_thread(admin_service.start_run)
    except admin_service.RunAlreadyActive as exc:
        # 409: a run is already in flight (a prior click, or the daily cron).
        raise HTTPException(status_code=409, detail=str(exc)) from None
    # The long dbt build runs detached; the row is already 'running', so the
    # client polls GET /runs/{id} to watch it resolve.
    background_tasks.add_task(admin_service.complete_run_bg, row["run_id"])
    return EtlRunOut.from_row(row)


@router.get("/runs/{run_id}", response_model=EtlRunOut, dependencies=[_admin_only])
async def get_run(run_id: uuid.UUID) -> EtlRunOut:
    row = await asyncio.to_thread(admin_service.get_run, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="etl run not found")
    return EtlRunOut.from_row(row)


@router.post("/runs/{run_id}/clear", response_model=EtlRunOut, dependencies=[_admin_only])
async def clear_run(run_id: uuid.UUID) -> EtlRunOut:
    """Resolve an interrupted (stale 'running') run to 'failed' so it stops wedging
    the trigger. Refuses a live or finished run (409); 404 if unknown."""
    try:
        row = await asyncio.to_thread(admin_service.clear_interrupted_run, run_id)
    except admin_service.NotInterrupted as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if row is None:
        raise HTTPException(status_code=404, detail="etl run not found")
    return EtlRunOut.from_row(row)
