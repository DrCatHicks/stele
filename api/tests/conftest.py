"""Shared test fixtures.

Each test runs inside a transaction that is rolled back at teardown, so tests
never persist data and stay isolated from one another. The FastAPI session
dependency is overridden to use that same transaction-bound session.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Tests round-trip cookies over plain HTTP (http://test), so a Secure cookie
# would never be sent back. Force it off for the suite; production keeps the
# default (Secure on). Set before the app/auth modules read it.
os.environ.setdefault("STELE_COOKIE_SECURE", "false")

from api.db import get_session

# The API app carries routes at their bare prefixes (the ``/api`` namespace is
# added by the production mount in api.main:app); drive it directly here.
from api.main import api_app as app

TEST_DATABASE_URL = os.environ.get(
    "STELE_DATABASE_URL",
    "postgresql+psycopg://stele_dev:dev@localhost:5432/stele",
)


@pytest.fixture(autouse=True)
def _stub_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publishing a survey flagged for real respondents runs the round-trip
    oracle (Node + survey-core) by default. Stub it to a no-op so the suite
    doesn't depend on a Node toolchain; the wiring tests override this with
    their own behaviour, and the real-oracle e2e tests restore it explicitly
    (and skip when Node/survey-core is unavailable)."""
    from api.survey_engine import round_trip

    monkeypatch.setattr(round_trip, "run_round_trip", lambda *args, **kwargs: None)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL)
    connection = await engine.connect()
    transaction = await connection.begin()
    # join_transaction_mode keeps any commit() inside the app from ending the
    # outer transaction, so the final rollback always undoes the test's writes.
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


ADMIN_EMAIL = "rbac-admin@example.com"
ADMIN_PASSWORD = "correct-horse-battery-staple"


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, db_session: AsyncSession) -> AsyncClient:
    """A ``client`` logged in as an admin.

    Authoring and withdrawal are gated (M3.2); admin clears every operator gate,
    so tests exercising those endpoints' happy paths depend on this rather than
    the anonymous ``client``. The login cookie persists in the client jar.
    """
    # Imported here so conftest stays import-light and the auth package is only
    # pulled in when a test actually needs an authenticated client.
    from api.auth import service

    await service.create_user(db_session, ADMIN_EMAIL, ADMIN_PASSWORD, ["admin"])
    resp = await client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return client


@pytest.fixture
def elevated_conn():  # type: ignore[no-untyped-def]
    """A committed, autocommit psycopg connection over the *elevated* provisioning
    identity (CREATEROLE/superuser), for seeding rows the least-privileged stele_api
    test session may not write — notably app.secret_deliveries, whose INSERT is
    revoked from stele_api by design (§3.10). CI supplies STELE_PROVISION_DATABASE_URL;
    local dev uses the superuser fallback. Skips cleanly when neither is available.

    Rows written here are *committed* (outside the per-test rollback), so callers
    must clean up what they insert — deleting the seeded app.users row cascades to
    its secret_deliveries.
    """
    import os

    import psycopg

    from api.auth import provisioning

    os.environ.setdefault("STELE_ALLOW_DEV_FALLBACK", "1")
    try:
        conn = psycopg.connect(provisioning.provision_conninfo(), autocommit=True)
    except (provisioning.ProvisioningError, psycopg.OperationalError) as exc:
        pytest.skip(f"no elevated connection available for seeding: {exc}")
    try:
        yield conn
    finally:
        conn.close()


@pytest_asyncio.fixture
async def live_client() -> AsyncIterator[AsyncClient]:
    """An AsyncClient with NO session override — the app uses its real, *committing*
    session (api.db.get_session), not the rollback-per-test one.

    Integration tests that seed committed rows over ``elevated_conn`` (e.g.
    app.secret_deliveries, which stele_api may not INSERT) must use this rather than
    ``client``: mixing externally-committed rows with the transactional session
    deadlocks at cleanup (the open transaction holds row/FK locks the elevated
    DELETE waits on). Here each request commits and releases its locks, so the test
    can clean up its own committed rows in a finally. Such tests own their cleanup.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
