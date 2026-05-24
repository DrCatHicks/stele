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


def test_not_yet_wired_types_rejected() -> None:
    # boolean, rating land in a later M5 story with their dbt staging; publishing
    # one today would silently drop the answer in the warehouse. (checkbox,
    # ranking, matrix and matrixdropdown are now wired — see the type-specific
    # tests below.)
    for qtype in ("boolean", "rating"):
        with pytest.raises(InvalidDefinition, match="unsupported type"):
            validate_definition(_def({"type": qtype, "name": "q1", "choices": ["a", "b"]}))


def test_multi_select_passes() -> None:
    # checkbox is wired end-to-end (M5.1): validated like a single-select choice,
    # fanned out to one option_key row per selection in dbt.
    validate_definition(_def({"type": "checkbox", "name": "q1", "choices": ["a", "b"]}))


def test_multi_select_duplicate_option_value_rejected() -> None:
    # The choices lint is shared with single-select, so dup values are caught.
    with pytest.raises(InvalidDefinition, match="duplicate option value"):
        validate_definition(_def({"type": "checkbox", "name": "q1", "choices": ["a", "a"]}))


def test_multi_select_without_choices_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty 'choices'"):
        validate_definition(_def({"type": "checkbox", "name": "q1"}))


def test_ranked_passes() -> None:
    # ranking is wired end-to-end (M5.2): validated like a single-select choice,
    # fanned out in dbt to one option_key row per ranked option, each with a rank.
    validate_definition(_def({"type": "ranking", "name": "q1", "choices": ["a", "b"]}))


def test_ranked_duplicate_option_value_rejected() -> None:
    # Shares the choices lint with the other option types.
    with pytest.raises(InvalidDefinition, match="duplicate option value"):
        validate_definition(_def({"type": "ranking", "name": "q1", "choices": ["a", "a"]}))


def test_ranked_without_choices_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty 'choices'"):
        validate_definition(_def({"type": "ranking", "name": "q1"}))


# --- matrix (M5.3) -----------------------------------------------------------


def _matrix(name: str = "m1", **extra: Any) -> dict[str, Any]:
    # A single-choice matrix: each row chooses one of the shared columns.
    return {
        "type": "matrix",
        "name": name,
        "rows": [{"value": "r1", "text": "Row 1"}, {"value": "r2", "text": "Row 2"}],
        "columns": [{"value": "c1", "text": "Col 1"}, {"value": "c2", "text": "Col 2"}],
        **extra,
    }


def _matrixdropdown(name: str = "md1", **extra: Any) -> dict[str, Any]:
    return {
        "type": "matrixdropdown",
        "name": name,
        "rows": [{"value": "r1", "text": "Row 1"}],
        "columns": [
            {"name": "brand", "cellType": "dropdown", "choices": ["apple", "dell"]},
            {"name": "os", "cellType": "radiogroup", "choices": ["mac", "win"]},
        ],
        **extra,
    }


def test_matrix_passes() -> None:
    # matrix is wired end-to-end (M5.3): each row is a single-select sub-question
    # over the shared columns; the chosen column resolves to an option_key.
    validate_definition(_def(_matrix()))


def test_matrix_scalar_rows_and_columns_pass() -> None:
    # Rows/columns accept bare scalars too, like single-select choices.
    validate_definition(_def({"type": "matrix", "name": "m1", "rows": ["r1"], "columns": ["c1"]}))


def test_matrix_without_rows_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty 'rows'"):
        validate_definition(_def({"type": "matrix", "name": "m1", "columns": ["c1"]}))


def test_matrix_missing_row_identifier_rejected() -> None:
    # FR-2 "missing matrix row identifiers": a row becomes a sub-question identity.
    rows = [{"text": "No id here"}]
    with pytest.raises(InvalidDefinition, match="row is missing an identifier"):
        validate_definition(_def({"type": "matrix", "name": "m1", "rows": rows, "columns": ["c1"]}))


def test_matrix_duplicate_row_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="duplicate matrix row"):
        validate_definition(
            _def({"type": "matrix", "name": "m1", "rows": ["r1", "r1"], "columns": ["c1"]})
        )


def test_matrix_without_columns_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty 'columns'"):
        validate_definition(_def({"type": "matrix", "name": "m1", "rows": ["r1"]}))


def test_matrix_duplicate_column_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="duplicate matrix column"):
        validate_definition(
            _def({"type": "matrix", "name": "m1", "rows": ["r1"], "columns": ["c1", "c1"]})
        )


def test_matrixdropdown_passes() -> None:
    # Option-based cells (dropdown / radiogroup) resolve to an option_key per cell.
    validate_definition(_def(_matrixdropdown()))


def test_matrixdropdown_shared_choices_pass() -> None:
    # A column without its own `choices` inherits the matrix-level shared choices.
    definition = _def(
        {
            "type": "matrixdropdown",
            "name": "md1",
            "rows": ["r1"],
            "choices": ["x", "y"],
            "columns": [{"name": "c1", "cellType": "dropdown"}],
        }
    )
    validate_definition(definition)


def test_matrixdropdown_missing_column_name_rejected() -> None:
    definition = _def(
        {"type": "matrixdropdown", "name": "md1", "rows": ["r1"], "columns": [{"choices": ["a"]}]}
    )
    with pytest.raises(InvalidDefinition, match="column needs a 'name'"):
        validate_definition(definition)


def test_matrixdropdown_duplicate_column_name_rejected() -> None:
    definition = _def(
        {
            "type": "matrixdropdown",
            "name": "md1",
            "rows": ["r1"],
            "columns": [
                {"name": "c1", "choices": ["a"]},
                {"name": "c1", "choices": ["b"]},
            ],
        }
    )
    with pytest.raises(InvalidDefinition, match="duplicate matrixdropdown column"):
        validate_definition(definition)


def test_matrixdropdown_unsupported_celltype_rejected() -> None:
    # Free-text/scalar cells need value_text/PII or numeric storage — deferred.
    definition = _def(
        {
            "type": "matrixdropdown",
            "name": "md1",
            "rows": ["r1"],
            "columns": [{"name": "note", "cellType": "comment"}],
        }
    )
    with pytest.raises(InvalidDefinition, match="cellType 'comment' is not supported"):
        validate_definition(definition)


def test_matrixdropdown_column_without_choices_rejected() -> None:
    definition = _def(
        {
            "type": "matrixdropdown",
            "name": "md1",
            "rows": ["r1"],
            "columns": [{"name": "c1", "cellType": "dropdown"}],
        }
    )
    with pytest.raises(InvalidDefinition, match="non-empty 'choices'"):
        validate_definition(definition)


def test_matrixdropdown_duplicate_choice_value_rejected() -> None:
    definition = _def(
        {
            "type": "matrixdropdown",
            "name": "md1",
            "rows": ["r1"],
            "columns": [{"name": "c1", "cellType": "dropdown", "choices": ["a", "a"]}],
        }
    )
    with pytest.raises(InvalidDefinition, match="duplicate option value"):
        validate_definition(definition)


async def test_matrix_missing_row_id_publishes_422(authed_client: AsyncClient) -> None:
    # The row-id lint reaches the editor as the 422 detail (FR-2).
    rows = [{"text": "No id"}]
    definition = _def({"type": "matrix", "name": "m1", "rows": rows, "columns": ["c1"]})
    survey_id = await _create_draft(authed_client, definition)
    response = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert response.status_code == 422
    assert "row is missing an identifier" in response.json()["detail"]


def test_nameless_display_element_ignored() -> None:
    # An html block carries no name → not a question, not gated.
    validate_definition(_def({"type": "html", "html": "<p>hi</p>"}, _radio()))


def test_null_name_treated_as_display_element() -> None:
    # A JSON null name is not-a-question to dbt; the validator agrees and skips.
    validate_definition(_def({"type": "html", "name": None, "html": "x"}, _radio()))


def test_empty_string_name_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty string"):
        validate_definition(_def({"type": "radiogroup", "name": "", "choices": ["a"]}))


def test_non_string_name_rejected() -> None:
    # name 0 would become stable_name '0' in dbt — a question, not display.
    with pytest.raises(InvalidDefinition, match="non-empty string"):
        validate_definition(_def({"type": "radiogroup", "name": 0, "choices": ["a"]}))


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
        validate_definition(_def({"type": "dropdown", "name": "q1", "choices": choices}))


def test_distinct_object_choices_pass() -> None:
    choices = [{"value": "a", "text": "A"}, {"value": "b", "text": "B"}]
    validate_definition(_def({"type": "dropdown", "name": "q1", "choices": choices}))


def test_boolean_and_string_choice_collapse_rejected() -> None:
    # dbt renders JSON true as 'true', so [true, "true"] collapses to one option
    # in the warehouse — the publish dup-check must catch it the same way.
    with pytest.raises(InvalidDefinition, match="duplicate option value"):
        validate_definition(_def({"type": "radiogroup", "name": "q1", "choices": [True, "true"]}))


def test_null_choice_value_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="missing a value"):
        validate_definition(_def({"type": "radiogroup", "name": "q1", "choices": [None]}))


# --- shape: pages and elements are mutually exclusive (mirrors dbt) ----------


def test_top_level_elements_ignored_when_pages_present() -> None:
    # dbt's int_survey_elements uses pages when present and ignores top-level
    # elements; the validator agrees, so a name reused across the two shapes is
    # NOT a duplicate (the top-level one is never seen, by either side).
    definition = {
        "pages": [{"name": "p1", "elements": [_radio("q1")]}],
        "elements": [_radio("q1")],
    }
    validate_definition(definition)


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


def test_visible_if_calculated_value_reference_passes() -> None:
    # A calculatedValue is a legal {name} target, not a dangling reference.
    definition = _def(_radio("q1", visibleIf="{score} > 2"))
    definition["calculatedValues"] = [{"name": "score", "expression": "{q1} + 1"}]
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
