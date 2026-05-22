"""read-model tables: responses, response_items

Revision ID: fa3b82f432f9
Revises: 953cf853cada
Create Date: 2026-05-22 13:26:39.927623

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fa3b82f432f9"
down_revision: str | Sequence[str] | None = "953cf853cada"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Normalized read-model, populated by the API at submission time and
    # rebuildable from raw_responses at any time (design doc §3.4). NOT an ETL
    # input — dbt reads raw_responses only (invariant 1). Supports operational
    # queries like "has this respondent completed survey X".
    op.create_table(
        "responses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("raw_response_id", sa.BigInteger(), nullable=False),
        sa.Column("respondent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("survey_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("survey_version", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_response_id"],
            ["app.raw_responses.id"],
            name="fk_responses_raw_response",
        ),
        # 1:1 with a submission; rebuilding from raw must not duplicate.
        sa.UniqueConstraint("raw_response_id", name="uq_responses_raw_response"),
        schema="app",
    )
    op.create_index(
        "ix_responses_respondent_survey",
        "responses",
        ["respondent_id", "survey_id"],
        schema="app",
    )

    op.create_table(
        "response_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("response_id", sa.BigInteger(), nullable=False),
        sa.Column("question_name", sa.Text(), nullable=False),
        # Raw SurveyJS answer value: scalar, array (multi-select), or object
        # (matrix/panel). Stored as-is; null for shown-but-skipped questions.
        sa.Column("value", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["response_id"],
            ["app.responses.id"],
            name="fk_response_items_response",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "response_id",
            "question_name",
            name="uq_response_items_response_question",
        ),
        schema="app",
    )
    op.create_index(
        "ix_response_items_response",
        "response_items",
        ["response_id"],
        schema="app",
    )


def downgrade() -> None:
    op.drop_index("ix_response_items_response", table_name="response_items", schema="app")
    op.drop_table("response_items", schema="app")
    op.drop_index("ix_responses_respondent_survey", table_name="responses", schema="app")
    op.drop_table("responses", schema="app")
