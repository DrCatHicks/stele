"""initial app tables: survey_definitions, raw_responses

Revision ID: 953cf853cada
Revises:
Create Date: 2026-05-22 11:10:48.667398

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "953cf853cada"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # survey_definitions: draft + published survey JSON. Publish freezes a row
    # (status='published', hash + published_at set); see design doc §3.4.
    op.create_table(
        "survey_definitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("survey_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition_json", postgresql.JSONB(), nullable=False),
        sa.Column("definition_hash", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("survey_id", "version", name="uq_survey_definitions_survey_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'published')",
            name="ck_survey_definitions_status",
        ),
        # A published row must be fully frozen: hash and timestamp present.
        sa.CheckConstraint(
            "status = 'draft' OR (definition_hash IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_survey_definitions_published_frozen",
        ),
        schema="app",
    )

    # raw_responses: append-only audit log, sole source of truth for ETL
    # (design doc §3.4, invariant 1). payload/shown_questions/client_metadata
    # are nullable so the tombstone workflow can null them while keeping the row.
    op.create_table(
        "raw_responses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("respondent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("survey_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("survey_version", sa.Integer(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("shown_questions", postgresql.JSONB(), nullable=True),
        sa.Column("client_metadata", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["survey_id", "survey_version"],
            ["app.survey_definitions.survey_id", "app.survey_definitions.version"],
            name="fk_raw_responses_survey_version",
        ),
        schema="app",
    )
    op.create_index(
        "ix_raw_responses_respondent",
        "raw_responses",
        ["respondent_id"],
        schema="app",
    )


def downgrade() -> None:
    op.drop_index("ix_raw_responses_respondent", table_name="raw_responses", schema="app")
    op.drop_table("raw_responses", schema="app")
    op.drop_table("survey_definitions", schema="app")
