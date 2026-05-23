"""RBAC endpoint gating (M3.2).

Authoring (create/edit/publish/new-version) is gated to {researcher, admin};
respondent withdrawal — irreversible PII erasure — to {admin} alone. The
respondent-facing GET and submit endpoints stay public. Across every gated
route: no session → 401, valid session with the wrong role → 403.

The "allowed role" assertions deliberately target a *nonexistent* survey/
respondent and only assert the status is not 401/403 — they prove the gate was
cleared (the request reached the handler, typically 404) without re-testing the
business logic that test_surveys/test_withdrawal already cover. The exception is
withdrawal, where a never-seen respondent is a valid zero-count erasure (200).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import service

PASSWORD = "correct-horse-battery-staple"
NONEXISTENT = "00000000-0000-0000-0000-000000000000"
VALID_DEFINITION: dict[str, Any] = {
    "pages": [{"name": "p1", "elements": [{"type": "radiogroup", "name": "q1", "choices": ["a"]}]}]
}

# (method, path, json-body-or-None) for each authoring route gated to authors.
AUTHORING_ROUTES = [
    ("post", "/surveys", {"definition_json": VALID_DEFINITION}),
    ("post", f"/surveys/{NONEXISTENT}/drafts", None),
    ("put", f"/surveys/{NONEXISTENT}/versions/1", {"definition_json": VALID_DEFINITION}),
    ("post", f"/surveys/{NONEXISTENT}/versions/1/publish", None),
]
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


@pytest.mark.parametrize(("method", "path", "body"), AUTHORING_ROUTES)
async def test_authoring_requires_authentication(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    resp = await _call(client, method, path, body)
    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), AUTHORING_ROUTES)
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
@pytest.mark.parametrize(("method", "path", "body"), AUTHORING_ROUTES)
async def test_authoring_allowed_for_authors(
    client: AsyncClient,
    db_session: AsyncSession,
    role: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    await _login_as(client, db_session, role)
    resp = await _call(client, method, path, body)
    # Gate cleared: reached the handler (404 for the missing survey, 201 for create).
    assert resp.status_code not in (401, 403)


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
