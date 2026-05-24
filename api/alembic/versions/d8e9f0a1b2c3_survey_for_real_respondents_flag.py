"""app.survey_definitions.for_real_respondents: round-trip gate flag

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-05-24 02:00:00.000000

The publish round-trip gate (design doc §3.6 / FR-2) runs only for surveys
"flagged as going to real respondents" — a draft/sandbox survey can publish
without the headless branch walk. This column carries that flag.

Default TRUE: gating is the safe default, so a survey is round-tripped unless an
author deliberately opts out. Set at draft create/edit and frozen with the row
on publish (published rows are immutable, invariant 2). It lives on app and dbt
never reads survey_definitions, so no grant changes are needed (stele_api's
access inherits from the app-schema default privileges).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "survey_definitions",
        sa.Column(
            "for_real_respondents",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("survey_definitions", "for_real_respondents", schema="app")
