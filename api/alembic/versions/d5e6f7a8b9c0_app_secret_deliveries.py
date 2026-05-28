"""app.secret_deliveries: one-time, encrypted handoff of a DB password

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-05-28 10:05:00.000000

The delivery half of UI-driven provisioning (design doc §3.10 revision). The CLI
shows a freshly-minted password once on the terminal (``/dev/tty``) and forgets
it. The UI flow has no terminal, so the worker instead writes the password here —
**encrypted** (Fernet, key from ``STELE_ENCRYPTION_KEY``; see
``api.auth.secret_delivery``) — and the recipient reveals it exactly once from
their own authenticated session. After the first reveal the ciphertext is wiped
and ``consumed_at`` set, so the password exists in the database only between the
worker minting it and the recipient's first login, and only as ciphertext.

- target_user_id: the app user allowed to reveal this secret. Reveal is gated to
  *this* user's session, never an unauthenticated link. ON DELETE CASCADE: if the
  recipient account is removed, their pending secret goes with it.
- login_role: the Postgres login the password belongs to (also recorded, as
  metadata only, in app.db_credential_grants).
- ciphertext: Fernet token of the password. Nulled on reveal. Never a plaintext
  password, and never the encryption key.
- expires_at: a short TTL (the service sets it). An unrevealed secret past expiry
  can't be revealed; the recipient must ask for a regenerate (rotate).
- consumed_at: set on the single successful reveal; a second attempt is refused.

Grants. Default privileges give stele_api SELECT/INSERT/UPDATE on new app tables.
stele_api must read the ciphertext (reveal) and UPDATE it (wipe + mark consumed),
but must NOT write secrets — only the privileged worker inserts — so INSERT is
REVOKEd from stele_api here. The worker connects as the elevated provisioning
identity (owner/superuser-capable) and needs no explicit grant.

This row holds a credential secret, so — like app.users/app.sessions
(f4a5b6c7d8e9) — SELECT is explicitly REVOKEd from stele_etl: it must never reach
the ETL role or the warehouse. (Model-C already default-denies app SELECT to
stele_etl; the explicit revoke is defense-in-depth for a secret-bearing table.)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secret_deliveries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("login_role", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["app.users.id"],
            name="fk_secret_deliveries_target_user",
            ondelete="CASCADE",
        ),
        schema="app",
    )
    # Reveal looks up the recipient's still-pending secret; index that lookup.
    op.create_index(
        "ix_secret_deliveries_pending",
        "secret_deliveries",
        ["target_user_id"],
        postgresql_where=sa.text("consumed_at IS NULL"),
        schema="app",
    )
    # Only the worker writes secrets; the API reads + wipes them.
    op.execute("REVOKE INSERT ON app.secret_deliveries FROM stele_api")
    # A credential secret must never reach the ETL role or the warehouse.
    op.execute("REVOKE SELECT ON app.secret_deliveries FROM stele_etl")


def downgrade() -> None:
    op.execute("GRANT SELECT ON app.secret_deliveries TO stele_etl")
    op.execute("GRANT INSERT ON app.secret_deliveries TO stele_api")
    op.drop_index("ix_secret_deliveries_pending", table_name="secret_deliveries", schema="app")
    op.drop_table("secret_deliveries", schema="app")
