"""ORM models for operator accounts and sessions (app schema).

Created/altered via Alembic (see f4a5b6c7d8e9), not from this metadata. They
share the single declarative ``Base`` so the whole app maps to one MetaData.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from api.survey_engine.models import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    # One of {admin, researcher, reviewer}; DB CHECK constraint backs this up.
    role: Mapped[str] = mapped_column(Text)
    disabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "app"}

    # Opaque high-entropy token; the value carried (signed) in the cookie.
    token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app.users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class DbCredentialGrant(Base):
    """Audit record of an analyst/reviewer Postgres credential (design doc §3.10).

    Metadata only — never a password. Rows are written by the out-of-band
    provisioning CLI (which holds the role-DDL privilege stele_api lacks) and read
    by the admin-only GET /admin/db-credentials endpoint. See migration b6c7d8e9f0a1.
    """

    __tablename__ = "db_credential_grants"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Normalized (trim+lower) human identifier for the credential's owner.
    subject_label: Mapped[str] = mapped_column(Text)
    # 'analyst' | 'reviewer'; DB CHECK backs this up.
    access: Mapped[str] = mapped_column(Text)
    # Per-person Postgres login role the CLI created; unique across all history.
    login_role: Mapped[str] = mapped_column(Text)
    # 'active' | 'revoked'; DB CHECK backs this up.
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    # Operator who requested it, when known (NULL from the CLI today).
    provisioned_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
