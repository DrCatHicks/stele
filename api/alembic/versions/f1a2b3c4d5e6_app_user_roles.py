"""app.user_roles: multi-role operator accounts (M9.1)

Revision ID: f1a2b3c4d5e6
Revises: c3d4e5f6a7b8
Create Date: 2026-05-27 12:00:00.000000

Moves operator authorization from a single ``app.users.role`` column to a
``app.user_roles`` join table, so one account can hold several application roles
(e.g. researcher + reviewer). This supersedes the single-role model of
f4a5b6c7d8e9 (design doc §3.10; a §3.10 revision is drafted for review). Roles
stay an *application* concept layered on the one least-privileged stele_api
connection — they are not the Postgres roles of §3.3.

- app.user_roles: (user_id, role) is the natural key; one row per granted role.
  ON DELETE CASCADE from users drops a deleted account's grants. The same role
  CHECK that backed the old column backs each row, so a bad write still can't
  introduce an unknown role.
- Existing rows are backfilled from app.users.role before the column is dropped,
  so no account loses its access across the migration.

Grants. The schema-wide ALTER DEFAULT PRIVILEGES in the init SQL already give
stele_api SELECT/INSERT/UPDATE (+ sequence USAGE) on new app tables. Granting and
revoking roles (M9.2) also needs DELETE, granted here (table-specific grants
can't ride the create-later default-privilege inheritance; same rationale as the
sessions and withdrawals migrations).

No grant to stele_etl: under the model-C least-privilege posture (a5b6c7d8e9f0)
stele_etl has no schema-wide SELECT on app — only on app.raw_responses, dbt's
sole source (invariant 1/4) — so user_roles is already invisible to the ETL role
and the warehouse, like users/sessions. No revoke is needed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "role", name="pk_user_roles"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.users.id"],
            name="fk_user_roles_user",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'researcher', 'reviewer')", name="ck_user_roles_role"
        ),
        schema="app",
    )

    # Preserve every account's access: one user_roles row per existing role.
    op.execute("INSERT INTO app.user_roles (user_id, role) SELECT id, role FROM app.users")

    # Roles now live in the join table; drop the single-role column and its CHECK.
    op.drop_constraint("ck_users_role", "users", schema="app", type_="check")
    op.drop_column("users", "role", schema="app")

    # Grant/revoke of roles (M9.2) deletes rows; SELECT/INSERT/UPDATE ride the
    # app-schema default privileges, DELETE does not.
    op.execute("GRANT DELETE ON app.user_roles TO stele_api")


def downgrade() -> None:
    # Reversing multi-role → single-role is inherently lossy: an account holding
    # more than one role keeps only its broadest one (admin > researcher >
    # reviewer); the others are dropped. That collapse is unavoidable here.
    #
    # A zero-role account, however, has no defensible single value — the old
    # column is NOT NULL, and inventing a role would *grant access an account
    # didn't have* (picking 'reviewer' would hand it the PII-cleared role). Rather
    # than a silent default, fail loudly and let an operator resolve it first.
    conn = op.get_bind()
    orphans = conn.execute(
        sa.text(
            "SELECT count(*) FROM app.users u "
            "WHERE NOT EXISTS (SELECT 1 FROM app.user_roles ur WHERE ur.user_id = u.id)"
        )
    ).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{orphans} user(s) hold no role; downgrade would have to invent one. "
            "Assign each a role (or delete the account) before downgrading."
        )

    op.add_column("users", sa.Column("role", sa.Text(), nullable=True), schema="app")
    op.execute(
        """
        UPDATE app.users u
        SET role = (
            SELECT ur.role FROM app.user_roles ur
            WHERE ur.user_id = u.id
            ORDER BY CASE ur.role
                WHEN 'admin' THEN 0 WHEN 'researcher' THEN 1 ELSE 2 END
            LIMIT 1
        )
        """
    )
    op.alter_column("users", "role", nullable=False, schema="app")
    op.create_check_constraint(
        "ck_users_role", "users", "role IN ('admin', 'researcher', 'reviewer')", schema="app"
    )

    op.drop_table("user_roles", schema="app")
