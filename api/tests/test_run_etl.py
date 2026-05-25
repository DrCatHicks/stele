"""Tests for the ETL runner (M6.2, design doc §3.7).

Two layers:

- **Pure units** (no DB): source enumeration from sources.yml, artifact archival,
  reproducibility metadata.
- **Integration** against a real ``ops.etl_runs``, connecting as the runner's own
  ``stele_etl`` role with ``dbt build`` stubbed. This exercises the actual grants
  (INSERT/UPDATE on ops.etl_runs, SELECT on the declared sources) rather than a
  superuser, so a missing grant fails here. Skips cleanly when the DB / role is
  unreachable. Rows are a log keyed by a random run_id, so runs don't collide and
  there's nothing to tear down (stele_etl deliberately has no DELETE).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

# Integration tests connect as stele_etl via the runner's dev fallback when
# STELE_ETL_DATABASE_URL is unset (CI/local). Set before the module reads it.
os.environ.setdefault("STELE_ALLOW_DEV_FALLBACK", "1")

from api.etl import runner

# --- pure units ----------------------------------------------------------------


def test_declared_sources_reads_sources_yml() -> None:
    sources = runner.declared_sources()
    # Both declared dbt sources, schema-qualified (see dbt/models/sources.yml).
    assert "app.raw_responses" in sources
    assert "pii.free_text_review_decisions" in sources


def test_archive_artifacts_copies_present_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "DBT_DIR", tmp_path)
    monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "etl_artifacts")
    target = tmp_path / "target"
    target.mkdir()
    (target / "manifest.json").write_text('{"a": 1}')
    # run_results.json deliberately absent: archival must tolerate a partial set.

    run_id = uuid.uuid4()
    dest = runner.archive_artifacts(run_id)

    assert dest == tmp_path / "etl_artifacts" / str(run_id)
    assert (dest / "manifest.json").read_text() == '{"a": 1}'
    assert not (dest / "run_results.json").exists()


def test_metadata_helpers_return_values() -> None:
    # dbt-core is a project dependency; git is present in the repo checkout.
    assert runner.dbt_version() is not None
    sha = runner.git_sha()
    assert sha is not None
    assert len(sha) == 40


def test_resolve_conninfo_strips_sqlalchemy_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "STELE_ETL_DATABASE_URL", "postgresql+psycopg://stele_etl:dev@localhost:5432/stele"
    )
    assert "+psycopg" not in runner.resolve_conninfo()


# --- integration (real ops.etl_runs as stele_etl, dbt stubbed) -----------------


def _can_connect() -> bool:
    try:
        with psycopg.connect(runner.resolve_conninfo()):
            return True
    except (psycopg.OperationalError, RuntimeError):
        return False


requires_db = pytest.mark.skipif(
    not _can_connect(), reason="stele_etl Postgres connection unavailable"
)


@requires_db
def test_execute_run_success_records_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep artifact archival out of the repo tree during tests.
    monkeypatch.setattr(runner, "DBT_DIR", tmp_path)
    monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "etl_artifacts")
    with psycopg.connect(runner.resolve_conninfo()) as conn:
        result = runner.execute_run(
            conn,
            dbt_build=lambda _extra: 0,
            sources=["app.raw_responses"],
        )
        assert result.status == "success"
        assert result.returncode == 0

        row = conn.execute(
            """
            SELECT status, source_row_counts, mart_row_counts, completed_at,
                   dbt_version, git_sha
            FROM ops.etl_runs WHERE run_id = %s
            """,
            (result.run_id,),
        ).fetchone()

    assert row is not None
    status, source_counts, mart_counts, completed_at, dbt_ver, sha = row
    assert status == "success"
    assert isinstance(source_counts["app.raw_responses"], int)
    assert isinstance(mart_counts, dict)  # {} before any marts exist; populated after
    assert completed_at is not None
    assert dbt_ver is not None
    assert sha is not None


@requires_db
def test_execute_run_failure_records_failed_with_null_marts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "DBT_DIR", tmp_path)
    monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "etl_artifacts")
    with psycopg.connect(runner.resolve_conninfo()) as conn:
        result = runner.execute_run(
            conn,
            dbt_build=lambda _extra: 1,
            sources=["app.raw_responses"],
        )
        assert result.status == "failed"
        assert result.returncode == 1

        row = conn.execute(
            "SELECT status, mart_row_counts, completed_at FROM ops.etl_runs WHERE run_id = %s",
            (result.run_id,),
        ).fetchone()

    assert row is not None
    status, mart_counts, completed_at = row
    assert status == "failed"
    assert mart_counts is None  # a failed build's marts are not a trustworthy snapshot
    assert completed_at is not None


@requires_db
def test_execute_run_resolves_row_when_dbt_build_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raised step (e.g. a missing dbt binary) must not leave a stuck 'running'
    row: the run is marked failed before the exception propagates."""
    monkeypatch.setattr(runner, "DBT_DIR", tmp_path)
    monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "etl_artifacts")

    run_ids: list[uuid.UUID] = []
    original = runner.record_run_start

    def _capture(conn, run_id, *args):  # type: ignore[no-untyped-def]
        run_ids.append(run_id)
        return original(conn, run_id, *args)

    monkeypatch.setattr(runner, "record_run_start", _capture)

    def _raise(_extra: list[str] | None) -> int:
        raise FileNotFoundError("dbt not on PATH")

    with psycopg.connect(runner.resolve_conninfo()) as conn:
        with pytest.raises(FileNotFoundError):
            runner.execute_run(conn, dbt_build=_raise, sources=["app.raw_responses"])

        assert run_ids  # the 'running' row was inserted
        row = conn.execute(
            "SELECT status, completed_at FROM ops.etl_runs WHERE run_id = %s",
            (run_ids[0],),
        ).fetchone()

    assert row is not None
    status, completed_at = row
    assert status == "failed"  # resolved, not stuck at 'running'
    assert completed_at is not None
