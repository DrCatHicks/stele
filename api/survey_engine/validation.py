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

# Choice-based types carry a `choices` array. Mirrors int_survey_options, which
# unnests `choices` for exactly these (closed-ended) questions.
CHOICE_TYPES = frozenset({"radiogroup", "dropdown", "checkbox", "tagbox", "ranking", "imagepicker"})

# Matrix question with fixed `rows`. Only the simple single-select matrix is
# row-id-checked here; matrixdropdown/matrixdynamic carry optional/dynamic rows
# and are out of scope until they're wired end-to-end (M5).
MATRIX_ROW_TYPES = frozenset({"matrix"})

# Question types we support end-to-end enough to publish. A name-bearing element
# of any other type is rejected — you can't publish a type the runtime, gate and
# dbt staging don't all handle (CLAUDE.md §"New question type = three places").
# M5 extends this set as each new type lands in all three.
KNOWN_QUESTION_TYPES = (
    FREE_TEXT_TYPES
    | CHOICE_TYPES
    | MATRIX_ROW_TYPES
    | frozenset({"boolean", "rating", "matrixdropdown", "matrixdynamic"})
)

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
    """A free-text question and its PII-risk tagging, read from the definition."""

    name: str
    pii_risk: str | None
    pii_risk_rationale: str | None

    @property
    def effective_risk(self) -> str:
        # Absent pii_risk defaults to 'high' — the safe path is the default
        # (design doc §3.9, CLAUDE.md §"silent defaults").
        return self.pii_risk or "high"


def _iter_elements(definition: dict[str, Any]) -> Iterator[dict[str, Any]]:
    # Both SurveyJS shapes: pages[].elements[] and top-level elements[]. Mirrors
    # the unnest in dbt's int_survey_elements so API and warehouse agree on what
    # counts as a question.
    pages = definition.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, dict):
                for element in page.get("elements", []) or []:
                    if isinstance(element, dict):
                        yield element
    for element in definition.get("elements", []) or []:
        if isinstance(element, dict):
            yield element


def extract_free_text_questions(definition: dict[str, Any]) -> list[FreeTextQuestion]:
    """Free-text questions (SurveyJS text/comment) with their pii_risk tagging."""
    questions: list[FreeTextQuestion] = []
    for element in _iter_elements(definition):
        if element.get("type") in FREE_TEXT_TYPES and element.get("name"):
            questions.append(
                FreeTextQuestion(
                    name=element["name"],
                    pii_risk=element.get("pii_risk"),
                    pii_risk_rationale=element.get("pii_risk_rationale"),
                )
            )
    return questions


def _choice_value(choice: Any) -> str | None:
    # Mirror int_survey_options: an object choice keys on `value`; a scalar is
    # the value itself (stringified for comparison).
    if isinstance(choice, dict):
        value = choice.get("value")
        return None if value is None else str(value)
    return str(choice)


def _row_id(row: Any) -> str | None:
    # A matrix row id is `value` (SurveyJS) or `name`, or the scalar itself.
    if isinstance(row, dict):
        ident = row.get("value", row.get("name"))
        return None if ident is None else str(ident)
    return str(row)


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
        name = element.get("name")
        if not name:
            # Nameless elements are display-only (html/image/…). dbt treats
            # name-bearing elements as questions; we gate the same boundary.
            continue
        if name in names:
            raise InvalidDefinition(f"duplicate question name {name!r} is not allowed")
        names.add(name)

        qtype = element.get("type")
        if qtype not in KNOWN_QUESTION_TYPES:
            raise InvalidDefinition(
                f"question {name!r}: unsupported type {qtype!r} "
                f"(supported: {', '.join(sorted(KNOWN_QUESTION_TYPES))})"
            )

        if qtype in CHOICE_TYPES:
            _validate_choices(name, element.get("choices"))
        if qtype in MATRIX_ROW_TYPES:
            _validate_matrix_rows(name, element.get("rows"))
    return names


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


def _validate_matrix_rows(name: str, rows: Any) -> None:
    if not isinstance(rows, list) or not rows:
        raise InvalidDefinition(f"question {name!r}: matrix requires a non-empty 'rows'")
    seen: set[str] = set()
    for row in rows:
        ident = _row_id(row)
        if ident is None:
            raise InvalidDefinition(f"question {name!r}: a matrix row is missing an identifier")
        if ident in seen:
            raise InvalidDefinition(f"question {name!r}: duplicate matrix row id {ident!r}")
        seen.add(ident)


def _validate_visible_if(definition: dict[str, Any], question_names: set[str]) -> None:
    """Every question referenced by a visibleIf/enableIf must exist (no dangling
    references). Routing is captured at submit, never reconstructed in SQL
    (invariant 3) — this is a publish-time reference check, not evaluation."""
    for element in _iter_elements(definition):
        for prop in ("visibleIf", "enableIf"):
            for ref in _question_name_refs(element.get(prop)):
                if ref not in question_names:
                    owner = element.get("name") or "<element>"
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
