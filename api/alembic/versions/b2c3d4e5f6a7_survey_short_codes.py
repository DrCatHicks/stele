"""app.survey_short_codes: operator-chosen short link codes per survey

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-27 12:00:00.000000

Lets an operator give a survey a memorable short code (e.g. "climate-2026") so
respondents can be sent a clean ``/s/<code>`` link instead of the bare
``?survey=<uuid>&version=<n>`` URL. The code resolves at request time to the
survey's *latest published version*, so one link keeps working across version
bumps (the resolution lives in the service layer, not here).

Grain. One code per survey, so ``survey_id`` is the primary key and ``short_code``
is uniquely constrained. The code belongs to the survey identity, not a single
version row — survey_definitions has no survey-level row to hang it on (it is
keyed (survey_id, version)), so a small side table is the natural home. No FK to
survey_definitions: survey_id is not unique there (one row per version), so there
is nothing to reference; the service validates the survey exists before writing.

Grants. The API owns this table (reads to render the admin list, writes when an
operator sets/clears a code). The ``app`` schema grants stele_api
SELECT/INSERT/UPDATE via ALTER DEFAULT PRIVILEGES; this migration adds DELETE so
an operator can remove a code (free it for reuse / take a link offline). It is
deliberately NOT granted to stele_etl: dbt's sole app source is raw_responses
(invariant 1/4), and the app schema is default-deny for ETL, so this table stays
invisible to the warehouse without any action.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "survey_short_codes",
        # One code per survey: the survey identity is the key.
        sa.Column("survey_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("short_code", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("short_code", name="uq_survey_short_codes_short_code"),
        # The link-safe format is validated in the service layer (service.normalize_
        # short_code); mirror it as storage-boundary CHECKs so a code inserted via
        # psql or admin tooling can't bypass the rules. Kept in lockstep with that
        # regex: lowercase letters/digits/hyphens, no leading/trailing hyphen, 3-64
        # chars. Postgres-only SQL is fine here — migrations are not dbt models.
        sa.CheckConstraint(
            "short_code ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'",
            name="ck_survey_short_codes_format",
        ),
        sa.CheckConstraint(
            "char_length(short_code) BETWEEN 3 AND 64",
            name="ck_survey_short_codes_length",
        ),
        schema="app",
    )
    # app default privileges already grant stele_api SELECT/INSERT/UPDATE; add
    # DELETE so an operator can clear a code. No stele_etl grant by design — keeps
    # the table out of dbt's reach (invariant 1/4).
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.survey_short_codes TO stele_api")


def downgrade() -> None:
    op.drop_table("survey_short_codes", schema="app")
