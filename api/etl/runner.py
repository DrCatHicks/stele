"""ETL runner: wrap ``dbt build``, log the run, archive dbt artifacts (§3.7).

This is the operational half of FR-11 ("every ETL run is logged with row counts,
timings, and reproducibility metadata") and NFR-2 ("dbt artifacts archived per
run"). One invocation = one row in ``ops.etl_runs``:

1. Count rows in each declared dbt source (``models/sources.yml``) at run start.
2. INSERT a ``running`` row (committed immediately, so a crashed run leaves a
   visible ``running`` row rather than nothing).
3. Run ``dbt build``.
4. Archive ``target/manifest.json`` + ``target/run_results.json`` to
   ``dbt/etl_artifacts/<run_id>/`` (the row is the index into that dir).
5. UPDATE the row to ``success`` (with per-table marts row counts) or ``failed``.

The runner connects as ``stele_etl`` — the same role dbt uses — so it reuses the
existing least-privilege grants (SELECT on the declared sources, ownership of
marts) plus the SELECT/INSERT/UPDATE on ``ops.etl_runs`` granted by the migration
that creates the table. It never DELETEs (the log is append-then-update).

Connection. ``STELE_ETL_DATABASE_URL`` points at the ETL role. For local/CI use
where it's unset, opt into the stele_etl dev fallback with
``STELE_ALLOW_DEV_FALLBACK=1`` (mirrors the provisioning CLI). Running as the real
``stele_etl`` rather than a superuser is deliberate: it proves the grants are
sufficient instead of hiding behind dev privilege.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg import sql
from psycopg.types.json import Jsonb

# api/etl/runner.py → repo root is three levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]
DBT_DIR = REPO_ROOT / "dbt"
SOURCES_YML = DBT_DIR / "models" / "sources.yml"
ARTIFACTS_DIR = DBT_DIR / "etl_artifacts"
ARTIFACT_FILES = ("manifest.json", "run_results.json")

_DEV_FALLBACK_URL = "postgresql://stele_etl:dev@localhost:5432/stele"
_FALLBACK_FLAG = "STELE_ALLOW_DEV_FALLBACK"


# --- connection / reproducibility metadata -------------------------------------


def resolve_conninfo() -> str:
    """libpq conninfo for the ETL role.

    ``STELE_ETL_DATABASE_URL`` wins; otherwise the run fails unless the dev
    fallback flag is set, so a missing/misspelled var can't silently target the
    wrong database. The SQLAlchemy ``+psycopg`` suffix isn't valid libpq.
    """
    url = os.environ.get("STELE_ETL_DATABASE_URL")
    if not url:
        if os.environ.get(_FALLBACK_FLAG, "").strip().lower() not in {"1", "true", "yes"}:
            raise RuntimeError(
                "STELE_ETL_DATABASE_URL is not set. Point it at the stele_etl role "
                f"(the role dbt uses), or set {_FALLBACK_FLAG}=1 for the local dev fallback."
            )
        url = _DEV_FALLBACK_URL
    return url.replace("+psycopg", "", 1)


def dbt_version() -> str | None:
    """Installed dbt-core version (reproducibility metadata), or None if absent."""
    try:
        return importlib.metadata.version("dbt-core")
    except importlib.metadata.PackageNotFoundError:
        return None


def git_sha() -> str | None:
    """Current commit sha. Falls back to ``$GIT_SHA`` when git isn't available."""
    try:
        # Safe: fixed argv, shell=False, no untrusted input.
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.environ.get("GIT_SHA") or None


def declared_sources(path: Path = SOURCES_YML) -> list[str]:
    """``schema.table`` for every source declared in dbt's ``sources.yml``.

    Read from the manifest of record so the run log can't drift from what dbt
    actually reads. Uses each source's ``schema`` (falling back to its ``name``).
    """
    doc = yaml.safe_load(path.read_text())
    out: list[str] = []
    for source in doc.get("sources", []):
        schema = source.get("schema", source["name"])
        for table in source.get("tables", []):
            out.append(f"{schema}.{table['name']}")
    return out


# --- row counts ----------------------------------------------------------------


def _count(conn: psycopg.Connection[Any], schema: str, table: str) -> int:
    row = conn.execute(
        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
    ).fetchone()
    assert row is not None  # count(*) always returns a row
    return int(row[0])


def read_source_counts(conn: psycopg.Connection[Any], sources: list[str]) -> dict[str, int | None]:
    """Rows per declared source. A source we can't read records None, not a crash."""
    counts: dict[str, int | None] = {}
    for qualified in sources:
        schema, _, table = qualified.partition(".")
        try:
            counts[qualified] = _count(conn, schema, table)
        except psycopg.Error:
            conn.rollback()  # clear the aborted transaction so later counts run
            counts[qualified] = None
    return counts


def read_mart_counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """Rows per base table in the marts schema (empty before the first build)."""
    rows = conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'marts' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    ).fetchall()
    return {f"marts.{name}": _count(conn, "marts", name) for (name,) in rows}


# --- run-log writes ------------------------------------------------------------


def record_run_start(
    conn: psycopg.Connection[Any],
    run_id: uuid.UUID,
    source_counts: dict[str, int | None],
    dbt_ver: str | None,
    sha: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO ops.etl_runs
            (run_id, status, source_row_counts, dbt_version, git_sha)
        VALUES (%s, 'running', %s, %s, %s)
        """,
        (run_id, Jsonb(source_counts), dbt_ver, sha),
    )
    conn.commit()


def record_run_finish(
    conn: psycopg.Connection[Any],
    run_id: uuid.UUID,
    status: str,
    mart_counts: dict[str, int] | None,
) -> None:
    conn.execute(
        """
        UPDATE ops.etl_runs
        SET status = %s, completed_at = now(), mart_row_counts = %s
        WHERE run_id = %s
        """,
        (status, Jsonb(mart_counts) if mart_counts is not None else None, run_id),
    )
    conn.commit()


# --- dbt invocation + artifact archival ----------------------------------------


def run_dbt_build(extra_args: list[str] | None = None) -> int:
    """Run ``dbt build`` in the dbt project dir; return its exit code."""
    cmd = ["dbt", "build", "--profiles-dir", "."]
    if extra_args:
        cmd.extend(extra_args)
    # Safe: fixed dbt invocation, shell=False; extra_args are operator-supplied
    # pass-through flags, not network/user input.
    return subprocess.run(cmd, cwd=DBT_DIR).returncode  # noqa: S603


def archive_artifacts(run_id: uuid.UUID) -> Path:
    """Copy dbt's manifest/run_results into ``etl_artifacts/<run_id>/`` (NFR-2).

    Best-effort: copies whichever artifacts exist (a failed build may not emit
    both), so archival never masks the run's real outcome.
    """
    dest = ARTIFACTS_DIR / str(run_id)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_FILES:
        src = DBT_DIR / "target" / name
        if src.exists():
            shutil.copy2(src, dest / name)
    return dest


# --- orchestration -------------------------------------------------------------


@dataclass
class RunResult:
    run_id: uuid.UUID
    status: str
    returncode: int
    artifacts_dir: Path


def execute_run(
    conn: psycopg.Connection[Any],
    *,
    dbt_build: Callable[[list[str] | None], int] = run_dbt_build,
    sources: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> RunResult:
    """Record a run on an open connection: start → dbt build → finish.

    The connection and the dbt-build callable are injectable so the orchestration
    can be tested against a real ``ops.etl_runs`` with dbt stubbed.
    """
    run_id = uuid.uuid4()
    if sources is None:
        sources = declared_sources()

    record_run_start(conn, run_id, read_source_counts(conn, sources), dbt_version(), git_sha())

    returncode = dbt_build(extra_args)
    artifacts_dir = archive_artifacts(run_id)

    if returncode == 0:
        record_run_finish(conn, run_id, "success", read_mart_counts(conn))
        status = "success"
    else:
        # Leave mart_row_counts null: a failed build's marts are not a trustworthy
        # snapshot. status='failed' + the archived run_results.json tell the story.
        record_run_finish(conn, run_id, "failed", None)
        status = "failed"

    return RunResult(run_id, status, returncode, artifacts_dir)


def run_etl(extra_args: list[str] | None = None) -> int:
    """Top-level entry point: connect as stele_etl, run, print a summary.

    Returns dbt's exit code so callers (``make etl``, CI) propagate failure.
    """
    with psycopg.connect(resolve_conninfo()) as conn:
        result = execute_run(conn, extra_args=extra_args)

    print(f"ETL run {result.run_id}: {result.status} (dbt exit {result.returncode})")
    print(f"  artifacts: {result.artifacts_dir}")
    return result.returncode
