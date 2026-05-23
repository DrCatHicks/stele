"""pii.free_text_review_decisions: reviewer screening outcomes

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-05-23 22:00:00.000000

A designated reviewer screens high-PII-risk free text and promotes individually
safe answers to the analyst marts (design doc §3.9 / §3.10, invariant 6). This
table records that per-response decision, keyed by the same (raw_response_id,
question_name) grain as pii.free_text_responses. A "pending" answer is simply a
free_text_responses row with no decision here (the review UI LEFT JOINs the two).

It deliberately holds NO PII: only the decision (promoted/rejected), who made it,
when, and an optional non-identifying note. The free-text value itself stays in
pii.free_text_responses. That separation is what lets dbt read this table without
gaining access to the PII text: the marts build needs the *decision* to surface a
promoted value_text, but it already sees the answer content in raw_responses, so
exposing the decision adds nothing the ETL role couldn't already derive — while
free_text_responses stays unreadable to it.

Grants — the trust-boundary-relevant part. dbt (stele_etl) must read this table
to gate value_text in marts.fact_response_item, but the init SQL gives stele_etl
no access to the pii schema at all (pii is granted only to stele_api and
stele_pii_reviewer). So this migration adds, scoped to exactly this table:
  - USAGE on schema pii (needed even to reference a pii object), and
  - SELECT on pii.free_text_review_decisions.
It does NOT grant stele_etl SELECT on pii.free_text_responses or pii.withdrawals,
so the PII text and erasure audit remain out of the warehouse's reach. This is the
"a future ETL source adds its own GRANT in its migration" pattern from the model-C
least-privilege migration (a5b6c7d8e9f0), extended to a pii-schema source.

stele_api INSERT/SELECT and stele_pii_reviewer SELECT inherit from the pii schema
ALTER DEFAULT PRIVILEGES, so no grant for them here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "free_text_review_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("raw_response_id", sa.BigInteger(), nullable=False),
        sa.Column("question_name", sa.Text(), nullable=False),
        # The reviewer's call. 'promoted' surfaces value_text in marts; 'rejected'
        # records that it was screened and held back (distinct from "not yet
        # reviewed", which has no row here).
        sa.Column("status", sa.Text(), nullable=False),
        # Who decided. FK to app.users; ON DELETE SET NULL so removing an operator
        # account doesn't erase the audit of decisions they made.
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Optional rationale. Not identifying by design — reviewers must not put
        # the screened content here.
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status in ('promoted', 'rejected')",
            name="ck_free_text_review_decisions_status",
        ),
        # ON DELETE CASCADE so the withdrawal/tombstone workflow drops the decision
        # when its raw row is deleted, same as pii.free_text_responses.
        sa.ForeignKeyConstraint(
            ["raw_response_id"],
            ["app.raw_responses.id"],
            name="fk_free_text_review_decisions_raw_response",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["app.users.id"],
            name="fk_free_text_review_decisions_reviewer",
            ondelete="SET NULL",
        ),
        # One decision per screened answer; the review/promote path upserts on it.
        sa.UniqueConstraint(
            "raw_response_id",
            "question_name",
            name="uq_free_text_review_decisions_raw_question",
        ),
        schema="pii",
    )
    op.create_index(
        "ix_free_text_review_decisions_raw_response",
        "free_text_review_decisions",
        ["raw_response_id"],
        schema="pii",
    )

    # ETL read access, scoped to this decision-only table (see module docstring).
    # USAGE on pii is required to reference any pii object; SELECT is on this table
    # alone — never free_text_responses (PII text) or withdrawals (erasure audit).
    op.execute("GRANT USAGE ON SCHEMA pii TO stele_etl")
    op.execute("GRANT SELECT ON pii.free_text_review_decisions TO stele_etl")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON pii.free_text_review_decisions FROM stele_etl")
    op.execute("REVOKE USAGE ON SCHEMA pii FROM stele_etl")
    op.drop_index(
        "ix_free_text_review_decisions_raw_response",
        table_name="free_text_review_decisions",
        schema="pii",
    )
    op.drop_table("free_text_review_decisions", schema="pii")
