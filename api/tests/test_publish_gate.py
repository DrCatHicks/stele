"""Publish-gate schema + lint checks (M4.1, design doc §3.6 / FR-2).

Most rules are exercised as fast unit tests against `validate_definition`; a few
go through the publish endpoint to prove the wiring and that the failure message
surfaces as the 422 `detail` (the editor shows it). PII-gate cases live in
test_surveys.py.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from api.survey_engine.validation import (
    InvalidDefinition,
    extract_free_text_questions,
    validate_definition,
)


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


def test_still_unwired_types_rejected() -> None:
    # Types with no dbt staging yet are still rejected (CLAUDE.md "three places").
    # rating/boolean/numeric+date text are now wired — see the scalar tests below.
    for qtype in ("signaturepad", "imagepicker", "file", "multipletext"):
        with pytest.raises(InvalidDefinition, match="unsupported type"):
            validate_definition(_def({"type": qtype, "name": "q1"}))


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


async def test_matrix_alongside_plain_questions_pass() -> None:
    # A matrix and plainly-named questions coexist fine when names don't clash.
    validate_definition(_def(_radio("q1"), _matrix("m1"), {"type": "text", "name": "note"}))


# The collision is rejected whichever order the elements appear; the message just
# differs by which one is seen second — a plain name landing on an existing matrix
# sub-name hits the generic duplicate-name check, the reverse hits the matrix guard.
_NAME_CLASH = "duplicate question name|collides with another question name"


def test_matrix_subquestion_name_collides_with_plain_question_rejected() -> None:
    # _matrix("m1") has rows r1/r2 → sub-question "m1.r1"; a plain question named
    # "m1.r1" would hash to the same question_id. Caught at publish, not dbt build.
    with pytest.raises(InvalidDefinition, match=_NAME_CLASH):
        validate_definition(_def(_matrix("m1"), {"type": "text", "name": "m1.r1"}))


def test_matrix_subquestion_collision_order_independent() -> None:
    # Matrix seen second → its sub-name lands on the existing plain name.
    with pytest.raises(InvalidDefinition, match="collides with another question name"):
        validate_definition(_def({"type": "text", "name": "m1.r1"}, _matrix("m1")))


def test_matrixdropdown_subquestion_name_collision_rejected() -> None:
    # _matrixdropdown("md1") → cell "md1.r1.brand"; a plain question of that name clashes.
    with pytest.raises(InvalidDefinition, match=_NAME_CLASH):
        validate_definition(_def(_matrixdropdown("md1"), {"type": "text", "name": "md1.r1.brand"}))


async def test_matrix_missing_row_id_publishes_422(authed_client: AsyncClient) -> None:
    # The row-id lint reaches the editor as the 422 detail (FR-2).
    rows = [{"text": "No id"}]
    definition = _def({"type": "matrix", "name": "m1", "rows": rows, "columns": ["c1"]})
    survey_id = await _create_draft(authed_client, definition)
    response = await authed_client.post(f"/surveys/{survey_id}/versions/1/publish")
    assert response.status_code == 422
    assert "row is missing an identifier" in response.json()["detail"]


# --- paneldynamic / repeating groups (M5.4) ----------------------------------


def _paneldynamic(name: str = "panel", **extra: Any) -> dict[str, Any]:
    # A repeating group with one option cell and one free-text cell — the M5.4
    # supported surface. Each becomes a "panel.element" sub-question.
    return {
        "type": "paneldynamic",
        "name": name,
        "templateElements": [
            {"type": "dropdown", "name": "kind", "choices": ["phone", "laptop"]},
            {"type": "comment", "name": "nickname", "pii_risk": "high"},
        ],
        **extra,
    }


def test_paneldynamic_passes() -> None:
    # paneldynamic is wired end-to-end (M5.4): option + free-text template cells.
    validate_definition(_def(_paneldynamic()))


def test_paneldynamic_without_template_elements_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty 'templateElements'"):
        validate_definition(_def({"type": "paneldynamic", "name": "panel"}))


def test_paneldynamic_empty_template_elements_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty 'templateElements'"):
        validate_definition(_def({"type": "paneldynamic", "name": "panel", "templateElements": []}))


def test_paneldynamic_element_without_name_rejected() -> None:
    definition = _def(
        {"type": "paneldynamic", "name": "panel", "templateElements": [{"type": "text"}]}
    )
    with pytest.raises(InvalidDefinition, match="template element needs a 'name'"):
        validate_definition(definition)


def test_paneldynamic_duplicate_element_rejected() -> None:
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "panel",
            "templateElements": [
                {"type": "text", "name": "dup"},
                {"type": "text", "name": "dup"},
            ],
        }
    )
    with pytest.raises(InvalidDefinition, match="duplicate paneldynamic element"):
        validate_definition(definition)


def test_paneldynamic_unsupported_cell_type_rejected() -> None:
    # checkbox/ranking/scalar/matrix/nested-panel cells need array-of-array fan-out
    # or the dead numeric/date columns — rejected, deferred with the scalar slice.
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "panel",
            "templateElements": [{"type": "checkbox", "name": "c", "choices": ["a", "b"]}],
        }
    )
    with pytest.raises(InvalidDefinition, match="is not supported"):
        validate_definition(definition)


def test_paneldynamic_option_cell_without_choices_rejected() -> None:
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "panel",
            "templateElements": [{"type": "dropdown", "name": "kind"}],
        }
    )
    with pytest.raises(InvalidDefinition, match="non-empty 'choices'"):
        validate_definition(definition)


def test_paneldynamic_subquestion_name_collision_rejected() -> None:
    # _paneldynamic("panel") → sub-question "panel.kind"; a plain question of that
    # name would hash to the same question_id. Caught at publish, not dbt build.
    with pytest.raises(InvalidDefinition, match=_NAME_CLASH):
        validate_definition(_def(_paneldynamic("panel"), {"type": "text", "name": "panel.kind"}))


def test_paneldynamic_free_text_cell_low_risk_needs_rationale() -> None:
    # The free-text PII gate descends into panels: a 'low' panel cell without a
    # rationale is rejected exactly like a top-level free-text question.
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "panel",
            "templateElements": [{"type": "comment", "name": "note", "pii_risk": "low"}],
        }
    )
    with pytest.raises(InvalidDefinition, match="requires a non-empty pii_risk_rationale"):
        validate_definition(definition)


def test_paneldynamic_free_text_cell_low_risk_with_rationale_passes() -> None:
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "panel",
            "templateElements": [
                {
                    "type": "comment",
                    "name": "note",
                    "pii_risk": "low",
                    "pii_risk_rationale": "screened non-identifying",
                }
            ],
        }
    )
    validate_definition(definition)


def test_paneldynamic_cell_dangling_visible_if_rejected() -> None:
    # A visibleIf on a panel template cell must reference a defined question — the
    # dangling-reference lint descends into panel cells, not just top-level
    # elements (a cell-level conditional is idiomatic SurveyJS).
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "panel",
            "templateElements": [
                {"type": "dropdown", "name": "kind", "choices": ["a", "b"]},
                {
                    "type": "comment",
                    "name": "why",
                    "visibleIf": "{ghost} = 'x'",
                },
            ],
        }
    )
    with pytest.raises(InvalidDefinition, match="references unknown question 'ghost'"):
        validate_definition(definition)


def test_paneldynamic_cell_visible_if_valid_reference_passes() -> None:
    # A cell conditioned on a real top-level question is fine.
    definition = _def(
        _radio("q1"),
        {
            "type": "paneldynamic",
            "name": "panel",
            "templateElements": [
                {"type": "comment", "name": "why", "visibleIf": "{q1} = 'a'"},
            ],
        },
    )
    validate_definition(definition)


def test_matrixdropdown_column_dangling_visible_if_rejected() -> None:
    # Same descent for matrix columns (matrixdropdown cells).
    definition = _def(
        {
            "type": "matrixdropdown",
            "name": "md1",
            "rows": ["r1"],
            "columns": [
                {
                    "name": "c1",
                    "cellType": "dropdown",
                    "choices": ["a", "b"],
                    "visibleIf": "{ghost} = 'x'",
                }
            ],
        }
    )
    with pytest.raises(InvalidDefinition, match="references unknown question 'ghost'"):
        validate_definition(definition)


# --- scalar types: rating / boolean / numeric+date text (M5.5) ---------------


def test_rating_passes() -> None:
    validate_definition(_def({"type": "rating", "name": "score", "rateMax": 5}))


def test_rating_numeric_rate_values_pass() -> None:
    validate_definition(_def({"type": "rating", "name": "score", "rateValues": [1, 2, 3]}))


def test_rating_text_rate_values_rejected() -> None:
    # Text-valued ratings would need option-key treatment; rejected for now so the
    # answer can't land as a null value_numeric.
    with pytest.raises(InvalidDefinition, match="numeric rateValues only"):
        validate_definition(
            _def({"type": "rating", "name": "score", "rateValues": ["low", "high"]})
        )


def test_boolean_passes() -> None:
    validate_definition(_def({"type": "boolean", "name": "agree"}))


def test_boolean_custom_value_rejected() -> None:
    # A custom valueTrue stores a non-true/false scalar, breaking the 1/0 mapping.
    with pytest.raises(InvalidDefinition, match="custom boolean 'valueTrue'"):
        validate_definition(_def({"type": "boolean", "name": "agree", "valueTrue": "yes"}))


def test_numeric_and_date_text_inputs_pass() -> None:
    validate_definition(
        _def(
            {"type": "text", "name": "age", "inputType": "number"},
            {"type": "text", "name": "joined", "inputType": "date"},
        )
    )


def test_numeric_date_text_excluded_from_free_text_pii() -> None:
    # A numeric/date text input is NOT free text → no PII row, no rationale needed.
    # Only the plain comment is treated as free text.
    definition = _def(
        {"type": "text", "name": "age", "inputType": "number"},
        {"type": "text", "name": "joined", "inputType": "date"},
        {"type": "comment", "name": "feedback"},
    )
    names = {q.name for q in extract_free_text_questions(definition)}
    assert names == {"feedback"}


def test_numeric_text_input_skips_low_risk_rationale_requirement() -> None:
    # Because it's excluded from the free-text gate, a numeric input with no
    # pii_risk/rationale publishes — a plain low-risk comment without one would not.
    validate_definition(_def({"type": "text", "name": "age", "inputType": "number"}))


def test_comment_with_stray_input_type_stays_free_text() -> None:
    # A comment is inherently multi-line free text; SurveyJS ignores inputType on it.
    # A stray inputType must NOT divert it off the PII path (the safe direction) —
    # so it stays a free-text question and a missing 'low' rationale would still bite.
    definition = _def({"type": "comment", "name": "story", "inputType": "number"})
    names = {q.name for q in extract_free_text_questions(definition)}
    assert names == {"story"}


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


# --- construct tags ----------------------------------------------------------
# construct_block / construct_item are optional authored *provenance* tags
# (this question is item N of a reusable scale). They are NOT pooling — that
# stays the parent_question_id opt-in (invariant 5). Publish-time validation
# guards shape and the block/item co-occurrence; the dbt construct_pair_integrity
# test backstops the warehouse-side invariant.


def test_construct_tags_on_plain_question_pass() -> None:
    validate_definition(_def(_radio("q1", construct_block="phq9", construct_item="phq9_q1")))


def test_construct_block_alone_passes() -> None:
    # A block without an item is fine (e.g. "this question is from PHQ-9 but
    # I haven't pinned the item id yet"); only construct_item demands a block.
    validate_definition(_def(_radio("q1", construct_block="phq9")))


def test_construct_item_without_block_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="construct_item set without construct_block"):
        validate_definition(_def(_radio("q1", construct_item="phq9_q1")))


def test_construct_block_must_be_string() -> None:
    with pytest.raises(InvalidDefinition, match="construct_block must be a non-empty string"):
        validate_definition(_def(_radio("q1", construct_block=42)))


def test_construct_item_empty_string_rejected() -> None:
    with pytest.raises(InvalidDefinition, match="construct_item must be a non-empty string"):
        validate_definition(_def(_radio("q1", construct_block="phq9", construct_item="   ")))


def test_construct_tags_on_matrix_inheritance_pass() -> None:
    # Authored canonical pattern for a scale-in-a-matrix: block on the matrix,
    # item on each row. Row inherits the matrix's block.
    definition = _def(
        {
            "type": "matrix",
            "name": "phq9",
            "construct_block": "phq9",
            "rows": [
                {"value": "q1", "text": "Little interest", "construct_item": "phq9_q1"},
                {"value": "q2", "text": "Down or hopeless", "construct_item": "phq9_q2"},
            ],
            "columns": ["0", "1", "2", "3"],
        }
    )
    validate_definition(definition)


def test_construct_item_on_matrix_container_rejected() -> None:
    # A matrix groups items; it doesn't *have* an item — that's a row's job.
    definition = _def(
        {
            "type": "matrix",
            "name": "m1",
            "construct_block": "phq9",
            "construct_item": "phq9_q1",  # the bug
            "rows": ["r1"],
            "columns": ["c1"],
        }
    )
    with pytest.raises(InvalidDefinition, match="construct_item belongs to a matrix row"):
        validate_definition(definition)


def test_construct_item_on_matrix_row_without_block_rejected() -> None:
    # Row carries an item, but neither the row nor the matrix gives a block.
    definition = _def(
        {
            "type": "matrix",
            "name": "m1",
            "rows": [{"value": "r1", "construct_item": "phq9_q1"}],
            "columns": ["c1"],
        }
    )
    with pytest.raises(InvalidDefinition, match="no construct_block in scope"):
        validate_definition(definition)


def test_construct_block_on_matrixdropdown_column_rejected() -> None:
    # int_survey_questions reads construct_* from rows only — a column-level tag
    # would be silently dropped by the warehouse. Reject loudly.
    definition = _def(
        {
            "type": "matrixdropdown",
            "name": "md1",
            "construct_block": "phq9",
            "rows": [{"value": "r1", "construct_item": "phq9_q1"}],
            "columns": [
                {
                    "name": "brand",
                    "cellType": "dropdown",
                    "choices": ["a", "b"],
                    "construct_block": "gad7",  # the bug
                }
            ],
        }
    )
    with pytest.raises(InvalidDefinition, match="construct_block on a matrixdropdown column"):
        validate_definition(definition)


def test_construct_item_on_matrixdropdown_column_rejected() -> None:
    definition = _def(
        {
            "type": "matrixdropdown",
            "name": "md1",
            "construct_block": "phq9",
            "rows": [{"value": "r1"}],
            "columns": [
                {
                    "name": "brand",
                    "cellType": "dropdown",
                    "choices": ["a", "b"],
                    "construct_item": "phq9_q1",  # the bug
                }
            ],
        }
    )
    with pytest.raises(InvalidDefinition, match="construct_item on a matrixdropdown column"):
        validate_definition(definition)


def test_construct_block_on_row_overrides_matrix() -> None:
    # A row may carry its own block — the leaf wins over the container.
    definition = _def(
        {
            "type": "matrix",
            "name": "m1",
            "construct_block": "phq9",
            "rows": [{"value": "r1", "construct_block": "gad7", "construct_item": "gad7_q1"}],
            "columns": ["c1"],
        }
    )
    validate_definition(definition)


def test_construct_tags_on_paneldynamic_inheritance_pass() -> None:
    # Mirror of the matrix story: block on the panel, item on each template cell.
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "screener",
            "construct_block": "phq9",
            "templateElements": [
                {
                    "type": "dropdown",
                    "name": "q1",
                    "choices": ["0", "1", "2", "3"],
                    "construct_item": "phq9_q1",
                }
            ],
        }
    )
    validate_definition(definition)


def test_construct_item_on_panel_container_rejected() -> None:
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "screener",
            "construct_block": "phq9",
            "construct_item": "phq9_q1",  # belongs on a template element
            "templateElements": [{"type": "dropdown", "name": "q1", "choices": ["a", "b"]}],
        }
    )
    with pytest.raises(
        InvalidDefinition, match="construct_item belongs to a paneldynamic template"
    ):
        validate_definition(definition)


def test_construct_item_on_panel_cell_without_block_rejected() -> None:
    definition = _def(
        {
            "type": "paneldynamic",
            "name": "screener",
            "templateElements": [
                {
                    "type": "dropdown",
                    "name": "q1",
                    "choices": ["a", "b"],
                    "construct_item": "phq9_q1",
                }
            ],
        }
    )
    with pytest.raises(InvalidDefinition, match="no construct_block in scope"):
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
