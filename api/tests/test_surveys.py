from typing import Any

from httpx import AsyncClient

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


async def _create_draft(
    client: AsyncClient, definition: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await client.post(
        "/surveys", json={"definition_json": definition or VALID_DEFINITION}
    )
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


async def test_create_draft(client: AsyncClient) -> None:
    body = await _create_draft(client)
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["definition_hash"] is None
    assert body["published_at"] is None


async def test_publish_freezes_with_hash(client: AsyncClient) -> None:
    draft = await _create_draft(client)
    survey_id = draft["survey_id"]
    response = await client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert response.status_code == 200
    published = response.json()
    assert published["status"] == "published"
    assert published["definition_hash"] is not None
    assert len(published["definition_hash"]) == 64  # sha256 hex
    assert published["published_at"] is not None


async def test_published_survey_is_immutable(client: AsyncClient) -> None:
    draft = await _create_draft(client)
    survey_id = draft["survey_id"]
    await client.post(f"/surveys/{survey_id}/versions/1/publish")

    response = await client.put(
        f"/surveys/{survey_id}/versions/1",
        json={"definition_json": VALID_DEFINITION},
    )
    assert response.status_code == 409


async def test_publish_is_idempotent_rejection(client: AsyncClient) -> None:
    draft = await _create_draft(client)
    survey_id = draft["survey_id"]
    await client.post(f"/surveys/{survey_id}/versions/1/publish")

    again = await client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert again.status_code == 409


async def test_edit_draft_then_read_back(client: AsyncClient) -> None:
    draft = await _create_draft(client)
    survey_id = draft["survey_id"]
    edited = {"pages": [{"name": "p1", "elements": []}], "title": "edited"}

    put = await client.put(f"/surveys/{survey_id}/versions/1", json={"definition_json": edited})
    assert put.status_code == 200
    assert put.json()["status"] == "draft"

    got = await client.get(f"/surveys/{survey_id}/versions/1")
    assert got.status_code == 200
    assert got.json()["definition_json"] == edited


async def test_publish_rejects_invalid_definition(client: AsyncClient) -> None:
    draft = await _create_draft(client, definition={"title": "no pages or elements"})
    survey_id = draft["survey_id"]
    response = await client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert response.status_code == 422


async def test_new_draft_version_after_publish(client: AsyncClient) -> None:
    draft = await _create_draft(client)
    survey_id = draft["survey_id"]
    await client.post(f"/surveys/{survey_id}/versions/1/publish")

    response = await client.post(f"/surveys/{survey_id}/drafts")
    assert response.status_code == 201
    new_version = response.json()
    assert new_version["version"] == 2
    assert new_version["status"] == "draft"

    # cloned definition is preserved
    got = await client.get(f"/surveys/{survey_id}/versions/2")
    assert got.json()["definition_json"] == VALID_DEFINITION


async def test_get_unknown_survey_404(client: AsyncClient) -> None:
    response = await client.get("/surveys/00000000-0000-0000-0000-000000000000/versions/1")
    assert response.status_code == 404
