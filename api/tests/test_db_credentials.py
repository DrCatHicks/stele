"""DB-credential provisioning: pure helpers + the admin-only registry endpoint (M3.5).

The privileged CLI path (CREATE ROLE / GRANT) is covered separately in
test_provision_cli.py, which needs an elevated connection. Here we cover the
injection-critical name derivation and the authorization on GET /admin/db-credentials.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import provisioning, service
from api.auth.models import DbCredentialGrant

PASSWORD = "correct-horse-battery-staple"


# --- pure helpers (no DB) ---------------------------------------------------


def test_group_role_maps_access_tiers() -> None:
    assert provisioning.group_role_for("analyst") == "stele_analyst"
    assert provisioning.group_role_for("reviewer") == "stele_pii_reviewer"


def test_group_role_rejects_unknown_access() -> None:
    with pytest.raises(provisioning.ProvisioningError):
        provisioning.group_role_for("superuser")


def test_derive_login_role_is_well_formed() -> None:
    login = provisioning.derive_login_role("analyst", "JDoe@Example.com", "a1b2")
    # access stem preserved, subject slugified and lowercased, suffix appended.
    assert login == "stele_analyst_jdoe_example_com_a1b2"


@pytest.mark.parametrize(
    "subject",
    [
        "robert'); DROP TABLE app.users;--",
        'evil" OR 1=1',
        "a;b|c`d",
    ],
)
def test_derive_login_role_neutralizes_injection(subject: str) -> None:
    # A hostile subject must reduce to a strict identifier — no quotes, no
    # semicolons, no backticks survive the slug.
    login = provisioning.derive_login_role("reviewer", subject, "ffff")
    assert provisioning._LOGIN_ROLE_RE.fullmatch(login)
    for bad in "'\";|`() ":
        assert bad not in login


def test_derive_login_role_rejects_empty_slug() -> None:
    # A subject with no usable characters must fail loudly, never emit a nameless role.
    with pytest.raises(provisioning.ProvisioningError):
        provisioning.derive_login_role("analyst", "@#$%", "a1b2")


def test_generated_password_is_quote_safe() -> None:
    pw = provisioning.generate_password()
    assert pw
    assert not (set(pw) & set("'\" "))


# --- admin-only registry endpoint ------------------------------------------


async def _login(client: AsyncClient, session: AsyncSession, role: str) -> None:
    email = f"{role}@example.com"
    await service.create_user(session, email, PASSWORD, [role])
    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200


async def _seed_grant(session: AsyncSession, login_role: str = "stele_analyst_jdoe_a1b2") -> None:
    session.add(
        DbCredentialGrant(
            subject_label="jdoe@example.com",
            access="analyst",
            login_role=login_role,
            status="active",
        )
    )
    await session.commit()


async def test_list_credentials_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/admin/db-credentials")
    assert resp.status_code == 401


@pytest.mark.parametrize("role", ["researcher", "reviewer"])
async def test_list_credentials_forbidden_for_non_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
) -> None:
    await _login(client, db_session, role)
    resp = await client.get("/admin/db-credentials")
    assert resp.status_code == 403


async def test_list_credentials_returns_registry_for_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, db_session, "admin")
    await _seed_grant(db_session)

    resp = await client.get("/admin/db-credentials")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["login_role"] == "stele_analyst_jdoe_a1b2"
    assert row["access"] == "analyst"
    assert row["status"] == "active"
    # The registry — and therefore the endpoint — never exposes a password.
    assert "password" not in row
