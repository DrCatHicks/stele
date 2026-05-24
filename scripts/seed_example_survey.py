"""Seed the example survey (single/multi-select + free-text) and a few responses.

Exercises the real write path (api.survey_engine.service) — including the
definition-snapshot embedding and the PII-risk routing of free-text answers — so
`dbt build` downstream has data spanning all three routing states (answered /
shown-skipped / routed-past) and both PII-risk levels. Used by the verification
runbooks (docs/verification/) and by CI.

Run:  uv run python scripts/seed_example_survey.py
Honors STELE_DATABASE_URL (CI/prod point it at a least-privileged role).

Questions:
  q1, q2   single-select (radiogroup)
  q3       multi-select (checkbox) → fans out to one fact row per chosen option
  q4       ranking → fans out to one fact row per ranked option, each with a rank
  q5       matrix → one single-select sub-question per row (q5.taste, q5.price)
  q6       matrixdropdown → one sub-question per cell (q6.laptop.brand, q6.laptop.os)
  q7       paneldynamic (repeating group) → one sub-question per template element
           (q7.kind option cell, q7.nickname high-risk free-text cell), repeated
           per occurrence: the panel array position drives the fact `occurrence`
  ft_low   free-text, pii_risk='low'  → value reaches marts.value_text
  ft_high  free-text, pii_risk='high' → redacted in marts; copied to
           pii.free_text_responses for the reviewer

One high-risk free-text answer (R1's ft_high) is then promoted by the reviewer
path so `dbt build` exercises the promotion round-trip: a promoted high-risk
response surfaces value_text in the marts while the rest stay redacted. q7's
high-risk panel cell (q7.nickname) stays redacted — exercising a per-occurrence
PII copy (one pii.free_text_responses row per occurrence) that is NOT promoted.

Expected marts after `dbt build` (printed at the end for the runbook):
  dim_respondent: 4 · dim_survey_version: 1 · dim_question: 12
  dim_question_version: 12 · dim_option: 22
  fact_response_item: 55 (24 with an option_key · 4 with value_text · 8 redacted
                          · 6 with a rank)
  fact_response: 50 · pii.free_text_responses: 4 · pii.free_text_review_decisions: 1
  q1 selections — a:2  b:1  c:1     q2 selections — x:1  y:1
  q3 selections — red:1  green:1  blue:1   (R1 picked red+blue, R2 picked green)
  q4 ranks — R1: quality>speed>cost   R2: cost>quality>speed
            (speed rank 2,3 · cost rank 3,1 · quality rank 1,2)
  q5.taste — good:1 (R1) bad:1 (R2)   q5.price — good:1 (R2; R1 left blank)
  q6.laptop.brand — apple:1 (R1) dell:1 (R2)   q6.laptop.os — mac:1 (R1; R2 left blank)
  q7.kind — phone:1 (R1 occ1) laptop:1 (R1 occ2) tablet:1 (R2 occ1)
  q7.nickname — 2 high-risk PII copies (R1 occ1+occ2), redacted in marts
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import select

from api.db import SessionLocal
from api.survey_engine import service
from api.survey_engine.models import FreeTextResponse
from api.survey_engine.schemas import ResponseSubmit

DEFINITION: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [
                {
                    "type": "radiogroup",
                    "name": "q1",
                    "title": "Pick one",
                    "choices": ["a", "b", "c"],
                },
                {
                    "type": "radiogroup",
                    "name": "q2",
                    "title": "Pick another",
                    "choices": ["x", "y"],
                },
                {
                    "type": "checkbox",
                    "name": "q3",
                    "title": "Pick all that apply",
                    "choices": ["red", "green", "blue"],
                },
                {
                    "type": "ranking",
                    "name": "q4",
                    "title": "Rank these in order",
                    "choices": ["speed", "cost", "quality"],
                },
                {
                    "type": "matrix",
                    "name": "q5",
                    "title": "Rate each aspect",
                    # One single-select sub-question per row (q5.taste, q5.price)
                    # over the shared columns; each chosen column → an option_key.
                    "rows": [
                        {"value": "taste", "text": "Taste"},
                        {"value": "price", "text": "Price"},
                    ],
                    "columns": [
                        {"value": "good", "text": "Good"},
                        {"value": "bad", "text": "Bad"},
                    ],
                },
                {
                    "type": "matrixdropdown",
                    "name": "q6",
                    "title": "Describe your laptop",
                    # One single-select sub-question per (row, column):
                    # q6.laptop.brand, q6.laptop.os.
                    "rows": [{"value": "laptop", "text": "Laptop"}],
                    "columns": [
                        {"name": "brand", "cellType": "dropdown", "choices": ["apple", "dell"]},
                        {"name": "os", "cellType": "radiogroup", "choices": ["mac", "win"]},
                    ],
                },
                {
                    "type": "paneldynamic",
                    "name": "q7",
                    "title": "List your devices",
                    # One sub-question per template element (q7.kind, q7.nickname),
                    # repeated per occurrence; the answer is an array of objects.
                    "templateElements": [
                        {
                            "type": "dropdown",
                            "name": "kind",
                            "choices": ["phone", "tablet", "laptop"],
                        },
                        {
                            "type": "comment",
                            "name": "nickname",
                            # High-risk free-text cell: redacted in marts, copied to
                            # pii.free_text_responses once per occurrence.
                            "pii_risk": "high",
                        },
                    ],
                },
                {
                    "type": "comment",
                    "name": "ft_low",
                    "title": "Anything to add? (non-identifying)",
                    "pii_risk": "low",
                    "pii_risk_rationale": "open feedback, screened as non-identifying",
                },
                {
                    "type": "comment",
                    "name": "ft_high",
                    "title": "Describe your role in your own words",
                    # pii_risk defaults to 'high' when absent; stated here for clarity.
                    "pii_risk": "high",
                },
            ],
        }
    ]
}

# (shown_questions, payload) per respondent — covers every routing state across
# single-select, multi-select, ranking, matrix, paneldynamic, and free-text
# questions. q3 (checkbox) and q4 (ranking) answers are arrays that fan out in
# fact_response_item; q4's array order is the rank (first = rank 1). q5 (matrix)
# answers are an object {row: column}; q6 (matrixdropdown) a nested object
# {row: {column: value}} — each cell is a single-select sub-question. q7
# (paneldynamic) answers are an array of objects, one per occurrence — the array
# position is the fact `occurrence`. The shown-set carries the matrix/panel element
# name (q5/q6/q7), not the sub-questions, mirroring the SurveyJS engine. An
# empty/absent array, a row absent from the matrix object, or a cell key absent from
# a panel occurrence object, is a routing row:
#   R1: all shown+answered; q5's "price" row blank (shown-skipped cell); q7 has 2
#       occurrences (one panel cell, q7.nickname, present both times)
#   R2: all shown+answered; q6's "os" cell blank; q7 has 1 occurrence with nickname
#       left blank (shown-skipped panel cell at occurrence 1)
#   R3: q2/q3/q4/q5/q6/q7/ft_high shown but skipped; q1 + ft_low answered
#   R4: only q1 shown/answered; everything else routed past
SUBMISSIONS: list[tuple[list[str], dict[str, Any]]] = [
    (
        ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "ft_low", "ft_high"],
        {
            "q1": "a",
            "q2": "x",
            "q3": ["red", "blue"],
            "q4": ["quality", "speed", "cost"],
            "q5": {"taste": "good"},
            "q6": {"laptop": {"brand": "apple", "os": "mac"}},
            "q7": [
                {"kind": "phone", "nickname": "my work phone"},
                {"kind": "laptop", "nickname": "the big one"},
            ],
            "ft_low": "great",
            "ft_high": "I lead the platform team",
        },
    ),
    (
        ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "ft_low", "ft_high"],
        {
            "q1": "b",
            "q2": "y",
            "q3": ["green"],
            "q4": ["cost", "quality", "speed"],
            "q5": {"taste": "bad", "price": "good"},
            "q6": {"laptop": {"brand": "dell"}},
            "q7": [{"kind": "tablet"}],
            "ft_low": "good",
            "ft_high": "Senior engineer at Acme",
        },
    ),
    (
        ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "ft_low", "ft_high"],
        {"q1": "a", "ft_low": "ok"},
    ),
    (["q1"], {"q1": "c"}),
]


async def seed() -> None:
    async with SessionLocal() as session:
        # Fixture data, not a real-respondent survey — skip the publish round-trip
        # gate so seeding doesn't require the Node/survey-core toolchain (the dbt
        # CI job seeds without installing frontend deps).
        survey = await service.create_draft(session, DEFINITION, for_real_respondents=False)
        published = await service.publish(session, survey.survey_id, survey.version)
        assert published.definition_hash is not None

        for shown_questions, payload in SUBMISSIONS:
            await service.submit_response(
                session,
                published.survey_id,
                published.version,
                ResponseSubmit(
                    definition_hash=published.definition_hash,
                    payload=payload,
                    shown_questions=shown_questions,
                    respondent_id=uuid.uuid4(),
                    client_metadata={"source": "seed_example_survey"},
                ),
            )

        # Reviewer screening pass: promote one high-risk free-text answer so the
        # marts exercise the promotion round-trip (R1's ft_high, "I lead the
        # platform team"). reviewer_id is None — no operator row is seeded here.
        first_high = (
            await session.execute(
                select(FreeTextResponse)
                .where(FreeTextResponse.question_name == "ft_high")
                .order_by(FreeTextResponse.id)
                .limit(1)
            )
        ).scalar_one()
        await service.record_free_text_decision(
            session, first_high.id, reviewer_id=None, status="promoted"
        )

    print(
        f"Seeded survey {published.survey_id} v{published.version} with "
        f"{len(SUBMISSIONS)} responses; promoted 1 high-risk free-text answer."
    )


if __name__ == "__main__":
    asyncio.run(seed())
