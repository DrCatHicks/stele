"""RBAC endpoint gating (M3.2).

Authoring (create/edit/publish/new-version) is gated to {researcher, admin};
respondent withdrawal — irreversible PII erasure — to {admin} alone. The
respondent-facing GET and submit endpoints stay public. Across every gated
route: no session → 401, valid session with the wrong role → 403.

The "allowed role" assertions target a *nonexistent* survey/respondent and
assert the specific status the handler returns once the gate is cleared (201 for
create, 404 for the operations on a missing survey, 200 for a zero-count
withdrawal). Pinning the exact status — rather than merely "not 401/403" — means
an unexpected 5xx can't masquerade as a passing gate, while still leaving the
business-logic detail to test_surveys/test_withdrawal.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import service
from api.auth.deps import require_role

PASSWORD = "correct-horse-battery-staple"
NONEXISTENT = "00000000-0000-0000-0000-000000000000"
VALID_DEFINITION: dict[str, Any] = {
    "pages": [{"name": "p1", "elements": [{"type": "radiogroup", "name": "q1", "choices": ["a"]}]}]
}

# (method, path, json-body-or-None, expected-status-once-gate-cleared) per
# authoring route. Create yields 201; the rest target a missing survey → 404.
AUTHORING_ROUTES = [
    ("post", "/surveys", {"definition_json": VALID_DEFINITION}, 201),
    ("post", f"/surveys/{NONEXISTENT}/drafts", None, 404),
    ("put", f"/surveys/{NONEXISTENT}/versions/1", {"definition_json": VALID_DEFINITION}, 404),
    ("post", f"/surveys/{NONEXISTENT}/versions/1/publish", None, 404),
]
# Same routes without the expected status, for the gate-only (401/403) tests.
GATED_AUTHORING = [route[:3] for route in AUTHORING_ROUTES]
WITHDRAWAL = f"/respondents/{NONEXISTENT}/withdrawal"


async def _login_as(client: AsyncClient, db_session: AsyncSession, role: str) -> None:
    """Create an operator with ``role`` and log this client in as them."""
    email = f"{role}@rbac.example.com"
    await service.create_user(db_session, email, PASSWORD, role)
    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200


async def _call(client: AsyncClient, method: str, path: str, body: dict[str, Any] | None) -> Any:
    return (
        await getattr(client, method)(path)
        if body is None
        else (await getattr(client, method)(path, json=body))
    )


# --- Authoring: gated to {researcher, admin} -------------------------------


@pytest.mark.parametrize(("method", "path", "body"), GATED_AUTHORING)
async def test_authoring_requires_authentication(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    resp = await _call(client, method, path, body)
    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), GATED_AUTHORING)
async def test_authoring_forbidden_for_reviewer(
    client: AsyncClient,
    db_session: AsyncSession,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    await _login_as(client, db_session, "reviewer")
    resp = await _call(client, method, path, body)
    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["researcher", "admin"])
@pytest.mark.parametrize(("method", "path", "body", "expected"), AUTHORING_ROUTES)
async def test_authoring_allowed_for_authors(
    client: AsyncClient,
    db_session: AsyncSession,
    role: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    expected: int,
) -> None:
    await _login_as(client, db_session, role)
    resp = await _call(client, method, path, body)
    # Gate cleared → handler ran and returned its real status (a 5xx would fail).
    assert resp.status_code == expected


async def test_researcher_can_create_and_publish(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Positive end-to-end: a researcher (not just admin) can author and publish."""
    await _login_as(client, db_session, "researcher")
    created = await client.post("/surveys", json={"definition_json": VALID_DEFINITION})
    assert created.status_code == 201
    survey_id = created.json()["survey_id"]
    published = await client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert published.status_code == 200


# --- Withdrawal: gated to {admin} ------------------------------------------


async def test_withdrawal_requires_authentication(client: AsyncClient) -> None:
    resp = await client.post(WITHDRAWAL, json={})
    assert resp.status_code == 401


@pytest.mark.parametrize("role", ["researcher", "reviewer"])
async def test_withdrawal_forbidden_for_non_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
) -> None:
    await _login_as(client, db_session, role)
    resp = await client.post(WITHDRAWAL, json={})
    assert resp.status_code == 403


async def test_withdrawal_allowed_for_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_as(client, db_session, "admin")
    resp = await client.post(WITHDRAWAL, json={})
    # A never-seen respondent is a valid, idempotent zero-count erasure.
    assert resp.status_code == 200


# --- Factory misconfiguration fails fast -----------------------------------


def test_require_role_rejects_no_roles() -> None:
    # An empty gate would 403 every request — catch it at declaration, not runtime.
    with pytest.raises(ValueError, match="at least one role"):
        require_role()


def test_require_role_rejects_unknown_role() -> None:
    # A typo'd role would never match any user → silent 403; fail at import instead.
    with pytest.raises(ValueError, match="unknown role"):
        require_role("admin", "auther")


# --- Public exemptions: no operator auth required --------------------------


async def test_get_survey_is_public(client: AsyncClient) -> None:
    resp = await client.get(f"/surveys/{NONEXISTENT}/versions/1")
    # 404, not 401 — the handler ran without any session.
    assert resp.status_code == 404


async def test_submit_response_is_public(client: AsyncClient) -> None:
    resp = await client.post(
        f"/surveys/{NONEXISTENT}/versions/1/responses",
        json={"definition_hash": "x", "payload": {}, "shown_questions": []},
    )
    # Respondents are anonymous; submit must not require operator auth.
    assert resp.status_code == 404
