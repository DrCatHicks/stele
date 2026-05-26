"""ops.etl_runs.dbt_run_results: durable per-model dbt outcomes (design doc §3.7)

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-05-26 12:00:00.000000

M7.5 runs ETL as a Railway cron service. Railway's container filesystem is
**ephemeral**: the ``dbt/etl_artifacts/<run_id>/`` archive the runner writes
(manifest.json / run_results.json) is discarded when the cron container exits, so
on Railway it can't be the durable record. ``ops.etl_runs`` (in managed Postgres)
already is — but at run grain only (timings, status, source/mart counts, version,
sha). The per-model detail that makes a *failed* run debuggable (which node
errored, with what message) lived solely in the now-ephemeral run_results.json.

This migration pulls that detail into the durable record: a ``dbt_run_results``
JSONB column holding a compact summary the runner parses from run_results.json
(``elapsed_time`` + one entry per node: status, execution_time, message,
rows_affected). The full manifest stays ephemeral (large, and reconstructible from
the code at ``git_sha``); the debuggable part survives.

No new grants: ``stele_etl`` already holds INSERT/UPDATE on ops.etl_runs (granted
by f0a1b2c3d4e5), and a column inherits the table's privileges. Nullable, because
a run can fail before dbt emits run_results.json at all (e.g. a missing binary),
and older rows predate the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "etl_runs",
        sa.Column("dbt_run_results", postgresql.JSONB(), nullable=True),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_column("etl_runs", "dbt_run_results", schema="ops")
