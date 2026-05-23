"""pii.withdrawals: respondent withdrawal audit records

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-05-23 12:00:00.000000

GDPR right-to-erasure is reconciled with the append-only audit log via the
tombstone workflow (design doc §3.8): on withdrawal the API nulls the content
columns of the respondent's raw_responses rows, purges the read-model, and
deletes the PII copy. This table is the retained evidence that the erasure
happened, keyed by respondent_id with the request timestamp. The unique
constraint enforces one withdrawal per respondent and is the structural
backstop for the idempotent (re-)withdrawal path.

Lives in the pii schema: respondent_id is identifying, and the schema is out of
dbt's reach (stele_etl has no pii access), so withdrawal records can never leak
into the warehouse. The pii schema's ALTER DEFAULT PRIVILEGES already grant
stele_api INSERT/UPDATE/SELECT (+ sequence USAGE), so this table itself needs no
grant.

It does, however, grant stele_api the DELETE the tombstone workflow needs on the
read-model (app.responses) and the PII copy (pii.free_text_responses). That grant
lives here, not in the init SQL, on purpose: init SQL runs before any table
exists, and table-specific grants (unlike ALTER DEFAULT PRIVILEGES) can't ride
the create-later inheritance mechanism — so they must run after the tables are
created. Least-privilege by table: stele_api gets DELETE on exactly these two,
never on raw_responses (append-only, design doc §3.8 / invariant 1) which it can
only UPDATE-tombstone. app.response_items needs no grant — its rows go via the
ON DELETE CASCADE from app.responses, which runs with the FK's privilege.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "withdrawals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("respondent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "requested_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Optional free-text note (e.g. ticket reference). Not identifying by
        # design — callers should not put PII here.
        sa.Column("reason", sa.Text(), nullable=True),
        # One withdrawal per respondent: structural anchor for idempotency. A
        # second request finds the existing row and returns it unchanged.
        sa.UniqueConstraint("respondent_id", name="uq_withdrawals_respondent"),
        schema="pii",
    )

    # Tombstone-workflow DELETE grant (see module docstring). Idempotent enough
    # for Alembic's once-per-revision application; named tables exist by now.
    op.execute("GRANT DELETE ON app.responses, pii.free_text_responses TO stele_api")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON app.responses, pii.free_text_responses FROM stele_api")
    op.drop_table("withdrawals", schema="pii")
