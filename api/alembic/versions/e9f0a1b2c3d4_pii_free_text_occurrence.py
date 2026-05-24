"""pii free-text: add occurrence to support repeating-group (paneldynamic) cells

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-05-24 20:00:00.000000

M5.4 adds the paneldynamic (repeating group) question type. A panel repeats a
set of template elements N times, so a free-text panel cell ("panel.element")
produces one high-risk answer *per occurrence* — not one per (raw_response,
question). The fact grain has always carried `occurrence`
((respondent, survey_version, question_id, occurrence, selected_option) —
invariant 7); this migration carries the same coordinate into the two PII tables
so a reviewer can promote occurrence 1 of a panel free-text while redacting
occurrence 2, and so marts.fact_response_item can resolve the promotion decision
at the per-occurrence grain it now fans out to.

Both tables gain `occurrence INTEGER NOT NULL DEFAULT 1`. Default 1 is the value
for every existing row and for every non-panel free-text answer (a plain
question is occurrence 1 by definition), so the backfill is implicit and the
column is immediately NOT NULL. The unique keys widen to include occurrence:
  - pii.free_text_responses:        (raw_response_id, question_name, occurrence)
  - pii.free_text_review_decisions: (raw_response_id, question_name, occurrence)
The review/promote path upserts on the decisions key, so widening it is what lets
two occurrences of one panel cell carry independent decisions.

No grants here: occurrence is a metadata column on tables whose grants already
exist (stele_api INSERT/UPDATE/SELECT via the pii default privileges; stele_etl
SELECT on free_text_review_decisions only, from c7d8e9f0a1b2). The PII text in
free_text_responses stays out of the warehouse's reach, unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # free_text_responses: add occurrence, widen the unique key to include it.
    op.add_column(
        "free_text_responses",
        sa.Column(
            "occurrence",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        schema="pii",
    )
    op.drop_constraint(
        "uq_free_text_responses_raw_question",
        "free_text_responses",
        schema="pii",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_free_text_responses_raw_question_occurrence",
        "free_text_responses",
        ["raw_response_id", "question_name", "occurrence"],
        schema="pii",
    )

    # free_text_review_decisions: same coordinate, so a per-occurrence promote/
    # reject decision has its own row.
    op.add_column(
        "free_text_review_decisions",
        sa.Column(
            "occurrence",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        schema="pii",
    )
    op.drop_constraint(
        "uq_free_text_review_decisions_raw_question",
        "free_text_review_decisions",
        schema="pii",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_free_text_review_decisions_raw_question_occurrence",
        "free_text_review_decisions",
        ["raw_response_id", "question_name", "occurrence"],
        schema="pii",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_free_text_review_decisions_raw_question_occurrence",
        "free_text_review_decisions",
        schema="pii",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_free_text_review_decisions_raw_question",
        "free_text_review_decisions",
        ["raw_response_id", "question_name"],
        schema="pii",
    )
    op.drop_column("free_text_review_decisions", "occurrence", schema="pii")

    op.drop_constraint(
        "uq_free_text_responses_raw_question_occurrence",
        "free_text_responses",
        schema="pii",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_free_text_responses_raw_question",
        "free_text_responses",
        ["raw_response_id", "question_name"],
        schema="pii",
    )
    op.drop_column("free_text_responses", "occurrence", schema="pii")
