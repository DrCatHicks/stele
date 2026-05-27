"""Survey short codes: set/clear/resolve + the admin list join.

Authoring (set/clear) is operator-only; resolution (the /s/<code> public link
backend) is anonymous. The round-trip publish gate is stubbed in conftest, so
publishing here is synchronous.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.survey_engine.models import SurveyShortCode

VALID_DEFINITION: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [
                {"type": "radiogroup", "name": "q1", "choices": ["a", "b"]},
            ],
        }
    ]
}


async def _create_draft(authed_client: AsyncClient) -> str:
    response = await authed_client.post("/surveys", json={"definition_json": VALID_DEFINITION})
    assert response.status_code == 201
    survey_id: str = response.json()["survey_id"]
    return survey_id


async def _publish(authed_client: AsyncClient, survey_id: str, version: int) -> None:
    resp = await authed_client.post(f"/surveys/{survey_id}/versions/{version}/publish")
    assert resp.status_code == 200


async def test_set_short_code_returns_code(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    resp = await authed_client.put(
        f"/surveys/{survey_id}/short-code", json={"short_code": "climate-2026"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["survey_id"] == survey_id
    assert body["short_code"] == "climate-2026"


async def test_set_short_code_normalises(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    resp = await authed_client.put(
        f"/surveys/{survey_id}/short-code", json={"short_code": "  Climate-2026  "}
    )
    assert resp.status_code == 200
    assert resp.json()["short_code"] == "climate-2026"


async def test_short_code_appears_on_every_version_in_list(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    # A second version so we can confirm the survey-level code repeats across rows.
    await authed_client.post(f"/surveys/{survey_id}/drafts")
    await authed_client.put(f"/surveys/{survey_id}/short-code", json={"short_code": "abc-survey"})

    rows = (await authed_client.get("/surveys")).json()
    mine = [r for r in rows if r["survey_id"] == survey_id]
    assert len(mine) == 2
    assert all(r["short_code"] == "abc-survey" for r in mine)


async def test_survey_without_code_lists_null(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    rows = (await authed_client.get("/surveys")).json()
    mine = next(r for r in rows if r["survey_id"] == survey_id)
    assert mine["short_code"] is None


async def test_reassigning_same_survey_replaces_code(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    await authed_client.put(f"/surveys/{survey_id}/short-code", json={"short_code": "first-code"})
    resp = await authed_client.put(
        f"/surveys/{survey_id}/short-code", json={"short_code": "second-code"}
    )
    assert resp.status_code == 200
    assert resp.json()["short_code"] == "second-code"
    # The freed-up first code can now be claimed by the public resolver as unknown.
    await _publish(authed_client, survey_id, 1)
    assert (await authed_client.get("/surveys/by-code/first-code")).status_code == 404
    assert (await authed_client.get("/surveys/by-code/second-code")).status_code == 200


async def test_short_code_collision_rejected(authed_client: AsyncClient) -> None:
    first = await _create_draft(authed_client)
    second = await _create_draft(authed_client)
    await authed_client.put(f"/surveys/{first}/short-code", json={"short_code": "shared"})
    resp = await authed_client.put(f"/surveys/{second}/short-code", json={"short_code": "shared"})
    assert resp.status_code == 409


async def test_invalid_short_codes_rejected(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    for bad in ["ab", "-leading", "trailing-", "has space", "UPPER!", "x" * 65]:
        resp = await authed_client.put(f"/surveys/{survey_id}/short-code", json={"short_code": bad})
        assert resp.status_code == 422, f"expected 422 for {bad!r}"


async def test_set_short_code_unknown_survey(authed_client: AsyncClient) -> None:
    resp = await authed_client.put(
        "/surveys/00000000-0000-0000-0000-000000000000/short-code",
        json={"short_code": "ghost"},
    )
    assert resp.status_code == 404


async def test_clear_short_code(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    await authed_client.put(f"/surveys/{survey_id}/short-code", json={"short_code": "to-clear"})

    cleared = await authed_client.delete(f"/surveys/{survey_id}/short-code")
    assert cleared.status_code == 204

    rows = (await authed_client.get("/surveys")).json()
    mine = next(r for r in rows if r["survey_id"] == survey_id)
    assert mine["short_code"] is None
    # The code is freed for another survey to claim.
    other = await _create_draft(authed_client)
    reuse = await authed_client.put(f"/surveys/{other}/short-code", json={"short_code": "to-clear"})
    assert reuse.status_code == 200


async def test_clear_when_none_is_idempotent(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    resp = await authed_client.delete(f"/surveys/{survey_id}/short-code")
    assert resp.status_code == 204


async def test_resolve_returns_latest_published_version(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    await _publish(authed_client, survey_id, 1)
    # v2 published, v3 left as a draft — resolver should land on v2, not v1 or v3.
    await authed_client.post(f"/surveys/{survey_id}/drafts")
    await _publish(authed_client, survey_id, 2)
    await authed_client.post(f"/surveys/{survey_id}/drafts")
    await authed_client.put(f"/surveys/{survey_id}/short-code", json={"short_code": "latest-test"})

    resp = await authed_client.get("/surveys/by-code/latest-test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["survey_id"] == survey_id
    assert body["version"] == 2


async def test_resolve_unknown_code_404(client: AsyncClient) -> None:
    assert (await client.get("/surveys/by-code/does-not-exist")).status_code == 404


async def test_resolve_code_with_no_published_version_404(authed_client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    await authed_client.put(f"/surveys/{survey_id}/short-code", json={"short_code": "draft-only"})
    # Code exists but nothing is published — same 404 as an unknown code.
    assert (await authed_client.get("/surveys/by-code/draft-only")).status_code == 404


async def test_resolve_is_case_insensitive(authed_client: AsyncClient, client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    await _publish(authed_client, survey_id, 1)
    await authed_client.put(f"/surveys/{survey_id}/short-code", json={"short_code": "mixed-case"})
    # The stored code is lowercase; resolution lowercases the path segment too.
    assert (await client.get("/surveys/by-code/MIXED-CASE")).status_code == 200


async def test_resolve_is_public(authed_client: AsyncClient, client: AsyncClient) -> None:
    survey_id = await _create_draft(authed_client)
    await _publish(authed_client, survey_id, 1)
    await authed_client.put(f"/surveys/{survey_id}/short-code", json={"short_code": "public-link"})
    # `client` is anonymous — resolution must not require an operator session.
    assert (await client.get("/surveys/by-code/public-link")).status_code == 200


async def test_set_and_clear_require_auth(client: AsyncClient) -> None:
    sid = "00000000-0000-0000-0000-000000000000"
    put = await client.put(f"/surveys/{sid}/short-code", json={"short_code": "x"})
    assert put.status_code == 401
    delete = await client.delete(f"/surveys/{sid}/short-code")
    assert delete.status_code == 401


@pytest.mark.parametrize("bad_code", ["Bad Code!", "ab", "-leading", "trailing-", "x" * 65])
async def test_db_check_rejects_malformed_code(db_session: AsyncSession, bad_code: str) -> None:
    # The format/length rules are enforced at the storage boundary too (CHECK
    # constraints), so a code written directly — bypassing the service-layer
    # validation — still can't violate the invariant.
    db_session.add(SurveyShortCode(survey_id=uuid.uuid4(), short_code=bad_code))
    with pytest.raises(IntegrityError):
        await db_session.flush()
