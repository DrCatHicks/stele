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
    disabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class UserRole(Base):
    """One granted application role for an operator (design doc §3.10).

    Roles are multi-valued: a user holds zero or more of {admin, researcher,
    reviewer}, one row each. Loaded explicitly by the auth service rather than via
    an ORM relationship to keep role resolution a plain, awaited query (async
    SQLAlchemy makes lazy relationship access on a detached instance a footgun).
    Created/altered via Alembic (see f1a2b3c4d5e6).
    """

    __tablename__ = "user_roles"
    __table_args__ = {"schema": "app"}

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="CASCADE"), primary_key=True
    )
    # One of {admin, researcher, reviewer}; DB CHECK constraint backs this up.
    role: Mapped[str] = mapped_column(Text, primary_key=True)


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


class ProvisionRequest(Base):
    """Outbox row: an admin's request to provision/rotate/revoke a DB credential.

    stele_api INSERTs these and reads their status; the privileged worker (which
    holds the role-DDL stele_api lacks) drains the queue and flips status. See
    migration c4d5e6f7a8b9.
    """

    __tablename__ = "provision_requests"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 'provision' | 'rotate' | 'revoke'; DB CHECK backs this up.
    action: Mapped[str] = mapped_column(Text)
    # 'analyst' | 'reviewer' for provision; NULL for rotate/revoke. DB CHECK backs this up.
    access: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Normalized recipient identifier (== target user's email for provision).
    subject_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="SET NULL"), nullable=True
    )
    requested_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="SET NULL"), nullable=True
    )
    # Role acted on; NULL at provision-enqueue (worker derives + writes it back).
    login_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'pending' | 'done' | 'failed'; DB CHECK backs this up.
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class SecretDelivery(Base):
    """One-time, encrypted handoff of a freshly-minted DB password (§3.10).

    Written by the worker (ciphertext only), revealed exactly once from the
    recipient's own session, then wiped. See migration d5e6f7a8b9c0.
    """

    __tablename__ = "secret_deliveries"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="CASCADE")
    )
    login_role: Mapped[str] = mapped_column(Text)
    # Fernet token of the password; nulled on reveal. Never plaintext, never the key.
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
