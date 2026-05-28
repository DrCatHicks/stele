"""app.provision_requests: outbox for UI-driven DB-credential provisioning

Revision ID: c4d5e6f7a8b9
Revises: f1a2b3c4d5e6
Create Date: 2026-05-28 10:00:00.000000

The request half of the UI-driven provisioning flow (design doc §3.10 revision,
drafted for review). The whole point of §3.10 is that ``stele_api`` has no
role-DDL privilege and never mints Postgres logins — so the API cannot provision
a credential directly. Instead it *enqueues intent* here, and a separate
privileged worker (``api.provisioning.worker``, run over an elevated
``STELE_PROVISION_DATABASE_URL`` connection that owns ``CREATEROLE`` + admin on
the group roles) drains the queue and performs the ``CREATE ROLE`` / ``GRANT``.
``stele_api`` only ever INSERTs a row and reads its status; it can never flip a
row to done, nor touch role DDL.

- action: 'provision' (mint a new per-person login), 'rotate' (new password on an
  existing login — the self-service regenerate), or 'revoke' (drop it).
- access: 'analyst' | 'reviewer' — the §3.3 group role tier. Required for
  provision; null for rotate/revoke, which identify the credential by login_role.
- subject_label: normalized (trim+lower) identifier of the recipient; for
  provision it equals the target user's email so the resulting credential links
  back to them. Per-action field requirements are enforced in the service layer.
- target_user_id: the app user who will self-reveal the credential (FK→users,
  SET NULL so a request row survives user deletion as an audit trace).
- requested_by: the admin who enqueued it (FK→users, SET NULL likewise).
- login_role: the role acted on. Null at provision-enqueue (the worker derives a
  fresh name); supplied for rotate/revoke; the worker writes back the derived
  name on a completed provision so the UI can show it.
- status: 'pending' → 'done' | 'failed'; error_detail carries the failure reason.

Grants. The schema-wide ALTER DEFAULT PRIVILEGES in the init SQL already give
stele_api SELECT/INSERT/UPDATE on new app tables. The API only enqueues (INSERT)
and polls (SELECT); state transitions belong to the worker, so UPDATE is REVOKEd
from stele_api here — it must not be able to mark its own request done. The
worker connects as the elevated provisioning identity (owner/superuser-capable,
the same connection that already writes app.db_credential_grants from the CLI),
so it needs no explicit grant.

No grant to stele_etl: under the model-C least-privilege posture (a5b6c7d8e9f0)
the ETL role has no schema-wide SELECT on app, so this table is already invisible
to it and the warehouse — no revoke needed, same as db_credential_grants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provision_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("access", sa.Text(), nullable=True),
        sa.Column("subject_label", sa.Text(), nullable=True),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column("login_role", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action IN ('provision', 'rotate', 'revoke')", name="ck_provision_requests_action"
        ),
        sa.CheckConstraint(
            "access IS NULL OR access IN ('analyst', 'reviewer')",
            name="ck_provision_requests_access",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'failed')", name="ck_provision_requests_status"
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["app.users.id"],
            name="fk_provision_requests_target_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["app.users.id"],
            name="fk_provision_requests_requested_by",
            ondelete="SET NULL",
        ),
        schema="app",
    )
    # The worker claims pending rows oldest-first (FOR UPDATE SKIP LOCKED); index
    # the queue scan so it stays cheap as completed rows accumulate.
    op.create_index(
        "ix_provision_requests_pending",
        "provision_requests",
        ["id"],
        postgresql_where=sa.text("status = 'pending'"),
        schema="app",
    )
    # The API enqueues and polls; only the privileged worker may advance state.
    op.execute("REVOKE UPDATE ON app.provision_requests FROM stele_api")


def downgrade() -> None:
    op.execute("GRANT UPDATE ON app.provision_requests TO stele_api")
    op.drop_index("ix_provision_requests_pending", table_name="provision_requests", schema="app")
    op.drop_table("provision_requests", schema="app")
