"""Publish-gate round-trip stage (M4.2, design doc §3.6 / FR-2).

Two layers:
  - wiring tests monkeypatch the oracle adapter, so they don't need Node: they
    assert publish runs the gate only when the survey is flagged, surfaces a
    failed round-trip as 422, and an unavailable oracle as 503;
  - end-to-end tests drive the real Node + survey-core oracle through publish,
    skipping when the toolchain isn't installed (CI's vitest job covers the
    oracle's own logic).

The autouse `_stub_round_trip` fixture (conftest) no-ops the oracle for the rest
of the suite; here we override it per-test.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from api.survey_engine import round_trip
from api.survey_engine.validation import InvalidDefinition

# The autouse `_stub_round_trip` fixture replaces round_trip.run_round_trip with
# a no-op before each test, so capture the real adapter at import time for the
# end-to-end tests to restore.
_REAL_RUN_ROUND_TRIP = round_trip.run_round_trip

BRANCHING: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [
                {"type": "radiogroup", "name": "q1", "choices": ["a", "b"]},
                {
                    "type": "radiogroup",
                    "name": "q2",
                    "choices": ["x", "y"],
                    "visibleIf": "{q1} = 'a'",
                },
            ],
        }
    ]
}

# q2 is gated on a value q1 can never take → unreachable.
UNREACHABLE: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [
                {"type": "radiogroup", "name": "q1", "choices": ["a", "b"]},
                {"type": "radiogroup", "name": "q2", "choices": ["x"], "visibleIf": "{q1} = 'z'"},
            ],
        }
    ]
}


async def _create(client: AsyncClient, definition: dict[str, Any], **extra: Any) -> str:
    body = {"definition_json": definition, **extra}
    resp = await client.post("/surveys", json=body)
    assert resp.status_code == 201, resp.text
    survey_id: str = resp.json()["survey_id"]
    return survey_id


# --- wiring (monkeypatched oracle) -------------------------------------------


async def test_flagged_survey_runs_round_trip(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(round_trip, "run_round_trip", lambda d: calls.append(d))

    survey_id = await _create(authed_client, BRANCHING)  # for_real_respondents defaults True
    resp = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert resp.status_code == 200
    assert len(calls) == 1  # the gate ran


async def test_failed_round_trip_blocks_publish_with_422(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(_definition: dict[str, Any]) -> None:
        raise InvalidDefinition("unreachable question(s): q2")

    monkeypatch.setattr(round_trip, "run_round_trip", _fail)

    survey_id = await _create(authed_client, BRANCHING)
    resp = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert resp.status_code == 422
    assert "q2" in resp.json()["detail"]


async def test_unavailable_oracle_returns_503(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unavailable(_definition: dict[str, Any]) -> None:
        raise round_trip.RoundTripUnavailable("node not found")

    monkeypatch.setattr(round_trip, "run_round_trip", _unavailable)

    survey_id = await _create(authed_client, BRANCHING)
    resp = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]


async def test_sandbox_survey_skips_round_trip(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(round_trip, "run_round_trip", lambda d: calls.append(d))

    # for_real_respondents=False → the gate must not run even if it would fail.
    survey_id = await _create(authed_client, UNREACHABLE, for_real_respondents=False)
    resp = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert resp.status_code == 200
    assert calls == []  # gate skipped


async def test_definition_only_edit_preserves_sandbox_flag(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(round_trip, "run_round_trip", lambda d: calls.append(d))

    survey_id = await _create(authed_client, UNREACHABLE, for_real_respondents=False)
    # A PUT carrying only the definition must not flip the flag back to True.
    edit = await authed_client.put(
        f"/surveys/{survey_id}/versions/1", json={"definition_json": UNREACHABLE}
    )
    assert edit.status_code == 200
    assert edit.json()["for_real_respondents"] is False

    resp = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert resp.status_code == 200
    assert calls == []  # still skipped after the edit


async def test_clone_draft_version_inherits_flag(authed_client: AsyncClient) -> None:
    # A cloned new draft version carries the sandbox flag forward, so the gate
    # decision isn't silently reset across versions.
    survey_id = await _create(authed_client, BRANCHING, for_real_respondents=False)
    resp = await authed_client.post(f"/surveys/{survey_id}/drafts")  # clone=True default
    assert resp.status_code == 201
    new_version = resp.json()
    assert new_version["version"] == 2
    assert new_version["for_real_respondents"] is False


# --- end-to-end (real Node + survey-core oracle) -----------------------------

requires_node = pytest.mark.skipif(
    not round_trip.is_available(),
    reason="Node + survey-core round-trip oracle not available",
)


@requires_node
async def test_real_oracle_passes_valid_branching_survey(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(round_trip, "run_round_trip", _REAL_RUN_ROUND_TRIP)  # un-stub

    survey_id = await _create(authed_client, BRANCHING)
    resp = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert resp.status_code == 200


@requires_node
async def test_real_oracle_blocks_unreachable_branch(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(round_trip, "run_round_trip", _REAL_RUN_ROUND_TRIP)  # un-stub

    survey_id = await _create(authed_client, UNREACHABLE)
    resp = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert resp.status_code == 422
    assert "unreachable" in resp.json()["detail"].lower()
