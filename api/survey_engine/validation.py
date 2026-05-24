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
# dim_option. These are the only choice types wired end-to-end today — dbt's
# fact_response_item resolves a single answer_value against dim_option; multi-
# select (array answers) needs fan-out that doesn't exist yet (M5).
CHOICE_TYPES = frozenset({"radiogroup", "dropdown"})

# Question types we support end-to-end enough to publish. A name-bearing element
# of any other type is rejected — you can't publish a type the runtime, gate and
# dbt staging don't all handle (CLAUDE.md §"New question type = three places").
# Deliberately narrow: fact_response_item only populates option_key (single
# choice) and value_text (free-text); value_numeric/value_date are unpopulated,
# so boolean/rating/numeric/date answers would land as all-null fact rows —
# silently dropped and indistinguishable from "shown & skipped". Multi-select,
# matrix, ranking, etc. each land here in M5 alongside their dbt staging + tests.
KNOWN_QUESTION_TYPES = FREE_TEXT_TYPES | CHOICE_TYPES

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

        if qtype in CHOICE_TYPES:
            _validate_choices(name, element.get("choices"))
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


def _validate_visible_if(definition: dict[str, Any], question_names: set[str]) -> None:
    """Reject dangling references: a question's visibleIf/enableIf may only
    reference a defined question or calculatedValue. A publish-time *reference*
    check, not evaluation — the round-trip oracle (next gate stage) exercises the
    full expression semantics; routing itself is captured at submit, never
    re-evaluated downstream (invariant 3)."""
    defined = question_names | _calculated_value_names(definition)
    for element in _iter_elements(definition):
        for prop in ("visibleIf", "enableIf"):
            for ref in _question_name_refs(element.get(prop)):
                if ref not in defined:
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
