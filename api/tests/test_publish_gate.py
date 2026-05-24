"""Publish-gate schema + lint checks (M4.1, design doc §3.6 / FR-2).

Most rules are exercised as fast unit tests against `validate_definition`; a few
go through the publish endpoint to prove the wiring and that the failure message
surfaces as the 422 `detail` (the editor shows it). PII-gate cases live in
test_surveys.py.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from api.survey_engine.validation import InvalidDefinition, validate_definition


def _def(*elements: dict[str, Any]) -> dict[str, Any]:
    return {"pages": [{"name": "p1", "elements": list(elements)}]}


def _radio(name: str = "q1", **extra: Any) -> dict[str, Any]:
    return {"type": "radiogroup", "name": name, "choices": ["a", "b"], **extra}


# --- structural / schema -----------------------------------------------------


def test_empty_definition_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty"):
        validate_definition({})


def test_missing_pages_and_elements_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="must contain"):
        validate_definition({"title": "no questions"})


def test_valid_single_select_passes() -> None:
    validate_definition(_def(_radio()))


def test_top_level_elements_shape_passes() -> None:
    validate_definition({"elements": [_radio()]})


def test_unsupported_question_type_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="unsupported type"):
        validate_definition(_def({"type": "signaturepad", "name": "sig"}))


def test_nameless_display_element_ignored() -> None:
    # An html block carries no name → not a question, not gated.
    validate_definition(_def({"type": "html", "html": "<p>hi</p>"}, _radio()))


# --- duplicate question names ------------------------------------------------


def test_duplicate_question_name_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="duplicate question name"):
        validate_definition(_def(_radio("q1"), {"type": "text", "name": "q1"}))


def test_duplicate_name_across_pages_rejected() -> None:
    definition = {
        "pages": [
            {"name": "p1", "elements": [_radio("dup")]},
            {"name": "p2", "elements": [_radio("dup")]},
        ]
    }
    with pytest.raises(InvalidDefinition, match="duplicate question name"):
        validate_definition(definition)


# --- choices -----------------------------------------------------------------


def test_choice_question_without_choices_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty 'choices'"):
        validate_definition(_def({"type": "radiogroup", "name": "q1"}))


def test_duplicate_scalar_option_value_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="duplicate option value"):
        validate_definition(_def({"type": "radiogroup", "name": "q1", "choices": ["a", "a"]}))


def test_duplicate_object_option_value_rejected() -> None:
    choices = [{"value": "a", "text": "A"}, {"value": "a", "text": "Aagain"}]
    with pytest.raises(InvalidDefinition, match="duplicate option value"):
        validate_definition(_def({"type": "checkbox", "name": "q1", "choices": choices}))


def test_distinct_object_choices_pass() -> None:
    choices = [{"value": "a", "text": "A"}, {"value": "b", "text": "B"}]
    validate_definition(_def({"type": "checkbox", "name": "q1", "choices": choices}))


# --- matrix rows -------------------------------------------------------------


def test_matrix_without_rows_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty 'rows'"):
        validate_definition(_def({"type": "matrix", "name": "m1", "columns": ["1", "2"]}))


def test_matrix_row_missing_identifier_rejected() -> None:
    rows = [{"text": "row with no id"}]
    with pytest.raises(InvalidDefinition, match="missing an identifier"):
        validate_definition(_def({"type": "matrix", "name": "m1", "rows": rows, "columns": ["1"]}))


def test_matrix_with_row_ids_passes() -> None:
    rows = [{"value": "r1", "text": "Row 1"}, "r2"]
    validate_definition(_def({"type": "matrix", "name": "m1", "rows": rows, "columns": ["1", "2"]}))


# --- dangling visibleIf ------------------------------------------------------


def test_dangling_visible_if_rejected() -> None:
    definition = _def(_radio("q1"), _radio("q2", visibleIf="{ghost} = 'a'"))
    with pytest.raises(InvalidDefinition, match="references unknown question 'ghost'"):
        validate_definition(definition)


def test_valid_visible_if_reference_passes() -> None:
    definition = _def(_radio("q1"), _radio("q2", visibleIf="{q1} = 'a'"))
    validate_definition(definition)


def test_visible_if_context_var_not_flagged() -> None:
    # {row.x} / {panel.y} are dynamic-context references, not question names.
    definition = _def(_radio("q1", visibleIf="{row.score} > 2"))
    validate_definition(definition)


def test_enable_if_dangling_reference_rejected() -> None:
    definition = _def(_radio("q1", enableIf="{missing} notempty"))
    with pytest.raises(InvalidDefinition, match="references unknown question 'missing'"):
        validate_definition(definition)


# --- endpoint wiring + detail surfacing --------------------------------------


async def _create_draft(authed_client: AsyncClient, definition: dict[str, Any]) -> str:
    response = await authed_client.post("/surveys", json={"definition_json": definition})
    assert response.status_code == 201
    survey_id: str = response.json()["survey_id"]
    return survey_id


async def test_publish_rejects_dangling_visible_if_with_detail(authed_client: AsyncClient) -> None:
    definition = _def(_radio("q1"), _radio("q2", visibleIf="{ghost} = 'a'"))
    survey_id = await _create_draft(authed_client, definition)
    response = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert response.status_code == 422
    # The lint reason reaches the editor as `detail`.
    assert "ghost" in response.json()["detail"]


async def test_publish_rejects_duplicate_option_value(authed_client: AsyncClient) -> None:
    definition = _def({"type": "radiogroup", "name": "q1", "choices": ["a", "a"]})
    survey_id = await _create_draft(authed_client, definition)
    response = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert response.status_code == 422
    assert "duplicate option value" in response.json()["detail"]


async def test_publish_accepts_valid_branching_survey(authed_client: AsyncClient) -> None:
    definition = _def(_radio("q1"), _radio("q2", visibleIf="{q1} = 'a'"))
    survey_id = await _create_draft(authed_client, definition)
    response = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert response.status_code == 200
