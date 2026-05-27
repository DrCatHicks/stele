"""Admin-triggered ETL: start a run, complete it in the background, read status.

The scheduled path (``scripts/run_etl.py`` → ``api.etl.runner.run_etl``) runs ETL
as a separate Railway cron service as ``stele_etl``. This module is the *on-demand*
path the admin console drives (design doc § 5 frames ETL as on-demand; the daily
cron is the deferred-decision addition). It adds no new runtime component (NFR-6):
the web service runs the same prebuilt image as the cron, so ``dbt`` + the dbt
project + the runner are already present. The web service is given the
``STELE_ETL_DATABASE_URL`` + ``DBT_*`` env it needs (infra/railway/main.tf) so this
code can connect as least-privilege ``stele_etl`` — the same role dbt uses, which
already holds SELECT/INSERT/UPDATE on ``ops.etl_runs``. So the whole feature —
trigger *and* status feedback — rides the ``stele_etl`` connection and needs no
new grant (``stele_api``, the web process's runtime role, has no USAGE on ``ops``).

Shape of a triggered run:

1. :func:`start_run` (synchronous, run off the event loop): take an advisory lock,
   refuse if a non-stale ``running`` row already exists (the cron mid-run, or a
   prior click), else record the ``running`` start row and return it — so the UI's
   first poll sees the run immediately.
2. The endpoint schedules :func:`complete_run_bg` as a background task: a fresh
   connection runs the (minutes-long) ``dbt build`` and resolves the row to
   ``success`` / ``failed`` via ``runner.complete_run``.

A crashed container — or a web redeploy mid-build, which is routine for this
service — can leave a row stuck at ``running``. A ``running`` row older than
``STELE_ETL_RUN_STALE_SECONDS`` (default 30 min) is treated as *interrupted*: the
start guard ignores it (so it never wedges the button) and :func:`is_interrupted`
flags it so the console can surface it and offer :func:`clear_interrupted_run` to
resolve it to ``failed`` explicitly, rather than letting it silently age out.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from api.etl import runner

logger = logging.getLogger(__name__)

# Fixed key for the session-level advisory lock that serialises start_run, so two
# near-simultaneous clicks (or a click racing the cron's own start) can't both
# insert a 'running' row and launch colliding full-refresh builds. Arbitrary but
# stable; scoped to this one purpose.
_ADVISORY_LOCK_KEY = 8927461

_DEFAULT_STALE_SECONDS = 1800  # 30 min: longer than any plausible full-refresh build.

# Columns of ops.etl_runs the admin console reads. Listed explicitly (not SELECT *)
# so the API payload is a stable, reviewed shape.
_RUN_COLUMNS = (
    "run_id",
    "started_at",
    "completed_at",
    "status",
    "source_row_counts",
    "mart_row_counts",
    "dbt_version",
    "git_sha",
    "dbt_run_results",
)
# Safe: _RUN_COLUMNS is a fixed module constant, never request data — the only
# runtime values (run_id, limit) are passed as bound parameters below.
_COLUMNS_SQL = ", ".join(_RUN_COLUMNS)
_SELECT_RUN = f"SELECT {_COLUMNS_SQL} FROM ops.etl_runs"  # noqa: S608


class RunAlreadyActive(Exception):
    """A non-stale ``running`` row already exists, so a new run is refused (→ 409)."""

    def __init__(self, run_id: uuid.UUID, started_at: datetime) -> None:
        self.run_id = run_id
        self.started_at = started_at
        super().__init__(
            f"an ETL run is already in progress (run {run_id}, started {started_at.isoformat()})"
        )


class NotInterrupted(Exception):
    """A run can't be cleared because it isn't an interrupted (stale 'running') row."""

    def __init__(self, run_id: uuid.UUID, status: str) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(
            f"run {run_id} is {status}, not interrupted — only a stale 'running' run can be cleared"
        )


def _stale_seconds() -> int:
    """Age past which a ``running`` row is treated as an abandoned (crashed) run.

    Read at call time so it's configurable per environment and overridable in tests.
    """
    raw = os.environ.get("STELE_ETL_RUN_STALE_SECONDS")
    if raw is None:
        return _DEFAULT_STALE_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_STALE_SECONDS
    # A negative window would invert the semantics — make_interval(secs => -n)
    # adds time, so no run ever counts as active (guard bypassed) and every run
    # looks interrupted. Treat it as the misconfiguration it is. 0 stays valid
    # (it means "treat any running row as stale").
    return value if value >= 0 else _DEFAULT_STALE_SECONDS


def _active_running_row(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    """The newest non-stale ``running`` run, or None. Holds within the caller's txn."""
    row = conn.execute(
        """
        SELECT run_id, started_at
        FROM ops.etl_runs
        WHERE status = 'running'
          AND started_at > now() - make_interval(secs => %s)
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (_stale_seconds(),),
    ).fetchone()
    if row is None:
        return None
    return {"run_id": row[0], "started_at": row[1]}


def start_run() -> dict[str, Any]:
    """Begin a run: guard against a concurrent/active run, record the start row.

    Synchronous (psycopg); call off the event loop. Returns the started run row.
    Raises :class:`RunAlreadyActive` if a non-stale ``running`` row exists.
    """
    with psycopg.connect(runner.resolve_conninfo()) as conn:
        # Compute start metadata before the guarded section: read_source_counts may
        # roll back the txn on an unreadable source, which would drop the advisory
        # lock if taken first.
        sources = runner.declared_sources()
        source_counts = runner.read_source_counts(conn, sources)
        dbt_ver, sha = runner.dbt_version(), runner.git_sha()

        # Mutex: the lock + guard SELECT + start INSERT all live in one transaction,
        # released by record_run_start's commit — so the check-then-insert is atomic.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))
        active = _active_running_row(conn)
        if active is not None:
            conn.rollback()  # release the advisory lock; nothing was written
            raise RunAlreadyActive(active["run_id"], active["started_at"])

        run_id = uuid.uuid4()
        runner.record_run_start(conn, run_id, source_counts, dbt_ver, sha)
        started = get_run(run_id, conn=conn)
        assert started is not None  # just inserted
        return started


def complete_run_bg(run_id: uuid.UUID) -> None:
    """Run ``dbt build`` for an already-started run and resolve its row.

    The background half of a triggered run. Opens its own connection (the start
    connection is long gone) and delegates to ``runner.complete_run``, which marks
    the row ``failed`` before re-raising on any error — so a failure here never
    leaves the row stuck at ``running``. Errors are logged, not propagated: this
    runs detached from the request, so there's no caller to surface them to.
    """
    try:
        with psycopg.connect(runner.resolve_conninfo()) as conn:
            # Resolve run_dbt_build at call time (not as a default arg) so tests can
            # monkeypatch runner.run_dbt_build to stub the real build.
            runner.complete_run(conn, run_id, dbt_build=runner.run_dbt_build)
    except Exception:
        logger.exception("background ETL run %s failed to complete", run_id)


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Most-recent runs first, for the admin console's history panel."""
    with psycopg.connect(runner.resolve_conninfo(), row_factory=dict_row) as conn:
        return conn.execute(f"{_SELECT_RUN} ORDER BY started_at DESC LIMIT %s", (limit,)).fetchall()


def get_run(
    run_id: uuid.UUID, *, conn: psycopg.Connection[Any] | None = None
) -> dict[str, Any] | None:
    """A single run by id, or None. Reuses ``conn`` when given (else opens one)."""
    if conn is not None:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(f"{_SELECT_RUN} WHERE run_id = %s", (run_id,)).fetchone()
    with psycopg.connect(runner.resolve_conninfo(), row_factory=dict_row) as own:
        return own.execute(f"{_SELECT_RUN} WHERE run_id = %s", (run_id,)).fetchone()


def is_interrupted(row: dict[str, Any]) -> bool:
    """True for a ``running`` row older than the stale window — a run that almost
    certainly died with its container (e.g. a web redeploy mid-build) rather than
    one still in flight. The console flags these and offers to clear them."""
    if row["status"] != "running":
        return False
    started: datetime = row["started_at"]
    now = datetime.now(started.tzinfo or UTC)
    return started < now - timedelta(seconds=_stale_seconds())


def clear_interrupted_run(run_id: uuid.UUID) -> dict[str, Any] | None:
    """Resolve an interrupted (stale ``running``) run to ``failed``; return its row.

    Admin escape hatch for a run orphaned by a container restart. Refuses to touch
    a genuinely live run or an already-finished one — only a ``running`` row past
    the stale window qualifies — so it can't be used to falsify an in-progress run.
    Returns None if the run doesn't exist; raises :class:`NotInterrupted` if it
    exists but isn't clearable.
    """
    with psycopg.connect(runner.resolve_conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            f"""
            UPDATE ops.etl_runs
            SET status = 'failed', completed_at = now()
            WHERE run_id = %s
              AND status = 'running'
              AND started_at < now() - make_interval(secs => %s)
            RETURNING {_COLUMNS_SQL}
            """,  # noqa: S608 — _COLUMNS_SQL is a fixed constant; run_id/secs are bound
            (run_id, _stale_seconds()),
        ).fetchone()
        if row is not None:
            conn.commit()
            return row
        conn.rollback()
        existing = conn.execute(
            "SELECT status FROM ops.etl_runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        if existing is None:
            return None
        raise NotInterrupted(run_id, existing["status"])


def failures_from(dbt_run_results: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per-node failures from a run's ``dbt_run_results`` summary (empty if clean).

    The runner stores a compact per-node summary; the console only needs the nodes
    that didn't pass, with their message, to explain a failed run.
    """
    if not dbt_run_results:
        return []
    return [
        {
            "unique_id": node.get("unique_id"),
            "status": node.get("status"),
            "message": node.get("message"),
        }
        for node in dbt_run_results.get("results", [])
        if node.get("status") not in ("success", "pass")
    ]
