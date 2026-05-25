"""ops.etl_runs: per-invocation ETL run log (design doc §3.7, FR-11 / NFR-2)

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-05-25 12:00:00.000000

M6.2 makes ETL runs auditable. Every `dbt build` is wrapped by the runner
(``scripts/run_etl.py`` → ``api.etl.runner``), which records one row here per
invocation: timings, outcome, the row counts of each declared source at run
start, the row counts of each marts table after a successful run, and the
reproducibility metadata (dbt version + git sha). dbt's ``manifest.json`` /
``run_results.json`` are archived on disk alongside, keyed by ``run_id`` (NFR-2);
this table is the index into that archive.

Schema placement. ``etl_runs`` lives in a new ``ops`` schema (created with USAGE
grants in the init SQL), deliberately *outside* the analyst star schema in
``marts`` — run metadata is operational, not analytical. The runner connects as
``stele_etl`` (the same role dbt uses), so it needs write access here. Following
the model-C least-privilege pattern (a5b6c7d8e9f0): the init SQL grants only
schema USAGE, and *this migration* grants the table-level privileges —
``stele_etl`` gets SELECT/INSERT/UPDATE (never DELETE: the log is append-then-
update, never purged), and ``stele_analyst`` gets SELECT so run history is
queryable next to the marts they read. No PII, no secrets, so analyst-readable
run metadata is safe.

``run_id`` is supplied by the runner (a uuid4) rather than defaulted in the DB,
because the runner needs the id *before* the INSERT to name the on-disk artifact
directory ``dbt/etl_artifacts/<run_id>/``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etl_runs",
        # Supplied by the runner (uuid4) so it can name the artifact dir before
        # the INSERT; not server-defaulted.
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Null until the run finishes (success or failure).
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="running",
        ),
        # Rows per declared dbt source at run start.
        sa.Column("source_row_counts", postgresql.JSONB(), nullable=True),
        # Rows per marts table after a successful run; null on failure.
        sa.Column("mart_row_counts", postgresql.JSONB(), nullable=True),
        sa.Column("dbt_version", sa.Text(), nullable=True),
        sa.Column("git_sha", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_etl_runs_status",
        ),
        schema="ops",
    )

    # Table-level grants (model-C: init SQL grants only schema USAGE on ops).
    # The runner connects as stele_etl; it inserts a 'running' row, then updates
    # it to success/failed — never deletes, so no DELETE grant. Analysts read
    # run history alongside marts.
    op.execute("GRANT SELECT, INSERT, UPDATE ON ops.etl_runs TO stele_etl")
    op.execute("GRANT SELECT ON ops.etl_runs TO stele_analyst")


def downgrade() -> None:
    op.drop_table("etl_runs", schema="ops")
