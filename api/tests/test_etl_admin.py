"""Tests for the admin-triggered ETL path (api.etl.admin_service + admin_router).

Three layers, mirroring test_run_etl:

- **Pure units** (no DB): the failure-extraction helper.
- **Service integration** against a real ``ops.etl_runs`` as ``stele_etl`` (dbt
  stubbed): the concurrency guard, start/complete, staleness. Exercises the actual
  grants, so a missing one fails here. Skips cleanly when the role is unreachable.
- **API** through the FastAPI app: the admin gate (403), 202 + poll-to-success, and
  the 409 when a run is already active.

Every test that starts a run also *finishes* it (stele_etl has UPDATE, not DELETE),
so it never leaves a 'running' row that would 409 a later run within the stale
window — the log is keyed by a random run_id, so there's otherwise nothing to tear
down (same as test_run_etl).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# Connect as stele_etl via the runner's dev fallback when STELE_ETL_DATABASE_URL is
# unset (CI/local). Set before the modules read it.
os.environ.setdefault("STELE_ALLOW_DEV_FALLBACK", "1")

from api.etl import admin_service, runner

# --- pure units ----------------------------------------------------------------


def test_failures_from_keeps_only_non_passing_nodes() -> None:
    summary = {
        "elapsed_time": 1.5,
        "results": [
            {"unique_id": "model.a", "status": "success", "message": "INSERT 0 16"},
            {"unique_id": "test.b", "status": "fail", "message": "Got 3, expected 0"},
            {"unique_id": "test.c", "status": "pass", "message": None},
            {"unique_id": "model.d", "status": "error", "message": "boom"},
        ],
    }
    failures = admin_service.failures_from(summary)
    assert [f["unique_id"] for f in failures] == ["test.b", "model.d"]
    assert failures[0]["status"] == "fail"
    assert failures[1]["message"] == "boom"


def test_failures_from_handles_missing_summary() -> None:
    # A run that failed before dbt emitted run_results.json stores NULL there.
    assert admin_service.failures_from(None) == []
    assert admin_service.failures_from({"results": []}) == []


def test_stale_seconds_rejects_negative_and_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    # 0 is valid ("treat any running row as stale"); negative would invert the
    # guard, and non-int is a misconfiguration — both fall back to the default.
    monkeypatch.setenv("STELE_ETL_RUN_STALE_SECONDS", "0")
    assert admin_service._stale_seconds() == 0
    monkeypatch.setenv("STELE_ETL_RUN_STALE_SECONDS", "-5")
    assert admin_service._stale_seconds() == admin_service._DEFAULT_STALE_SECONDS
    monkeypatch.setenv("STELE_ETL_RUN_STALE_SECONDS", "nonsense")
    assert admin_service._stale_seconds() == admin_service._DEFAULT_STALE_SECONDS


def test_is_interrupted_flags_only_stale_running_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STELE_ETL_RUN_STALE_SECONDS", "1800")
    now = datetime.now(UTC)

    def row(status: str, age_secs: int) -> dict[str, object]:
        return {"status": status, "started_at": now - timedelta(seconds=age_secs)}

    assert admin_service.is_interrupted(row("running", 3600)) is True  # old + running
    assert admin_service.is_interrupted(row("running", 60)) is False  # fresh run
    assert admin_service.is_interrupted(row("success", 3600)) is False  # finished
    assert admin_service.is_interrupted(row("failed", 3600)) is False


# --- integration (real ops.etl_runs as stele_etl, dbt stubbed) -----------------


def _can_connect() -> bool:
    try:
        with psycopg.connect(runner.resolve_conninfo(), connect_timeout=2):
            return True
    except (psycopg.OperationalError, RuntimeError):
        return False


requires_db = pytest.mark.skipif(
    not _can_connect(), reason="stele_etl Postgres connection unavailable"
)


@pytest.fixture
def _isolate_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep dbt artifact archival out of the repo tree (DBT_DIR stays real so
    declared_sources still finds sources.yml)."""
    monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "etl_artifacts")


def _finish(run_id: uuid.UUID) -> None:
    """Resolve a started run to success so it stops blocking later starts."""
    with psycopg.connect(runner.resolve_conninfo()) as conn:
        runner.complete_run(conn, run_id, dbt_build=lambda _extra: 0)


def _age(run_id: uuid.UUID, seconds: int) -> None:
    """Back-date a run's start so it falls outside the stale window (simulating a
    run orphaned by a container restart)."""
    with psycopg.connect(runner.resolve_conninfo()) as conn:
        conn.execute(
            "UPDATE ops.etl_runs SET started_at = now() - make_interval(secs => %s) "
            "WHERE run_id = %s",
            (seconds, run_id),
        )
        conn.commit()


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
def test_start_run_records_running_row() -> None:
    started = admin_service.start_run()
    try:
        assert started["status"] == "running"
        assert started["completed_at"] is None
        assert isinstance(started["source_row_counts"], dict)

        fetched = admin_service.get_run(started["run_id"])
        assert fetched is not None
        assert fetched["status"] == "running"
    finally:
        _finish(started["run_id"])


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
def test_start_run_refuses_while_a_run_is_active() -> None:
    first = admin_service.start_run()
    try:
        with pytest.raises(admin_service.RunAlreadyActive) as exc:
            admin_service.start_run()
        assert exc.value.run_id == first["run_id"]
    finally:
        _finish(first["run_id"])


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
def test_stale_running_row_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    # With a zero stale window, any existing 'running' row is treated as abandoned,
    # so a fresh run is allowed (the crashed-container escape hatch).
    monkeypatch.setenv("STELE_ETL_RUN_STALE_SECONDS", "0")
    first = admin_service.start_run()
    second = admin_service.start_run()
    try:
        assert first["run_id"] != second["run_id"]
        assert second["status"] == "running"
    finally:
        _finish(first["run_id"])
        _finish(second["run_id"])


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
def test_complete_run_bg_resolves_running_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "run_dbt_build", lambda _extra=None: 0)
    started = admin_service.start_run()
    admin_service.complete_run_bg(started["run_id"])

    done = admin_service.get_run(started["run_id"])
    assert done is not None
    assert done["status"] == "success"
    assert done["completed_at"] is not None


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
def test_clear_interrupted_run_resolves_a_stale_running_row() -> None:
    started = admin_service.start_run()
    _age(started["run_id"], 3600)  # older than the 30-min default window

    cleared = admin_service.clear_interrupted_run(started["run_id"])
    assert cleared is not None
    assert cleared["status"] == "failed"
    assert cleared["completed_at"] is not None

    # And it no longer blocks a fresh start.
    follow_up = admin_service.start_run()
    _finish(follow_up["run_id"])


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
def test_clear_refuses_a_live_run() -> None:
    started = admin_service.start_run()  # fresh, within the stale window
    try:
        with pytest.raises(admin_service.NotInterrupted):
            admin_service.clear_interrupted_run(started["run_id"])
    finally:
        _finish(started["run_id"])


@requires_db
def test_clear_unknown_run_returns_none() -> None:
    assert admin_service.clear_interrupted_run(uuid.uuid4()) is None


# --- API (admin gate + trigger/poll/conflict) ----------------------------------


async def _login_as(client: AsyncClient, db_session: AsyncSession, role: str) -> None:
    from api.auth import service

    email = f"etl-{role}@example.com"
    await service.create_user(db_session, email, "correct-horse-battery-staple", [role])
    resp = await client.post(
        "/auth/login", json={"email": email, "password": "correct-horse-battery-staple"}
    )
    assert resp.status_code == 200


@requires_db
async def test_trigger_requires_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    """A researcher authors surveys but can't trigger ETL — the operational gate
    is admin-only (mirrors the GDPR console)."""
    await _login_as(client, db_session, "researcher")
    resp = await client.post("/admin/etl/runs")
    assert resp.status_code == 403


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
async def test_trigger_starts_run_then_polls_to_success(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "run_dbt_build", lambda _extra=None: 0)

    resp = await authed_client.post("/admin/etl/runs")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] in ("running", "success")  # background may finish during the call
    run_id = body["run_id"]
    assert body["failures"] == []

    # The detached build resolves the row; poll the way the console does.
    status = None
    for _ in range(20):
        got = await authed_client.get(f"/admin/etl/runs/{run_id}")
        assert got.status_code == 200
        status = got.json()["status"]
        if status != "running":
            break
        await asyncio.sleep(0.05)
    assert status == "success"

    listing = await authed_client.get("/admin/etl/runs")
    assert listing.status_code == 200
    assert run_id in {r["run_id"] for r in listing.json()}


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
async def test_trigger_conflicts_with_active_run(authed_client: AsyncClient) -> None:
    # Seed an un-finished 'running' row, then the endpoint must refuse with 409.
    active = admin_service.start_run()
    try:
        resp = await authed_client.post("/admin/etl/runs")
        assert resp.status_code == 409
        assert str(active["run_id"]) in resp.json()["detail"]
    finally:
        _finish(active["run_id"])


@requires_db
async def test_get_unknown_run_is_404(authed_client: AsyncClient) -> None:
    resp = await authed_client.get(f"/admin/etl/runs/{uuid.uuid4()}")
    assert resp.status_code == 404


@requires_db
@pytest.mark.usefixtures("_isolate_artifacts")
async def test_clear_endpoint_resolves_interrupted_run(authed_client: AsyncClient) -> None:
    # A genuinely live run can't be cleared (409)…
    started = admin_service.start_run()
    live = await authed_client.post(f"/admin/etl/runs/{started['run_id']}/clear")
    assert live.status_code == 409

    # …but once it's stale (orphaned by a restart), the admin can clear it (200).
    _age(started["run_id"], 3600)
    cleared = await authed_client.post(f"/admin/etl/runs/{started['run_id']}/clear")
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["status"] == "failed"
    assert body["interrupted"] is False

    unknown = await authed_client.post(f"/admin/etl/runs/{uuid.uuid4()}/clear")
    assert unknown.status_code == 404
