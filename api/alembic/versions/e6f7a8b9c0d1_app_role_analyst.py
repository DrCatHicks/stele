"""app.user_roles: add the 'analyst' application role

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-05-28 10:10:00.000000

UI-driven provisioning couples a DB-credential holder to an app login: to receive
and reveal their own Postgres credential, the recipient signs in (design doc
§3.10 revision). Reviewers already have an app account (the PII-review console is
app-gated); analysts did not — they were pure DB principals. This adds a minimal
``analyst`` application role so an analyst can hold a login whose only purpose is
to reveal/regenerate their DB credential. It grants no app capability beyond that
self-service page; every ``require_role`` gate is an explicit allow-list, so an
analyst-only account reaches nothing else.

Mechanically: widen the ``ck_user_roles_role`` CHECK (f1a2b3c4d5e6) to include
'analyst'. No new Postgres grant — application roles are not Postgres roles.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES_WITH_ANALYST = "role IN ('admin', 'researcher', 'reviewer', 'analyst')"
_ROLES_WITHOUT = "role IN ('admin', 'researcher', 'reviewer')"


def upgrade() -> None:
    op.drop_constraint("ck_user_roles_role", "user_roles", schema="app", type_="check")
    op.create_check_constraint(
        "ck_user_roles_role", "user_roles", _ROLES_WITH_ANALYST, schema="app"
    )


def downgrade() -> None:
    # analyst rows would violate the restored CHECK; they have no valid older value
    # (inventing one would grant access the account didn't have), so drop them and
    # let an operator re-provision if the role is ever reintroduced.
    op.execute("DELETE FROM app.user_roles WHERE role = 'analyst'")
    op.drop_constraint("ck_user_roles_role", "user_roles", schema="app", type_="check")
    op.create_check_constraint("ck_user_roles_role", "user_roles", _ROLES_WITHOUT, schema="app")
