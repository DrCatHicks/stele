"""SQLAlchemy ORM models for the operational (app) schema.

Tables are created and altered via Alembic migrations, not from this metadata;
these classes exist so the API can read and write rows with typed attributes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP


class Base(DeclarativeBase):
    pass


class SurveyDefinition(Base):
    __tablename__ = "survey_definitions"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    definition_hash: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    # Whether publishing runs the headless round-trip gate (design doc §3.6).
    # Default true: gate unless an author opts a sandbox survey out.
    for_real_respondents: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class RawResponse(Base):
    __tablename__ = "raw_responses"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    respondent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    survey_version: Mapped[int] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    # none_as_null=True so writing None yields SQL NULL, not a JSON 'null' scalar.
    # The tombstone workflow nulls these columns, and dbt's stg_raw_responses
    # excludes withdrawn rows via `definition_snapshot is not null` — a JSON-null
    # scalar would slip past that filter (jsonb_typeof = 'null', but IS NOT NULL),
    # leaking a withdrawn respondent into the warehouse.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), default=None)
    shown_questions: Mapped[list[Any] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )
    client_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )
    # Frozen copy of the published definition (+ its hash and published_at) the
    # response was answered against. Lets dbt build dimensions from raw_responses
    # alone — keeping it the sole, reproducible ETL source (invariant 1/4, NFR-1)
    # — without reading app.survey_definitions. Nullable like the other content
    # columns so the M2 tombstone workflow can null it on withdrawal.
    definition_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )


class Response(Base):
    __tablename__ = "responses"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_response_id: Mapped[int] = mapped_column(BigInteger)
    respondent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    survey_version: Mapped[int] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class ResponseItem(Base):
    __tablename__ = "response_items"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(BigInteger)
    question_name: Mapped[str] = mapped_column(Text)
    value: Mapped[Any | None] = mapped_column(JSONB, default=None)


class FreeTextResponse(Base):
    """Restricted store for high-PII-risk free-text answers (pii schema).

    Written by the API at submission time; readable only by the PII-cleared
    role. The analyst-facing marts redact these (value_text null,
    value_text_redacted true) — design doc §3.9, invariant 6.
    """

    __tablename__ = "free_text_responses"
    __table_args__ = {"schema": "pii"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_response_id: Mapped[int] = mapped_column(BigInteger)
    question_name: Mapped[str] = mapped_column(Text)
    # 1-based panel occurrence for a paneldynamic free-text cell (M5.4); 1 for a
    # plain free-text question. Part of the (raw_response_id, question_name,
    # occurrence) unique key so each repeated cell answer has its own row.
    occurrence: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    value_text: Mapped[str | None] = mapped_column(Text, default=None)
    pii_risk: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class FreeTextReviewDecision(Base):
    """Reviewer screening outcome for a high-PII-risk free-text answer (pii schema).

    Keyed by the same (raw_response_id, question_name, occurrence) grain as
    FreeTextResponse. Holds only the decision — never the screened text — so dbt
    can read it to gate value_text in the marts without gaining access to the PII
    store (design doc §3.9 / §3.10, invariant 6). A "pending" answer has no row
    here.
    """

    __tablename__ = "free_text_review_decisions"
    __table_args__ = {"schema": "pii"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_response_id: Mapped[int] = mapped_column(BigInteger)
    question_name: Mapped[str] = mapped_column(Text)
    # Matches the screened FreeTextResponse row's occurrence (M5.4) so a panel
    # cell's repeated answers can be promoted/rejected independently.
    occurrence: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, default=None)
    reviewed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)


class Withdrawal(Base):
    """Audit record that a respondent withdrew and their data was tombstoned.

    Retained as evidence the erasure happened (design doc §3.8). Lives in the
    pii schema because respondent_id is identifying and the schema is out of
    dbt's reach. Unique on respondent_id (one withdrawal per respondent).
    """

    __tablename__ = "withdrawals"
    __table_args__ = {"schema": "pii"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    respondent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    requested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    reason: Mapped[str | None] = mapped_column(Text, default=None)
