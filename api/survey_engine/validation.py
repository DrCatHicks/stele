"""Publish-time definition validation: schema + lint (design doc §3.6, FR-2).

The publish gate runs in order — **schema validation → lint → round-trip →
hash+freeze** (CLAUDE.md §"Publish gate"). This module covers the first two
stages synchronously at publish time; the headless round-trip (survey-core
oracle) is a later stage. Failures raise `InvalidDefinition`, which the router
maps to 422 with the message as `detail` so the editor can show it.

We validate against our *own* SurveyJS-compatible structural contract, not the
official SurveyJS JSON schema: we own what we accept, only gate the question
types we support, and the JSON stays loadable by survey-core. Element walking
and option/row shapes mirror dbt's int_survey_* models so the API and warehouse
agree on what counts as a question (invariant 4).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

# SurveyJS free-text element types. value goes to value_text downstream, routed
# by pii_risk (design doc §3.9). Other types resolve via options / numeric / date.
FREE_TEXT_TYPES = frozenset({"text", "comment"})

# Single-select choice types: one scalar answer resolved to an option_key via
# dim_option (dbt's fact_response_item resolves a single answer_value against it).
CHOICE_TYPES = frozenset({"radiogroup", "dropdown"})

# Multi-select choice types: the answer is an *array* of chosen option values.
# dbt's fact_response_item fans these out to one row per selection, each carrying
# an option_key (M5.1). Validated identically to single-select choices (a
# non-empty, duplicate-free `choices` list) — the difference is only downstream.
MULTI_SELECT_TYPES = frozenset({"checkbox"})

# Ranked choice types: the answer is an *ordered array* of chosen option values,
# the array position carrying the rank. dbt fans these out like multi-select —
# one option_key row per ranked item — but the per-selection ordinal populates
# fact_response_item.rank (M5.2). Same `choices` lint as the others; the ordering
# semantics live downstream.
RANKED_TYPES = frozenset({"ranking"})

# Every option-bearing (top-level `choices`) type shares the same `choices` lint.
OPTION_TYPES = CHOICE_TYPES | MULTI_SELECT_TYPES | RANKED_TYPES

# Matrix types (M5.3). A matrix decomposes into one single-select sub-question per
# cell (row by column) — keyed by the row (and, for matrixdropdown, the column).
# dbt expands each into its own dim_question (stable_name = "matrix.row[.column]")
# resolving to an option_key, so the fact grain handles them uniformly with
# single-select (design-doc §3.5, FR-4 "matrix sub-questions"). 'matrix' is a
# single radio choice per row over shared `columns`; 'matrixdropdown' has a typed
# editor per column (we support the option-based cell types only — see
# MATRIX_CELL_TYPES). Rows/columns must carry stable identifiers, enforced below
# (FR-2 "missing matrix row identifiers" lint).
MATRIX_TYPES = frozenset({"matrix", "matrixdropdown"})

# matrixdropdown cell types we support end-to-end: each resolves a single scalar
# answer to an option_key, exactly like a single-select. Free-text / scalar /
# multi-select cells (comment, text, rating, checkbox, …) need value_text/PII or
# array fan-out per cell — deferred with the rest of the scalar/repeating slice;
# rejected at publish so the answer can't be silently dropped downstream.
MATRIX_CELL_TYPES = frozenset({"dropdown", "radiogroup"})

# Repeating-group types (M5.4). A paneldynamic repeats a set of templateElements N
# times; the answer is an *array of objects*, one per occurrence. dbt expands each
# template element into its own sub-question (stable_name = "panel.element") and
# the array position drives the fact grain's `occurrence` — so the (respondent,
# survey_version, question, occurrence, option) grain handles a repeated answer
# uniformly with a plain one (design-doc §3.5, FR-4 "repeating groups").
REPEATING_TYPES = frozenset({"paneldynamic"})

# Template-element (panel cell) types we support end-to-end: single-select choices
# (→ option_key, per occurrence) and free-text (→ value_text / PII store, per
# occurrence). Multi-select / ranked / scalar / matrix / nested-panel cells need
# array-of-array fan-out or the dead value_numeric/value_date columns — rejected at
# publish, deferred with the scalar slice — so a panel answer can't be silently
# dropped downstream.
PANEL_CELL_TYPES = CHOICE_TYPES | FREE_TEXT_TYPES

# Question types we support end-to-end enough to publish. A name-bearing element
# of any other type is rejected — you can't publish a type the runtime, gate and
# dbt staging don't all handle (CLAUDE.md §"New question type = three places").
# Still narrow by design: fact_response_item populates option_key (single/multi/
# ranked choice + matrix cell + panel option cell) and value_text (free-text, incl.
# panel free-text cells); value_numeric/value_date stay unpopulated, so
# rating/numeric/date answers would land as all-null fact rows — silently dropped
# and indistinguishable from "shown & skipped". Those rejoin here in the scalar M5
# story alongside their dbt staging + tests.
KNOWN_QUESTION_TYPES = FREE_TEXT_TYPES | OPTION_TYPES | MATRIX_TYPES | REPEATING_TYPES

# SurveyJS context variables that may appear as `{base...}` inside an expression
# without being a question name (dynamic panels/matrix rows, self-reference). A
# reference whose base is none of these and not a defined question is dangling.
_EXPRESSION_CONTEXT_VARS = frozenset({"row", "panel", "composite", "self", "parent"})

# `{token}` references inside a SurveyJS expression (visibleIf, enableIf, …).
_BRACE_REF = re.compile(r"\{([^{}]+)\}")


class InvalidDefinition(Exception):
    """Definition failed publish-time validation."""


@dataclass(frozen=True)
class FreeTextQuestion:
    """A free-text question and its PII-risk tagging, read from the definition.

    For a plain question, `name` is the question name and `panel_name` /
    `element_name` are None. For a free-text cell inside a paneldynamic (M5.4),
    `name` is the composite sub-question stable_name ("panel.element") used to key
    the PII store and join the marts, `panel_name` is the panel's payload key (an
    array), and `element_name` is the cell's key within each occurrence object —
    so the submit path can navigate `payload[panel_name][i][element_name]` and copy
    one PII row per occurrence.
    """

    name: str
    pii_risk: str | None
    pii_risk_rationale: str | None
    panel_name: str | None = None
    element_name: str | None = None

    @property
    def effective_risk(self) -> str:
        # Absent pii_risk defaults to 'high' — the safe path is the default
        # (design doc §3.9, CLAUDE.md §"silent defaults").
        return self.pii_risk or "high"


def _iter_elements(definition: dict[str, Any]) -> Iterator[dict[str, Any]]:
    # The two SurveyJS shapes are mutually exclusive, exactly as dbt's
    # int_survey_elements treats them: pages[].elements[] wins; top-level
    # elements[] is the fallback only when there's no pages array. Walking both
    # would diverge from the warehouse on a definition carrying both keys
    # (invariant 4) and double-count those questions.
    pages = definition.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, dict):
                for element in page.get("elements", []) or []:
                    if isinstance(element, dict):
                        yield element
        return
    for element in definition.get("elements", []) or []:
        if isinstance(element, dict):
            yield element


def _panel_template_elements(element: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The named template elements of a paneldynamic, in order."""
    for tmpl in element.get("templateElements", []) or []:
        if isinstance(tmpl, dict) and tmpl.get("name"):
            yield tmpl


def extract_free_text_questions(definition: dict[str, Any]) -> list[FreeTextQuestion]:
    """Free-text questions (SurveyJS text/comment) with their pii_risk tagging.

    Descends into paneldynamic templates (M5.4): a free-text panel cell is one
    free-text question per the survey definition, but its answer repeats per
    occurrence — the caller fans the per-occurrence PII rows from `panel_name` /
    `element_name`.
    """
    questions: list[FreeTextQuestion] = []
    for element in _iter_elements(definition):
        etype = element.get("type")
        if etype in FREE_TEXT_TYPES and element.get("name"):
            questions.append(
                FreeTextQuestion(
                    name=element["name"],
                    pii_risk=element.get("pii_risk"),
                    pii_risk_rationale=element.get("pii_risk_rationale"),
                )
            )
        elif etype in REPEATING_TYPES and element.get("name"):
            panel = element["name"]
            for tmpl in _panel_template_elements(element):
                if tmpl.get("type") in FREE_TEXT_TYPES:
                    questions.append(
                        FreeTextQuestion(
                            name=f"{panel}.{tmpl['name']}",
                            pii_risk=tmpl.get("pii_risk"),
                            pii_risk_rationale=tmpl.get("pii_risk_rationale"),
                            panel_name=panel,
                            element_name=tmpl["name"],
                        )
                    )
    return questions


def _normalize_scalar(value: Any) -> str | None:
    # Render a scalar the way dbt's int_survey_options does (`#>> '{}'` extracts
    # a jsonb scalar as unquoted text), so the publish-time uniqueness check
    # agrees with how the warehouse resolves option_key (invariant 4). A bare
    # Python str() diverges: str(True) == 'True' (dbt: 'true'), str(None) ==
    # 'None' (dbt: SQL NULL → missing). JSON null is treated as missing.
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return str(value)


def _choice_value(choice: Any) -> str | None:
    # An object choice keys on `value`; a scalar is the value itself. Both pass
    # through the dbt-matching scalar normalization above.
    if isinstance(choice, dict):
        return _normalize_scalar(choice.get("value"))
    return _normalize_scalar(choice)


def _question_name_refs(expression: Any) -> set[str]:
    """Base identifiers referenced inside a SurveyJS expression string.

    Extracts each `{token}`, takes the base before any `.`/`[`, and drops the
    known context variables. A coarse reference check — enough to catch a
    visibleIf pointing at a question that doesn't exist; the round-trip oracle
    (next gate stage) validates full expression semantics.
    """
    if not isinstance(expression, str):
        return set()
    refs: set[str] = set()
    for token in _BRACE_REF.findall(expression):
        base = re.split(r"[.\[]", token.strip(), maxsplit=1)[0].strip()
        if base and base not in _EXPRESSION_CONTEXT_VARS:
            refs.add(base)
    return refs


def _validate_questions(definition: dict[str, Any]) -> set[str]:
    """Schema + per-question lint. Returns the set of defined question names."""
    names: set[str] = set()
    for element in _iter_elements(definition):
        raw_name = element.get("name")
        if raw_name is None:
            # No name (key absent or JSON null) → display-only element
            # (html/image/…). dbt treats a null `name` as not-a-question; we
            # gate the same boundary.
            continue
        if not isinstance(raw_name, str) or not raw_name:
            # A present-but-non-string/empty name would still be a question to
            # dbt (`->> 'name'` casts 0 → '0', '' is not null), creating a bogus
            # or empty stable_name. Reject rather than silently skip it.
            raise InvalidDefinition(f"question name must be a non-empty string, got {raw_name!r}")
        name = raw_name
        if name in names:
            raise InvalidDefinition(f"duplicate question name {name!r} is not allowed")
        names.add(name)

        qtype = element.get("type")
        if qtype not in KNOWN_QUESTION_TYPES:
            raise InvalidDefinition(
                f"question {name!r}: unsupported type {qtype!r} "
                f"(supported: {', '.join(sorted(KNOWN_QUESTION_TYPES))})"
            )

        if qtype in OPTION_TYPES:
            _validate_choices(name, element.get("choices"))
        elif qtype in MATRIX_TYPES:
            # A matrix expands to one sub-question per cell, each with a composite
            # stable_name ("matrix.row[.column]"). Those names share the warehouse
            # question namespace, so guard the collision here — e.g. a matrix 'm'
            # row 'r' and a plain question literally named 'm.r' would otherwise
            # both hash to one question_id and only surface as a dim uniqueness
            # failure at dbt build. Caught at publish as a clear 422 instead.
            for sub_name in _validate_matrix(name, element):
                _claim_subquestion_name(names, name, sub_name, "matrix")
        elif qtype in REPEATING_TYPES:
            # A paneldynamic expands to one sub-question per template element
            # ("panel.element"); the same composite-name collision applies (a plain
            # question named "panel.element" would hash to the same question_id).
            for sub_name in _validate_paneldynamic(name, element):
                _claim_subquestion_name(names, name, sub_name, "panel")
    return names


def _claim_subquestion_name(names: set[str], owner: str, sub_name: str, kind: str) -> None:
    """Add a composite sub-question stable_name to the question namespace, rejecting
    a collision with an already-defined question (design discussion: keep stable_name
    a clean unique identity rather than fold coordinates into the surrogate key)."""
    if sub_name in names:
        raise InvalidDefinition(
            f"question {owner!r}: {kind} sub-question {sub_name!r} collides with "
            "another question name"
        )
    names.add(sub_name)


def _validate_choices(name: str, choices: Any) -> None:
    if not isinstance(choices, list) or not choices:
        raise InvalidDefinition(
            f"question {name!r}: choice question requires a non-empty 'choices'"
        )
    seen: set[str] = set()
    for choice in choices:
        value = _choice_value(choice)
        if value is None:
            raise InvalidDefinition(f"question {name!r}: a choice is missing a value")
        if value in seen:
            raise InvalidDefinition(f"question {name!r}: duplicate option value {value!r}")
        seen.add(value)


def _matrix_rows(name: str, element: dict[str, Any]) -> list[str]:
    """Row identifiers for a matrix. Every row must carry a value (FR-2 'missing
    matrix row identifiers') and rows must be unique — each becomes a sub-question
    identity downstream (stable_name = "matrix.row"), so a missing or duplicate
    row id would collide or vanish in the warehouse."""
    rows = element.get("rows")
    if not isinstance(rows, list) or not rows:
        raise InvalidDefinition(f"question {name!r}: matrix requires a non-empty 'rows'")
    seen: set[str] = set()
    values: list[str] = []
    for row in rows:
        value = _choice_value(row)
        if value is None:
            raise InvalidDefinition(f"question {name!r}: a matrix row is missing an identifier")
        if value in seen:
            raise InvalidDefinition(f"question {name!r}: duplicate matrix row {value!r}")
        seen.add(value)
        values.append(value)
    return values


def _validate_matrix(name: str, element: dict[str, Any]) -> list[str]:
    """Lint a matrix / matrixdropdown and return its cell sub-question stable_names.

    Checks row ids, column ids, and (for matrixdropdown) per-column cell type +
    choices. Each cell resolves to an option_key downstream, so the column choices
    get the same uniqueness lint as a single-select's `choices`. The returned
    composite names ("matrix.row[.column]") mirror dbt's subquestion_name macro and
    feed the caller's question-name collision guard."""
    rows = _matrix_rows(name, element)
    columns = element.get("columns")
    if not isinstance(columns, list) or not columns:
        raise InvalidDefinition(f"question {name!r}: matrix requires a non-empty 'columns'")

    if element.get("type") == "matrix":
        # Columns are the shared option set every row chooses from.
        seen: set[str] = set()
        for column in columns:
            value = _choice_value(column)
            if value is None:
                raise InvalidDefinition(f"question {name!r}: a matrix column is missing a value")
            if value in seen:
                raise InvalidDefinition(f"question {name!r}: duplicate matrix column {value!r}")
            seen.add(value)
        return [f"{name}.{row}" for row in rows]

    # matrixdropdown: each column is a typed sub-question keyed by its `name`.
    # col_names stays an ordered list — it carries the column order through to the
    # returned sub-question names (the `in` check below also rejects duplicates, so
    # order is the only thing a set would lose; column counts are tiny).
    col_names: list[str] = []
    shared_choices = element.get("choices")
    default_cell = element.get("cellType", "dropdown")
    for column in columns:
        if not isinstance(column, dict) or not column.get("name"):
            raise InvalidDefinition(
                f"question {name!r}: every matrixdropdown column needs a 'name'"
            )
        col_name = column["name"]
        if col_name in col_names:
            raise InvalidDefinition(
                f"question {name!r}: duplicate matrixdropdown column {col_name!r}"
            )
        col_names.append(col_name)
        cell_type = column.get("cellType", default_cell)
        if cell_type not in MATRIX_CELL_TYPES:
            raise InvalidDefinition(
                f"question {name!r}: matrixdropdown column {col_name!r} cellType {cell_type!r} "
                f"is not supported (supported: {', '.join(sorted(MATRIX_CELL_TYPES))})"
            )
        # A column's choices fall back to the matrix-level shared `choices`.
        _validate_choices(f"{name}.{col_name}", column.get("choices", shared_choices))
    return [f"{name}.{row}.{col}" for row in rows for col in col_names]


def _validate_paneldynamic(name: str, element: dict[str, Any]) -> list[str]:
    """Lint a paneldynamic (repeating group) and return its template sub-question
    stable_names ("panel.element").

    A panel repeats its `templateElements` N times. Each named element becomes a
    sub-question; its answer repeats per occurrence (the array drives the fact
    grain's `occurrence`). We support single-select choice cells (→ option_key) and
    free-text cells (→ value_text / PII store) end-to-end — other cell types are
    rejected so a panel answer can't be silently dropped (PANEL_CELL_TYPES). Cell
    pii_risk is gated by _validate_free_text_pii, which descends into panels too.
    The composite names mirror dbt's subquestion_name macro and feed the caller's
    question-name collision guard."""
    templates = element.get("templateElements")
    if not isinstance(templates, list) or not templates:
        raise InvalidDefinition(
            f"question {name!r}: paneldynamic requires non-empty 'templateElements'"
        )
    sub_names: list[str] = []
    seen: set[str] = set()
    for tmpl in templates:
        if not isinstance(tmpl, dict) or not tmpl.get("name"):
            raise InvalidDefinition(
                f"question {name!r}: every paneldynamic template element needs a 'name'"
            )
        element_name = tmpl["name"]
        if not isinstance(element_name, str):
            raise InvalidDefinition(
                f"question {name!r}: paneldynamic element name must be a string, "
                f"got {element_name!r}"
            )
        if element_name in seen:
            raise InvalidDefinition(
                f"question {name!r}: duplicate paneldynamic element {element_name!r}"
            )
        seen.add(element_name)
        cell_type = tmpl.get("type")
        if cell_type not in PANEL_CELL_TYPES:
            raise InvalidDefinition(
                f"question {name!r}: paneldynamic element {element_name!r} type {cell_type!r} "
                f"is not supported (supported: {', '.join(sorted(PANEL_CELL_TYPES))})"
            )
        if cell_type in OPTION_TYPES:
            _validate_choices(f"{name}.{element_name}", tmpl.get("choices"))
        sub_names.append(f"{name}.{element_name}")
    return sub_names


def _calculated_value_names(definition: dict[str, Any]) -> set[str]:
    # SurveyJS calculatedValues are legal `{name}` targets in expressions, so
    # they count as defined references (not dangling).
    names: set[str] = set()
    calculated = definition.get("calculatedValues")
    if isinstance(calculated, list):
        for entry in calculated:
            if isinstance(entry, dict) and entry.get("name"):
                names.add(entry["name"])
    return names


def _iter_conditional_elements(
    definition: dict[str, Any],
) -> Iterator[tuple[dict[str, Any], str]]:
    """Every element that may carry a visibleIf/enableIf, paired with an owner
    label for error messages. Beyond top-level questions this descends into the
    sub-elements a composite type can gate independently: matrix columns
    (matrixdropdown cells) and paneldynamic template elements. Intra-panel/-matrix
    conditional cells are idiomatic SurveyJS, so their dangling references must be
    caught at publish too — the round-trip oracle treats these composite types as
    non-enumerable drivers and would not flag a dangling cell reference."""
    for element in _iter_elements(definition):
        owner = element.get("name") or "<element>"
        yield element, owner
        if not isinstance(owner, str):
            continue
        if element.get("type") in MATRIX_TYPES:
            columns = element.get("columns")
            if isinstance(columns, list):
                for column in columns:
                    if isinstance(column, dict):
                        yield column, f"{owner}.{column.get('name', '<column>')}"
        elif element.get("type") in REPEATING_TYPES:
            for tmpl in _panel_template_elements(element):
                yield tmpl, f"{owner}.{tmpl['name']}"


def _validate_visible_if(definition: dict[str, Any], question_names: set[str]) -> None:
    """Reject dangling references: a question's visibleIf/enableIf may only
    reference a defined question or calculatedValue. A publish-time *reference*
    check, not evaluation — the round-trip oracle (next gate stage) exercises the
    full expression semantics; routing itself is captured at submit, never
    re-evaluated downstream (invariant 3)."""
    defined = question_names | _calculated_value_names(definition)
    for element, owner in _iter_conditional_elements(definition):
        for prop in ("visibleIf", "enableIf"):
            for ref in _question_name_refs(element.get(prop)):
                if ref not in defined:
                    raise InvalidDefinition(
                        f"question {owner!r}: {prop} references unknown question {ref!r}"
                    )


def _validate_free_text_pii(definition: dict[str, Any]) -> None:
    """Free-text PII gate (invariant 6): pii_risk must be low/high if set, and a
    downgrade to 'low' demands an explicit rationale at definition time. Never
    silently downgrade — the default is 'high'."""
    for question in extract_free_text_questions(definition):
        if question.pii_risk is not None and question.pii_risk not in ("low", "high"):
            raise InvalidDefinition(
                f"question {question.name!r}: pii_risk must be 'low' or 'high', "
                f"got {question.pii_risk!r}"
            )
        if question.effective_risk == "low" and not (question.pii_risk_rationale or "").strip():
            raise InvalidDefinition(
                f"question {question.name!r}: downgrading pii_risk to 'low' requires a "
                "non-empty pii_risk_rationale"
            )


def validate_definition(definition: dict[str, Any]) -> None:
    """Run the synchronous publish gate: structural schema → lint → PII checks.

    Raises InvalidDefinition on the first failure (the message reaches the editor
    as the 422 detail). The headless round-trip is a separate, later stage.
    """
    if not definition:
        raise InvalidDefinition("definition must be a non-empty object")
    if "pages" not in definition and "elements" not in definition:
        raise InvalidDefinition("definition must contain 'pages' or 'elements'")
    question_names = _validate_questions(definition)
    _validate_visible_if(definition, question_names)
    _validate_free_text_pii(definition)
