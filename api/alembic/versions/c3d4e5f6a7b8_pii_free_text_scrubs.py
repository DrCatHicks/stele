"""pii.free_text_scrubs: field-level free-text scrub audit records

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-27 00:00:00.000000

Field-level scrub (design doc §3.8) is the surgical sibling of the whole-respondent
withdrawal/tombstone: the reviewer destroys one high-risk free-text answer's PII
across all three durable copies — the raw_responses payload value (nulled in
place, key kept so the answer still reads as shown+answered downstream), the
operational read-model item, and pii.free_text_responses.value_text — while the
rest of the response survives. This table is the retained evidence that a scrub
happened, keyed by the same (raw_response_id, question_name, occurrence) grain the
reviewer screens at. The unique constraint enforces one scrub record per answer
and is the structural anchor for the idempotent re-scrub path.

Lives in the pii schema: question_name is harmless but the row points at a
specific respondent's answer, and the schema is out of dbt's reach (stele_etl has
no pii access), so scrub records can never leak into the warehouse.

No grants here: the pii schema's ALTER DEFAULT PRIVILEGES already grant stele_api
INSERT/UPDATE/SELECT (+ sequence USAGE) and stele_pii_reviewer SELECT/UPDATE, so
this table inherits them. The scrub's UPDATEs all land on tables stele_api can
already write: raw_responses (the same UPDATE-only tombstone privilege as
withdrawal — never DELETE; invariant 1 / design §3.8), app.response_items (app
default privileges), and pii.free_text_responses.value_text (pii default
privileges). So the field-level scrub needs no new grant at all.

The FK to app.raw_responses ON DELETE CASCADE mirrors pii.free_text_responses:
it never fires under the tombstone workflow (which NULLs, never deletes, raw
rows), but keeps the audit structurally consistent with its sibling PII table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "free_text_scrubs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("raw_response_id", sa.BigInteger(), nullable=False),
        sa.Column("question_name", sa.Text(), nullable=False),
        # 1-based panel occurrence (paneldynamic cell, M5.4); 1 for a plain
        # free-text question — same grain pii.free_text_responses is keyed at.
        sa.Column("occurrence", sa.Integer(), server_default=sa.text("1"), nullable=False),
        # The reviewer who scrubbed (app.users.id). No FK — mirrors
        # free_text_review_decisions.reviewed_by; the id is recorded for audit, not
        # joined. Nullable so a scrub survives a later user-row removal.
        sa.Column("scrubbed_by", sa.Integer(), nullable=True),
        sa.Column(
            "scrubbed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Optional free-text note (e.g. ticket reference). Not for the scrubbed
        # content / PII — callers should not put identifying data here.
        sa.Column("reason", sa.Text(), nullable=True),
        # See module docstring: cascade mirrors free_text_responses; never fires
        # under the NULL-only tombstone workflow.
        sa.ForeignKeyConstraint(
            ["raw_response_id"],
            ["app.raw_responses.id"],
            name="fk_free_text_scrubs_raw_response",
            ondelete="CASCADE",
        ),
        # One scrub record per (answer) — structural anchor for idempotency. A
        # repeat scrub finds this row and returns it unchanged.
        sa.UniqueConstraint(
            "raw_response_id",
            "question_name",
            "occurrence",
            name="uq_free_text_scrubs_raw_question_occurrence",
        ),
        schema="pii",
    )
    op.create_index(
        "ix_free_text_scrubs_raw_response",
        "free_text_scrubs",
        ["raw_response_id"],
        schema="pii",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_free_text_scrubs_raw_response",
        table_name="free_text_scrubs",
        schema="pii",
    )
    op.drop_table("free_text_scrubs", schema="pii")
