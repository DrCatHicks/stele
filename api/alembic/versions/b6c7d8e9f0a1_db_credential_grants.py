"""app.db_credential_grants: analyst/reviewer DB-credential registry

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-05-23 16:30:00.000000

Audit registry for the analyst/reviewer database credentials of design doc §3.10.
Analyst and reviewer *data* access is not mediated by the application: they
connect to Postgres directly as members of the ``stele_analyst`` / ``stele_pii_reviewer``
roles of §3.3. An admin provisions, rotates, and revokes those credentials as an
operational procedure — the privileged ``CREATE ROLE`` / ``GRANT`` lives in
``scripts/provision_db_credential.py`` (run by an operator with role-DDL rights),
never in the public ``stele_api`` connection, which has neither CREATEROLE nor any
business minting Postgres logins.

This table is the *record* of those grants — one row per provisioned login role —
so the lifecycle is auditable and the (later) admin UI has something to list. It
holds metadata only: **never a password**. The CLI generates each credential's
password, applies it to the role, and shows it once; it is never persisted here.

- subject_label: normalized (trim+lower) human identifier for the person the
  credential is for (e.g. their email). Not an FK to app.users — analysts and
  reviewers are not operators and never log into the app; they're DB principals.
- access: 'analyst' | 'reviewer'. Maps to the §3.3 group role the login is granted.
- login_role: the per-person Postgres login role the CLI created (e.g.
  stele_analyst_jdoe_a1b2). Unique across all history so audit rows never collide
  and a re-provision after revoke is a distinct role, not a reused name.
- status: 'active' (CLI created the role + granted the group) | 'revoked' (CLI
  dropped the role). Rows are never deleted — this is an audit log.
- provisioned_by: the operator who requested it, when that's known (FK→users,
  SET NULL so audit survives user deletion). The CLI leaves it NULL today; a
  future API-driven request flow (M3.3 admin UI) would populate it.

A partial unique index forbids two *active* credentials for the same
(subject_label, access): one person holds at most one live analyst credential and
one live reviewer credential at a time. Revoked rows are exempt so the history of
prior grants is retained.

Grants. None added here, deliberately:
- stele_api needs only SELECT (the read-only GET /admin/db-credentials endpoint);
  the init SQL's schema-wide ALTER DEFAULT PRIVILEGES already grant it
  SELECT/INSERT/UPDATE on new app tables created by the migration runner. The CLI
  writes rows over its own elevated connection, not as stele_api.
- stele_etl gets nothing: new app tables are default-deny for the ETL role under
  the model-C least-privilege scheme (init SQL grants it USAGE only; table SELECT
  is per-source in migrations). Who-can-read-pii is access-control metadata that
  has no place in the warehouse, and default-deny already keeps it out — so unlike
  the M3.1 users/sessions migration, no explicit REVOKE is needed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "a5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "db_credential_grants",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("subject_label", sa.Text(), nullable=False),
        sa.Column("access", sa.Text(), nullable=False),
        sa.Column("login_role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("provisioned_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("login_role", name="uq_db_credential_grants_login_role"),
        # The two data-access tiers of §3.10, mapped to the §3.3 group roles.
        sa.CheckConstraint(
            "access IN ('analyst', 'reviewer')", name="ck_db_credential_grants_access"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_db_credential_grants_status"
        ),
        # Operator who requested it; SET NULL keeps the audit row if the user is
        # ever deleted. nullable because the CLI provisions without an app identity.
        sa.ForeignKeyConstraint(
            ["provisioned_by"],
            ["app.users.id"],
            name="fk_db_credential_grants_provisioned_by",
            ondelete="SET NULL",
        ),
        schema="app",
    )
    # At most one *live* credential per person per access tier; revoked history exempt.
    op.create_index(
        "uq_db_credential_grants_active_subject",
        "db_credential_grants",
        ["subject_label", "access"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_db_credential_grants_active_subject",
        table_name="db_credential_grants",
        schema="app",
    )
    op.drop_table("db_credential_grants", schema="app")
